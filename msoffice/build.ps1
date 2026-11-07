[CmdletBinding()]
param([switch]$Test)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
$out = Join-Path $repo 'dist\msoffice'
New-Item -ItemType Directory -Path $out -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework 4.x compiler is required' }
$sources = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' | ForEach-Object FullName)
$assembly = Join-Path $out 'TwoDrive.Office.dll'
& $compiler /nologo /target:library /platform:anycpu /optimize+ /warnaserror+ "/out:$assembly" /reference:System.Windows.Forms.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:Microsoft.CSharp.dll @sources
if ($LASTEXITCODE -ne 0) { throw 'MS Office add-in compilation failed' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install.ps1') -Destination $out -Force
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $out -Force
if ($Test) {
    $testExe = Join-Path $out 'TwoDrive.Office.Tests.exe'
    & $compiler /nologo /target:exe /platform:anycpu /warnaserror+ "/out:$testExe" "/reference:$assembly" (Join-Path $repo 'tests\msoffice\ControllerTests.cs')
    if ($LASTEXITCODE -ne 0) { throw 'MS Office tests compilation failed' }
    & $testExe
    if ($LASTEXITCODE -ne 0) { throw 'MS Office regression failed' }
}
Get-FileHash -LiteralPath $assembly -Algorithm SHA256 | Select-Object Hash,Path
