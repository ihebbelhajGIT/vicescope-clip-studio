import os
import subprocess
import sys


def test_timeline_seek_does_not_reseek_on_player_updates():
    code = '''
from PySide6.QtWidgets import QApplication
from app.preview import VideoPreview
app = QApplication([])
preview = VideoPreview()
calls = []
preview.player.setPosition = lambda position: calls.append(position)
preview.timeline.setRange(0, 10000)
preview.timeline.setValue(5000)
assert calls == [5000]
preview.update_position(6000)
assert calls == [5000] and preview.timeline.value() == 6000
preview.close()
'''
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=30)
