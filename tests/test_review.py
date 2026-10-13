from dataclasses import replace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from student_headshot_manager.database import Database
from student_headshot_manager.capture_repository import CaptureRepository
from student_headshot_manager.review_repository import ReviewRepository
from student_headshot_manager.processing import ProcessingSettings
from student_headshot_manager.processing_service import ProcessingService
from student_headshot_manager.ui.main_window import MainWindow
from tests.test_processing_ui import wait_for


def clone_take(db, capture_id):
    row=dict(CaptureRepository(db).get(capture_id))
    row.update(id='second-take',content_hash=None,source_key='second-source')
    columns=','.join(row); slots=','.join('?' for _ in row)
    with db.transaction(): db.connection.execute(f'INSERT INTO captures ({columns}) VALUES ({slots})',tuple(row.values()))
    return row['id']


def test_explicit_selection_replace_and_restart(db,processing_capture):
    capture,*_=processing_capture; repo=ReviewRepository(db); student=repo.students()[0]['id']
    assert repo.students()[0]['final_capture_id'] is None
    second=clone_take(db,capture)
    repo.select(student,capture); repo.select(student,second)
    assert sum(row['selected'] for row in repo.takes(student))==1
    reopened=Database(db.path)
    try:
        saved=ReviewRepository(reopened)
        assert saved.students()[0]['final_capture_id']==second
        saved.clear_selection(student)
        assert saved.students()[0]['review_status']=='Needs review'
    finally: reopened.close()


def test_flags_filters_and_assignment_invalidate_final(db,processing_capture):
    capture,*_=processing_capture; repo=ReviewRepository(db); student=repo.students()[0]['id']
    other=db.save_student('002','Another Student',db.create_class('Other class'))
    assert len(repo.students(filter_name='Not photographed'))==1
    with pytest.raises(ValueError): repo.select(other,capture)
    repo.select(student,capture)
    assert len(repo.students(filter_name='Selected'))==1
    repo.flag(capture,'Retake required')
    assert not repo.students(filter_name='Selected')
    assert len(repo.students(filter_name='Retake required'))==1
    with pytest.raises(ValueError): repo.select(student,capture)
    reopened=Database(db.path)
    try: assert ReviewRepository(reopened).takes(student)[0]['review_flag']=='Retake required'
    finally: reopened.close()
    repo.flag(capture,''); repo.select(student,capture)
    CaptureRepository(db).reassign(capture,other)
    assert not repo.students(filter_name='Selected')
    with pytest.raises(ValueError): repo.select(other,capture)


def test_discard_and_delete_clear_selection(db,processing_capture):
    capture,*_=processing_capture; repo=ReviewRepository(db); student=repo.students()[0]['id']
    repo.select(student,capture); CaptureRepository(db).discard(capture)
    assert repo.students()[0]['review_status']=='Not photographed'
    assert db.connection.execute('SELECT COUNT(*) FROM final_portraits').fetchone()[0]==0
    db.delete_student(student)


def test_navigation_shortcuts_preview_and_no_automatic_selection(qapp,db,processing_capture):
    capture,*_=processing_capture
    student=ReviewRepository(db).students()[0]['id']
    db.save_student('002','Zed Student',db.student(student)['class_id'])
    ProcessingService(db).process(capture,replace(ProcessingSettings(),canvas_width=180,canvas_height=240))
    window=MainWindow(db); window.show(); window.navigate(2); browser=window.review_workspace.browser
    try:
        wait_for(qapp,lambda:browser.payload is not None)
        assert ReviewRepository(db).students()[0]['final_capture_id'] is None
        assert all(view.canvas.has_image for view in browser.views.values())
        browser.students.setFocus(); window.activateWindow(); qapp.processEvents()
        QTest.keyClick(browser.students,Qt.Key_Right,Qt.AltModifier)
        assert browser.student_id()!=student and browser.grid.count()==0
        QTest.keyClick(browser.students,Qt.Key_Left,Qt.AltModifier)
        assert browser.student_id()==student
        wait_for(qapp,lambda:browser.payload is not None)
        QTest.keyClick(browser.students,Qt.Key_Return,Qt.ControlModifier)
        assert ReviewRepository(db).students()[0]['final_capture_id']==capture
        wait_for(qapp,lambda:browser.open_button.isEnabled())
        QTest.keyClick(browser.students,Qt.Key_O,Qt.ControlModifier)
        wait_for(qapp,lambda:bool(browser.dialogs))
        tabs=browser.dialogs[0].findChild(__import__('PySide6.QtWidgets',fromlist=['QTabWidget']).QTabWidget)
        assert tabs.widget(0).canvas.sceneRect().width()==120
        assert tabs.widget(2).canvas.sceneRect().width()==180
        browser.dialogs[0].close()
        browser.filter.setCurrentText('Not photographed')
        assert browser.students.count()==1 and browser.grid.count()==0
    finally:
        window.review_page.controller.stop(); window.capture_page.controller.stop()
        wait_for(qapp,lambda:not window.review_page.controller.is_running() and not window.capture_page.controller.is_running())
        window.close()

@pytest.mark.parametrize('flag',['Retake required','Processing adjustment','Manual review'])
def test_every_flag_persists_and_resolving_never_selects(db,processing_capture,flag):
    capture,*_=processing_capture; repo=ReviewRepository(db); student=repo.students()[0]['id']
    repo.select(student,capture); repo.flag(capture,flag)
    reopened=Database(db.path)
    try:
        review=ReviewRepository(reopened)
        assert review.takes(student)[0]['review_flag']==flag
        assert review.students(filter_name='Needs review')
        assert not review.students(filter_name='Selected')
        review.flag(capture,'')
        assert not review.students(filter_name='Selected')
    finally: reopened.close()


def test_full_resolution_payload_is_not_thumbnail_sized():
    import io
    from PIL import Image
    from student_headshot_manager.processing_worker import preview_payload
    data=io.BytesIO(); Image.new('RGB',(2500,100),'green').save(data,format='JPEG')
    assert preview_payload(None,data.getvalue())['original'].width()==2048
    assert preview_payload(None,data.getvalue(),full=True)['original'].width()==2500


def test_version_three_upgrade_preserves_roster_and_captures(db,processing_capture):
    capture,*_=processing_capture
    db.connection.executescript('DROP TRIGGER invalidate_final_assignment; DROP TABLE final_portraits; DROP TABLE portrait_flags; PRAGMA user_version=3;')
    reopened=Database(db.path)
    try:
        assert reopened.connection.execute('PRAGMA user_version').fetchone()[0]==4
        assert ReviewRepository(reopened).students()[0]['take_count']==1
        assert CaptureRepository(reopened).get(capture)['processing_status']=='imported'
    finally: reopened.close()
