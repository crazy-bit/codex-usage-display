# ESP32-S3-ePaper-1.54 V2 固件

界面采用两组核心信息：REMAINING 下方为 48px 剩余百分比，RESETS AT 下方为 20px 北京时间。无标题、进度条或常驻 BLE 状态；仅等待数据、数据过期或多窗口切换时出现底部小提示。字体配置保存在 firmware/lv_conf.h，构建脚本自动同步到 LVGL 库配置。相同文字不触发重复设置。

当前里程碑：BLE 接收电脑实时用量。兼容目标为非 G 型号的黑白 200×200 V2；本例不初始化触控。设备启动显示等待数据，运行根目录 ble_sender.py 后显示真实百分比。协议见 BLE_PROTOCOL.md。

## 开发环境与烧录

### 从干净仓库准备依赖

- 将官方 [Arduino CLI 1.5.1 Windows x64](https://downloads.arduino.cc/arduino-cli/arduino-cli_1.5.1_Windows_64bit.zip) 解压到 tools/arduino-cli，使其中存在 arduino-cli.exe。
- 下载或克隆微雪官方仓库到 work/waveshare；本项目核对的版本为 aaf395c1bbd86f7c5b7a2f9f0401241ff5c44956。
- 运行 configure-arduino.ps1 生成当前目录的配置，然后执行：

```powershell
.\tools\arduino-cli\arduino-cli.exe core update-index --config-file arduino-cli.yaml
.\tools\arduino-cli\arduino-cli.exe core install esp32:esp32@3.3.0 --config-file arduino-cli.yaml
Copy-Item -LiteralPath work/waveshare/01_Arduino_Libraries/lvgl8/lvgl -Destination work/arduino-user/libraries/lvgl -Recurse
.\build-firmware.ps1 -Upload -Port COM3
```

COM3 是本机测试端口，其他电脑按实际端口修改。构建脚本会从 firmware/lv_conf.h 同步字体配置。下载目录和生成配置均被 Git 忽略。

1. 安装 Arduino IDE 2，并在开发板管理器安装 esp32 by Espressif Systems（官方要求 3.3.0 或以上，初次复现建议固定 3.3.0）。
2. 使用本项目 `work/waveshare/01_Arduino_Libraries/lvgl8` 中官方配套的 LVGL 8 库及 lv_conf.h，按厂商说明安装；不要混装 LVGL 9。确认 Arduino 能找到 lvgl.h，且 LV_COLOR_DEPTH 为 16。
3. 打开 `codex_display/codex_display.ino`，开发板选 ESP32S3 Dev Module；V2 使用 8MB Flash、OPI PSRAM，启用 USB CDC On Boot。其余设置对照官方仓库的 Tools Configuration.png，避免套用 V1 的存储配置。
4. 用数据 USB 线连接设备，选对应端口，编译后上传。无法进入下载模式时，按住 BOOT 重新接通 USB，识别端口后再松开。
5. 屏幕应显示 CODEX、--% 和等待提示；串口日志波特率 115200。电脑运行 BLE 发送程序后更新真实用量。首次初始化可能出现闪屏。

2026-10-21 已安装工程内 Arduino CLI 1.5.1、ESP32 core 3.3.0 和官方示例随附 LVGL（library.properties 标记 8.4.0，沿用配套 lv_conf.h）。编译成功，经 COM3 烧录并通过写入校验；串口确认输出 `Codex V2 static demo ready; values are simulated.`。屏幕视觉效果仍需查看实物确认。电池开关和低功耗逻辑后续实现；当前不提供长按关机处理。

## 本机命令行开发

在项目根目录运行：

```powershell
.\build-firmware.ps1
.\build-firmware.ps1 -Upload -Port COM3
```

第一条只编译，第二条编译并烧录。配置为 ESP32S3 Dev Module、USB CDC 开启、8MB Flash、OPI PSRAM、Huge APP 分区。CLI 位于 tools/arduino-cli，下载工具链和库位于 work/，无需系统 PATH。arduino-cli.yaml 使用本工程绝对路径，迁移目录时需修改。

当前 BLE 固件占 Flash 867907 字节，静态 RAM 85432 字节。构建文件位于 work/build。已验证广播、电脑连接、真实用量发送、设备确认及连续更新；拔电与长时间稳定性仍需后续观察。

## 驱动来源

`codex_display.ino` 的 LVGL 移植框架、user_config.h、src/display 和 src/power 来自微雪官方仓库的 `02_Example/Arduino/09_LVGL_V8_Test`；user_app.cpp 为本项目替换的静态页面。

官方仓库：https://github.com/waveshareteam/ESP32-S3-ePaper-1.54

官方教程：https://docs.waveshare.net/ESP32-S3-ePaper-1.54/Arduino/

驱动使用 SPI2，DC=10、CS=11、CLK=12、MOSI=13、RST=9、BUSY=8；屏幕供电 GPIO6 低电平有效，电池保持 GPIO17 高电平。以厂商 V2 原理图和代码为准，不需要外部接线。

## 低功耗同步

设备收到并确认一帧有效数据后，等待墨水屏刷新和语音播放完成，再进入 300 秒深度休眠。GPIO17 在休眠期间保持高电平，RTC 定时器唤醒后重新广播 BLE。若 45 秒内没有完成同步，设备仍会休眠；连续 3 个唤醒周期失败后，屏幕显示 `OFFLINE`。桌面端在设备休眠时每 5 秒扫描一次，并将该状态显示为“等待下次唤醒”。

RTC 内存保存上一帧数据，因此唤醒时不会先用空白占位内容覆盖墨水屏。电池电压通过 BLE 通知回传并写入桌面同步状态。

## 后续验收顺序

- 编译通过、上电显示正确，确认方向与字形。
- 加 BLE GATT 接收和确认机制，回调只入队，由显示任务更新画面。
- 电脑端增加持续采集、BLE 发送、分包和重连。
- 加数据过期提示、多窗口显示，再测试局刷残影及周期性全刷。
- 测量电流后再选择持续连接或定时唤醒策略。
