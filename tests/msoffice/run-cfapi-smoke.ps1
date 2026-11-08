param([Parameter(Mandatory=$true)][string]$FixtureOwner)
$ErrorActionPreference = 'Stop'
if (Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue) { throw 'Office already running; test refused.' }
$root = Join-Path $env:TEMP ('twodrive-cfapi-fixture-office-' + [Guid]::NewGuid().ToString('N'))
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = (Resolve-Path -LiteralPath $FixtureOwner).Path
$info.Arguments = '"' + $root + '"'
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$owner = [Diagnostics.Process]::Start($info)
try {
    $ready = $owner.StandardOutput.ReadLineAsync()
    if (-not $ready.Wait(30000) -or $ready.Result -ne 'ready:generated-office-only') { throw 'CFAPI fixture did not become ready.' }
    & (Join-Path $PSScriptRoot 'run-host-smoke.ps1') -NativeRoot $root
} finally {
    if (-not $owner.HasExited) {
        $owner.StandardInput.WriteLine('finish')
        $owner.StandardInput.Flush()
        if (-not $owner.WaitForExit(10000)) { $owner.Kill(); throw 'Fixture cleanup timed out; retain and inspect the owned root.' }
    }
    $result = $owner.StandardOutput.ReadToEnd()
    if ($owner.ExitCode -ne 0 -or $result.Trim() -ne 'finished:unregistered-empty-root' -or (Test-Path -LiteralPath $root)) {
        throw 'Fixture was not completely retired; preserve the owned root for recovery.'
    }
    $owner.Dispose()
}
'PASS: real CFAPI root binding, three Office hosts and complete fixture cleanup.'
