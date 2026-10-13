"""Two-step export manager: inspect the plan, then explicitly start copying."""
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,
    QCheckBox,QLineEdit,QPushButton,QFileDialog,QTextEdit,QProgressBar)
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from ..capture_repository import CaptureRepository
from ..exporting import MODES,ExportOptions
from ..export_worker import ExportController


class ExportPage(QWidget):
    def __init__(self,db,parent=None):
        super().__init__(parent)
        self.db=db; self.plan=None; self.busy=False; self.completed=None
        self.controller=ExportController(db.path,self)
        layout=QVBoxLayout(self); layout.setContentsMargins(28,24,28,20); layout.setSpacing(12)
        title=QLabel('Export photographs'); title.setObjectName('title'); layout.addWidget(title)
        intro=QLabel('Prepare and inspect the export, then copy into a new folder. Existing exports and source files stay unchanged.')
        intro.setWordWrap(True); layout.addWidget(intro)
        form=QFormLayout(); form.setSpacing(6)
        self.scope=QComboBox(); form.addRow('Class / project',self.scope)
        self.mode=QComboBox(); self.mode.addItems(MODES); form.addRow('Export type',self.mode)
        self.every=QCheckBox('Also include every take (originals, transparent and grey outputs)'); form.addRow('',self.every)
        self.incomplete=QCheckBox('Include incomplete students and any available files'); form.addRow('',self.incomplete)
        note=QLabel('Incomplete: missing selection, unapproved selected processing, or missing/invalid files. Unchecked excludes their files; the manifest still lists them.')
        note.setWordWrap(True); form.addRow('',note)
        row=QHBoxLayout(); self.destination=QLineEdit(); self.destination.setAccessibleName('Export destination')
        self.browse=QPushButton('Choose folder'); self.browse.clicked.connect(self.choose_folder)
        row.addWidget(self.destination,1); row.addWidget(self.browse); form.addRow('Destination',row)
        layout.addLayout(form)
        self.report=QTextEdit(); self.report.setReadOnly(True); self.report.setAccessibleName('Export preview, missing selections and errors')
        self.report.setMinimumHeight(110)
        self.report.setPlainText('Prepare an export to list missing selections, incomplete students and planned file counts.')
        layout.addWidget(self.report,1)
        self.progress=QProgressBar(); self.progress.setValue(0); layout.addWidget(self.progress)
        self.status=QLabel('Ready to prepare'); self.status.setWordWrap(True); self.status.setTextFormat(Qt.PlainText); layout.addWidget(self.status)
        actions=QHBoxLayout()
        self.prepare=QPushButton('Prepare export'); self.prepare.clicked.connect(self.prepare_export)
        self.start=QPushButton('Start export'); self.start.setObjectName('primary'); self.start.setEnabled(False); self.start.clicked.connect(self.start_export)
        self.open=QPushButton('Open exported folder'); self.open.setEnabled(False); self.open.clicked.connect(self.open_folder)
        for button in (self.prepare,self.start,self.open): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.controller.done.connect(self.on_done); self.controller.progress.connect(self.on_progress)
        for combo in (self.scope,self.mode): combo.currentIndexChanged.connect(self.invalidate)
        self.every.toggled.connect(self.invalidate); self.incomplete.toggled.connect(self.invalidate); self.destination.textChanged.connect(self.invalidate)
        self.refresh()

    def showEvent(self,event):
        super().showEvent(event)
        if not self.busy: self.refresh()

    def refresh(self):
        current=self.scope.currentData(); self.scope.blockSignals(True); self.scope.clear(); self.scope.addItem('Entire project',None)
        for row in self.db.classes(): self.scope.addItem(row['name'],row['id'])
        self.scope.setCurrentIndex(max(0,self.scope.findData(current))); self.scope.blockSignals(False)
        if not self.destination.text():
            folders=CaptureRepository(self.db).settings('folders',{})
            self.destination.setText(folders.get('export',''))
        self.invalidate()

    def invalidate(self,*_):
        self.plan=None; self.start.setEnabled(False)
        if not self.busy: self.status.setText('Prepare the export to review the current options.')

    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'Choose export destination',self.destination.text())
        if path: self.destination.setText(path)

    def set_busy(self,busy):
        self.busy=busy
        for widget in (self.scope,self.mode,self.every,self.incomplete,self.destination,self.browse,self.prepare): widget.setEnabled(not busy)
        self.start.setEnabled(not busy and self.plan is not None)

    def prepare_export(self):
        if self.busy: return
        if not self.destination.text().strip(): self.status.setText('Choose an export destination first.'); return
        self.plan=None; self.set_busy(True); self.progress.setRange(0,0); self.status.setText('Checking selections and local file integrity…')
        options=ExportOptions(self.mode.currentText(),self.scope.currentData(),self.incomplete.isChecked(),self.every.isChecked())
        self.controller.command('plan',options)

    def start_export(self):
        if self.busy or self.plan is None: return
        self.set_busy(True); self.progress.setRange(0,max(1,len(self.plan.files))); self.progress.setValue(0)
        self.status.setText('Copying verified files to a new export folder…')
        self.controller.command('export',self.plan,self.destination.text().strip())

    def on_progress(self,done,total,message):
        self.progress.setRange(0,max(1,total)); self.progress.setValue(done)
        self.status.setText(f'{done}/{total}: {message}')

    def on_done(self,action,success,result):
        self.progress.setRange(0,100)
        if not success:
            self.plan=None; self.status.setText('Export operation failed: '+result['error']); self.report.append(result['error']); self.progress.setValue(0)
        elif action=='plan':
            self.plan=result
            included=sum(row['included'] for row in result.students)
            lines=[f'{len(result.students)} students · {included} included · {len(result.students)-included} excluded',
                   f'{len(result.files)} files planned. A manifest will also list students with no exported files.',
                   'Incomplete students: '+('included' if result.options.include_incomplete else 'excluded'),'',
                   'Missing selections and file/processing checks:']
            lines.extend(result.warnings or ['No missing selections or incomplete students.'])
            self.report.setPlainText('\n'.join(lines)); self.status.setText('Review this report, then choose Start export.'); self.progress.setValue(0)
        else:
            self.completed=result['destination']; self.open.setEnabled(True); self.plan=None
            self.report.setPlainText(f"Saved to: {result['destination']}\n{result['copied']}/{result['planned']} files copied\n"
                f"{result['included_students']} students included · {result['excluded_students']} excluded\n"
                f"{len(result['errors'])} errors\n\n"+'\n'.join(result['errors']))
            self.status.setText('Export completed with errors. Review the report.' if result['errors'] else 'Export complete. Manifest and summary saved.')
            self.progress.setValue(100)
        self.set_busy(False)

    def open_folder(self):
        if self.completed: QDesktopServices.openUrl(QUrl.fromLocalFile(self.completed))
