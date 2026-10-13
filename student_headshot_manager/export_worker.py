"""Background export planning and copying, with a read-only database connection."""
import logging
import threading
from PySide6.QtCore import QObject, Signal
from .exporting import build_plan, execute_export


class ExportController(QObject):
    done=Signal(str,bool,object)
    progress=Signal(int,int,str)

    def __init__(self,database,parent=None):
        super().__init__(parent)
        self.database=database
        self.thread=None
        self.stopping=False

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self):
        self.stopping=True  # Finish the current copy and manifest before closing.

    def command(self,action,*args):
        if self.is_running() or self.stopping: return
        def run():
            try:
                result=(build_plan(self.database,*args) if action=='plan' else
                        execute_export(self.database,*args,progress=self.progress.emit))
                success=True
            except Exception as exc:
                logging.exception('Background operation failed')
                result={'error':str(exc)}; success=False
            self.thread=None
            self.done.emit(action,success,result)
        self.thread=threading.Thread(target=run,name='Local export',daemon=True)
        self.thread.start()
