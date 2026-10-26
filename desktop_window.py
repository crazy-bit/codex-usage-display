import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QSlider, QLineEdit, QSystemTrayIcon, QMenu, QMessageBox,
    QDialog, QPlainTextEdit, QSpinBox)
from app_paths import data_dir, write_json
from desktop_settings import validated

TITLE = 'Codex 用量显示器'


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


class DesktopApp(QWidget):
    def __init__(self, smoke=False):
        super().__init__()
        self.home = data_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.settings = validated(read_json(self.home / 'desktop-settings.json'))
        self.worker = None
        self.tray = None
        self.pending_id = None
        self.pending_since = 0
        self.exiting = False
        self.setWindowTitle(TITLE)
        self.resize(540, 620)
        self.setMinimumSize(500, 600)
        from desktop_app import icon_image
        icon_path = self.home / 'desktop.ico'
        icon_image().save(icon_path, sizes=[(16,16), (32,32), (64,64)])
        self.icon = QIcon(str(icon_path))
        self.setWindowIcon(self.icon)
        self.setStyleSheet('''
            QWidget {font-family: "Microsoft YaHei UI"; font-size:14px; color:#263b34; background:#f5f7f6;}
            QLabel#title {font-size:25px; font-weight:600;}
            QLabel#quota {font-family:"Segoe UI";font-size:48px;font-weight:700;color:#163c36;}
            QLabel#muted {color:#68766f;}
            QPushButton {background:white;border:1px solid #cbd6d0;border-radius:7px;padding:9px 14px;}
            QPushButton:hover {background:#e5eee8;}
            QPushButton#primary {background:#163c36;color:white;border:none;}
            QComboBox,QLineEdit {background:white;border:1px solid #cbd6d0;border-radius:5px;padding:6px;}
            QCheckBox {spacing:9px;}
            QSlider::groove:horizontal {height:5px;background:#dce5df;border-radius:2px;}
            QSlider::sub-page:horizontal {background:#256c58;}
            QSlider::handle:horizontal {width:18px;margin:-7px 0;background:#256c58;border-radius:9px;}
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 25, 28, 24)
        layout.setSpacing(13)
        title = QLabel('用量，随时可见'); title.setObjectName('title'); layout.addWidget(title)
        subtitle = QLabel('通过蓝牙同步到你的墨水屏'); subtitle.setObjectName('muted'); layout.addWidget(subtitle)
        row = QHBoxLayout()
        self.quota = QLabel('--%'); self.quota.setObjectName('quota'); row.addWidget(self.quota)
        self.reset = QLabel('等待同步'); self.reset.setAlignment(Qt.AlignRight | Qt.AlignVCenter); row.addWidget(self.reset)
        layout.addLayout(row)
        self.state = QLabel('准备就绪'); self.state.setObjectName('muted'); self.state.setWordWrap(True); layout.addWidget(self.state)
        row = QHBoxLayout(); row.addWidget(QLabel('监控项目')); row.addStretch()
        self.provider = QComboBox(); self.provider.addItems(['Codex']); self.provider.setMinimumWidth(160); row.addWidget(self.provider)
        layout.addLayout(row)
        self.monitor = QCheckBox('开启后台同步（每 5 分钟）'); self.monitor.setChecked(self.settings['monitor']); layout.addWidget(self.monitor)
        self.voice = QCheckBox('额度重置后说“干活啦”'); self.voice.setChecked(self.settings['voice']); layout.addWidget(self.voice)
        row = QHBoxLayout(); row.addWidget(QLabel('提示音量')); row.addStretch()
        self.volume_text = QLabel(str(self.settings['volume'])); row.addWidget(self.volume_text); layout.addLayout(row)
        self.volume = QSlider(Qt.Horizontal); self.volume.setRange(0,100); self.volume.setValue(self.settings['volume'])
        self.volume.valueChanged.connect(lambda v: self.volume_text.setText(str(v))); layout.addWidget(self.volume)
        row = QHBoxLayout(); row.addWidget(QLabel('设备地址（可留空）'))
        self.address = QLineEdit(self.settings['address']); self.address.setPlaceholderText('自动发现'); row.addWidget(self.address); layout.addLayout(row)
        row = QHBoxLayout()
        self.save_button = QPushButton('保存并应用'); self.save_button.setObjectName('primary'); self.save_button.clicked.connect(lambda: self.apply())
        self.preview_button = QPushButton('试听“干活啦”'); self.preview_button.clicked.connect(lambda: self.apply(preview=True))
        row.addWidget(self.save_button); row.addWidget(self.preview_button); row.addStretch(); layout.addLayout(row)
        self.feedback = QLabel('关闭窗口后继续运行；从系统托盘退出可停止同步。')
        self.feedback.setObjectName('muted'); self.feedback.setWordWrap(True); layout.addWidget(self.feedback)
        self.reset_state = QLabel('免费重置通知等待检查'); self.reset_state.setObjectName('muted'); self.reset_state.setWordWrap(True)
        layout.addWidget(self.reset_state)
        layout.addStretch()
        row = QHBoxLayout()
        hide = QPushButton('最小化到托盘'); hide.clicked.connect(self.hide_to_tray); row.addWidget(hide); row.addStretch()
        reset_button = QPushButton('重置通知设置'); reset_button.clicked.connect(self.open_reset_settings); row.addWidget(reset_button)
        logs = QPushButton('打开日志'); logs.clicked.connect(self.open_logs)
        row.addWidget(logs); layout.addLayout(row)
        if not smoke:
            self.tray = QSystemTrayIcon(self.icon, self)
            self.menu = QMenu()
            self.menu.addAction('打开设置', self.open_settings)
            self.menu.addAction('开始 / 暂停同步', self.toggle)
            self.menu.addSeparator(); self.menu.addAction('退出', self.exit)
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self.tray_activated)
            self.tray.setToolTip(TITLE); self.tray.show()
            if self.settings['monitor']:
                self.start_worker(); self.request_volume(False)
            self.timer = QTimer(self); self.timer.timeout.connect(self.poll); self.timer.start(1000)

    def open_settings(self):
        self.hide(); self.showNormal(); self.raise_(); self.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    def toggle(self):
        self.monitor.setChecked(not self.settings['monitor']); self.apply()

    def open_reset_settings(self):
        from reset_monitor import settings
        config = settings()
        dialog = QDialog(self); dialog.setWindowTitle('免费重置通知'); dialog.resize(640,480)
        layout = QVBoxLayout(dialog)
        source = QLabel('Data: <a href="https://codex-reset.com/">codex-reset.com</a>')
        source.setOpenExternalLinks(True); layout.addWidget(source)
        enabled = QCheckBox('启用免费重置通知'); enabled.setChecked(config['enabled']); layout.addWidget(enabled)
        interval = QSpinBox(); interval.setRange(60,3600); interval.setSingleStep(60)
        interval.setValue(config['interval']); interval.setSuffix(' 秒 / 次'); layout.addWidget(interval)
        note = QLabel('无需 Token。列表显示预告与已确认重置，每 2 秒刷新；后台按检查间隔拉取。Tibo 预告也语音提醒；确认重置另行播报。个人用量以 Codex 为准。')
        note.setWordWrap(True); layout.addWidget(note)
        records = QPlainTextEdit(); records.setReadOnly(True); layout.addWidget(records)
        checked = QLabel(); checked.setWordWrap(True); layout.addWidget(checked)
        def refresh_records():
            import datetime
            status = read_json(self.home/'reset-status.json', {})
            def local_time(value):
                try:
                    if isinstance(value, (int,float)):
                        when = datetime.datetime.fromtimestamp(value,datetime.timezone.utc)
                    else:
                        when = datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
                    return when.astimezone(datetime.timezone(datetime.timedelta(hours=8))).strftime('%m/%d %H:%M:%S')
                except (ValueError, TypeError, AttributeError):
                    return '未知'
            checked.setText('最近检查：'+local_time(status.get('time'))+'（北京时间） · '
                + {'ready':'读取成功','disabled':'监控已暂停','error':status.get('error','读取失败')}.get(status.get('state'),'等待检查'))
            content = '\n\n'.join(('【已确认重置】' if p.get('confirmed') else '【Tibo 重置预告】')
                +' '+local_time(p.get('announced_at'))+'\n'+p.get('summary','')+'\n'+p.get('url','')
                for p in status.get('events', [])) or '尚无重置消息。后台开启后自动检查。'
            if records.toPlainText() != content:
                scroll = records.verticalScrollBar().value()
                records.setPlainText(content)
                records.verticalScrollBar().setValue(scroll)
        refresh_records()
        timer = QTimer(dialog); timer.timeout.connect(refresh_records); timer.start(2000)
        save = QPushButton('保存配置'); layout.addWidget(save)
        def accept():
            write_json(self.home/'reset-settings.json', {'enabled':enabled.isChecked(),
                'interval':interval.value(), 'revision':uuid.uuid4().hex})
            dialog.accept(); self.feedback.setText('免费通知设置已保存，后台运行时约 5 秒内生效。')
        save.clicked.connect(accept); dialog.exec()

    def open_logs(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('运行日志')
        dialog.resize(760, 500)
        layout = QVBoxLayout(dialog)
        location = QLabel('日志目录：' + str(self.home)); location.setWordWrap(True); layout.addWidget(location)
        text = QPlainTextEdit(); text.setReadOnly(True); layout.addWidget(text)
        def refresh():
            sections = []
            for name in ('quota-status.json', 'sync-status.json', 'reset-status.json', 'forecast-status.json', 'ble-sync.log', 'ble-sync-error.log', 'desktop-error.log'):
                path = self.home / name
                try:
                    with path.open('rb') as stream:
                        stream.seek(max(0, path.stat().st_size - 24000))
                        content = stream.read().decode('utf-8', errors='replace')
                except FileNotFoundError:
                    content = '暂无日志'
                except OSError as error:
                    content = str(error)
                sections.append(name + '\n' + content)
            text.setPlainText('\n\n'.join(sections))
        refresh_button = QPushButton('刷新'); refresh_button.clicked.connect(refresh); layout.addWidget(refresh_button)
        refresh(); dialog.exec()

    def start_worker(self):
        if self.worker and self.worker.poll() is None: return
        write_json(self.home / 'sync-status.json', {'state': 'starting', 'time': time.time()})
        command = [sys.executable, '--worker'] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).with_name('desktop_app.py')), '--worker']
        if self.settings['address']: command += ['--address', self.settings['address']]
        if not self.settings['voice']: command.append('--no-voice')
        env = os.environ.copy(); env['CODEX_USAGE_DATA_DIR'] = str(self.home); env['PYTHONUNBUFFERED'] = '1'
        self.worker = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def stop_worker(self):
        if self.worker and self.worker.poll() is None:
            subprocess.run(['taskkill','/PID',str(self.worker.pid),'/T','/F'], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), timeout=10)
            self.worker.wait(timeout=5)
        self.worker = None

    def request_volume(self, preview):
        request = {'id':uuid.uuid4().hex,'volume':self.settings['volume'],'preview':preview}
        write_json(self.home / 'volume-request.json', request)
        self.pending_id = request['id']; self.pending_since = time.time()
        self.feedback.setText('设置已保存，等待设备确认音量…')

    def apply(self, preview=False):
        candidate = {'provider':'Codex','monitor':self.monitor.isChecked(),'voice':self.voice.isChecked(),
            'volume':self.volume.value(),'address':self.address.text().strip()}
        settings = validated(candidate)
        if settings['address'] != candidate['address']:
            QMessageBox.warning(self,TITLE,'设备地址格式应为 AC:27:6E:D3:76:BD，也可以留空。'); return
        if preview and not settings['monitor']:
            QMessageBox.information(self,TITLE,'请先开启后台同步，再试听。'); return
        restart = any(settings[k] != self.settings[k] for k in ('voice','address'))
        self.settings = settings; write_json(self.home / 'desktop-settings.json', settings)
        if not settings['monitor'] or restart: self.stop_worker()
        if settings['monitor']:
            self.start_worker(); self.request_volume(preview)
        else:
            (self.home / 'volume-request.json').unlink(missing_ok=True)
            self.pending_id = None; self.feedback.setText('设置已保存，同步已暂停。')

    def poll(self):
        status = read_json(self.home / 'sync-status.json', {})
        quota = read_json(self.home / 'quota-status.json', {})
        reset_status = read_json(self.home / 'reset-status.json', {})
        message = {'disabled':'免费重置通知已暂停', 'ready':'免费重置通知已检查',
            'error':reset_status.get('error','重置数据查询失败')}.get(reset_status.get('state'),'免费重置通知等待检查')
        self.reset_state.setText(message + ' · Data: <a href="https://codex-reset.com/">codex-reset.com</a>')
        self.reset_state.setOpenExternalLinks(True)
        # Desktop monitoring is independent of whether a BLE screen is connected.
        if quota.get('state') == 'ready':
            windows = quota.get('windows', [])
            if windows:
                r = windows[0].get('r')
                self.quota.setText(f'{r:g}%' if isinstance(r, (int, float)) else '--%')
                reset = windows[0].get('t')
                if isinstance(reset, (int, float)):
                    import datetime
                    when = datetime.datetime.fromtimestamp(reset, datetime.timezone(datetime.timedelta(hours=8)))
                    self.reset.setText('北京时间重置\n' + when.strftime('%m/%d  %H:%M'))
        if not self.worker: text='同步已暂停'
        elif self.worker.poll() is not None: text='同步进程已停止，请检查 Codex 登录或打开日志；点击保存可重试。'
        else:
            state = status.get('state')
            text={'starting':'正在启动…','scanning':'正在寻找蓝牙显示器…','sleeping':'显示器休眠中，等待下次唤醒…','connected':'设备已连接，正在读取用量…',
                'synced':'已同步 · 每 5 分钟自动更新','retrying':'设备连接或查询失败，正在自动重试…',
                'error':'启动失败，请打开日志查看。'}.get(state,'正在同步…')
            if quota.get('state') == 'ready' and state == 'sleeping':
                text = 'Codex 用量已读取 · 显示器休眠中，等待下次唤醒…'
            elif quota.get('state') == 'ready' and state in ('scanning', 'retrying'):
                text = 'Codex 用量已读取 · 正在等待显示器…'
            if quota.get('state') == 'error':
                text = 'Codex 用量读取失败，请检查登录；详情见运行日志。'
            elif quota.get('state') == 'ready' and time.time() - quota.get('time', 0) > 420:
                text = 'Codex 用量暂未更新，正在重试…'
            if state=='synced':
                windows=status.get('windows',[])
                if windows:
                    r=windows[0].get('r'); self.quota.setText(f'{r:g}%' if isinstance(r,(int,float)) else '--%')
                    reset=windows[0].get('t')
                    if isinstance(reset,(int,float)):
                        import datetime
                        when=datetime.datetime.fromtimestamp(reset,datetime.timezone(datetime.timedelta(hours=8)))
                        self.reset.setText('北京时间重置\n'+when.strftime('%m/%d  %H:%M'))
                if time.time()-status.get('time',0)>420: text='同步暂未更新，请检查设备连接。'
        self.state.setText(text)
        if self.tray: self.tray.setToolTip(f'{TITLE} · {self.quota.text()}\n{text}')
        if self.pending_id:
            result=read_json(self.home / 'volume-result.json',{})
            if result.get('id')==self.pending_id:
                self.feedback.setText(f'设备已确认音量 {result["volume"]}，重启后保留。'); self.pending_id=None
            elif time.time()-self.pending_since>45: self.feedback.setText('设备暂未确认音量；连接恢复后会自动应用。')

    def hide_to_tray(self):
        if self.tray and QSystemTrayIcon.isSystemTrayAvailable(): self.hide()
        else: self.showMinimized()

    def closeEvent(self, event):
        if self.exiting: event.accept()
        else: event.ignore(); self.hide_to_tray()

    def exit(self):
        if self.exiting: return
        self.exiting=True; self.stop_worker()
        if self.tray: self.tray.hide()
        QApplication.instance().quit()
