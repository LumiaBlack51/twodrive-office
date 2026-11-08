[CmdletBinding()]
param(
    [string]$Root,
    [ValidateRange(5,3600)][int]$IntervalSeconds = 30,
    [switch]$Uninstall,
    [switch]$MachineCom
)
$ErrorActionPreference = 'Stop'
if (Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue) {
    throw 'Close Word, Excel and PowerPoint before changing the add-in installation.'
}
$progId = 'TwoDrive.Office.AutoSave'
$clsId = '{72D11CA7-C180-463F-BC48-822DC433A169}'
$base = Join-Path $env:LOCALAPPDATA 'TwoDrive-Office'
$componentBase=if($MachineCom){Join-Path $env:ProgramFiles 'TwoDrive-Office'}else{$base}
$install = Join-Path $componentBase 'msoffice\0.4.0'
$assemblyPath = Join-Path $install 'TwoDrive.Office.dll'
$platform = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue).Platform
if ($platform -notin @('x86','x64')) { throw 'Unable to determine Office bitness; installation was not changed.' }
$view = if ($platform -eq 'x64') { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
$registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, $view)
try {
    $admin=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if($MachineCom -and !$admin){throw '-MachineCom requires an administrator'}
    $uac=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction Stop).EnableLUA
    if(!$Uninstall -and !$MachineCom -and $uac -eq 0 -and $admin){throw 'Windows ignores per-user COM for administrators with UAC disabled. Use -MachineCom for a protected component; Office settings remain per-user.'}
    if ($Uninstall) {
        $scope=if($MachineCom){'LocalMachine'}else{'CurrentUser'}
        if(Test-Path -LiteralPath $assemblyPath){& (Join-Path $PSScriptRoot 'register-component.ps1') -AssemblyPath $assemblyPath -Scope $scope -Platform $platform -Unregister}
        foreach($hostName in @('Word','Excel','PowerPoint')){
            $path="Software\Microsoft\Office\$hostName\Addins\$progId"
            $key=$registry.OpenSubKey($path)
            if($key){try{if($key.GetValue('FriendlyName') -ne 'TwoDrive AutoSave'){throw 'An unrelated Office registration owns this name'}}finally{$key.Dispose()};$registry.DeleteSubKeyTree($path,$false)}
        }
        Write-Output 'Current-user Office registration removed; program files/settings retained. Machine component removal requires -MachineCom.'
        return
    }
    if (-not $Root) { throw '-Root must name an active TwoDrive Development native sync root.' }
    $candidate = Join-Path $PSScriptRoot 'TwoDrive.Office.dll'
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { throw 'Run the installer from the built package.' }
    Add-Type -Path $candidate
    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\')
    $identity = [TwoDrive.Office.NativeRoot]::Identity($resolvedRoot)
    $separator = $identity.IndexOf(':')
    $registration = [Text.UTF8Encoding]::new($false,$true).GetString([Convert]::FromBase64String($identity.Substring($separator+1)))
    if ($registration -cnotmatch '^twodrive-dev-native-v1:[a-f0-9]{64}$') {
        throw 'Installation requires an account-bound production root; generated fixture roots are not installation targets.'
    }
    # Validate before any normal Office profile/registration is changed.
    foreach($p in @($componentBase,(Join-Path $componentBase 'msoffice'),$install)){
        if((Test-Path -LiteralPath $p) -and ((Get-Item -LiteralPath $p).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Component directory cannot be a link'}
    }
    New-Item -ItemType Directory -Path $install -Force | Out-Null
    if($MachineCom){
        # A machine COM server must never load code writable by ordinary users.
        $acl=New-Object Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true,$false)
        $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
        foreach($sid in 'S-1-5-18','S-1-5-32-544'){$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','ContainerInherit,ObjectInherit','None','Allow'))}
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'ReadAndExecute','ContainerInherit,ObjectInherit','None','Allow'))
        foreach($p in @($componentBase,(Join-Path $componentBase 'msoffice'),$install)){Set-Acl -LiteralPath $p -AclObject $acl}
    }
    Copy-Item -LiteralPath $candidate -Destination $assemblyPath -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination $install -Force
    if($MachineCom){
        foreach($p in $assemblyPath,(Join-Path $install 'LICENSE')){
            $fileAcl=New-Object Security.AccessControl.FileSecurity;$fileAcl.SetAccessRuleProtection($true,$false)
            $fileAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
            foreach($sid in 'S-1-5-18','S-1-5-32-544'){$fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),'FullControl','Allow'))}
            $fileAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-32-545'),'ReadAndExecute','Allow'))
            Set-Acl -LiteralPath $p -AclObject $fileAcl
        }
    }
    $assemblyName = [Reflection.AssemblyName]::GetAssemblyName($assemblyPath).FullName
    $configuration = @{ RootPath=$resolvedRoot; RootIdentity=$identity; IntervalSeconds=$IntervalSeconds } | ConvertTo-Json
    $tempSettings = Join-Path $base ('settings-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tempSettings, $configuration, [Text.UTF8Encoding]::new($false))
    $settingsPath = Join-Path $base 'msoffice.json'
    if (Test-Path -LiteralPath $settingsPath) { [IO.File]::Replace($tempSettings, $settingsPath, [NullString]::Value) }
    else { [IO.File]::Move($tempSettings, $settingsPath) }
    $componentScope=if($MachineCom){'LocalMachine'}else{'CurrentUser'}
    & (Join-Path $PSScriptRoot 'register-component.ps1') -AssemblyPath $assemblyPath -Scope $componentScope -Platform $platform
    if($MachineCom){
        # Remove only our former per-user component, leaving Office enablement
        # and this user's root settings in HKCU/LocalAppData.
        $oldDll=Join-Path $base 'msoffice\0.4.0\TwoDrive.Office.dll'
        if(Test-Path -LiteralPath $oldDll){& (Join-Path $PSScriptRoot 'register-component.ps1') -AssemblyPath $oldDll -Scope CurrentUser -Platform $platform -Unregister}
    }
    foreach ($hostName in @('Word','Excel','PowerPoint')) {
        $key = $registry.CreateSubKey("Software\Microsoft\Office\$hostName\Addins\$progId")
        try {
            $key.SetValue('FriendlyName','TwoDrive AutoSave')
            $key.SetValue('Description','Save named TwoDrive documents locally; cloud upload is managed by TwoDrive.')
            $key.SetValue('LoadBehavior',3,[Microsoft.Win32.RegistryValueKind]::DWord)
        } finally { $key.Dispose() }
    }
    Write-Output 'Registered TwoDrive AutoSave for the next Office start. No editor was launched.'
} finally { $registry.Dispose() }
