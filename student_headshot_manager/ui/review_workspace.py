"""Student-first take review, alongside the existing processing editor."""
import sqlite3
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QIcon, QPixmap, QKeySequence
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QListWidget, QListWidgetItem, QSplitter, QTabWidget, QDialog)
from ..review_repository import ReviewRepository, FILTERS, FLAGS
from .image_view import ImageView


class ReviewWorkspace(QTabWidget):
    def __init__(self, db, processor, parent=None):
        super().__init__(parent)
        self.processor = processor
        self.browser = TakeBrowser(db, processor, self)
        self.addTab(self.browser, 'Students && takes')
        self.addTab(processor, 'Processing tools')


class TakeBrowser(QWidget):
    def __init__(self, db, processor, workspace):
        super().__init__()
        self.db, self.processor, self.workspace = db, processor, workspace
        self.repo = ReviewRepository(db)
        self.token = -100
        self.payload = None
        self.dialogs = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        title = QLabel('Choose final portraits'); title.setObjectName('title'); layout.addWidget(title)
        filters = QHBoxLayout()
        self.classes = QComboBox(); self.classes.setAccessibleName('Review class')
        self.filter = QComboBox(); self.filter.addItems(FILTERS); self.filter.setAccessibleName('Review status filter')
        filters.addWidget(QLabel('Class')); filters.addWidget(self.classes, 1)
        filters.addWidget(QLabel('Status')); filters.addWidget(self.filter, 1)
        refresh = QPushButton('Refresh'); refresh.clicked.connect(self.refresh); filters.addWidget(refresh)
        layout.addLayout(filters)
        nav = QHBoxLayout()
        self.previous = QPushButton('Previous student'); self.previous.clicked.connect(lambda: self.move_student(-1))
        self.next = QPushButton('Next student'); self.next.clicked.connect(lambda: self.move_student(1))
        nav.addWidget(self.previous); nav.addWidget(self.next)
        hint = QLabel('Alt+Left / Right · Ctrl+Return selects · Ctrl+O opens'); hint.setWordWrap(True); nav.addWidget(hint,1)
        layout.addLayout(nav)
        split = QSplitter(Qt.Horizontal)
        self.students = QListWidget(); self.students.setAccessibleName('Students and review status'); self.students.setMinimumWidth(180)
        self.students.setWordWrap(True)
        self.students.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        split.addWidget(self.students)
        right = QWidget(); content = QVBoxLayout(right); content.setContentsMargins(8,0,0,0)
        self.summary = QLabel(); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.PlainText); content.addWidget(self.summary)
        self.grid = QListWidget(); self.grid.setViewMode(QListWidget.IconMode); self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static); self.grid.setIconSize(QSize(76,76)); self.grid.setGridSize(QSize(145,125))
        self.grid.setWordWrap(True); self.grid.setMinimumHeight(135); self.grid.setMaximumHeight(165)
        self.grid.setAccessibleName('All takes for selected student'); content.addWidget(self.grid)
        self.previews = QTabWidget(); self.views = {}
        for key, label in [('original','Original'),('cutout','Transparent cutout'),('portrait','Grey composite')]:
            view = ImageView(label + ' · preview up to 2048 px', checker=key=='cutout')
            view.canvas.setMinimumHeight(50)
            self.views[key] = view; self.previews.addTab(view,label)
        content.addWidget(self.previews,1)
        self.message = QLabel('Choose a student, then a take.'); self.message.setWordWrap(True); self.message.setTextFormat(Qt.PlainText); content.addWidget(self.message)
        split.addWidget(right); split.setSizes([220,700]); split.setChildrenCollapsible(False); layout.addWidget(split,1)
        actions = QHBoxLayout()
        self.select_button = QPushButton('Mark selected'); self.select_button.setObjectName('primary'); self.select_button.clicked.connect(self.select_take)
        self.clear_button = QPushButton('Clear selection'); self.clear_button.clicked.connect(self.clear_selection)
        self.open_button = QPushButton('Open full resolution'); self.open_button.clicked.connect(self.open_image)
        self.process_button = QPushButton('Adjust processing'); self.process_button.clicked.connect(self.edit_processing)
        for button in (self.select_button,self.clear_button,self.open_button,self.process_button): actions.addWidget(button)
        layout.addLayout(actions)
        flags = QHBoxLayout(); flags.addWidget(QLabel('Take flag'))
        self.flag = QComboBox(); self.flag.addItems(['No flag / resolved',*FLAGS[1:]]); flags.addWidget(self.flag,1)
        self.flag_button = QPushButton('Save flag'); self.flag_button.clicked.connect(self.save_flag); flags.addWidget(self.flag_button)
        layout.addLayout(flags)
        self.classes.currentIndexChanged.connect(self.refresh_students)
        self.filter.currentIndexChanged.connect(self.refresh_students)
        self.students.currentItemChanged.connect(self.student_changed)
        self.grid.currentItemChanged.connect(self.take_changed)
        self.grid.itemDoubleClicked.connect(lambda _: self.open_image())
        processor.controller.done.connect(self.on_done)
        for shortcut, callback in [('Alt+Left',lambda:self.move_student(-1)),('Alt+Right',lambda:self.move_student(1)),('Ctrl+Return',self.select_take),('Ctrl+O',self.open_image)]:
            action=QAction(self); action.setShortcut(QKeySequence(shortcut)); action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            action.triggered.connect(callback); self.addAction(action)
        self.refresh()

    def showEvent(self,event):
        super().showEvent(event)
        self.refresh()

    def student_id(self):
        item=self.students.currentItem()
        return item.data(Qt.UserRole) if item else None

    def take(self):
        item=self.grid.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh(self):
        current=self.classes.currentData()
        self.classes.blockSignals(True); self.classes.clear(); self.classes.addItem('All classes',None)
        for row in self.db.classes(): self.classes.addItem(row['name'],row['id'])
        self.classes.setCurrentIndex(max(0,self.classes.findData(current))); self.classes.blockSignals(False)
        self.refresh_students()

    def refresh_students(self):
        current=self.student_id(); old_index=self.students.currentRow()
        self.students.blockSignals(True); self.students.clear()
        rows=self.repo.students(self.classes.currentData(),self.filter.currentText())
        index=-1
        for i,row in enumerate(rows):
            text=f"{row['full_name']} ({row['student_id']})\n{row['review_status']} · {row['take_count']} takes"
            if row['final_capture_id'] and row['review_status']!='Selected': text+=' · final selected'
            item=QListWidgetItem(text); item.setData(Qt.UserRole,row['id']); item.setToolTip(text+'\n'+row['class_name']); self.students.addItem(item)
            if row['id']==current: index=i
        if rows: self.students.setCurrentRow(index if index>=0 else min(max(old_index,0),len(rows)-1))
        self.students.blockSignals(False); self.student_changed()

    def move_student(self,step):
        index=self.students.currentRow()+step
        if 0<=index<self.students.count(): self.students.setCurrentRow(index)

    def student_changed(self,*_):
        old=self.take(); old_id=old['id'] if old else None
        self.grid.blockSignals(True); self.grid.clear()
        student=self.student_id(); takes=self.repo.takes(student) if student is not None else []
        for i,row in enumerate(takes):
            row=dict(row); status='FINAL SELECTED' if row['selected'] else row['review_flag'] or 'Unreviewed'
            if row['processing_status']!='imported': status=row['processing_status'].replace('_',' ')
            item=QListWidgetItem(f'Take {i+1}\n{status}'); item.setData(Qt.UserRole,row)
            item.setToolTip(f"{Path(row['original_path']).name}\n{row['session_name']}\n{row['review_flag'] or status}")
            if row['thumbnail']:
                pix=QPixmap(); pix.loadFromData(row['thumbnail']); item.setIcon(QIcon(pix))
            self.grid.addItem(item)
            if row['id']==old_id: self.grid.setCurrentItem(item)
        if takes and self.grid.currentRow()<0: self.grid.setCurrentRow(0)  # Browsing only; never writes selection.
        self.grid.blockSignals(False)
        final=any(row['selected'] for row in takes)
        self.summary.setText('No students match these filters.' if student is None else
            'No images. Import and assign photographs first.' if not takes else
            f"{len(takes)} takes · {'Final portrait selected' if final else 'Unreviewed images · no final selection'}")
        self.previous.setEnabled(self.students.currentRow()>0)
        self.next.setEnabled(0<=self.students.currentRow()<self.students.count()-1)
        self.clear_button.setEnabled(final)
        self.take_changed()

    def take_changed(self,*_):
        self.token-=1; self.payload=None
        for view in self.views.values(): view.set_image(None)
        take=self.take(); accepted=bool(take and take['processing_status']=='imported')
        self.select_button.setEnabled(accepted and not take['review_flag'])
        self.open_button.setEnabled(False); self.process_button.setEnabled(accepted); self.flag_button.setEnabled(bool(take))
        self.flag.setCurrentIndex(FLAGS.index(take['review_flag']) if take else 0)
        if accepted:
            self.message.setText('Loading local previews…')
            self.processor.controller.command('take_preview',self.token,take['id'])
        else: self.message.setText('Confirm the assignment to preview this take.' if take else 'Choose a student with photographs.')

    def on_done(self,action,token,success,payload):
        if action == 'worker':
            self.message.setText('Preview worker unavailable: ' + payload['error'])
            return
        if action not in ('take_preview','take_full') or token!=self.token: return
        if not success:
            if action == 'take_full': self.open_button.setEnabled(True)
            self.message.setText('Unable to open image: '+payload['error']); return
        self.payload=payload; self.open_button.setEnabled(True)
        if action=='take_full':
            dialog=QDialog(self); dialog.setWindowTitle('Full-resolution take'); dialog.resize(1000,760)
            layout=QVBoxLayout(dialog); tabs=QTabWidget(); layout.addWidget(tabs)
            for key,label in [('original','Original'),('cutout','Transparent cutout'),('portrait','Grey composite')]:
                if key in payload:
                    view=ImageView(label+' · full resolution',checker=key=='cutout'); view.set_image(payload[key]); tabs.addTab(view,label)
            dialog.setAttribute(Qt.WA_DeleteOnClose); self.dialogs.append(dialog)
            dialog.destroyed.connect(lambda: self.dialogs.remove(dialog) if dialog in self.dialogs else None)
            dialog.show(); return
        for key,view in self.views.items(): view.set_image(payload.get(key))
        self.message.setText('Final take choice is separate from processing approval.' if payload['job'] else
                             'No current processed outputs. Use Adjust processing to create the cutout and composite.')

    def mutate(self,callback):
        try: callback()
        except (ValueError,sqlite3.Error) as exc: self.message.setText(str(exc)); return
        self.refresh_students()

    def select_take(self):
        take=self.take()
        if take: self.mutate(lambda:self.repo.select(self.student_id(),take['id']))

    def clear_selection(self):
        self.mutate(lambda:self.repo.clear_selection(self.student_id()))

    def save_flag(self):
        take=self.take()
        if take: self.mutate(lambda:self.repo.flag(take['id'],FLAGS[self.flag.currentIndex()]))

    def open_image(self):
        take=self.take()
        if take and self.open_button.isEnabled():
            self.open_button.setEnabled(False); self.message.setText('Loading full-resolution images…')
            self.processor.controller.command('take_full',self.token,take['id'])

    def edit_processing(self):
        take=self.take()
        if take and self.process_button.isEnabled():
            self.processor.refresh_photos()
            self.processor.photos.setCurrentIndex(self.processor.photos.findData(take['id']))
            self.workspace.setCurrentWidget(self.processor)
