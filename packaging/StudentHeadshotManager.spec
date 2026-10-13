# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
root=Path(SPECPATH).parent
runtime=Path(os.environ['SHM_VC_RUNTIME'])
a=Analysis([str(root/'app.py')],pathex=[str(root)],
 binaries=[(str(file),'.') for file in runtime.glob('*.dll')],
 datas=[(str(root/'assets'),'assets'),(str(root/'samples'/'students.csv'),'samples'),
        (str(root/'packaging'/'generated-licenses'),'licenses'),
        (str(root/'packaging'/'application-source.zip'),'source'),
        (str(root/'README.md'),'docs'),(str(root/'CAMERA_SETUP.md'),'docs'),
        (str(root/'THIRD_PARTY_NOTICES.md'),'docs')],
 hiddenimports=['watchdog.observers.read_directory_changes','PySide6.QtPrintSupport'],
 hookspath=[],hooksconfig={},runtime_hooks=[],
 excludes=['pytest','tkinter','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtQml','PySide6.QtQuick'],
 noarchive=False)
# Windows 10/11 supply UCRT and API-set forwarding DLLs. Do not accidentally
# collect alternate copies from unrelated applications on the build machine PATH.
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower()!='ucrtbase.dll'
            and not Path(entry[0]).name.lower().startswith(('api-ms-win-','ext-ms-win-'))]
# The workstation uses native widgets and JPEG/PNG, not PDF or Qt Quick/virtual
# keyboard modules. Exclude those optional plugin families and their libraries.
unused=('qt6pdf','qt6virtualkeyboard','qt6qml','qt6quick','qpdf.dll','qtvirtualkeyboardplugin.dll')
a.binaries=[entry for entry in a.binaries if not Path(entry[0]).name.lower().startswith(unused)]
if any('codex-runtimes' in entry[1].lower() for entry in a.binaries):
    raise RuntimeError('Unrelated development runtime leaked into the bundle')
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='StudentHeadshotManager',
 debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
 icon=str(root/'assets'/'app.ico'),version=str(root/'packaging'/'version_info.txt'))
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='StudentHeadshotManager')
