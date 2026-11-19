from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)
from app.core import discover, transcribe, require_rights
from app.storage import Store
from app.video import render, prepare_package, tool_path


class Worker(QObject):
    finished = Signal(object)
    failed = Signal(str)
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
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ViceScopeClipStudio"
        root.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=root / "app.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        self.store = Store(root / "studio.sqlite3")
        self.setWindowTitle("ViceScope Clip Studio")
        self.resize(1180, 760)
        self.threads = []
        central = QWidget(); self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        self.nav = QListWidget(); self.nav.setFixedWidth(200)
        self.pages = QStackedWidget(); layout.addWidget(self.nav); layout.addWidget(self.pages, 1)
        self.dashboard(); self.discovery(); self.campaigns(); self.editor(); self.transcription()
        for name in ("Livestreams", "Export Queue", "Analytics"):
            self.page(name, "Not implemented yet. No data or publishing actions are simulated.")
        self.diagnostics()
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex); self.nav.setCurrentRow(0)
        self.setStyleSheet("QWidget{background:#111522;color:#e7eaf3;font-size:14px} QListWidget,QLineEdit,QTextEdit{background:#1b2131;border:1px solid #30394d;border-radius:6px;padding:8px} QPushButton{background:#794ee8;border:0;border-radius:6px;padding:10px;color:white} QPushButton:hover{background:#916afa} QLabel#heading{font-size:25px;font-weight:bold}")

    def page(self, name, description):
        self.nav.addItem(name); w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(32, 30, 32, 30)
        title = QLabel(name); title.setObjectName("heading"); v.addWidget(title)
        note = QLabel(description); note.setWordWrap(True); v.addWidget(note); self.pages.addWidget(w)
        return v

    def dashboard(self):
        v = self.page("Dashboard", "Local workspace overview. Source approval is specific to each file.")
        self.summary = QLabel(); v.addWidget(self.summary); self.refresh(); v.addStretch()
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
        self.creator = QLineEdit(); self.url = QLineEdit(); self.evidence = QLineEdit(); self.approved = QCheckBox("I explicitly approve publication use of this exact source")
        self.source.textChanged.connect(self.source_changed)
        form.addRow("Creator", self.creator); form.addRow("Source URL", self.url); form.addRow("Permission evidence", self.evidence); form.addRow(self.approved)
        b = QPushButton("Save source rights"); b.clicked.connect(self.save_source); v.addWidget(b)
        self.start_time = QDoubleSpinBox(); self.end_time = QDoubleSpinBox()
        for field in (self.start_time, self.end_time): field.setRange(0, 86400); field.setDecimals(2)
        self.end_time.setValue(15); form.addRow("Start (seconds)", self.start_time); form.addRow("End (seconds)", self.end_time)
        self.hook = QLineEdit(); form.addRow("Opening hook", self.hook)
        self.title = QLineEdit(); self.description = QTextEdit(); form.addRow("Title", self.title); form.addRow("Description", self.description)
        b = QPushButton("Render vertical MP4 and metadata"); b.clicked.connect(self.render_clip); v.addWidget(b)
        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); v.addWidget(self.progress); v.addStretch()
    def source_changed(self):
        self.approved.setChecked(False)
        self.words = []
        if hasattr(self, "transcript"): self.transcript.clear()

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
        path = QFileDialog.getSaveFileName(self, "Save new MP4", "clip.mp4", "MP4 (*.mp4)")[0]
        if not path: return
        source, start, end = self.source.text(), self.start_time.value(), self.end_time.value()
        hook, title, description = self.hook.text(), self.title.text(), self.description.toPlainText()
        words = list(getattr(self, "words", []))
        self.progress.setRange(0, 0)
        def work():
            result = render(self.store, source, start, end, path, words, hook, title)
            prepare_package(self.store, result, str(Path(path).with_suffix(".json")), description)
            return result
        def completed(result):
            self.progress.setRange(0, 1); self.progress.setValue(1)
            QMessageBox.information(self, "Export complete", f"Saved {result['output_path']} and metadata. Nothing was published.")
        self.background(work, completed)

    def diagnostics(self):
        v = self.page("Settings", "Media tool diagnostics")
        for name in ("ffmpeg", "ffprobe"):
            try: value = tool_path(name)
            except FileNotFoundError: value = "Not found"
            label = QLabel(f"{name}: {value}"); label.setWordWrap(True); v.addWidget(label)
        v.addStretch()

    def transcription(self):
        v = self.page("Highlight Finder", "Transcribe the selected local source. The model may be downloaded on first use. Highlight scoring and subtitle editing are not implemented.")
        b = QPushButton("Transcribe source"); b.clicked.connect(self.start_transcription); v.addWidget(b)
        self.transcript = QTextEdit(); self.transcript.setReadOnly(True); v.addWidget(self.transcript)
    def start_transcription(self):
        source = self.source.text()
        def work():
            require_rights(self.store, source)
            return transcribe(source)
        self.background(work, self.show_transcript)
    def show_transcript(self, words):
        self.words = words
        self.transcript.setPlainText("\n".join(f"{w['start']:.2f}-{w['end']:.2f} {w['text']}" for w in words))

    def background(self, fn, done):
        thread = QThread(self); worker = Worker(fn); worker.moveToThread(thread)
        thread.started.connect(worker.run); worker.finished.connect(done); worker.failed.connect(self.show_error)
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit); worker.finished.connect(worker.deleteLater); worker.failed.connect(worker.deleteLater)
        thread.finished.connect(lambda: self.threads.remove((thread, worker))); thread.finished.connect(thread.deleteLater)
        self.threads.append((thread, worker)); thread.start()
    def show_error(self, message):
        self.progress.setRange(0, 1); self.progress.setValue(0)
        QMessageBox.warning(self, "Operation failed", message)

    def closeEvent(self, event):
        if self.threads:
            QMessageBox.warning(self, "Processing", "Wait for background operations to finish before closing."); event.ignore()
        else: event.accept()


def main():
    app = QApplication(sys.argv); window = Window(); window.show(); return app.exec()

if __name__ == "__main__":
    sys.exit(main())
