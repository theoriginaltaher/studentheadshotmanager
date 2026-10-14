"""Read-only export planning and verified, exclusive file copies."""
from dataclasses import dataclass, field
from datetime import datetime
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

MODES = ('Selected grey portraits', 'Selected transparent portraits', 'All originals',
         'All processed takes', 'Complete class', 'Complete project')


def safe_name(value, limit=45):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:limit].rstrip(' .')
    if not text: text = 'unnamed'
    if text.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        text = '_' + text
    return text


def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''): result.update(block)
    return result.hexdigest()


def student_filename(student_id, name, extension):
    suffix = extension if str(extension).startswith('.') else f'.{extension}'
    return f"{safe_name(student_id,80)} - {safe_name(name,80)}{suffix.lower()}"


def unique_relative(path, used):
    path = Path(path)
    candidate = path
    counter = 2
    while str(candidate).casefold() in used:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        counter += 1
    used.add(str(candidate).casefold())
    return str(candidate)


@dataclass(frozen=True)
class ExportOptions:
    mode: str = MODES[0]
    class_id: int | None = None
    include_incomplete: bool = False
    every_take: bool = False


@dataclass
class ExportPlan:
    options: ExportOptions
    students: list = field(default_factory=list)
    files: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    protected: list = field(default_factory=list)
    snapshot: str = ''


