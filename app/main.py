from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from app.core import discover, transcribe, require_rights
from app.platform_support import application_paths, settings
from app.storage import Store
from app.video import export_bundle, tool_path
from app.preview import VideoPreview
from app.highlights import analyze_audio
from app.autoclip import generate, regenerate
from app.review import ReviewQueue
from app.youtube import YouTubeAccount
from app.youtube_jobs import YouTubeJobs
from app.youtube_ui import YouTubePanel
from app.acquisition import youtube_id, authorize, acquire
from app.automation_ui import AutomationPanel


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
    def run(self):
        try:
            self.finished.emit(self.fn())
        except Exception as exc:
            logging.exception("Background operation failed")
            self.failed.emit(str(exc))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        root, _ = application_paths()
        root.mkdir(parents=True, exist_ok=True)
        if not logging.getLogger().handlers:
            handler = RotatingFileHandler(root / "app.log", maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
            logging.basicConfig(handlers=[handler], level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        self.store = Store(root / "studio.sqlite3")
        self.review_queue = ReviewQueue(self.store)
        channel_id = os.environ.get('VICESCOPE_YOUTUBE_CHANNEL_ID', settings().get('youtube_channel_id', '')).strip()
        account = YouTubeAccount(root / 'youtube-client.json', channel_id)
        self.youtube_jobs = YouTubeJobs(self.review_queue, account)
        self.setWindowTitle("ViceScope Clip Studio")
        self.resize(1180, 760)
        self.threads = []
        central = QWidget(); self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        self.nav = QListWidget(); self.nav.setFixedWidth(200)
        self.pages = QStackedWidget(); layout.addWidget(self.nav); layout.addWidget(self.pages, 1)
        self.dashboard(); self.discovery(); self.campaigns(); self.editor(); self.transcription()
        self.automation_page()
        for name in ("Livestreams", "Analytics"):
            self.page(name, "Not implemented yet. No data or publishing actions are simulated.")
        self.review_page()
        self.export_history()
        self.diagnostics()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex); self.nav.setCurrentRow(0)
        self.setStyleSheet("QWidget{background:#111522;color:#e7eaf3;font-size:14px} QListWidget,QLineEdit,QTextEdit{background:#1b2131;border:1px solid #30394d;border-radius:6px;padding:8px} QPushButton{background:#794ee8;border:0;border-radius:6px;padding:10px;color:white} QPushButton:hover{background:#916afa} QLabel#heading{font-size:25px;font-weight:bold}")

    def page(self, name, description):
        self.nav.addItem(name); w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(32, 30, 32, 30)
        title = QLabel(name); title.setObjectName("heading"); v.addWidget(title)
        note = QLabel(description); note.setWordWrap(True); v.addWidget(note)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(w)
        self.pages.addWidget(scroll)
        return v

    def dashboard(self):
        v = self.page("Dashboard", "Local workspace overview. Source approval is specific to each file.")
        self.summary = QLabel(); v.addWidget(self.summary); self.refresh(); v.addStretch()

    def automation_page(self):
        v = self.page('Automation', 'Monitor sources and prepare unapproved clips while the application is open.')
        self.automation_panel = AutomationPanel(self.store, self)
        v.addWidget(self.automation_panel)
    def refresh(self):
        c = self.store.counts(); self.summary.setText(f"Sources: {c['sources']}    Campaigns: {c['campaigns']}")
    def discovery(self):
        v = self.page("Discover", "YouTube search results are unverified mentions, not verified gameplay or permission.")
        self.query = QLineEdit("GTA 6"); v.addWidget(self.query)
        button = QPushButton("Search YouTube"); button.clicked.connect(self.search); v.addWidget(button)
        self.results = QTextEdit(); self.results.setReadOnly(True); v.addWidget(self.results)
    def search(self):
        query = self.query.text()
        self.background(lambda: discover(query), self.show_results)
    def show_results(self, rows):
        self.results.setPlainText("\n\n".join(f"{x['title']} — {x['creator']}\n{x['url']}\nViews: {x['views'] if x['views'] is not None else 'unknown'} | Live: {x['live_status']} | Footage: unverified | Rights: not established" for x in rows) or "No results returned.")
    def campaigns(self):
        v = self.page("Campaigns", "Save briefs for manual verification. A campaign does not automatically authorize a source.")
        self.campaign_name = QLineEdit(); self.campaign_name.setPlaceholderText("Campaign name")
        self.brief = QTextEdit(); self.brief.setPlaceholderText("Paste the campaign brief, terms, expiry and evidence")
        v.addWidget(self.campaign_name); v.addWidget(self.brief)
        b = QPushButton("Save campaign"); b.clicked.connect(self.save_campaign); v.addWidget(b)
        self.campaign_list = QTextEdit(); self.campaign_list.setReadOnly(True); v.addWidget(self.campaign_list); self.refresh_campaigns()
    def refresh_campaigns(self):
        self.campaign_list.setPlainText("\n\n".join(f"{n}\n{b}" for n,b in self.store.campaigns()))
    def save_campaign(self):
        try:
            self.store.add_campaign(self.campaign_name.text(), self.brief.toPlainText()); self.refresh_campaigns(); self.refresh()
        except Exception as e: QMessageBox.warning(self, "Campaign", str(e))
    def editor(self):
        v = self.page("Clip Editor", "Choose a local video, record permission, select a segment, and render a vertical clip.")
        form = QFormLayout(); v.addLayout(form)
        self.source = QLineEdit(); browse = QPushButton("Choose local video"); browse.clicked.connect(self.pick_source)
        form.addRow(browse, self.source)
        download = QPushButton("Import an authorized YouTube video"); download.clicked.connect(self.import_youtube); v.addWidget(download)
        self.creator = QLineEdit(); self.url = QLineEdit(); self.evidence = QLineEdit(); self.approved = QCheckBox("I explicitly approve publication use of this exact source")
        self.source.textChanged.connect(self.source_changed)
        form.addRow("Creator", self.creator); form.addRow("Source URL", self.url); form.addRow("Permission evidence", self.evidence); form.addRow(self.approved)
        b = QPushButton("Save source rights"); b.clicked.connect(self.save_source); v.addWidget(b)
        self.start_time = QDoubleSpinBox(); self.end_time = QDoubleSpinBox()
        for field in (self.start_time, self.end_time): field.setRange(0, 86400); field.setDecimals(2)
        self.end_time.setValue(15); form.addRow("Start (seconds)", self.start_time); form.addRow("End (seconds)", self.end_time)
        self.preview = VideoPreview(); v.addWidget(self.preview)
        self.preview.selection_start.connect(self.start_time.setValue)
        self.preview.selection_end.connect(self.end_time.setValue)
        self.layout = QComboBox(); self.layout.addItem("Center crop", "center"); self.layout.addItem("Blurred background", "blur"); form.addRow("Layout", self.layout)
        self.hook = QLineEdit(); form.addRow("Opening hook", self.hook)
        self.title = QLineEdit(); self.description = QTextEdit(); form.addRow("Title", self.title); form.addRow("Description", self.description)
        self.clip_count = QSpinBox(); self.clip_count.setRange(1, 5); self.clip_count.setValue(3)
        self.clip_duration = QSpinBox(); self.clip_duration.setRange(1, 180); self.clip_duration.setValue(20); self.clip_duration.setSuffix(" seconds")
        self.minimum_score = QDoubleSpinBox(); self.minimum_score.setRange(0, 1); self.minimum_score.setSingleStep(.05); self.minimum_score.setValue(.1)
        form.addRow("Maximum clips", self.clip_count); form.addRow("Clip duration", self.clip_duration); form.addRow("Minimum highlight score", self.minimum_score)
        self.auto_button = QPushButton("Generate Shorts automatically"); self.auto_button.clicked.connect(self.generate_shorts); v.addWidget(self.auto_button)
        self.cancel_button = QPushButton("Cancel after current step"); self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel_generation); v.addWidget(self.cancel_button)
        self.progress_label = QLabel(""); v.addWidget(self.progress_label)
        b = QPushButton("Render vertical MP4 and metadata"); b.clicked.connect(self.render_clip); v.addWidget(b)
        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); v.addWidget(self.progress); v.addStretch()
    def source_changed(self):
        self.approved.setChecked(False)
        self.words = []
        if hasattr(self, "transcript"): self.transcript.setRowCount(0)
        if hasattr(self, "candidates"): self.candidates.clear()
        if hasattr(self, "preview"):
            from PySide6.QtCore import QUrl
            self.preview.player.stop(); self.preview.player.setSource(QUrl())
            if self.source.text() and Path(self.source.text()).is_file(): self.preview.load(str(Path(self.source.text()).resolve()))

    def import_youtube(self):
        url, ok = QInputDialog.getText(self, "YouTube URL", "HTTPS URL for the exact video:")
        if not ok: return
        try: video_id = youtube_id(url)
        except ValueError as exc:
            QMessageBox.warning(self, "Video URL", str(exc)); return
        creator, ok = QInputDialog.getText(self, "Source creator", "Creator name:")
        if not ok: return
        evidence, ok = QInputDialog.getText(self, "Permission evidence", "Where is permission recorded for this exact video?")
        if not ok: return
        directory = QFileDialog.getExistingDirectory(self, "Choose a folder for the authorized download")
        if not directory: return
        decision = QMessageBox.question(self, "Approve this source", f"Do you have permission to download and process the YouTube video {video_id} for your Shorts?\n\nEvidence: {evidence}")
        if decision != QMessageBox.StandardButton.Yes: return
        try: authorize(self.store, url, creator, evidence)
        except ValueError as exc:
            QMessageBox.warning(self, "Permission", str(exc)); return
        def complete(path):
            self.source.setText(path); self.creator.setText(creator); self.url.setText('https://www.youtube.com/watch?v=' + video_id)
            self.evidence.setText(evidence); self.approved.setChecked(True); self.refresh()
            QMessageBox.information(self, "Source imported", "Download complete and source hash recorded. You can now generate Shorts.")
        self.background(lambda: acquire(self.store, url, directory), complete)

    def pick_source(self):
        path = QFileDialog.getOpenFileName(self, "Select local video")[0]
        if path:
            self.approved.setChecked(False)
            self.evidence.clear()
            self.creator.clear()
            self.url.clear()
            self.source.setText(path)
    def save_source(self):
        try:
            self.store.save_source(self.source.text(), self.creator.text(), self.url.text(), self.evidence.text(), self.approved.isChecked()); self.refresh(); QMessageBox.information(self, "Source", "Source saved.")
        except Exception as e: QMessageBox.warning(self, "Source", str(e))
    def render_clip(self):
        path = QFileDialog.getSaveFileName(self, "Choose a new export directory name", "short-export")[0]
        if not path: return
        source, start, end = self.source.text(), self.start_time.value(), self.end_time.value()
        hook, title, description = self.hook.text(), self.title.text(), self.description.toPlainText()
        layout = self.layout.currentData()
        try: words = self.edited_words()
        except ValueError as exc:
            QMessageBox.warning(self, "Subtitle error", str(exc)); return
        self.progress.setRange(0, 0)
        def work():
            return export_bundle(self.store, source, start, end, path, words, hook, title, description, layout)
        def completed(result):
            self.progress.setRange(0, 1); self.progress.setValue(1)
            self.refresh_history()
            QMessageBox.information(self, "Export complete", f"Saved {result['output_path']}, metadata, and provenance. Nothing was published.")
        self.background(work, completed)

    def generate_shorts(self):
        directory = QFileDialog.getExistingDirectory(self, "Choose a folder for generated clips")
        if not directory: return
        source = self.source.text()
        maximum = self.clip_count.value(); duration = self.clip_duration.value(); score = self.minimum_score.value()
        self.progress.setRange(0, 100); self.progress.setValue(0)
        self.generation_cancel = threading.Event()
        self.auto_button.setEnabled(False); self.cancel_button.setEnabled(True)
        def completed(items):
            self.progress.setRange(0, 100); self.progress.setValue(100)
            self.refresh_review()
            QMessageBox.information(self, "Ready for review", f"Generated {len(items)} clip(s). Nothing was uploaded.")
        self.background(lambda report, cancelled: generate(self.store, source, directory, maximum=maximum, min_score=score, clip_duration=duration, progress=report, cancelled=cancelled), completed, self.generation_cancel)

    def cancel_generation(self):
        self.generation_cancel.set()
        self.progress_label.setText('Cancellation requested; waiting for the current step to finish')
        self.cancel_button.setEnabled(False)

    def update_generation_progress(self, value, message):
        self.progress.setValue(value)
        self.progress_label.setText(message)

    def review_page(self):
        v = self.page("Ready for Review", "Review each clip and approve its private upload. You can publish it separately after upload.")
        self.review_list = QListWidget(); self.review_list.currentRowChanged.connect(self.select_review); v.addWidget(self.review_list)
        self.review_preview = VideoPreview(); v.addWidget(self.review_preview)
        self.review_details = QLabel(); self.review_details.setWordWrap(True); v.addWidget(self.review_details)
        form = QFormLayout(); v.addLayout(form)
        self.review_title = QLineEdit(); self.review_description = QTextEdit(); self.review_tags = QLineEdit()
        form.addRow("Title", self.review_title); form.addRow("Description", self.review_description); form.addRow("Hashtags", self.review_tags)
        row = QHBoxLayout(); v.addLayout(row)
        for label, callback in (("Save metadata", self.save_review_metadata), ("Approve private upload", self.approve_review), ("Reject", self.reject_review), ("Regenerate", self.regenerate_review), ("Open file", self.open_review_file)):
            button = QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        self.youtube_panel = YouTubePanel(self, self.youtube_jobs)
        v.addWidget(self.youtube_panel)
        self.refresh_review()

    def refresh_review(self):
        self.review_rows = self.review_queue.list()
        self.review_list.clear()
        for item in self.review_rows:
            valid = self.review_queue.is_approved(item['id']) if item['status'] == 'approved_private' else False
            status = 'approved private' if valid else ('approval invalid' if item['status'] == 'approved_private' else item['status'])
            self.review_list.addItem(f"{status} | {item['start']:.1f}-{item['end']:.1f}s | score {item['score']:.2f}")
        self.youtube_panel.refresh()

    def selected_review(self):
        index = self.review_list.currentRow()
        return self.review_rows[index] if 0 <= index < len(self.review_rows) else None

    def select_review(self, index):
        self.youtube_panel.refresh()
        item = self.selected_review()
        if item is None: return
        data = json.loads(item['metadata'])
        self.review_title.setText(data.get('title', '')); self.review_description.setPlainText(data.get('description', '')); self.review_tags.setText(' '.join(data.get('hashtags', [])))
        try:
            rights = self.review_queue.validate(item)
            state = 'source and output hashes verified'
            creator = rights['creator']; url = rights['source_url']
        except Exception as exc:
            state = f'NOT VERIFIED: {exc}'; creator = ''; url = ''
        self.review_details.setText(f"Creator: {creator} | Source: {url}\n{item['explanation']}\n{state}")
        self.review_preview.load(item['output_path'])

    def save_review_metadata(self):
        item = self.selected_review()
        if not item: return
        try:
            self.review_queue.edit(item['id'], {'title': self.review_title.text(), 'description': self.review_description.toPlainText(), 'hashtags': self.review_tags.text().split()})
            self.refresh_review()
        except Exception as exc: QMessageBox.warning(self, "Review", str(exc))

    def review_decision(self, decision):
        item = self.selected_review()
        if not item: return
        try:
            self.review_queue.decide(item['id'], decision); self.refresh_review()
        except Exception as exc: QMessageBox.warning(self, "Review", str(exc))

    def review_fields_saved(self, item):
        displayed = {'title': self.review_title.text(), 'description': self.review_description.toPlainText(), 'hashtags': self.review_tags.text().split()}
        if displayed != json.loads(item['metadata']):
            QMessageBox.warning(self, "Review", "Save and approve the edited metadata before continuing.")
            return False
        return True

    def approve_review(self):
        item = self.selected_review()
        if item and self.review_fields_saved(item):
            self.review_decision('approved_private')

    def reject_review(self): self.review_decision('rejected')
    def regenerate_review(self):
        item = self.selected_review()
        if not item: return
        hook, ok = QInputDialog.getText(self, "Regenerate clip", "Opening hook for the new version:")
        if not ok: return
        self.progress.setRange(0, 0)
        def complete(new_id):
            self.progress.setRange(0, 1); self.progress.setValue(1); self.refresh_review()
            QMessageBox.information(self, "New version", "A new unapproved version is ready for review.")
        self.background(lambda: regenerate(self.store, item['id'], hook), complete)
    def open_review_file(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        item = self.selected_review()
        if item: QDesktopServices.openUrl(QUrl.fromLocalFile(item['output_path']))

    def export_history(self):
        v = self.page("Export Queue", "Only packages with video, metadata and provenance are listed as completed.")
        self.history = QListWidget(); v.addWidget(self.history)
        button = QPushButton("Refresh"); button.clicked.connect(self.refresh_history); v.addWidget(button)
        self.refresh_history()

    def refresh_history(self):
        self.history.clear()
        with self.store.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='completed_packages'").fetchone()
            if exists:
                for job_id, directory, created in db.execute("SELECT job_id, directory, created_at FROM completed_packages ORDER BY created_at DESC"):
                    self.history.addItem(f"{created} | {job_id} | {directory}")

    def diagnostics(self):
        v = self.page("Settings", "Media tool diagnostics")
        for name in ("ffmpeg", "ffprobe"):
            try: value = tool_path(name)
            except FileNotFoundError as exc: value = str(exc)
            label = QLabel(f"{name}: {value}"); label.setWordWrap(True); v.addWidget(label)
        v.addStretch()

    def transcription(self):
        v = self.page("Highlight Finder", "Analyze audio and optional transcript to suggest moments. Select a result for review; it is not a verified highlight.")
        b = QPushButton("Transcribe source"); b.clicked.connect(self.start_transcription); v.addWidget(b)
        analyze = QPushButton("Analyze audio and rank candidates"); analyze.clicked.connect(self.analyze_highlights); v.addWidget(analyze)
        self.candidates = QListWidget(); self.candidates.itemClicked.connect(self.choose_candidate); v.addWidget(self.candidates)
        self.transcript = QTableWidget(0, 3); self.transcript.setHorizontalHeaderLabels(["Start (s)", "End (s)", "Text"]); v.addWidget(self.transcript)
        self.preview.player.positionChanged.connect(self.highlight_subtitle)
    def analyze_highlights(self):
        source = self.source.text()
        words = list(getattr(self, "words", []))
        self.background(lambda: analyze_audio(self.store, source, words), lambda rows: self.show_candidates(rows) if self.source.text() == source else None)

    def show_candidates(self, candidates):
        self.candidate_values = candidates
        self.candidates.clear()
        for item in candidates:
            self.candidates.addItem(f"{item.start:.1f}?{item.end:.1f}s | {item.score:.2f} | {item.explanation}")

    def choose_candidate(self, item):
        candidate = self.candidate_values[self.candidates.row(item)]
        self.start_time.setValue(candidate.start); self.end_time.setValue(candidate.end)
        self.preview.player.setPosition(int(candidate.start * 1000))

    def start_transcription(self):
        source = self.source.text()
        def work():
            before = require_rights(self.store, source)
            words = transcribe(source)
            after = require_rights(self.store, source)
            if before["sha256"] != after["sha256"]:
                raise PermissionError("Source changed during transcription")
            return words
        def completed(words):
            if self.source.text() == source: self.show_transcript(words)
        self.background(work, completed)
    def show_transcript(self, words):
        self.words = words
        self.transcript.setRowCount(len(words))
        for row, word in enumerate(words):
            for col, value in enumerate((word["start"], word["end"], word["text"])):
                self.transcript.setItem(row, col, QTableWidgetItem(str(value)))

    def edited_words(self):
        words = []
        for row in range(self.transcript.rowCount()):
            try:
                start = float(self.transcript.item(row, 0).text())
                end = float(self.transcript.item(row, 1).text())
                text = self.transcript.item(row, 2).text()
            except (ValueError, AttributeError):
                raise ValueError(f"Invalid subtitle at row {row + 1}") from None
            if not (0 <= start < end < 864000): raise ValueError(f"Invalid subtitle timing at row {row + 1}")
            words.append({"start": start, "end": end, "text": text})
        return words

    def highlight_subtitle(self, position):
        seconds = position / 1000
        for row in range(self.transcript.rowCount()):
            try:
                start = float(self.transcript.item(row, 0).text()); end = float(self.transcript.item(row, 1).text())
                if start <= seconds < end:
                    self.transcript.selectRow(row); break
            except (AttributeError, ValueError): pass

    def background(self, fn, done, generation_cancel=None, failed=None):
        thread = QThread(self)
        worker = Worker((lambda: fn(worker.progress.emit, generation_cancel.is_set)) if generation_cancel else fn)
        worker.moveToThread(thread)
        worker.progress.connect(self.update_generation_progress)
        thread.started.connect(worker.run); worker.finished.connect(done); worker.failed.connect(failed or self.show_error)
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit); worker.finished.connect(worker.deleteLater); worker.failed.connect(worker.deleteLater)
        thread.finished.connect(lambda: self.threads.remove((thread, worker))); thread.finished.connect(thread.deleteLater)
        if generation_cancel:
            thread.finished.connect(lambda: (self.auto_button.setEnabled(True), self.cancel_button.setEnabled(False)))
        self.threads.append((thread, worker)); thread.start()
    def show_error(self, message):
        self.refresh_review()
        self.progress.setRange(0, 1); self.progress.setValue(0)
        QMessageBox.warning(self, "Operation failed", message)

    def closeEvent(self, event):
        if self.threads or (hasattr(self, 'automation_panel') and self.automation_panel.thread is not None):
            QMessageBox.warning(self, "Processing", "Wait for background operations to finish before closing."); event.ignore()
        else:
            from PySide6.QtCore import QUrl
            self.preview.player.stop(); self.preview.player.setSource(QUrl())
            self.review_preview.player.stop(); self.review_preview.player.setSource(QUrl())
            event.accept()


def main():
    app = QApplication(sys.argv)
    try:
        window = Window()
    except (ValueError, OSError) as exc:
        QMessageBox.critical(None, "ViceScope startup", str(exc))
        return 1
    window.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
