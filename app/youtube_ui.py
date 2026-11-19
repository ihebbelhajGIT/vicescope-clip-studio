"""YouTube actions for the Ready for Review page."""
from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget


class YouTubePanel(QWidget):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, window, jobs):
        super().__init__(window)
        self.window = window
        self.jobs = jobs
        self.busy = False
        self.on_success = None
        self.completed.connect(self.finish_success)
        self.failed.connect(self.finish_failure)
        self.video = None
        self.selection = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.account_status = QLabel('YouTube channel needs verification')
        self.account_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.account_status)
        account_row = QHBoxLayout()
        layout.addLayout(account_row)
        self.button(account_row, 'Connect YouTube', self.connect_account)
        self.button(account_row, 'Verify channel', self.verify_account)
        self.button(account_row, 'Disconnect YouTube', self.disconnect_account)
        self.video_status = QLabel()
        self.video_status.setTextFormat(Qt.TextFormat.PlainText)
        self.video_status.setWordWrap(True)
        layout.addWidget(self.video_status)
        self.description = QTextEdit()
        self.description.setReadOnly(True)
        self.description.setMaximumHeight(75)
        self.description.setAccessibleName('YouTube video description')
        layout.addWidget(self.description)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.upload_button = self.button(row, 'Upload Privately', self.upload)
        self.details_button = self.button(row, 'Refresh video details', self.preview)
        self.link_button = self.button(row, 'Open on YouTube', self.open_video)
        self.publish_button = self.button(row, 'Publish Publicly', self.confirm_publication)
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.refresh)
        self.refresh()

    def button(self, row, text, callback):
        button = QPushButton(text)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def refresh(self):
        item = self.window.selected_review() if hasattr(self.window, 'review_rows') else None
        item_id = item['id'] if item else None
        if item_id != self.selection:
            self.video = None
            self.selection = item_id
        row = self.jobs.record(item_id) if item_id else None
        has_video = bool(row and row['video_id'])
        approved = item and item['status'] == 'approved_private'
        self.upload_button.setEnabled(bool(approved and not has_video))
        self.details_button.setEnabled(has_video)
        self.link_button.setEnabled(has_video)
        self.publish_button.setEnabled(bool(approved and self.video and self.video['status']['privacyStatus'] == 'private'))
        self.description.setVisible(bool(self.video))
        if self.video:
            if self.description.toPlainText() != self.video['description']:
                self.description.setPlainText(self.video['description'])
            self.video_status.setText(f"{self.video['title']}\n{self.video['status']['privacyStatus']} • Video ID {self.video['id']}")
        elif has_video:
            self.video_status.setText(f"Video ID {row['video_id']}. Refresh video details to verify its current privacy.")
        elif row:
            self.video_status.setText(f"Upload progress: {row['sent'] * 100 // row['total']}% confirmed by YouTube")
        else:
            self.video_status.clear()

    def show_channel(self, channel):
        self.account_status.setText(f'Verified channel: {channel[0]} ({channel[1]})')

    def run(self, action, done):
        if self.busy:
            return
        self.busy = True
        self.on_success = done
        self.setEnabled(False)
        self.timer.start()
        self.window.background(action, self.completed.emit, failed=self.failed.emit)

    def finish(self):
        self.timer.stop()
        self.busy = False
        self.on_success = None
        self.setEnabled(True)
        self.refresh()

    @Slot(object)
    def finish_success(self, value):
        try:
            self.on_success(value)
        finally:
            self.finish()

    @Slot(str)
    def finish_failure(self, message):
        self.video = None
        self.finish()
        self.account_status.setText('Use Verify channel to check the current connection.')
        QMessageBox.warning(self, 'YouTube', message)

    def connect_account(self):
        self.reset_details()
        self.run(lambda: self.jobs.account_action(self.jobs.account.connect), self.show_channel)

    def verify_account(self):
        self.run(lambda: self.jobs.account_action(self.jobs.account.channel), self.show_channel)

    def disconnect_account(self):
        self.reset_details()
        self.run(lambda: self.jobs.account_action(self.jobs.account.disconnect),
                 lambda _: self.account_status.setText('YouTube disconnected'))

    def reset_details(self):
        self.video = None
        self.refresh()

    def show_video(self, item_id, video):
        self.show_channel(video['channel'])
        if self.selection == item_id:
            self.video = video
            self.refresh()

    def upload(self):
        item = self.window.selected_review()
        if item and self.window.review_fields_saved(item):
            item_id = item['id']
            self.run(lambda: self.jobs.upload(item_id), lambda video: self.show_video(item_id, video))

    def preview(self):
        item_id = self.selection
        if item_id:
            self.run(lambda: self.jobs.preview(item_id), lambda video: self.show_video(item_id, video))

    def open_video(self):
        row = self.jobs.record(self.selection)
        if row and row['video_id']:
            QDesktopServices.openUrl(QUrl('https://www.youtube.com/watch?v=' + row['video_id']))

    def confirm_publication(self):
        item = self.window.selected_review()
        video = self.video
        if not item or not video or not self.window.review_fields_saved(item):
            return
        dialog = QMessageBox(QMessageBox.Icon.Question, 'Publish Publicly', '', parent=self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Make this video public on {video['channel'][0]} ({video['channel'][1]})?\n"
                       f"Current privacy: {video['status']['privacyStatus']}\n\n"
                       f"{video['title']}")
        dialog.setDetailedText(video['description'])
        publish = dialog.addButton('Publish Publicly', QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() == publish:
            item_id = item['id']
            self.run(lambda: self.jobs.publish(item_id, video), lambda result: self.show_video(item_id, result))
