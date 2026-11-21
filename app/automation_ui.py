"""In-app automation controls; no background service or publishing."""
from __future__ import annotations

import threading
import os
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)
from app.automation import Automation
from app.acquisition import authorize


class ScanWorker(QObject):
    completed = Signal()
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, engine, options, stop_event, paused_event):
        super().__init__()
        self.engine, self.options = engine, options
        self.stop_event, self.paused_event = stop_event, paused_event

    def run(self):
        try:
            self.engine.scan(maximum=self.options['scan_limit'], streams=self.options['streams'])
            if self.options['generate'] and not self.paused_event.is_set() and not self.stop_event.is_set():
                self.engine.process_next(self.options['output'], maximum=self.options['maximum'],
                    clip_duration=self.options['duration'], min_score=self.options['score'],
                    cancelled=self.stop_event.is_set, progress=self.progress.emit,
                    cpu_threads=self.options['cpu_threads'], min_free_bytes=int(self.options['min_free_gib'] * 1024**3),
                    retry_limit=self.options['retry_limit'], retry_backoff=self.options['retry_backoff'])
            self.completed.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class AutomationPanel(QWidget):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.engine = Automation(store)
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick)
        self.thread = None; self.worker = None
        self.stop_event = threading.Event(); self.paused_event = threading.Event()
        self.running = False
        v = QVBoxLayout(self)
        info = QLabel('Folder files require individual approval unless you explicitly grant permission for every new file in that folder. Discovered YouTube videos require individual permission before download. No upload or publication is automatic.')
        info.setWordWrap(True); v.addWidget(info)
        form = QFormLayout(); v.addLayout(form)
        self.kind = QComboBox(); self.kind.addItem('Local folder', 'folder'); self.kind.addItem('Exact YouTube video', 'youtube_video'); self.kind.addItem('YouTube channel', 'youtube_channel'); self.kind.addItem('YouTube playlist', 'youtube_playlist')
        self.locator = QLineEdit(); self.creator = QLineEdit(); self.evidence = QLineEdit()
        form.addRow('Source type', self.kind); form.addRow('Folder or video URL', self.locator)
        choose = QPushButton('Choose source folder'); choose.clicked.connect(self.choose_folder); v.addWidget(choose)
        form.addRow('Creator', self.creator); form.addRow('Rights evidence', self.evidence)
        self.approval = QCheckBox('I authorize this source within the rights described above'); v.addWidget(self.approval)
        self.folder_scope = QCheckBox('I have permission to process every new video placed in this folder'); v.addWidget(self.folder_scope)
        self.kind.currentIndexChanged.connect(self.kind_changed)
        add = QPushButton('Add authorized source'); add.clicked.connect(self.add_source); v.addWidget(add)
        self.sources = QListWidget(); v.addWidget(self.sources)
        enable = QPushButton('Enable / disable selected source'); enable.clicked.connect(self.toggle_source); v.addWidget(enable)
        self.output = QLineEdit(r'D:\ViceScope\Shorts'); form.addRow('Output folder', self.output)
        out_button = QPushButton('Choose output folder'); out_button.clicked.connect(self.choose_output); v.addWidget(out_button)
        self.interval = QSpinBox(); self.interval.setRange(5, 86400); self.interval.setValue(60); self.interval.setSuffix(' seconds')
        self.maximum = QSpinBox(); self.maximum.setRange(1, 5); self.maximum.setValue(3)
        self.duration = QSpinBox(); self.duration.setRange(1, 180); self.duration.setValue(20); self.duration.setSuffix(' seconds')
        self.score = QDoubleSpinBox(); self.score.setRange(0, 1); self.score.setSingleStep(.05); self.score.setValue(.1)
        self.cpu_threads = QSpinBox(); self.cpu_threads.setRange(1, max(1, os.cpu_count() or 1)); self.cpu_threads.setValue(min(4, max(1, os.cpu_count() or 1)))
        self.min_free = QDoubleSpinBox(); self.min_free.setRange(0, 10000); self.min_free.setValue(5); self.min_free.setSuffix(' GiB')
        form.addRow('Scan interval', self.interval); form.addRow('Maximum clips', self.maximum)
        form.addRow('Clip duration', self.duration); form.addRow('Minimum score', self.score)
        form.addRow('CPU threads per task', self.cpu_threads); form.addRow('Minimum free disk before work', self.min_free)
        self.scan_limit = QSpinBox(); self.scan_limit.setRange(1, 100); self.scan_limit.setValue(20)
        self.streams = QCheckBox('Monitor completed livestream recordings (channel streams tab)'); v.addWidget(self.streams)
        form.addRow('Maximum videos scanned per source', self.scan_limit)
        self.retry_limit = QSpinBox(); self.retry_limit.setRange(1, 10); self.retry_limit.setValue(3)
        self.retry_backoff = QSpinBox(); self.retry_backoff.setRange(1, 3600); self.retry_backoff.setValue(30); self.retry_backoff.setSuffix(' seconds')
        form.addRow('Download retry limit', self.retry_limit); form.addRow('Initial retry backoff', self.retry_backoff)
        self.generate_enabled = QCheckBox('Automatically generate clips for authorized sources'); v.addWidget(self.generate_enabled)
        buttons = QHBoxLayout(); v.addLayout(buttons)
        for label, handler in (('Start / resume', self.start), ('Pause', self.pause), ('Stop', self.stop), ('Run now', self.run_now), ('Run discovery now', self.run_discovery)):
            button = QPushButton(label); button.clicked.connect(handler); buttons.addWidget(button)
        self.status = QLabel('Stopped. Automation runs only while the app is open.'); self.status.setWordWrap(True); v.addWidget(self.status)
        self.jobs = QListWidget(); v.addWidget(self.jobs)
        retry = QPushButton('Retry failed job'); retry.clicked.connect(self.retry_job); v.addWidget(retry)
        approve = QPushButton('Authorize selected exact video'); approve.clicked.connect(self.authorize_video); v.addWidget(approve)
        self.restore_settings(); self.refresh()

    def options(self):
        return {'output': self.output.text().strip(), 'interval': self.interval.value(), 'maximum': self.maximum.value(),
                'duration': self.duration.value(), 'score': self.score.value(), 'generate': self.generate_enabled.isChecked(),
                'cpu_threads': self.cpu_threads.value(), 'min_free_gib': self.min_free.value(),
                'scan_limit': self.scan_limit.value(), 'streams': self.streams.isChecked(),
                'retry_limit': self.retry_limit.value(), 'retry_backoff': self.retry_backoff.value()}

    def restore_settings(self):
        data = self.engine.settings()
        try:
            if isinstance(data.get('retry_limit'), int): self.retry_limit.setValue(data['retry_limit'])
            if isinstance(data.get('retry_backoff'), int): self.retry_backoff.setValue(data['retry_backoff'])
            if isinstance(data.get('scan_limit'), int): self.scan_limit.setValue(data['scan_limit'])
            if isinstance(data.get('streams'), bool): self.streams.setChecked(data['streams'])
            if isinstance(data.get('output'), str): self.output.setText(data['output'])
            if isinstance(data.get('interval'), int): self.interval.setValue(data['interval'])
            if isinstance(data.get('maximum'), int): self.maximum.setValue(data['maximum'])
            if isinstance(data.get('duration'), int): self.duration.setValue(data['duration'])
            if isinstance(data.get('score'), (float, int)): self.score.setValue(data['score'])
            if isinstance(data.get('generate'), bool): self.generate_enabled.setChecked(data['generate'])
            if isinstance(data.get('cpu_threads'), int): self.cpu_threads.setValue(data['cpu_threads'])
            if isinstance(data.get('min_free_gib'), (float, int)): self.min_free.setValue(data['min_free_gib'])
        except (TypeError, ValueError): pass

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Select authorized folder')
        if path: self.kind.setCurrentIndex(0); self.locator.setText(path)

    def kind_changed(self):
        folder = self.kind.currentData() == 'folder'
        self.folder_scope.setEnabled(folder)
        if not folder: self.folder_scope.setChecked(False)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Select output folder')
        if path: self.output.setText(path)

    def add_source(self):
        if not self.approval.isChecked():
            QMessageBox.warning(self, 'Authorization required', 'Explicitly confirm permission before adding a source.'); return
        try:
            self.engine.add_source(self.kind.currentData(), self.locator.text(), self.creator.text(), self.evidence.text(), self.folder_scope.isChecked())
            self.approval.setChecked(False); self.folder_scope.setChecked(False); self.refresh()
        except Exception as exc: QMessageBox.warning(self, 'Source', str(exc))

    def toggle_source(self):
        index = self.sources.currentRow()
        if index < 0 or index >= len(self.source_rows): return
        item = self.source_rows[index]
        self.engine.set_enabled(item['id'], not item['enabled'])
        if item['enabled'] and self.thread is not None:
            self.stop_event.set()
            self.status.setText('Source disabled; current work will stop after its current step.')
        self.refresh()
        if hasattr(self.window(), 'refresh_review'): self.window().refresh_review()

    def refresh(self):
        self.source_rows = self.engine.list_sources()
        self.sources.clear()
        for row in self.source_rows:
            self.sources.addItem(f"{'Enabled' if row['enabled'] else 'Disabled'} | {row['kind']} | {row['creator']} | {row['value']} | Last checked: {row['last_checked'] or 'never'} | New: {row['new_found']} | Individual video permission required")
        self.jobs.clear()
        self.job_rows = self.engine.list_jobs()[:200]
        for row in self.job_rows:
            self.jobs.addItem(f"{row['status']} | {row['path'] or row['source_key']} | {row['error']}")

    def authorize_video(self):
        index = self.jobs.currentRow()
        if index < 0 or index >= len(self.job_rows): return
        job = self.job_rows[index]
        url = job.get('video_url')
        source = next((row for row in self.source_rows if row['id'] == job['source_id']), None)
        if not url or not source:
            QMessageBox.warning(self, 'Authorization', 'Select a discovered YouTube video.'); return
        decision = QMessageBox.question(self, 'Approve exact video',
            f"Do you have permission to download and reuse this exact video?\n\n{url}\nCreator: {source['creator']}\nEvidence: {source['evidence']}")
        if decision != QMessageBox.StandardButton.Yes: return
        try:
            authorize(self.engine.store, url, source['creator'], source['evidence'])
            self.status.setText('Exact video permission recorded. Run discovery again to queue it.')
        except Exception as exc: QMessageBox.warning(self, 'Authorization', str(exc))

    def retry_job(self):
        index = self.jobs.currentRow()
        if index < 0 or index >= len(self.job_rows): return
        try:
            self.engine.retry_job(self.job_rows[index]['id'])
            self.refresh(); self.status.setText('Job queued for retry; rights will be checked again.')
        except Exception as exc:
            QMessageBox.warning(self, 'Retry unavailable', str(exc))

    def start(self):
        if self.thread is not None:
            self.status.setText('Wait for the current scan or processing step to finish.'); return
        self.engine.save_settings(self.options())
        self.running = True; self.paused_event.clear(); self.stop_event.clear()
        self.timer.start(self.interval.value() * 1000); self.status.setText('Running'); self.tick()

    def pause(self):
        self.running = False; self.paused_event.set(); self.timer.stop()
        self.status.setText('Paused. Current processing may finish; no new job will start.')

    def stop(self):
        self.running = False; self.paused_event.set(); self.timer.stop(); self.stop_event.set()
        self.status.setText('Stopping after the current processing step; interrupted jobs need review.')

    def run_now(self):
        self.engine.save_settings(self.options())
        if self.thread is not None:
            self.status.setText('A scan or processing job is already active.'); return
        self.stop_event.clear(); self.paused_event.clear(); self.tick()

    def run_discovery(self):
        if self.thread is not None:
            self.status.setText('A scan or processing job is already active.'); return
        self.stop_event.clear(); self.paused_event.clear(); self.tick(discovery_only=True)

    def tick(self, discovery_only=False):
        if self.thread is not None: return
        options = self.options()
        if discovery_only: options['generate'] = False
        self.thread = QThread(self); self.worker = ScanWorker(self.engine, options, self.stop_event, self.paused_event)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.show_progress)
        self.worker.completed.connect(self.task_finished)
        self.worker.failed.connect(self.task_failed)
        self.worker.completed.connect(self.thread.quit); self.worker.failed.connect(self.thread.quit)
        self.worker.completed.connect(self.worker.deleteLater); self.worker.failed.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished); self.thread.finished.connect(self.thread.deleteLater)
        self.status.setText('Scanning / processing...'); self.thread.start()

    def show_progress(self, value, message):
        self.status.setText(f'{message} ({value}%)')

    def task_finished(self):
        self.refresh(); self.status.setText('Running' if self.running else 'Scan complete; stopped or paused.')
        window = self.window()
        if hasattr(window, 'refresh_review'): window.refresh_review()

    def task_failed(self, message):
        self.refresh(); self.status.setText('Automation error: ' + message)

    def thread_finished(self):
        self.thread = None; self.worker = None
