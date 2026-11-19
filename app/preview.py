"""Embedded media preview and selection controls."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


class VideoPreview(QWidget):
    selection_start = Signal(float)
    selection_end = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.video = QVideoWidget()
        self.video.setMinimumHeight(180)
        layout.addWidget(self.video)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 0)
        self.timeline.sliderMoved.connect(self.player.setPosition)
        self.player.durationChanged.connect(lambda duration: self.timeline.setRange(0, duration))
        self.player.positionChanged.connect(self.update_position)
        layout.addWidget(self.timeline)
        buttons = QHBoxLayout()
        self.play_button = QPushButton('Play / Pause')
        self.play_button.clicked.connect(self.toggle)
        self.position_label = QLabel('0.00 / 0.00 s')
        mark_start = QPushButton('Set start here')
        mark_end = QPushButton('Set end here')
        mark_start.clicked.connect(lambda: self.selection_start.emit(self.player.position() / 1000))
        mark_end.clicked.connect(lambda: self.selection_end.emit(self.player.position() / 1000))
        for widget in (self.play_button, self.position_label, mark_start, mark_end): buttons.addWidget(widget)
        layout.addLayout(buttons)
        self.error_label = QLabel('')
        self.player.errorOccurred.connect(lambda error, message: self.error_label.setText(message))
        layout.addWidget(self.error_label)

    def load(self, path):
        self.player.stop()
        self.error_label.clear()
        self.player.setSource(QUrl.fromLocalFile(path))

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def update_position(self, position):
        if not self.timeline.isSliderDown(): self.timeline.setValue(position)
        self.position_label.setText(f'{position / 1000:.2f} / {self.player.duration() / 1000:.2f} s')
