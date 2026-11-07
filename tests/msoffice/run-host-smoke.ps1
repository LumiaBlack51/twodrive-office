$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue) { throw 'Office already running; test refused.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$assembly = Join-Path $repo 'dist\msoffice\TwoDrive.Office.dll'
$program = Join-Path $repo 'dist\msoffice\TwoDrive.Office.HostSmoke.exe'
& $compiler /nologo /target:exe /platform:x64 /warnaserror+ "/out:$program" "/reference:$assembly" /reference:Microsoft.CSharp.dll /reference:System.Windows.Forms.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll (Join-Path $PSScriptRoot 'HostSmoke.cs')
if ($LASTEXITCODE -ne 0) { throw 'Host smoke compilation failed' }
& $program
if ($LASTEXITCODE -ne 0) { throw 'Host smoke failed; inspect the reported boundary.' }
