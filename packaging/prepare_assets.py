"""Deterministic native icon, background swatch, notices and application sources."""
from pathlib import Path
import importlib.metadata as metadata
import shutil
import sys
import zipfile
from PIL import Image,ImageDraw

root=Path(__file__).resolve().parents[1]
assets=root/'assets';assets.mkdir(exist_ok=True)
image=Image.new('RGBA',(256,256),(0,0,0,0));draw=ImageDraw.Draw(image)
draw.rounded_rectangle((8,8,248,248),radius=42,fill='#252525',outline='#bdbdbd',width=8)
draw.rounded_rectangle((54,43,202,213),radius=14,outline='#eeeeee',width=9)
draw.ellipse((100,72,156,128),fill='#dedede');draw.rounded_rectangle((79,138,177,186),radius=19,fill='#dedede')
if (assets/'brand-icon.png').exists():
    # Format conversion only; preserve the supplied design and its entire frame.
    image=Image.open(assets/'brand-icon.png').convert('RGBA')
image.save(assets/'app.ico',sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
Image.new('RGB',(1,1),(180,180,180)).save(assets/'default-grey.png')
licenses=root/'packaging'/'generated-licenses';licenses.mkdir(exist_ok=True)
for name in ('PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','numpy','Pillow','opencv-python-headless','watchdog','PyInstaller'):
 dist=metadata.distribution(name)
 candidates=[]
 dist_info=Path(getattr(dist,'_path',Path()))
 if dist_info.is_dir():
  candidates.extend(file for file in dist_info.iterdir()
                    if file.is_file() and any(word in file.name.lower() for word in ('license','copying','notice')))
 for package_path in {Path(dist.locate_file('')), Path(dist.locate_file(name.split('-')[0]))}:
  for pattern in ('LICENSE*','COPYING*','NOTICE*'):
   candidates.extend(file for file in package_path.glob(pattern) if file.is_file())
 for source in candidates:
  target=licenses/name/source.name
  target.parent.mkdir(parents=True,exist_ok=True)
  shutil.copy2(source,target)
python_license=Path(sys.base_prefix)/'LICENSE.txt'
if python_license.exists():shutil.copy2(python_license,licenses/'Python-LICENSE.txt')
with zipfile.ZipFile(root/'packaging'/'application-source.zip','w',zipfile.ZIP_DEFLATED) as archive:
 for folder in ('student_headshot_manager','samples','tests'):
  for file in (root/folder).rglob('*.py'):archive.write(file,file.relative_to(root))
 for name in ('app.py','requirements.txt','requirements-build.lock','build_installer.ps1'):
  if (root/name).exists():archive.write(root/name,name)
