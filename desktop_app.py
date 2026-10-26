"""Windows tray frontend and packaged BLE worker entry point."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid
from app_paths import data_dir, write_json
from desktop_settings import validated

TITLE = 'Codex 用量显示器'
WORKER_MODE = '--worker' in sys.argv


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def icon_image():
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=16, fill='#163c36')
    draw.arc((14, 13, 50, 49), 45, 315, fill='#ffffff', width=6)
    draw.ellipse((42, 41, 53, 52), fill='#83dbb9')
    return image



def main():
    if '--worker' in sys.argv:
        sys.argv.remove('--worker')
        home = data_dir()
        home.mkdir(parents=True, exist_ok=True)
        sys.stdout = (home / 'ble-sync.log').open('w', encoding='utf-8', buffering=1)
        sys.stderr = (home / 'ble-sync-error.log').open('w', encoding='utf-8', buffering=1)
        try:
            from ble_sender import main as worker_main
            worker_main()
        except BaseException as error:
            if not isinstance(error, SystemExit) or error.code:
                write_json(home / 'sync-status.json', {'state': 'error', 'time': time.time(), 'error': str(error)})
                traceback.print_exc()
            raise
        return
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    from PySide6.QtCore import qVersion
    from desktop_window import DesktopApp
    qt = QApplication(sys.argv)
    qt.setQuitOnLastWindowClosed(False)
    if '--smoke-test' in sys.argv:
        from bleak import BleakScanner
        app = DesktopApp(smoke=True)
        app.show(); qt.processEvents()
        app.grab().save(str(data_dir() / 'desktop-preview.png'))
        write_json(data_dir() / 'desktop-smoke.json', {'ok': True, 'qt': qVersion(),
            'provider': app.provider.currentText(), 'volume': app.volume.value(),
            'trayAvailable': QSystemTrayIcon.isSystemTrayAvailable(), 'bleBackend': BleakScanner.__module__})
        app.exiting=True; app.close()
        return
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    server_name = 'CodexUsageDisplayDesktop'
    socket = QLocalSocket()
    socket.connectToServer(server_name)
    if socket.waitForConnected(1500):
        socket.write(b'show'); socket.flush(); socket.waitForBytesWritten(1500)
        socket.disconnectFromServer()
        return
    QLocalServer.removeServer(server_name)
    server = QLocalServer(qt)
    if not server.listen(server_name):
        raise RuntimeError('无法启动桌面窗口通信服务：' + server.errorString())
    app = DesktopApp()
    def show_existing():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            app.open_settings()
            connection.close(); connection.deleteLater()
    server.newConnection.connect(show_existing)
    app.show()
    qt.aboutToQuit.connect(app.stop_worker)
    qt.exec()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        home = data_dir()
        home.mkdir(parents=True, exist_ok=True)
        (home / 'desktop-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        if not WORKER_MODE and '--smoke-test' not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, '启动失败，请查看 ' + str(home / 'desktop-error.log'), TITLE, 16)
        raise