def read_snapshot(database):
    """No Database constructor/migrations: exporting cannot modify persistent state."""
    connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('BEGIN')
        data={table:[dict(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY rowid')]
              for table in ('classes','students','captures','processing_jobs','processing_reviews','portrait_flags','final_portraits','app_settings')}
        # Thumbnail blobs are irrelevant to the export snapshot.
        for photo in data['captures']: photo.pop('thumbnail',None)
        return data
    finally: connection.close()


def fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


def build_plan(database, options):
    if options.mode not in MODES: raise ValueError('Choose a valid export type.')
    if options.mode=='Complete class' and options.class_id is None: raise ValueError('Choose a class for a complete class export.')
    if options.mode=='Complete project' and options.class_id is not None: raise ValueError('Choose Entire project for a complete project export.')
    data=read_snapshot(database); plan=ExportPlan(options,snapshot=fingerprint(data))
    classes={row['id']:row['name'] for row in data['classes']}
    if options.class_id is not None and options.class_id not in classes: raise ValueError('This class no longer exists.')
    for setting in data['app_settings']:
        if setting['key']=='folders':
            config=json.loads(setting['value']); plan.protected.extend(config[name] for name in ('incoming','originals','processed'))
    jobs={}
    for job in data['processing_jobs']: jobs[job['capture_id']]=job
    selected={row['student_id']:row['capture_id'] for row in data['final_portraits']}
    flags={row['capture_id']:row['flag'] for row in data['portrait_flags']}
    reviews={row['capture_id']:row for row in data['processing_reviews']}
    students=list(data['students'])
    used_paths=set()
    # Archived photographs survive roster deletion; retain them in project-wide exports.
    if options.class_id is None and options.mode in ('All originals','All processed takes','Complete project'):
        for photo in data['captures']:
            if photo['student_record_id'] is None and photo['original_copy_path']:
                students.append(dict(id='orphan-'+photo['id'], student_id=photo['student_id'],full_name=photo['student_name'],class_id=None,orphan_capture=photo['id']))
    for student in students:
        if options.class_id is not None and student['class_id']!=options.class_id: continue
        sid=student['id']; name=student['full_name']; class_name=classes.get(student['class_id'],'Unassigned (deleted roster)')
        photos=[p for p in data['captures'] if (p['id']==student.get('orphan_capture') if 'orphan_capture' in student else p['student_record_id']==sid)]
        final=selected.get(sid); issues=[]; available={}
        if not final: issues.append('No selected portrait')
        for photo in photos:
            cid=photo['id']; assets={}; source=photo['original_copy_path']
            if source:
                plan.protected.append(str(Path(source).parent))
                try:
                    if digest(source)!=photo['content_hash']: raise ValueError('original checksum mismatch')
                    assets['original']=(source,photo['content_hash'])
                except (OSError,ValueError) as exc: issues.append(f'{cid}: original unavailable ({exc})')
            job=jobs.get(cid)
            if job and job['outputs_json'] and job['status']!='failed' and job['source_hash']==photo['content_hash'] and 'original' in assets:
                try:
                    outputs=json.loads(job['outputs_json'])
                    for key in ('cutout','portrait'):
                        try:
                            output=outputs[key]; plan.protected.append(str(Path(output['path']).parent))
                            if digest(output['path'])==output['sha256']: assets[key]=(output['path'],output['sha256'])
                            else: issues.append(f'{cid}: {key} checksum mismatch')
                        except (OSError,ValueError,KeyError,TypeError) as exc:
                            issues.append(f'{cid}: {key} unavailable ({exc})')
                except (OSError,ValueError,KeyError,TypeError) as exc: issues.append(f'{cid}: processed output unavailable ({exc})')
            available[cid]=assets
        final_photo=next((p for p in photos if p['id']==final),None)
        final_job=jobs.get(final)
        final_assets=available.get(final,{})
        review=reviews.get(final,{})
        if final:
            if not final_photo or final_photo['processing_status']!='imported': issues.append('Selected assignment is not confirmed')
            if not all(key in final_assets for key in ('portrait','cutout')): issues.append('Selected processed files missing or invalid')
            if not final_job or final_job['status']!='approved' or review.get('manual_review'): issues.append('Selected processing is not approved')
            if flags.get(final): issues.append('Selected take is flagged: '+flags[final])
        incomplete=bool(issues)
        included=not incomplete or options.include_incomplete
        row={'record_id':sid,'student_id':student['student_id'],'student_name':name,'class':class_name,
             'selected_filename':'','processing_status':'; '.join(issues) if issues else 'Selected processing approved',
             'included':included,'files':[],'errors':[]}
        plan.students.append(row)
        if issues: plan.warnings.append(f"{student['student_id']} · {name} · {class_name}: "+'; '.join(issues))
        if not included: continue
        class_folder=f"{safe_name(class_name)}--{student['class_id'] if student['class_id'] is not None else 'unassigned'}"
        for photo in photos:
            cid=photo['id']; assets=available[cid]
            if options.mode in ('Complete class','Complete project') or options.every_take: keys=('original','cutout','portrait')
            elif options.mode=='All originals': keys=('original',)
            elif options.mode=='All processed takes': keys=('cutout','portrait')
            elif cid==final: keys=('portrait',) if options.mode==MODES[0] else ('cutout',)
            else: keys=()
            for key in keys:
                if key not in assets: continue
                source,sha=assets[key]; folder={'original':'Originals','cutout':'Transparent','portrait':'Grey Backgrounds'}[key]
                extension = Path(source).suffix if key == 'original' else ('.png' if key == 'cutout' else '.jpg')
                filename=student_filename(student['student_id'],name,extension)
                relative=unique_relative(Path(class_folder)/folder/filename,used_paths)
                job=jobs.get(cid)
                status=photo['processing_status'] if key=='original' else job['status']
                item={'source':source,'sha256':sha,'relative':relative,'record_id':sid,'capture_id':cid,'kind':key,'selected':cid==final,
                      'processing_status':status,'review_flag':flags.get(cid,'')}
                plan.files.append(item); row['files'].append(relative)
        row['selected_filename']='; '.join(item['relative'] for item in plan.files if item['record_id']==sid and item['selected'] and item['kind']!='original')
    return plan


def execute_export(database, plan, destination, progress=lambda done,total,message:None):
    if fingerprint(read_snapshot(database))!=plan.snapshot:
        raise ValueError('Project changed since preview. Prepare the export again.')
    root=Path(destination).expanduser()
    if not root.is_absolute(): raise ValueError('Choose an absolute export destination.')
    root=root.resolve()
    for value in plan.protected:
        protected=Path(value).resolve()
        if root==protected or protected in root.parents:
            raise ValueError('Export destination must be outside Incoming, Originals and Processed storage.')
    root.mkdir(parents=True,exist_ok=True)
    target=root/('Export-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:12])
    target.mkdir(exist_ok=False)
    errors=[]; copied=[]
    rows={row['record_id']:dict(row,errors=[]) for row in plan.students}
    for index,item in enumerate(plan.files):
        path=target/item['relative']; created=False
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            checksum=hashlib.sha256()
            with Path(item['source']).open('rb') as source, path.open('xb') as output:
                created=True
                for block in iter(lambda:source.read(1024*1024),b''):
                    output.write(block); checksum.update(block)
            if checksum.hexdigest()!=item['sha256'] or digest(path)!=item['sha256']:
                raise ValueError('File changed or copy verification failed')
            copied.append(item)
        except (OSError,ValueError) as exc:
            message=item['relative']+': '+str(exc); errors.append(message); rows[item['record_id']]['errors'].append(message)
            if created and path.resolve().is_relative_to(target.resolve()):
                try: path.unlink()
                except OSError: pass
        progress(index+1,len(plan.files),item['relative'])
    try:
        # Only successfully copied selected outputs appear as selected filenames.
        with (target/'manifest.csv').open('x',newline='',encoding='utf-8-sig') as output:
            writer=csv.DictWriter(output,fieldnames=['student_id','student_name','class','selected_filename','processing_status','included','exported_files','errors'])
            writer.writeheader()
            for sid,row in rows.items():
                successful=[item for item in copied if item['record_id']==sid]
                writer.writerow({key:row[key] for key in ('student_id','student_name','class','processing_status','included')} |
                    {'selected_filename':'; '.join(i['relative'] for i in successful if i['selected'] and i['kind']!='original'),
                     'exported_files':'; '.join(i['relative'] for i in successful),'errors':'; '.join(row['errors'])})
        summary={'destination':str(target),'copied':len(copied),'planned':len(plan.files),'errors':errors,
                 'included_students':sum(row['included'] for row in plan.students),'excluded_students':sum(not row['included'] for row in plan.students)}
        with (target/'files.csv').open('x',newline='',encoding='utf-8-sig') as output:
            fields=['student_id','student_name','class','filename','capture_id','selected','processing_status','review_flag','sha256']
            writer=csv.DictWriter(output,fieldnames=fields); writer.writeheader()
            for item in copied:
                row=rows[item['record_id']]
                writer.writerow({key:row[key] for key in ('student_id','student_name','class')} |
                    {key:item[key] for key in ('capture_id','selected','processing_status','review_flag','sha256')} | {'filename':item['relative']})
        with (target/'summary.json').open('x',encoding='utf-8') as output:
            json.dump(summary,output,indent=2)
    except OSError as exc:
        raise OSError(f'Partial export saved at {target}. Unable to finish reports: {exc}') from exc
    return summary
