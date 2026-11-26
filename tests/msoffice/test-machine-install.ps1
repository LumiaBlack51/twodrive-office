# Run the packaged installer twice. Only Click-to-Run discovery and the
# account-bound CFAPI identity are simulated on the disposable Windows runner.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or ![Environment]::Is64BitProcess) {
    throw 'Run only in a disposable x64 GitHub-hosted Windows job.'
}
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$package = Join-Path $repo 'dist\msoffice'
$installer = Join-Path $package 'install.ps1'
$machineDir = Join-Path $env:ProgramFiles 'TwoDrive-Office'
$clsid = 'CLSID\{72D11CA7-C180-463F-BC48-822DC433A169}'
$progId = 'TwoDrive.Office.AutoSave'
foreach ($path in @($machineDir, "HKLM:\Software\Classes\$clsid", "HKLM:\Software\Classes\$progId",
        "HKCU:\Software\Classes\$clsid", "HKCU:\Software\Classes\$progId")) {
    if (Test-Path -LiteralPath $path) { throw "Pre-existing installation at $path" }
}
foreach ($app in 'Word','Excel','PowerPoint') {
    if (Test-Path -LiteralPath "HKCU:\Software\Microsoft\Office\$app\Addins\$progId") { throw 'Pre-existing Office add-in' }
}

$fixture = Join-Path $env:RUNNER_TEMP ('twodrive-office-install-' + [Guid]::NewGuid().ToString('N'))
$root = Join-Path $fixture 'root'
$oldLocalAppData = $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $root | Out-Null
Add-Type -TypeDefinition @'
using System;
using System.Text;
namespace TwoDrive.Office {
    public static class NativeRoot {
        public static string FixturePath;
        public static string Identity(string path) {
            if (path != FixturePath) throw new ArgumentException("Unexpected fixture path");
            string binding = "twodrive-dev-native-v1:" + new string('a', 64);
            return "0000000000000001:" + Convert.ToBase64String(Encoding.UTF8.GetBytes(binding));
        }
    }
}
'@
[TwoDrive.Office.NativeRoot]::FixturePath = $root
function Add-Type {
    param([string]$Path)
    if ($Path -ne (Join-Path $package 'TwoDrive.Office.dll')) { throw 'Unexpected installer assembly path' }
}
function Get-ItemProperty {
    [CmdletBinding()]
    param([string]$Path, [string]$Name)
    if ($Path -eq 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration') {
        return [pscustomobject]@{ Platform = 'x64' }
    }
    Microsoft.PowerShell.Management\Get-ItemProperty @PSBoundParameters
}
try {
    $env:LOCALAPPDATA = $fixture
    $base = Join-Path $fixture 'TwoDrive-Office'
    if (Test-Path -LiteralPath $base) { throw 'Fresh profile expected' }
    foreach ($interval in 30,45) {
        & $installer -Root $root -MachineCom -IntervalSeconds $interval
        $settings = Get-Content -Raw -LiteralPath (Join-Path $base 'msoffice.json') | ConvertFrom-Json
        if ($settings.RootPath -ne $root -or $settings.IntervalSeconds -ne $interval -or
            $settings.RootIdentity -ne [TwoDrive.Office.NativeRoot]::Identity($root)) { throw 'Incorrect settings' }
        if (!(Test-Path -LiteralPath "HKLM:\Software\Classes\$clsid\InprocServer32")) { throw 'Component missing' }
        if (@(Get-ChildItem -LiteralPath $base -Filter 'settings-*.tmp').Count) { throw 'Uncommitted settings file' }
    }
    Write-Output 'PASS: fresh machine installation and reinstallation wrote the current-user settings.'
} finally {
    try {
        & $installer -MachineCom -Uninstall
    } finally {
        $env:LOCALAPPDATA = $oldLocalAppData
        if (Test-Path -LiteralPath $machineDir) { Remove-Item -LiteralPath $machineDir -Recurse -Force }
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
