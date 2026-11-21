import os
import subprocess
import sys


def test_automation_panel_starts_scans_and_stops(tmp_path):
    code = r'''
import sys, time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from app.storage import Store
from app.automation_ui import AutomationPanel
root=Path(sys.argv[1]); folder=root/'incoming'; folder.mkdir(); (folder/'clip.mp4').write_bytes(b'test')
app=QApplication([]); panel=AutomationPanel(Store(root/'app.sqlite')); panel.show()
panel.locator.setText(str(folder)); panel.creator.setText('Creator'); panel.evidence.setText('Permission')
panel.approval.setChecked(True); panel.add_source()
assert len(panel.engine.list_sources())==1
panel.output.setText(str(root)); panel.generate_enabled.setChecked(False); panel.interval.setValue(5)
panel.start()
end=time.monotonic()+10
while panel.thread is not None and time.monotonic()<end:
    app.processEvents(); time.sleep(.01)
assert panel.thread is None and panel.engine.list_jobs()[0]['status']=='observing'
assert panel.timer.isActive(); panel.pause(); assert not panel.timer.isActive()
panel.start(); panel.stop()
end=time.monotonic()+10
while panel.thread is not None and time.monotonic()<end:
    app.processEvents(); time.sleep(.01)
assert panel.thread is None and not panel.timer.isActive()
panel.close(); app.processEvents()
'''
    env = os.environ.copy(); env['QT_QPA_PLATFORM'] = 'offscreen'
    subprocess.run([sys.executable, '-c', code, str(tmp_path)], env=env, check=True, timeout=30)


def test_selected_job_without_current_row_is_actionable(tmp_path):
    code = r'''
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from app.storage import Store
from app.automation_ui import AutomationPanel
app=QApplication([]); panel=AutomationPanel(Store(Path(sys.argv[1])/'test.sqlite'))
panel.job_rows=[{'id':'chosen'}]; panel.jobs.addItem('interrupted')
panel.jobs.item(0).setSelected(True)
assert panel.jobs.currentRow() == -1
called=[]; panel.engine.reconcile_job=lambda item_id: called.append(item_id) or 1
panel.reconcile_job()
assert called==['chosen']
panel.close()
'''
    env = os.environ.copy(); env['QT_QPA_PLATFORM'] = 'offscreen'
    subprocess.run([sys.executable, '-c', code, str(tmp_path)], env=env, check=True, timeout=30)
