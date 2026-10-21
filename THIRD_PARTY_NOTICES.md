# Third-party source and assets

The firmware uses sources from [Waveshare ESP32-S3-ePaper-1.54](https://github.com/waveshareteam/ESP32-S3-ePaper-1.54), reviewed at commit `aaf395c1bbd86f7c5b7a2f9f0401241ff5c44956`.

- Display and power drivers, user_config.h, and the LVGL port in codex_display.ino derive from the official `02_Example/Arduino/09_LVGL_V8_Test` example. Original source notices are retained where present.
- `firmware/codex_display/src/codec_board` derives from official `08_Audio_Test`, with its Espressif Modified MIT License retained in that directory. That license restricts use to Espressif Systems products.
- `firmware/codex_display/src/esp_codec_dev` derives from official `08_Audio_Test`, with its Apache-2.0 LICENSE and source notices retained.
- `firmware/lv_conf.h` derives from the configuration supplied with the official LVGL 8 example. LVGL itself is an external dependency and is not vendored here.
- `firmware/assets/gan-huo-la.wav` and `voice_pcm.h` are the reminder phrase “干活啦”, synthesized locally using the Windows Microsoft Huihui voice. The WAV is the source audio; the header is 16kHz, signed 16-bit stereo PCM used by the firmware.

Desktop dependencies are declared in requirements-desktop.txt and requirements-ble.txt; their respective licenses continue to apply. No repository-wide license grant is implied for the above third-party components.
