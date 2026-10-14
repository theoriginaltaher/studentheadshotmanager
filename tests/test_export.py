import csv
from dataclasses import replace
from pathlib import Path
import pytest
from samples.generate_processing_samples import make_sample
from student_headshot_manager.capture_repository import CaptureRepository
from student_headshot_manager.ingestion import IngestionEngine
from student_headshot_manager.exporting import ExportOptions,build_plan,execute_export,safe_name,digest
from student_headshot_manager.processing import ProcessingSettings
from student_headshot_manager.processing_service import ProcessingService
from student_headshot_manager.review_repository import ReviewRepository
from student_headshot_manager.ui.main_window import MainWindow
from tests.test_processing_ui import wait_for


def ready(db,processing_capture):
    capture,*_=processing_capture
    service=ProcessingService(db)
    job,_,_=service.process(capture,replace(ProcessingSettings(),canvas_width=120,canvas_height=160))
    service.approve(job['id'])
    student=ReviewRepository(db).students()[0]['id']
    ReviewRepository(db).select(student,capture)
    return capture,student


def another_take(db,processing_capture,student=None):
    _,_,_,folders=processing_capture; repo=CaptureRepository(db)
    student=student or ReviewRepository(db).students()[0]['id']
    path=Path(folders.incoming)/'second.jpg'
    image=make_sample(size=(140,180));image.save(path,quality=97)
    clock=[0]; engine=IngestionEngine(repo,folders,clock=lambda:clock[0])
    engine.discover(path,repo.assignment(student,repo.sessions()[0]['id']));clock[0]=3;engine.tick()
    capture=repo.photos()[0]['id'];engine.accept(capture)
    ProcessingService(db).process(capture,replace(ProcessingSettings(),canvas_width=120,canvas_height=160))
    return capture


@pytest.mark.parametrize('name',['CON','nul.txt','a/b:c*?<>|','..','a'*200,'名字'])
def test_windows_filename_sanitization(name):
    result=safe_name(name)
    assert result and len(result)<=46 and not any(char in result for char in '<>:"/\\|?*')
    assert not result.endswith((' ','.'))
    assert result.upper().split('.')[0] not in ('CON','NUL')


def test_selected_only_structure_manifest_and_readonly(db,processing_capture,tmp_path):
    capture,student=ready(db,processing_capture); another_take(db,processing_capture)
    db.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before=digest(db.path); originals={str(p):digest(p) for p in Path(processing_capture[3].originals).rglob('*.jpg')}
    plan=build_plan(db.path,ExportOptions())
    assert len(plan.files)==1 and plan.files[0]['capture_id']==capture
    result=execute_export(db.path,plan,tmp_path/'delivery')
    root=Path(result['destination']); file=root/plan.files[0]['relative']
    assert file.exists() and file.parent.name=='Grey Backgrounds'
    assert 'Test class--' in str(file) and file.name=='001 - Synthetic Subject.jpg'
    with (root/'manifest.csv').open(encoding='utf-8-sig',newline='') as stream: rows=list(csv.DictReader(stream))
    assert rows[0]['student_id']=='001' and rows[0]['selected_filename']==plan.files[0]['relative']
    assert digest(db.path)==before
    assert all(digest(path)==checksum for path,checksum in originals.items())
    again=execute_export(db.path,plan,tmp_path/'delivery')
    assert again['destination']!=result['destination'] and file.exists()


def test_missing_selection_include_exclude_and_empty_manifest(db,processing_capture,tmp_path):
    db.save_student('002','No Photos',db.create_class('Class B'))
    options=ExportOptions(mode='All originals')
    plan=build_plan(db.path,options)
    assert len(plan.warnings)==2 and not plan.files
    assert all(not row['included'] for row in plan.students)
    included=build_plan(db.path,replace(options,include_incomplete=True))
    assert len(included.files)==1 and all(row['included'] for row in included.students)
    result=execute_export(db.path,plan,tmp_path/'empty-export')
    with (Path(result['destination'])/'manifest.csv').open(encoding='utf-8-sig') as source:
        rows=list(csv.DictReader(source))
    assert len(rows)==2 and all(row['selected_filename']=='' for row in rows)


def test_complete_class_project_and_duplicate_names(db,processing_capture,tmp_path):
    capture,student=ready(db,processing_capture)
    classroom=db.student(student)['class_id']
    other=db.save_student('002','Synthetic Subject',db.create_class('Test/class'))
    another_take(db,processing_capture,other)
    options=ExportOptions(mode='Complete project',include_incomplete=True)
    plan=build_plan(db.path,options)
    names=[i['relative'].casefold() for i in plan.files]
    assert len(names)==len(set(names))==6
    result=execute_export(db.path,plan,tmp_path/'delivery')
    root=Path(result['destination'])
    assert len([p for p in root.iterdir() if p.is_dir()])==2
    for folder in (p for p in root.iterdir() if p.is_dir()):
        assert {p.name for p in folder.iterdir()}=={'Originals','Transparent','Grey Backgrounds'}
    scoped=build_plan(db.path,ExportOptions('Complete class',classroom,True))
    assert len(scoped.students)==1 and len(scoped.files)==3
    with pytest.raises(ValueError): build_plan(db.path,ExportOptions('Complete class'))
    with pytest.raises(ValueError): build_plan(db.path,ExportOptions('Complete project',classroom))


