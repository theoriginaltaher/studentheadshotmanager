"""Isolated packaged diagnostics; no user roster or camera is touched."""
import json
import logging
from pathlib import Path
import socket
import sys
import time
from dataclasses import replace
from PIL import Image,ImageDraw
from .database import Database
from .capture_repository import CaptureRepository
from .capture_worker import CaptureController
from .folders import Folders
from .processing import ProcessingSettings,PIPELINE_VERSION
from .processing_service import ProcessingService
from .review_repository import ReviewRepository
from .exporting import ExportOptions,build_plan,execute_export,digest
from .maintenance import backup_database,restore_database,VERSION
from .roster_import import read_csv,suggest_mapping,import_csv


def run(directory,app):
    directory=Path(directory).resolve()
    if directory.exists():return 2  # Never overwrite diagnostic runs or real data.
    directory.mkdir(parents=True)
    report={'version':VERSION,'frozen':getattr(sys,'frozen',False),'executable':sys.executable,'checks':[]}
    db=None;controller=None;window=None
    original_connect=socket.socket.connect
    def offline(*args,**kwargs):raise RuntimeError('Network use is forbidden in the packaged self-test')
    socket.socket.connect=offline
    def passed(message):report['checks'].append(message)
    def wait(predicate):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            app.processEvents()
            if predicate():return
            time.sleep(.02)
        raise AssertionError('Background operation timed out')
    try:
        db=Database(directory/'Roster ü.sqlite3')
        csv=directory/'Students ü.csv';csv.write_text(
            'Student ID,Full name,Class,Filename\n001,José 李 Silva,Prize Class A,001_PRIZE_JOSE\n'
            '002,Second Student,Prize Class A,002_PRIZE_SECOND\n',encoding='utf-8')
        data=read_csv(csv);assert import_csv(data,suggest_mapping(data),db)==2;passed('CSV import with Unicode and spaces')
        repo=CaptureRepository(db);student=db.students(search='001')[0]['id'];session=repo.create_session('Prize-giving synthetic session')
        folders=Folders(*(str(directory/'Photo files ü'/n) for n in ('Incoming','Originals','Processed','Export')))
        Path(folders.incoming).mkdir(parents=True);folders.validate();repo.set_settings('folders',folders.as_dict())
        controller=CaptureController(db.path);states=[];controller.status.connect(lambda message,watching:states.append(watching))
        controller.set_assignment(repo.assignment(student,session));controller.command('resume');wait(lambda:any(states))
        incoming=Path(folders.incoming)/'Synthetic ü.jpg';image=Image.new('RGB',(480,640),(0,220,0));draw=ImageDraw.Draw(image)
        draw.ellipse((185,90,295,230),fill=(180,130,95));draw.rounded_rectangle((140,225,340,570),radius=35,fill=(40,50,140));image.save(incoming,quality=98,subsampling=0)
        original_hash=digest(incoming)
        wait(lambda:bool(repo.photos()) and repo.photos()[0]['processing_status']=='awaiting_confirmation')
        capture=repo.photos()[0]['id'];controller.command('confirm',capture);wait(lambda:repo.get(capture)['processing_status']=='imported')
        controller.stop();wait(lambda:not controller.is_running());passed('Real watchdog event, stable JPEG ingestion and explicit assignment')
        service=ProcessingService(db);settings=replace(ProcessingSettings(),canvas_width=600,canvas_height=800)
        job,cache,_=service.process(capture,settings);assert not cache;service.approve(job['id'])
        assert service.process(capture,settings)[1];passed('Local OpenCV/Pillow PNG/JPEG processing, approval and cache')
        ReviewRepository(db).select(student,capture)
        result=execute_export(db.path,build_plan(db.path,ExportOptions('Complete project',include_incomplete=True)),folders.export)
        assert result['copied']==3 and not result['errors'];assert digest(incoming)==original_hash;passed('Export originals, transparent PNG, grey JPEG and CSV manifests; original unchanged')
        snapshot=backup_database(db.path);restored=directory/'Restored ü.sqlite3';restore_database(snapshot,restored)
        db.close();db=Database(restored)
        assert db.students(search='001')[0]['full_name']=='José 李 Silva';assert ReviewRepository(db).students(filter_name='Selected')
        passed('Database reconnect, final selection persistence, backup and restore')
        service=ProcessingService(db);pending=service.repo.create(capture,settings,PIPELINE_VERSION);service.repo.recover_interrupted()
        assert service.repo.get(pending)['status']=='failed';passed('Interrupted processing recovery')
        from .ui.main_window import MainWindow
        window=MainWindow(db);window.show()
        for index in range(4):window.navigate(index);app.processEvents()
        window.grab().save(str(directory/'window.png'));passed('Native Qt window and all four pages')
        import cv2,numpy,PySide6,PIL,watchdog
        report['modules']={module.__name__:module.__file__ for module in (cv2,numpy,PySide6,PIL,watchdog)}
        report['result']='PASS';passed('Network socket connections blocked during diagnostics')
        code=0
    except Exception as exc:
        logging.exception('Packaged diagnostics failed');report['result']='FAIL';report['error']=str(exc);code=1
    finally:
        if controller:controller.stop();wait(lambda:not controller.is_running())
        if window:
            controllers=(window.capture_page.controller,window.review_page.controller,window.export_page.controller)
            for worker in controllers:worker.stop()
            wait(lambda:not any(worker.is_running() for worker in controllers));window.close();app.processEvents()
        if db:db.close()
        socket.socket.connect=original_connect
        (directory/'result.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    return code
