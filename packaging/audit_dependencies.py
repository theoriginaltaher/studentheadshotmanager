"""Fail a build if any non-Windows native dependency is absent from the bundle."""
import json
import os
from pathlib import Path
import sys
import pefile

root=Path(sys.argv[1]).resolve()
files=[file for file in root.rglob('*') if file.suffix.lower() in ('.dll','.pyd','.exe')]
bundled={file.name.casefold() for file in files}
system=Path(os.environ['SystemRoot'])/'System32'
missing=[];os_dependencies=set();runtime=set()
for file in files:
    pe=pefile.PE(str(file),fast_load=True)
    pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
    for entry in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
        name=entry.dll.decode('ascii').lower()
        if name.startswith(('vcruntime','msvcp','concrt')):
            runtime.add(name)
            if name not in bundled:missing.append(f'{file.name}: unbundled VC runtime {name}')
        elif name not in bundled:
            if name.startswith(('api-ms-','ext-ms-')) or (system/name).exists():os_dependencies.add(name)
            else:missing.append(f'{file.name}: {name}')
    pe.close()
report={'native_files':len(files),'missing':missing,'app_local_runtime':sorted(runtime),'windows_dependencies':sorted(os_dependencies)}
print(json.dumps(report,indent=2))
if missing:sys.exit(1)