def test_transparent_processed_and_every_take(db,processing_capture):
    ready(db,processing_capture); another_take(db,processing_capture)
    plan=build_plan(db.path,ExportOptions('Selected transparent portraits'))
    assert len(plan.files)==1 and plan.files[0]['relative'].endswith('.png')
    assert len(build_plan(db.path,ExportOptions('All processed takes')).files)==4
    assert len(build_plan(db.path,ExportOptions(every_take=True)).files)==6


def test_changed_selection_requires_repreview(db,processing_capture,tmp_path):
    _,student=ready(db,processing_capture);plan=build_plan(db.path,ExportOptions())
    ReviewRepository(db).clear_selection(student)
    with pytest.raises(ValueError,match='changed since preview'):execute_export(db.path,plan,tmp_path/'delivery')
    assert not (tmp_path/'delivery').exists()


def test_changed_source_reports_error_without_overwriting(db,processing_capture,tmp_path):
    ready(db,processing_capture);plan=build_plan(db.path,ExportOptions())
    Path(plan.files[0]['source']).write_bytes(b'changed')
    result=execute_export(db.path,plan,tmp_path/'delivery')
    assert result['copied']==0 and len(result['errors'])==1
    assert not (Path(result['destination'])/plan.files[0]['relative']).exists()
    with (Path(result['destination'])/'manifest.csv').open(encoding='utf-8-sig') as source:
        row=next(csv.DictReader(source))
    assert row['selected_filename']=='' and row['errors']


def test_protected_destination_and_missing_file(db,processing_capture):
    ready(db,processing_capture);plan=build_plan(db.path,ExportOptions())
    with pytest.raises(ValueError,match='outside'):execute_export(db.path,plan,Path(processing_capture[3].originals)/'export')
    Path(plan.files[0]['source']).unlink()
    missing=build_plan(db.path,ExportOptions())
    assert not missing.files and missing.warnings


def test_project_retains_archived_orphans(db,processing_capture):
    _,student=ready(db,processing_capture);db.delete_student(student)
    plan=build_plan(db.path,ExportOptions('Complete project',include_incomplete=True))
    assert len(plan.files)==3 and plan.students[0]['class']=='Unassigned (deleted roster)'


def test_export_ui_preview_and_background_copy(qapp,db,processing_capture,tmp_path):
    ready(db,processing_capture)
    window=MainWindow(db); page=window.export_page
    try:
        page.destination.setText(str(tmp_path/'delivery')); assert not page.start.isEnabled()
        page.prepare_export();wait_for(qapp,lambda:not page.busy)
        assert page.plan is not None and page.start.isEnabled()
        page.incomplete.setChecked(True);assert page.plan is None and not page.start.isEnabled()
        page.prepare_export();wait_for(qapp,lambda:not page.busy)
        page.start_export();wait_for(qapp,lambda:not page.busy)
        assert page.completed and (Path(page.completed)/'manifest.csv').exists()
        assert page.open.isEnabled() and 'complete' in page.status.text()
    finally:
        for controller in (window.review_page.controller,window.capture_page.controller,page.controller):controller.stop()
        wait_for(qapp,lambda:not window.review_page.controller.is_running() and not page.controller.is_running())
        window.close()

def test_colliding_sanitized_names_still_have_distinct_paths(db,processing_capture):
    _,student=ready(db,processing_capture)
    class_id=db.student(student)['class_id'];db.update_class(class_id,'A/B')
    db.save_student('x/y','Same:Name',class_id,record_id=student)
    other=db.save_student('x:y','Same/Name',db.create_class('A:B'))
    another_take(db,processing_capture,other)
    plan=build_plan(db.path,ExportOptions('Complete project',include_incomplete=True))
    assert len({i['relative'].casefold() for i in plan.files})==6
    assert len({Path(i['relative']).parts[0] for i in plan.files})==2


def test_copy_failure_is_reported_and_new_export_retries(db,processing_capture,tmp_path,monkeypatch):
    ready(db,processing_capture);plan=build_plan(db.path,ExportOptions())
    original_open=Path.open
    def fail_copy(path,mode='r',*args,**kwargs):
        if mode=='xb': raise PermissionError('Simulated destination failure')
        return original_open(path,mode,*args,**kwargs)
    monkeypatch.setattr(Path,'open',fail_copy)
    failed=execute_export(db.path,plan,tmp_path/'delivery')
    assert failed['copied']==0 and failed['errors']
    assert (Path(failed['destination'])/'manifest.csv').exists()
    monkeypatch.undo()
    retried=execute_export(db.path,plan,tmp_path/'delivery')
    assert retried['copied']==1 and not retried['errors']
    assert retried['destination']!=failed['destination']


def test_including_incomplete_keeps_other_available_output(db,processing_capture):
    ready(db,processing_capture)
    plan=build_plan(db.path,ExportOptions('Selected transparent portraits'))
    Path(plan.files[0]['source']).unlink()
    partial=build_plan(db.path,ExportOptions('Complete project',include_incomplete=True))
    assert {item['kind'] for item in partial.files}=={'original','portrait'}
    assert partial.warnings
