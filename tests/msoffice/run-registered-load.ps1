[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ExpectedRoot,[Parameter(Mandatory=$true)][string]$Report)
$ErrorActionPreference='Stop'
# Explicit deployment acceptance. Never open/save documents or use a user editor.
if(@(Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue).Count){throw 'Office is already running; registered-load check refused'}
$base=Join-Path $env:LOCALAPPDATA 'TwoDrive-Office'
$settings=Get-Content -LiteralPath (Join-Path $base 'msoffice.json') -Raw|ConvertFrom-Json
if($settings.RootPath -ne $ExpectedRoot){throw 'Installed root does not match the requested deployment'}
$classes='Software\Classes\CLSID\{72D11CA7-C180-463F-BC48-822DC433A169}\InprocServer32'
$reg=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine,[Microsoft.Win32.RegistryView]::Registry64)
try{$key=$reg.OpenSubKey($classes);if($key){try{$dll=([Uri]$key.GetValue('CodeBase')).LocalPath}finally{$key.Dispose()}}}finally{$reg.Dispose()}
if(!$dll){$dll=Join-Path $base 'msoffice\0.4.0\TwoDrive.Office.dll'}
Add-Type -Path $dll
if([TwoDrive.Office.NativeRoot]::Identity($ExpectedRoot) -ne $settings.RootIdentity){throw 'Installed root identity changed'}
$results=@();$phase='initial'
try {
 foreach($hostName in 'Word','Excel','PowerPoint'){
  $app=$null;$docs=$null;$collection=$null;$addin=$null;$owned=$false;$ownedPid=$null
  try {
   $phase=$hostName+' create'
   if(@(Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue).Count){throw 'An Office editor appeared before acquisition'}
   $createdAfter=Get-Date
   $app=New-Object -ComObject ($hostName+'.Application')
   $phase=$hostName+' ownership'
   if($hostName -ne 'PowerPoint' -and $app.Visible){throw 'Visible Office instance refused'}
   $docs=if($hostName -eq 'Word'){$app.Documents}elseif($hostName -eq 'Excel'){$app.Workbooks}else{$app.Presentations}
   if($docs.Count -ne 0){throw 'An instance containing documents was not acquired'}
   $processName=if($hostName -eq 'Word'){'WINWORD'}elseif($hostName -eq 'Excel'){'EXCEL'}else{'POWERPNT'}
   $new=@(Get-Process -Name $processName|Where-Object StartTime -ge $createdAfter)
   if($new.Count -ne 1){throw 'Owned host process not uniquely identified'}
   $owned=$true;$ownedPid=$new[0].Id
   $phase=$hostName+' connect'
   $collection=$app.COMAddIns;$addin=$collection.Item('TwoDrive.Office.AutoSave');$addin.Connect=$true
   if(!$addin.Connect -or $addin.Guid -ne '{72D11CA7-C180-463F-BC48-822DC433A169}'){throw 'Registered managed add-in did not connect'}
   $results+=[pscustomobject]@{host=$hostName;connected=$true;pid=$ownedPid;documents=0}
  }finally{
   # Automation teardown explicitly disconnects, avoiding a retained add-in
   # timer/application reference. Restore the installed startup preference.
   if($addin){try{$addin.Connect=$false}catch{}}
   if($owned -and $app){try{if($hostName -eq 'Word'){$app.Quit(0)}else{$app.Quit()}}catch{}}
   foreach($obj in @($addin,$collection,$docs,$app)){if($obj -and [Runtime.InteropServices.Marshal]::IsComObject($obj)){try{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($obj)}catch{}}}
   $app=$null;$docs=$null;$collection=$null;$addin=$null;[GC]::Collect();[GC]::WaitForPendingFinalizers()
   $keyPath="HKCU:\Software\Microsoft\Office\$hostName\Addins\TwoDrive.Office.AutoSave"
   if(Test-Path -LiteralPath $keyPath){Set-ItemProperty -LiteralPath $keyPath -Name LoadBehavior -Value 3}
   if($ownedPid){
    $left=Get-Process -Id $ownedPid -ErrorAction SilentlyContinue
    if($left){[void]$left.WaitForExit(5000);$left=Get-Process -Id $ownedPid -ErrorAction SilentlyContinue
     if($left){if($left.MainWindowHandle -ne 0 -or $left.StartTime -lt $createdAfter){throw 'An unexpected visible host was retained'};Stop-Process -Id $ownedPid;[void]$left.WaitForExit(5000)}
    }
   }
  }
 }
 [pscustomobject]@{passed=$true;utc=[DateTime]::UtcNow.ToString('o');root=$ExpectedRoot;root_identity_verified=$true;documents_opened=0;documents_saved=0;results=$results}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath $Report -Encoding UTF8
}catch{[pscustomobject]@{passed=$false;phase=$phase;error=$_.Exception.Message;stack=$_.ScriptStackTrace;completed=$results}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath $Report -Encoding UTF8;throw}
