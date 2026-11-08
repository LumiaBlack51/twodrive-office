[CmdletBinding()]
param(
    [string]$Root,
    [ValidateRange(5,3600)][int]$IntervalSeconds = 30,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
if (Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue) {
    throw 'Close Word, Excel and PowerPoint before changing the add-in installation.'
}
$progId = 'TwoDrive.Office.AutoSave'
$clsId = '{72D11CA7-C180-463F-BC48-822DC433A169}'
$base = Join-Path $env:LOCALAPPDATA 'TwoDrive-Office'
$install = Join-Path $base 'msoffice\0.4.0'
$assemblyPath = Join-Path $install 'TwoDrive.Office.dll'
$platform = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue).Platform
if ($platform -notin @('x86','x64')) { throw 'Unable to determine Office bitness; installation was not changed.' }
$view = if ($platform -eq 'x64') { [Microsoft.Win32.RegistryView]::Registry64 } else { [Microsoft.Win32.RegistryView]::Registry32 }
$registry = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser, $view)
try {
    if ($Uninstall) {
        $owned = $registry.OpenSubKey("Software\Classes\CLSID\$clsId\InprocServer32")
        if ($null -eq $owned) { Write-Output 'Add-in is not registered.'; return }
        try { $codeBase = $owned.GetValue('CodeBase') } finally { $owned.Dispose() }
        if ($codeBase -ne ([Uri]$assemblyPath).AbsoluteUri) { throw 'Registration belongs to another installation; refusing removal.' }
        foreach ($hostName in @('Word','Excel','PowerPoint')) {
            $registry.DeleteSubKeyTree("Software\Microsoft\Office\$hostName\Addins\$progId", $false)
        }
        $registry.DeleteSubKeyTree("Software\Classes\CLSID\$clsId", $false)
        $registry.DeleteSubKeyTree("Software\Classes\$progId", $false)
        Write-Output 'Add-in registration removed. Settings and user data retained.'
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
    New-Item -ItemType Directory -Path $install -Force | Out-Null
    Copy-Item -LiteralPath $candidate -Destination $assemblyPath -Force
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'LICENSE') -Destination $install -Force
    $assemblyName = [Reflection.AssemblyName]::GetAssemblyName($assemblyPath).FullName
    $configuration = @{ RootPath=$resolvedRoot; RootIdentity=$identity; IntervalSeconds=$IntervalSeconds } | ConvertTo-Json
    $tempSettings = Join-Path $base ('settings-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tempSettings, $configuration, [Text.UTF8Encoding]::new($false))
    $settingsPath = Join-Path $base 'msoffice.json'
    if (Test-Path -LiteralPath $settingsPath) { [IO.File]::Replace($tempSettings, $settingsPath, $null) }
    else { [IO.File]::Move($tempSettings, $settingsPath) }
    $key = $registry.CreateSubKey("Software\Classes\CLSID\$clsId\InprocServer32")
    try {
        $key.SetValue('', 'mscoree.dll')
        $key.SetValue('ThreadingModel', 'Both')
        $key.SetValue('Class', 'TwoDrive.Office.AddIn')
        $key.SetValue('Assembly', $assemblyName)
        $key.SetValue('RuntimeVersion', 'v4.0.30319')
        $key.SetValue('CodeBase', ([Uri]$assemblyPath).AbsoluteUri)
    } finally { $key.Dispose() }
    $key = $registry.CreateSubKey("Software\Classes\CLSID\$clsId\ProgId")
    try { $key.SetValue('', $progId) } finally { $key.Dispose() }
    $key = $registry.CreateSubKey("Software\Classes\$progId\CLSID")
    try { $key.SetValue('', $clsId) } finally { $key.Dispose() }
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
