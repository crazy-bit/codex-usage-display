# Codex 用量显示器

Windows 桌面版已提供：双击 `dist/CodexUsageDisplay.exe`，可在窗口选择 Codex 监控、调整提示音量与试听，并驻留系统托盘。完整使用和打包说明见 DESKTOP.md。桌面版不需要安装 Python。

Windows 托盘客户端 + 微雪 ESP32-S3-ePaper-1.54 V2 固件：读取本机 Codex 账户额度，通过 BLE 更新墨水屏，并在周期重置后播放“干活啦”。桌面用量读取不依赖显示器在线。

## 从源码运行桌面版

```powershell
git clone https://github.com/crazy-bit/codex-usage-display.git
cd codex-usage-display
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
.\.venv\Scripts\python.exe desktop_app.py
```

桌面/BLE 程序要求 Windows、Python 3.11+、已登录的 Codex 和蓝牙适配器。执行 `build-desktop.ps1` 可生成 dist/CodexUsageDisplay.exe。仓库只包含源码，不包含 EXE、虚拟环境、下载工具链、日志或账户数据。固件构建见 firmware/README.md，第三方来源见 THIRD_PARTY_NOTICES.md。

要求：Python 3.10+、可运行的 Codex CLI、已通过 ChatGPT 登录。无第三方 Python 依赖。

```powershell
cd F:\ai\hobby\codex-usage-display
python usage.py
python usage.py --json
.\.venv\Scripts\python.exe -m unittest -v
```

默认先从 PATH 查找 Codex；找不到时，自动查找 Windows 桌面版的 `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe`，多个版本按文件修改时间选择最新者。无需修改系统 PATH。也可使用 `--codex "完整的 codex.exe 路径"` 显式指定；显式路径无效时直接报错。

每个 RPC 默认超时 30 秒，可用 `--timeout 10` 调整。登录问题先运行 `codex login status`，需要时由用户运行 `codex login`。

JSON 包含 fetchedAt 和 buckets 数组，各桶含 primary / secondary。未知数据为 null；不硬编码周期。resetsAt 为 Unix 秒，文本模式按运行电脑本地时区显示。JSON 不包含账号 ID、登录令牌或重置券详情。

usage.py 为单次快照模式，不自动操作额度重置。

## BLE 实时显示（V2）

板端已烧录 BLE 固件时，在本工程 PowerShell 运行：

```powershell
.\.venv\Scripts\python.exe ble_sender.py
```

默认每 60 秒同步；Ctrl+C 停止。单次发送使用 `--once`，多台设备时用 `--address AC:27:6E:D3:76:BD` 指定本机已验证的设备。读取其他额度桶可用 `--limit NAME`。不需要在 Windows 设置中预先配对，电脑蓝牙需要开启。

BLE 程序需要 Python 3.11+ 和 requirements-ble.txt 中的依赖；本机工程虚拟环境已安装。重新配置时：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-ble.txt
```

程序按服务 UUID 扫描；断线或查询失败会退避重试，成功接收后终端打印 Acknowledged。确认表示板端已校验并更新 UI 对象，墨水屏物理刷新随后完成。多窗口每 15 秒切换；设备 5 分钟没有成功接收则显示 STALE。屏幕重置时间为北京时间。只运行一个发送进程。

板上 Type-C 可供电；传输走 BLE。使用电池时，续航和长按关机尚未实现。当前 BLE 原型没有配对认证，附近设备可能连接或改写显示，不传输账户凭据。电脑关闭后不再获取新用量。

## 重置语音提醒

设备内置“干活啦”中文语音（Windows Huihui 离线合成，16kHz/16bit 双通道 PCM），经官方 ES8311 驱动播放，默认音量 80。音频驱动来自官方 08_Audio_Test 的 codec_board、esp_codec_dev 目录；录音数据不被读取。

后台同步运行时，可直接通过电脑调整音量，无需重新烧录或停止同步：

```powershell
.\.venv\Scripts\python.exe volume.py 80
.\.venv\Scripts\python.exe volume.py 50 --preview
```

范围 0～100（0 静音），--preview 设置后播放一次“干活啦”。音量存入板端 NVS，重启保留；未保存时默认80。新音量在下一次播报开始时应用，不中途改变正在播放的声音。命令通过本地请求文件交给后台发送进程，设备确认后打印成功；超过45秒无确认会报错，请求仍保留，恢复同步后可能执行。若连多台显示器，音量命令作用于本工程正在同步的设备。

后台同步默认启用提醒：历史重置时间已到、服务端返回更晚的重置时间，且剩余额度回升或恢复满额时触发。初次启动只保存基线，不播报；无法仅靠倒计时判断，不保证无网或无同步时准点提醒。电脑 work/reset-state-*.json 保存基线，板端 NVS 保存最近提醒事件，防止重连、重试或重启重复播报。多个窗口同次更新触发时合并为一次。播放入队即持久去重，因此播放期间突然断电不会自动补播。

试听命令（会播放一次，请先停掉后台同步，避免抢占连接）：

```powershell
.\.venv\Scripts\python.exe ble_sender.py --test-voice
```

试听同时发送真实用量，不伪造额度或执行账户重置。当前识别计划周期重置，提前手动重置不在判断范围内。

接口依据：https://learn.chatgpt.com/docs/app-server （account/rateLimits/read）。需要与当前安装的 Codex CLI 版本保持兼容。
