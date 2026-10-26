#include <Arduino.h>
#include <Preferences.h>
#include <atomic>
#include "voice_alert.h"
#include "voice_pcm.h"
#include "preview_pcm.h"
#include "user_config.h"
extern "C" {
#include "src/codec_board/codec_board.h"
#include "src/codec_board/codec_init.h"
#include "src/esp_codec_dev/include/esp_codec_dev.h"
}

struct VoiceEvent { uint32_t id; uint8_t kind; };
static QueueHandle_t events;
static Preferences preferences;
static uint32_t lastEvent;
static std::atomic<uint8_t> volume{80};
static std::atomic<bool> playing{false};

static void power_down_codec(esp_codec_dev_handle_t &playback) {
  if (playback) {
    esp_codec_dev_set_out_mute(playback, true);
    esp_codec_dev_close(playback);
  }
  deinit_codec();
  playback = nullptr;
  gpio_set_level(Audio_PWR_PIN, 1);
  playing = false;
}

static void player(void *) {
  esp_codec_dev_handle_t playback = nullptr;
  VoiceEvent event;
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
      if (init_codec(&config) != ESP_OK) {
        Serial.println("Voice codec init failed");
        power_down_codec(playback);
        continue;
      }
      playback = get_playback_handle();
      esp_codec_dev_sample_info_t format = {};
      format.sample_rate = 16000; format.channel = 2; format.bits_per_sample = 16;
      if (!playback || esp_codec_dev_open(playback, &format) != ESP_CODEC_DEV_OK) {
        Serial.println("Voice codec open failed");
        power_down_codec(playback);
        continue;
      }
    }
    esp_codec_dev_set_out_vol(playback, volume.load());
    esp_codec_dev_set_out_mute(playback, false);
    bool ok = true;
    // Copy flash PCM into a small RAM buffer for the codec write path.
    const uint8_t *pcm = event.kind == 1 ? preview_pcm : voice_pcm;
    size_t pcmSize = event.kind == 1 ? sizeof(preview_pcm) : sizeof(voice_pcm);
    uint8_t chunk[512];
    for (size_t offset = 0; offset < pcmSize; offset += sizeof(chunk)) {
      size_t count = min(sizeof(chunk), pcmSize - offset);
      memcpy(chunk, pcm + offset, count);
      if (esp_codec_dev_write(playback, chunk, count) != ESP_CODEC_DEV_OK) { ok = false; break; }
    }
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_codec_dev_set_out_mute(playback, true);
    Serial.printf("Voice alert event=%lu playback=%s\n", (unsigned long)event.id, ok ? "OK" : "failed");
    // The codec and speaker amplifier draw far more current than the idle BLE
    // display. Shut both down after every alert so battery operation remains
    // stable; the next queued alert initializes them again.
    power_down_codec(playback);
    Serial.println("Voice codec powered down");
  }
}
void voice_start() {
  pinMode(Audio_PWR_PIN, OUTPUT);
  gpio_set_level(Audio_PWR_PIN, 1);
  preferences.begin("codex-voice", false);
  lastEvent = preferences.getULong("last", 0);
  volume = preferences.getUChar("volume", 80);
  events = xQueueCreate(1, sizeof(VoiceEvent));
  assert(events);
  xTaskCreate(player, "voice", 6144, nullptr, 2, nullptr);
}
void voice_set_volume(uint8_t value) {
  if (value > 100) return;
  if (volume.exchange(value) != value) preferences.putUChar("volume", value);
  Serial.printf("Voice volume=%u\n", (unsigned)value);
}
void voice_alert(uint32_t event, uint8_t kind) {
  VoiceEvent message = {event, kind};
  if (!event || event == lastEvent) return;
  if (xQueueSend(events, &message, 0) == pdTRUE) {
    playing = true;
    // Persist before playback: retries/reboots must not repeat the same alert.
    lastEvent = event;
    preferences.putULong("last", event);
  }
}
bool voice_busy() { return playing.load(); }
