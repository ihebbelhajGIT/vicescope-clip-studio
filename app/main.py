from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)
from app.core import discover, transcribe, write_package
from app.storage import Store


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
        for name in ("Livestreams", "Export Queue", "Analytics", "Settings"):
            self.page(name, "Not implemented yet. No data or publishing actions are simulated.")
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
        v = self.page("Clip Editor", "Register a local source and document its permission. Exporting video is not implemented yet.")
        form = QFormLayout(); v.addLayout(form)
        self.source = QLineEdit(); browse = QPushButton("Choose local video"); browse.clicked.connect(self.pick_source)
        form.addRow(browse, self.source)
        self.creator = QLineEdit(); self.url = QLineEdit(); self.evidence = QLineEdit(); self.approved = QCheckBox("I explicitly approve publication use of this exact source")
        self.source.textChanged.connect(lambda: self.approved.setChecked(False))
        form.addRow("Creator", self.creator); form.addRow("Source URL", self.url); form.addRow("Permission evidence", self.evidence); form.addRow(self.approved)
        b = QPushButton("Save source rights"); b.clicked.connect(self.save_source); v.addWidget(b)
        self.clip = QLineEdit(); choose = QPushButton("Choose existing MP4"); choose.clicked.connect(lambda: self.clip.setText(QFileDialog.getOpenFileName(self, "Select MP4", "", "Video (*.mp4)")[0])); form.addRow(choose, self.clip)
        self.title = QLineEdit(); self.description = QTextEdit(); form.addRow("Title", self.title); form.addRow("Description", self.description)
        b = QPushButton("Prepare metadata draft"); b.clicked.connect(self.package); v.addWidget(b); v.addStretch()
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
    def package(self):
        path = QFileDialog.getSaveFileName(self, "Save metadata draft", "metadata.json", "JSON (*.json)")[0]
        if not path: return
        try:
            write_package(self.store, self.source.text(), self.clip.text(), path, self.title.text(), self.description.toPlainText()); QMessageBox.information(self, "Draft", "Metadata draft saved. Nothing was published.")
        except Exception as e: QMessageBox.warning(self, "Draft", str(e))
    def transcription(self):
        v = self.page("Highlight Finder", "Transcribe the selected local source. The model may be downloaded on first use. Highlight scoring is not implemented.")
        b = QPushButton("Transcribe source"); b.clicked.connect(self.start_transcription); v.addWidget(b)
        self.transcript = QTextEdit(); v.addWidget(self.transcript)
    def start_transcription(self):
        source = self.source.text()
        self.background(lambda: transcribe(source), lambda words: self.transcript.setPlainText("\n".join(f"{w['start']:.2f}-{w['end']:.2f} {w['text']}" for w in words)))
    def background(self, fn, done):
        thread = QThread(self); worker = Worker(fn); worker.moveToThread(thread)
        thread.started.connect(worker.run); worker.finished.connect(done); worker.failed.connect(lambda e: QMessageBox.warning(self, "Operation failed", e))
        worker.finished.connect(thread.quit); worker.failed.connect(thread.quit); worker.finished.connect(worker.deleteLater); worker.failed.connect(worker.deleteLater)
        thread.finished.connect(lambda: self.threads.remove((thread, worker))); thread.finished.connect(thread.deleteLater)
        self.threads.append((thread, worker)); thread.start()
    def closeEvent(self, event):
        if self.threads:
            QMessageBox.warning(self, "Processing", "Wait for background operations to finish before closing."); event.ignore()
        else: event.accept()


def main():
    app = QApplication(sys.argv); window = Window(); window.show(); return app.exec()

if __name__ == "__main__":
    sys.exit(main())
