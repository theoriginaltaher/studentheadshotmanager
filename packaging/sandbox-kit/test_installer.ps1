param([string]$Installer="$PSScriptRoot\..\release\StudentHeadshotManager-Setup.exe",[string]$EvidenceRoot="$PSScriptRoot\reports")
$ErrorActionPreference='Stop'
$identity='{F58BDCC2-2C40-4F93-BB09-F6CEBB681A67}_is1'
if (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$identity") { throw 'An existing per-user installation uses this AppId; test in another Windows account.' }
$run=Join-Path ([IO.Path]::GetFullPath($EvidenceRoot)) ('installer-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$install=Join-Path $run ('Installed app '+[char]0x00FC+' with spaces')
$userData=Join-Path $run 'Isolated AppData'
function Install-App([string]$log) {
    $process=Start-Process -FilePath ([IO.Path]::GetFullPath($Installer)) -ArgumentList ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR="'+$install+'" /LOG="'+$log+'"') -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer failed with exit code $($process.ExitCode); see $log" }
}
function Start-Isolated([string]$arguments) {
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $install 'StudentHeadshotManager.exe'
    $info.Arguments=$arguments; $info.UseShellExecute=$false; $info.WorkingDirectory=$env:TEMP
    $info.EnvironmentVariables['PATH']="$env:SystemRoot\System32;$env:SystemRoot"
    $info.EnvironmentVariables.Remove('PYTHONPATH'); $info.EnvironmentVariables.Remove('PYTHONHOME')
    $info.EnvironmentVariables.Remove('QT_QPA_PLATFORM')
    $info.EnvironmentVariables['LOCALAPPDATA']=$userData
    return [Diagnostics.Process]::Start($info)
}
Install-App (Join-Path $run 'install.log')
$diagnostics=Join-Path $run 'Synthetic workflow'
$process=Start-Isolated ('--self-test "'+$diagnostics+'"')
if (-not $process.WaitForExit(180000)) { $process.Kill(); throw 'Installed synthetic workflow timed out.' }
if ($process.ExitCode -ne 0) { throw 'Installed workflow failed; inspect result.json and logs.' }
$result=Get-Content -Raw -LiteralPath (Join-Path $diagnostics 'result.json') | ConvertFrom-Json
if ($result.result -ne 'PASS' -or -not $result.frozen) { throw 'Installed workflow did not pass.' }
$liveFolder=Join-Path $userData 'StudentHeadshotManager'
New-Item -ItemType Directory -Force -Path $liveFolder | Out-Null
$liveDatabase=Join-Path $liveFolder 'roster.sqlite3'
$diagnosticDatabase=Get-ChildItem -LiteralPath $diagnostics -Filter 'Roster*.sqlite3' | Select-Object -First 1
Copy-Item -LiteralPath $diagnosticDatabase.FullName -Destination $liveDatabase
for ($launch=0;$launch -lt 2;$launch++) {
    $process=Start-Isolated ''
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do { Start-Sleep -Milliseconds 200; $process.Refresh() } while ($process.MainWindowHandle -eq 0 -and -not $process.HasExited -and [DateTime]::UtcNow -lt $deadline)
    if ($process.HasExited -or $process.MainWindowHandle -eq 0 -or -not $process.Responding) { throw 'Normal installed GUI launch failed.' }
    if (-not $process.CloseMainWindow()) { throw 'Unable to request a clean GUI close.' }
    if (-not $process.WaitForExit(30000)) { throw 'GUI did not finish its background work before closing.' }
    if ($process.ExitCode -ne 0) { throw 'GUI returned an error.' }
    if ($launch -eq 0) {
        $before=(Get-FileHash -LiteralPath $liveDatabase).Hash
        Install-App (Join-Path $run 'upgrade.log')
        if ((Get-FileHash -LiteralPath $liveDatabase).Hash -ne $before) { throw 'Reinstallation changed user database.' }
    }
}
$before=(Get-FileHash -LiteralPath $liveDatabase).Hash
$uninstall=Start-Process -FilePath (Join-Path $install 'unins000.exe') -ArgumentList ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="'+(Join-Path $run 'uninstall.log')+'"') -WindowStyle Hidden -Wait -PassThru
if ($uninstall.ExitCode -ne 0) { throw 'Uninstall failed.' }
if ((Get-FileHash -LiteralPath $liveDatabase).Hash -ne $before) { throw 'Uninstallation changed or removed user database.' }
$photoFolder=Get-ChildItem -LiteralPath $diagnostics -Directory -Filter 'Photo files*' | Select-Object -First 1
if (-not $photoFolder -or -not (Test-Path -LiteralPath (Join-Path $photoFolder.FullName 'Originals'))) { throw 'Photographs were removed.' }
@{result='PASS';installation='per-user';gui_launches=2;upgrade='same-version reinstall, data preserved';uninstall='database and originals preserved';clean_vm='NOT RUN';reboot='NOT RUN';path=$run} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $run 'installer-result.json')
Write-Host "Installer validation passed: $run"
