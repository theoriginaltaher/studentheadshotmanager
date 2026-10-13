param(
    [string]$Python = "$PSScriptRoot\.venv\Scripts\python.exe",
    [string]$ISCC = "$PSScriptRoot\packaging\tools\InnoSetup\ISCC.exe",
    [string]$VcRuntime = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Redist\MSVC\14.44.35112\x64\Microsoft.VC143.CRT",
    [string]$ReleaseDir = "$PSScriptRoot\release"
)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
Set-Location -LiteralPath $root
function Remove-BuildDirectory {
    param([string]$Path)
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            [GC]::Collect()
            [GC]::WaitForPendingFinalizers()
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            if (-not (Test-Path -LiteralPath $Path)) { return }
            throw "Directory still exists after cleanup: $Path"
        } catch {
            if ($attempt -eq 5) { throw }
            Start-Sleep -Milliseconds (500 * $attempt)
        }
    }
}
foreach ($file in @($Python,$ISCC,(Join-Path $VcRuntime 'vcruntime140.dll'))) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing build tool: $file. See packaging/README.md (build machine only)." }
}
$reports = Join-Path $root 'packaging\reports'
New-Item -ItemType Directory -Force -Path $reports | Out-Null
& $Python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency validation failed.' }
& $Python -m pytest -q --junitxml="$reports\pytest.xml" 2>&1 | Tee-Object -FilePath "$reports\pytest.txt"
if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Build stopped before cleaning artifacts.' }
foreach ($targetPath in @((Join-Path $root 'build'),(Join-Path $root 'dist'),$ReleaseDir)) {
    $target = [IO.Path]::GetFullPath($targetPath)
    if ([IO.Path]::GetDirectoryName($target) -ne $root) { throw "Unsafe cleanup target: $target" }
    if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing linked cleanup target: $target" }
        Remove-BuildDirectory $target
    }
    New-Item -ItemType Directory -Path $target | Out-Null
}
& $Python packaging\prepare_assets.py
if ($LASTEXITCODE -ne 0) { throw 'Asset preparation failed.' }
$env:SHM_VC_RUNTIME = $VcRuntime
$env:PATH = "$root\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
& $Python -m PyInstaller --noconfirm --clean --distpath "$root\dist" --workpath "$root\build" packaging\StudentHeadshotManager.spec 2>&1 | Tee-Object -FilePath "$reports\pyinstaller.txt"
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
& $Python packaging\audit_dependencies.py "$root\dist\StudentHeadshotManager" | Tee-Object -FilePath "$reports\native-dependencies.json"
if ($LASTEXITCODE -ne 0) { throw 'Native dependency audit failed.' }
$exe = Join-Path $root 'dist\StudentHeadshotManager\StudentHeadshotManager.exe'
foreach ($file in @($exe,"$root\dist\StudentHeadshotManager\_internal\python312.dll","$root\dist\StudentHeadshotManager\_internal\vcruntime140.dll","$root\dist\StudentHeadshotManager\_internal\PySide6\plugins\platforms\qwindows.dll")) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Required bundled component missing: $file" }
}
# Isolate runtime environment and working directory; this is NOT a clean-VM test.
$smoke = Join-Path $reports ('packaged-smoke-'+[guid]::NewGuid().ToString('N'))
$info = New-Object Diagnostics.ProcessStartInfo
$info.FileName=$exe
$info.Arguments='--self-test "'+$smoke+'"'
$info.UseShellExecute=$false
$info.CreateNoWindow=$true
$info.WorkingDirectory=$env:TEMP
$info.EnvironmentVariables['PATH']="$env:SystemRoot\System32;$env:SystemRoot"
$info.EnvironmentVariables.Remove('PYTHONPATH')
$info.EnvironmentVariables.Remove('PYTHONHOME')
$info.EnvironmentVariables['QT_QPA_PLATFORM']='offscreen'
$info.EnvironmentVariables['LOCALAPPDATA']=Join-Path $reports 'isolated-user'
$testProcess=[Diagnostics.Process]::Start($info)
if (-not $testProcess.WaitForExit(180000)) { $testProcess.Kill(); throw 'Packaged smoke test timed out.' }
if ($testProcess.ExitCode -ne 0) { throw "Packaged smoke test failed; inspect $smoke and isolated-user logs." }
$smokeResult=Get-Content -Raw -LiteralPath (Join-Path $smoke 'result.json') | ConvertFrom-Json
if ($smokeResult.result -ne 'PASS' -or -not $smokeResult.frozen) { throw 'Frozen-app diagnostics did not pass.' }
$release = [IO.Path]::GetFullPath($ReleaseDir)
& $ISCC "/O$release" packaging\installer.iss 2>&1 | Tee-Object -FilePath "$reports\inno-setup.txt"
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
$installer=Join-Path $release 'StudentHeadshotManager-Setup.exe'
if (-not (Test-Path -LiteralPath $installer)) { throw 'Expected installer was not produced.' }
Compress-Archive -Path "$root\dist\StudentHeadshotManager" -DestinationPath "$release\StudentHeadshotManager-Portable.zip" -CompressionLevel Optimal
$result=[ordered]@{result='BUILD PASS';installer=$installer;bytes=(Get-Item -LiteralPath $installer).Length;sha256=(Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash;packaged_test=$smoke;clean_vm_test='NOT RUN';camera_test='NOT RUN'}
$result | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath "$release\build-result.json"
Write-Host ($result | ConvertTo-Json)
