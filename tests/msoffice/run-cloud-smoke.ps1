param(
    [Parameter(Mandatory=$true)][string]$FixtureOwner,
    [Parameter(Mandatory=$true)][string]$AccountState,
    [Parameter(Mandatory=$true)][string]$ExpectedOwner
)
$ErrorActionPreference='Stop'
if(Get-Process WINWORD,EXCEL,POWERPNT -ErrorAction SilentlyContinue){throw 'Office already running; generated test refused.'}
$program=& (Join-Path $PSScriptRoot 'run-host-smoke.ps1') -CompileOnly
function Start-HiddenPipe([string]$File,[string]$Arguments) {
    $info=[Diagnostics.ProcessStartInfo]::new()
    $info.FileName=(Resolve-Path -LiteralPath $File).Path
    $info.Arguments=$Arguments
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    [Diagnostics.Process]::Start($info)
}
function Read-BoundedLine($Process,[int]$Milliseconds) {
    $read=$Process.StandardOutput.ReadLineAsync()
    if(!$read.Wait($Milliseconds)){throw 'Generated integration stage timed out; preserve its recovery scope.'}
    if($null -eq $read.Result){throw 'Generated integration process ended unexpectedly.'}
    $read.Result
}
$root=Join-Path $env:TEMP ('twodrive-cfapi-fixture-office-cloud-'+[Guid]::NewGuid().ToString('N'))
$owner=$null; $hostProcess=$null; $checkpointReached=$false; $retired=$false
try {
    $owner=Start-HiddenPipe $FixtureOwner ('--allow-generated-cloud-write "'+$AccountState+'" "'+$ExpectedOwner+'" "'+$root+'"')
    $ownerErrors=$owner.StandardError.ReadToEndAsync()
    $ready=Read-BoundedLine $owner 60000 | ConvertFrom-Json
    if(!$ready.ready -or !(Test-Path -LiteralPath $ready.office_folder)){throw 'Native fixture was not prepared.'}
    $hostProcess=Start-HiddenPipe $program ('--native-root "'+$ready.office_folder+'" --cloud-checkpoint')
    $hostErrors=$hostProcess.StandardError.ReadToEndAsync()
    do {
        $line=Read-BoundedLine $hostProcess 90000
        if($line.StartsWith('ready-cloud:')){$baseline=$line.Substring(12);$checkpointReached=$true}
        else {Write-Output $line}
    } while(!$checkpointReached)
    $owner.StandardInput.WriteLine((@{baseline=$baseline}|ConvertTo-Json -Compress))
    $owner.StandardInput.Flush()
    do {$line=Read-BoundedLine $owner 180000;Write-Output $line} while($line -ne 'verified:all-office-documents')
    $hostProcess.StandardInput.WriteLine('finish');$hostProcess.StandardInput.Flush()
    if(!$hostProcess.WaitForExit(30000) -or $hostProcess.ExitCode -ne 0){throw ('Office cleanup failed: '+$hostErrors.Result)}
    if(Test-Path -LiteralPath $baseline){throw 'Generated Office baseline was not retired.'}
    $owner.StandardInput.WriteLine('retire');$owner.StandardInput.Flush()
    $last=Read-BoundedLine $owner 180000
    if($last -ne 'finished:office-cloud-and-native-scopes-retired' -or !$owner.WaitForExit(30000) -or $owner.ExitCode -ne 0 -or (Test-Path -LiteralPath $root)){throw ('Cloud/native retirement failed: '+$ownerErrors.Result)}
    $retired=$true
    'PASS: three actual Office timer saves, conditional personal-OneDrive versions, full readback and native acknowledgement; all generated scopes retired.'
} finally {
    # The host checkpoint is reached only after all its owned editors closed.
    # Never kill unrelated Office instances, or remove an unverified native root.
    if($hostProcess -and !$hostProcess.HasExited -and $checkpointReached){$hostProcess.StandardInput.WriteLine('finish');$hostProcess.StandardInput.Flush();[void]$hostProcess.WaitForExit(10000)}
    if($owner -and !$owner.HasExited){$owner.StandardInput.Close();[void]$owner.WaitForExit(10000)}
    if(!$retired){Write-Warning ('Generated recovery scope retained for inspection: '+$root)}
    if($hostProcess -and $hostProcess.HasExited){$hostProcess.Dispose()}
    if($owner -and $owner.HasExited){$owner.Dispose()}
}
