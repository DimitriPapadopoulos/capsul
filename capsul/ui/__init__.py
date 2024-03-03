import os

from soma.qt_gui.qt_backend.Qt import QVariant
from soma.web import WebBackend, json_exception, pyqtSlot


class CapsulWebBackend(WebBackend):
    def __init__(self, capsul):
        super().__init__()
        s = os.path.split(os.path.dirname(__file__)) + ("static",)
        self.static_path.append("/".join(s))
        self._capsul = capsul

    @pyqtSlot(result=QVariant)
    @json_exception
    def engines(self):
        return [engine.engine_status() for engine in self._capsul.engines()]

    @pyqtSlot(str, result=QVariant)
    @json_exception
    def engine_status(self, engine_label):
        try:
            engine = self._capsul.engine(engine_label)
        except ValueError:
            return {}
        return engine.engine_status()

    @pyqtSlot(str, result=QVariant)
    @json_exception
    def executions_summary(self, engine_label):
        return self._capsul.engine(engine_label).executions_summary()

    @pyqtSlot(str, str, result=QVariant)
    @json_exception
    def execution_report(self, engine_label, execution_id):
        with self._capsul.engine(engine_label) as engine:
            return engine.database.execution_report_json(engine.engine_id, execution_id)

    @pyqtSlot(str, str)
    @json_exception
    def stop_execution(self, engine_label, execution_id):
        with self._capsul.engine(engine_label) as engine:
            return engine.stop(execution_id, kill_running=True)

    @pyqtSlot(str, str)
    @json_exception
    def dispose_execution(self, engine_label, execution_id):
        with self._capsul.engine(engine_label) as engine:
            return engine.dispose(execution_id, bypass_persistence=True)

    @pyqtSlot(str, str)
    @json_exception
    def restart_execution(self, engine_label, execution_id):
        with self._capsul.engine(engine_label) as engine:
            return engine.restart(execution_id)

    @pyqtSlot(str, str, str)
    @json_exception
    def stop_job(self, engine_label, execution_id, job_id):
        with self._capsul.engine(engine_label) as engine:
            return engine.kill_jobs(execution_id, [job_id])
