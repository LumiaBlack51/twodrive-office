[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [ValidateSet('CurrentUser','LocalMachine')][string]$Scope='CurrentUser',
    [ValidateSet('x86','x64')][string]$Platform='x64',
    [switch]$Unregister
)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSEdition -eq 'Core'){
    $forward=@('-NoProfile','-NonInteractive','-File',$PSCommandPath,'-AssemblyPath',$AssemblyPath,'-Scope',$Scope,'-Platform',$Platform)
    if($Unregister){$forward+='-Unregister'}
    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" @forward
    if($LASTEXITCODE -ne 0){throw 'Windows PowerShell COM registration failed'}
    return
}
$prog='TwoDrive.Office.AutoSave'
$cls='{72D11CA7-C180-463F-BC48-822DC433A169}'
$resolved=(Resolve-Path -LiteralPath $AssemblyPath).ProviderPath
$name=[Reflection.AssemblyName]::GetAssemblyName($resolved)
if($name.Name -ne 'TwoDrive.Office'){throw 'Unexpected component assembly'}
$uri=[Reflection.Assembly]::ReflectionOnlyLoadFrom($resolved).CodeBase
$machine=$Scope -eq 'LocalMachine'
if($machine){
    if(![Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Machine COM registration requires an administrator'}
    $protected=@($env:ProgramFiles,${env:ProgramFiles(x86)},$env:ProgramW6432)|Where-Object{$_}|Select-Object -Unique
    if(!@($protected|Where-Object{$resolved.StartsWith($_.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)}).Count){throw 'Machine COM code must reside in protected Program Files'}
    $trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    $write=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
    $probe=$resolved
    while($probe){
        if((Get-Item -LiteralPath $probe).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Machine COM code path contains a link'}
        $acl=Get-Acl -LiteralPath $probe
        if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted){throw 'Machine COM code path has an untrusted owner'}
        foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
            $sid=$rule.IdentityReference.Value
            if($rule.AccessControlType -eq 'Allow' -and !($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and ($rule.FileSystemRights -band $write) -and $sid -notin $trusted){throw 'Machine COM code is writable outside trusted system principals'}
        }
        if(@($protected|Where-Object{$probe -eq $_}).Count){break}
        $probe=Split-Path $probe -Parent
    }
}
$hive=if($machine){[Microsoft.Win32.RegistryHive]::LocalMachine}else{[Microsoft.Win32.RegistryHive]::CurrentUser}
$view=if($Platform -eq 'x64'){[Microsoft.Win32.RegistryView]::Registry64}else{[Microsoft.Win32.RegistryView]::Registry32}
$reg=[Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
try {
    $base="Software\Classes\CLSID\$cls"
    $existing=$reg.OpenSubKey($base+'\InprocServer32')
    if($existing){try{$prior=$existing.GetValue('CodeBase')}finally{$existing.Dispose()};if(!$prior -or ([Uri]$prior).LocalPath -ne $resolved){throw 'A different component owns this COM class'}}
    if($Unregister){
        if(!$existing){return}
        $reg.DeleteSubKeyTree($base,$false)
        $reg.DeleteSubKeyTree("Software\Classes\$prog",$false)
        return
    }
    foreach($path in @($base,"Software\Classes\$prog")){$key=$reg.CreateSubKey($path);try{$key.SetValue('','TwoDrive.Office.AddIn')}finally{$key.Dispose()}}
    foreach($suffix in @('\InprocServer32',('\InprocServer32\'+$name.Version.ToString()))){
        $key=$reg.CreateSubKey($base+$suffix)
        try {
            if($suffix -eq '\InprocServer32'){$key.SetValue('','mscoree.dll');$key.SetValue('ThreadingModel','Both')}
            $key.SetValue('Class','TwoDrive.Office.AddIn');$key.SetValue('Assembly',$name.FullName)
            $key.SetValue('RuntimeVersion','v4.0.30319');$key.SetValue('CodeBase',$uri)
        }finally{$key.Dispose()}
    }
    $key=$reg.CreateSubKey($base+'\ProgId');try{$key.SetValue('',$prog)}finally{$key.Dispose()}
    $key=$reg.CreateSubKey("Software\Classes\$prog\CLSID");try{$key.SetValue('',$cls)}finally{$key.Dispose()}
    $key=$reg.CreateSubKey($base+'\Implemented Categories\{62C8FE65-4EBB-45E7-B440-6E39B2CDBF29}');$key.Dispose()
}finally{$reg.Dispose()}
