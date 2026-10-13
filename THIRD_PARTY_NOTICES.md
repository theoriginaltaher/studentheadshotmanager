# Bundled components

Python 3.12, PySide6/Qt 6.8.3, shiboken6, NumPy, OpenCV, Pillow and watchdog are
bundled. Exact versions are in requirements-build.lock. Upstream notices collected
from installed distributions accompany the application under _internal/licenses.

Qt/PySide are dynamically linked under their applicable licences. Compatible
libraries may be replaced and modifications debugged as allowed by those licences.
Application Python sources are included in _internal/source/application-source.zip
for inspection/rebuilding. No cryptographic mechanism prevents library replacement.
Corresponding upstream sources:

- https://download.qt.io/archive/qt/6.8/6.8.3/submodules/
- https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.3-src/
- https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3
- https://www.python.org/downloads/source/
- https://github.com/opencv/opencv/tree/4.12.0
- https://github.com/python-pillow/Pillow/tree/11.3.0
- https://github.com/numpy/numpy/tree/v2.2.6
- https://github.com/gorakhargosh/watchdog/tree/v6.0.0

PyInstaller's exception permits distribution of applications using its bootloader.
Microsoft VC runtime DLLs are deployed app-locally from the installed Visual Studio
2022 Build Tools redistributable directory. Build operators must hold the applicable
licence and redistribution rights. No arbitrary System32 DLLs are copied.
See https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files .
Universal CRT is an OS component on supported Windows 10/11. No prerequisites are
downloaded at installation or first launch. Inno Setup compiles the installer.
No Nikon software is redistributed. This application is not Nikon-endorsed.
No code-signing certificate was supplied for this build.
