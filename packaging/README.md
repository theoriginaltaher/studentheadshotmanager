# Windows packaging, build operators only

End users run StudentHeadshotManager-Setup.exe. They do not run Python or pip.

Build in PowerShell 7 on x64 Windows using Python 3.12, Inno Setup 6.7.3, and licensed Visual Studio
2022 Build Tools with its x64 VC143 redistributable directory.
Download Inno Setup from https://jrsoftware.org/isdl.php and verify Authenticode
(Pyrsys B.V.). This checkout uses packaging/tools/InnoSetup/ISCC.exe.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.lock
.\build_installer.ps1 -ISCC 'C:\Path To\Inno Setup 6\ISCC.exe' -VcRuntime 'C:\Path To\VC\Redist\MSVC\14.44.35112\x64\Microsoft.VC143.CRT'
```

The script runs all tests, stops on failure, safely cleans build/dist/release,
generates resources/notices, builds the one-folder bundle, verifies runtime/plugin
files, runs isolated frozen-app diagnostics, compiles Inno Setup and creates a ZIP.
It prints the installer's path, size and SHA-256. Logs are under packaging/reports.
No downloads occur in the installer or application. Source photographs and user
databases are excluded from build inputs. Application sources accompany Qt libraries.

Outputs: release/StudentHeadshotManager-Setup.exe, optional Portable.zip, build-result.json.
Keep the Inno AppId stable for upgrades. Bump maintenance.py, installer.iss and
version_info.txt versions together. Default machine-wide install uses Program Files
and requests administrator rights. An explicit per-user option installs binaries
under LocalAppData/Programs, separate from data. Uninstall removes installed files
and shortcuts only. App-local VC runtime DLLs avoid prerequisite installation.
Binary target: Windows 10 1809+ / Windows 11 x64. Use an OS edition still covered
by Microsoft support, including applicable Windows 10 servicing/ESU.

No publisher certificate was supplied. Installer/app are unsigned; Windows may
display unknown-publisher or SmartScreen prompts. Public release signing requires
the publisher's certificate. A development-PC smoke test is not clean-VM certification.

Frozen diagnostics from any directory, using a NEW output path:

```powershell
& 'C:\Program Files\Student Headshot Manager\StudentHeadshotManager.exe' --self-test 'C:\Test Results\Run 01'
```

Synthetic diagnostics write result.json and deliberately reject Python socket
connections; this does not disable the PC network adapter. See TEST_REPORT.md for
actual evidence and the remaining clean-machine/hardware qualification checklist.
