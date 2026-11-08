$ErrorActionPreference='Stop'
if($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted'){throw 'This registration test is restricted to disposable hosted CI'}
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$helper=Join-Path $repo 'msoffice\register-component.ps1'
$classKey='HKLM:\Software\Classes\CLSID\{72D11CA7-C180-463F-BC48-822DC433A169}'
if(Test-Path -LiteralPath $classKey){throw 'A pre-existing component must not be replaced by this test'}
$fixture=Join-Path $env:ProgramFiles ('TwoDrive Office COM Test '+[Guid]::NewGuid().ToString('N'))
$dll=Join-Path $fixture 'TwoDrive.Office.dll'
$other=Join-Path $fixture 'other\TwoDrive.Office.dll'
New-Item -ItemType Directory -Path (Split-Path $other) | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'dist\msoffice\TwoDrive.Office.dll') -Destination $dll
Copy-Item -LiteralPath $dll -Destination $other
$directoryAcl=New-Object Security.AccessControl.DirectorySecurity
$directoryAcl.SetAccessRuleProtection($true,$false);$directoryAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
foreach($sid in 'S-1-5-18','S-1-5-32-544'){$directoryAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
$directoryAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
foreach($p in $fixture,(Split-Path $other)){Set-Acl -LiteralPath $p -AclObject $directoryAcl}
foreach($p in $dll,$other){
 $secure=New-Object Security.AccessControl.FileSecurity;$secure.SetAccessRuleProtection($true,$false);$secure.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
 foreach($sid in 'S-1-5-18','S-1-5-32-544'){$secure.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))}
 $secure.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'ReadAndExecute','Allow'));Set-Acl -LiteralPath $p -AclObject $secure
}
$registered=$false
try {
    # Deliberately unsafe machine code must be refused before registration.
    $acl=Get-Acl -LiteralPath $dll
    $unsafe=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'Modify','Allow')
    $acl.AddAccessRule($unsafe);Set-Acl -LiteralPath $dll -AclObject $acl
    $refused=$false;try{& $helper -AssemblyPath $dll -Scope LocalMachine}catch{$refused=$_.Exception.Message -like '*writable outside*'}
    if(!$refused -or (Test-Path -LiteralPath $classKey)){throw 'Unsafe machine code was accepted'}
    $acl.RemoveAccessRuleSpecific($unsafe);Set-Acl -LiteralPath $dll -AclObject $acl
    & $helper -AssemblyPath $dll -Scope LocalMachine
    $registered=$true
    # The protected path contains spaces. URI escaping must match CLR/RegAsm.
    $activation='try {$object=New-Object -ComObject TwoDrive.Office.AutoSave; if(!$object.Status($null)){throw "Callback interface missing"}; exit 0} catch {Write-Error $_; exit 1}'
    $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($activation))
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $start.Arguments='-NoProfile -NonInteractive -Sta -WindowStyle Hidden -EncodedCommand '+$encoded
    $start.UseShellExecute=$false;$start.CreateNoWindow=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $child=[Diagnostics.Process]::Start($start)
    $stdout=$child.StandardOutput.ReadToEnd();$stderr=$child.StandardError.ReadToEnd();$child.WaitForExit()
    if($child.ExitCode -ne 0){throw ('Fresh-process COM activation failed: '+$stderr)}
    $refused=$false;try{& $helper -AssemblyPath $other -Scope LocalMachine -Unregister}catch{$refused=$_.Exception.Message -like '*different component*'}
    if(!$refused -or !(Test-Path -LiteralPath $classKey)){throw 'Foreign uninstall was not rejected'}
    & $helper -AssemblyPath $dll -Scope LocalMachine
    'PASS: protected machine COM activation, spaced path, ownership refusal and re-registration; no Office/root/profile used.'
} finally {
    if($registered){& $helper -AssemblyPath $dll -Scope LocalMachine -Unregister}
    $resolved=(Resolve-Path -LiteralPath $fixture).ProviderPath
    if((Split-Path $resolved) -ne $env:ProgramFiles -or (Split-Path $resolved -Leaf) -notlike 'TwoDrive Office COM Test *'){throw 'Unexpected fixture cleanup path'}
    Remove-Item -LiteralPath $other,$dll
    Remove-Item -LiteralPath (Split-Path $other),$resolved
}
