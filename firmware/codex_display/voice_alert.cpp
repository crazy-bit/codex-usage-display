#include <Arduino.h>
#include <Preferences.h>
#include <atomic>
#include "voice_alert.h"
#include "voice_pcm.h"
#include "user_config.h"
extern "C" {
#include "src/codec_board/codec_board.h"
#include "src/codec_board/codec_init.h"
#include "src/esp_codec_dev/include/esp_codec_dev.h"
}

static QueueHandle_t events;
static Preferences preferences;
static uint32_t lastEvent;
static std::atomic<uint8_t> volume{80};

static void player(void *) {
  esp_codec_dev_handle_t playback = nullptr;
  uint32_t event;
  for (;;) {
    if (xQueueReceive(events, &event, portMAX_DELAY) != pdTRUE) continue;
    if (!playback) {
      gpio_set_level(Audio_PWR_PIN, 0);
      vTaskDelay(pdMS_TO_TICKS(50));
      set_codec_board_type("S3_ePaper_1_54");
      codec_init_cfg_t config = {};
      config.in_mode = CODEC_I2S_MODE_STD;
      config.out_mode = CODEC_I2S_MODE_STD;
      config.reuse_dev = false;
      if (init_codec(&config) != ESP_OK) { Serial.println("Voice codec init failed"); continue; }
      playback = get_playback_handle();
      esp_codec_dev_sample_info_t format = {};
      format.sample_rate = 16000; format.channel = 2; format.bits_per_sample = 16;
      if (!playback || esp_codec_dev_open(playback, &format) != ESP_CODEC_DEV_OK) {
        Serial.println("Voice codec open failed"); playback = nullptr; continue;
      }
    }
    esp_codec_dev_set_out_vol(playback, volume.load());
    esp_codec_dev_set_out_mute(playback, false);
    bool ok = true;
    // Copy flash PCM into a small RAM buffer for the codec write path.
    uint8_t chunk[512];
    for (size_t offset = 0; offset < sizeof(voice_pcm); offset += sizeof(chunk)) {
      size_t count = min(sizeof(chunk), sizeof(voice_pcm) - offset);
      memcpy(chunk, voice_pcm + offset, count);
      if (esp_codec_dev_write(playback, chunk, count) != ESP_CODEC_DEV_OK) { ok = false; break; }
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_codec_dev_set_out_mute(playback, true);
    Serial.printf("Voice alert event=%lu playback=%s\n", (unsigned long)event, ok ? "OK" : "failed");
  }
}
void voice_start() {
  preferences.begin("codex-voice", false);
  lastEvent = preferences.getULong("last", 0);
  volume = preferences.getUChar("volume", 80);
  events = xQueueCreate(1, sizeof(uint32_t));
  assert(events);
  xTaskCreate(player, "voice", 6144, nullptr, 2, nullptr);
}
void voice_set_volume(uint8_t value) {
  if (value > 100) return;
  if (volume.exchange(value) != value) preferences.putUChar("volume", value);
  Serial.printf("Voice volume=%u\n", (unsigned)value);
}
void voice_alert(uint32_t event) {
  if (!event || event == lastEvent) return;
  if (xQueueSend(events, &event, 0) == pdTRUE) {
    // Persist before playback: retries/reboots must not repeat the same alert.
    lastEvent = event;
    preferences.putULong("last", event);
  }
}
