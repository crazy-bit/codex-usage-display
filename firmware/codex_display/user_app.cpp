#include <Arduino.h>
#include "lvgl.h"
#include "user_config.h"
#include "user_app.h"
#include "src/power/board_power_bsp.h"
#include "ble_link.h"
#include "voice_alert.h"
#include "cJSON.h"
#include "esp_sleep.h"
#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include <time.h>
#include <math.h>

epaper_driver_display *driver = nullptr;
static board_power_bsp_t *power = nullptr;
static lv_obj_t *percentLabel, *resetLabel, *statusLabel, *forecastLabel, *creditCountLabel, *creditExpiryLabel, *forecastTitleLabel, *forecastNoteLabel;
static uint32_t syncIntervalMs = 60000;
static double forecastProbability = -1;
static uint32_t forecastReceived = 0, forecastTtl = 0;
struct Window { double remaining; int minutes; double reset; };
static Window windows[2];
static int windowCount = 0, currentWindow = 0;
static uint32_t lastReceived = 0, lastSwitch = 0;
static char lastStatus[40] = "";
static constexpr uint32_t SLEEP_SECONDS = 300;
static constexpr uint32_t AWAKE_TIMEOUT_MS = 45000;
static constexpr uint32_t FLUSH_GRACE_MS = 5000;
RTC_DATA_ATTR static uint32_t cachedMagic = 0;
RTC_DATA_ATTR static uint32_t cachedAgeSeconds = 0;
RTC_DATA_ATTR static uint8_t missedCycles = 0;
RTC_DATA_ATTR static char cachedFrame[1024] = {};
static constexpr uint32_t CACHE_MAGIC = 0x43445831;
static uint32_t awakeAt = 0, sleepRequestedAt = 0;
static bool syncedThisWake = false;

static void enter_sleep() {
  if (!syncedThisWake && missedCycles < 255) ++missedCycles;
  if (cachedMagic == CACHE_MAGIC) cachedAgeSeconds += SLEEP_SECONDS;
  Serial.printf("Deep sleep %lus; missed=%u\n", (unsigned long)SLEEP_SECONDS, (unsigned)missedCycles);
  power->POWEER_EPD_OFF();
  power->POWEER_Audio_OFF();
  gpio_set_level(VBAT_PWR_PIN, 1);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
  esp_sleep_enable_timer_wakeup((uint64_t)SLEEP_SECONDS * 1000000ULL);
  rtc_gpio_hold_en(VBAT_PWR_PIN);
  Serial.flush();
  delay(50);
  esp_deep_sleep_start();
}

void user_app_init(void) {
  // Preserve the battery latch across deep sleep, then hand it back to the
  // digital GPIO driver without allowing a low pulse.
  gpio_set_level(VBAT_PWR_PIN, 1);
  gpio_set_direction(VBAT_PWR_PIN, GPIO_MODE_OUTPUT);
  gpio_hold_dis(VBAT_PWR_PIN);
  gpio_set_level(VBAT_PWR_PIN, 1);
  // Initialize GPIO after the Arduino runtime has started.
  power = new board_power_bsp_t(EPD_PWR_PIN, Audio_PWR_PIN, VBAT_PWR_PIN);
  power->VBAT_POWER_ON();
  power->POWEER_Audio_OFF();
  power->POWEER_EPD_ON();
  delay(20);
  custom_lcd_spi_t config = {};
  config.cs = EPD_CS_PIN;
  config.dc = EPD_DC_PIN;
  config.rst = EPD_RST_PIN;
  config.busy = EPD_BUSY_PIN;
  config.mosi = EPD_MOSI_PIN;
  config.scl = EPD_SCK_PIN;
  config.spi_host = EPD_SPI_NUM;
  config.buffer_len = EPD_WIDTH * EPD_HEIGHT / 8;
  driver = new epaper_driver_display(EPD_WIDTH, EPD_HEIGHT, config);
  driver->EPD_Init();
  if (esp_sleep_get_wakeup_cause() != ESP_SLEEP_WAKEUP_TIMER || cachedMagic != CACHE_MAGIC) {
    driver->EPD_Clear();
    driver->EPD_DisplayPartBaseImage();
  }
  driver->EPD_Init_Partial();
  awakeAt = millis();
}

static lv_obj_t *label(lv_obj_t *screen, const char *text, int y) {
  lv_obj_t *item = lv_label_create(screen);
  lv_label_set_text(item, text);
  lv_obj_set_style_text_color(item, lv_color_black(), 0);
  lv_obj_align(item, LV_ALIGN_TOP_MID, 0, y);
  return item;
}

static bool number(cJSON *item) { return cJSON_IsNumber(item) && isfinite(item->valuedouble); }

static void set_text(lv_obj_t *item, const char *text) {
  if (strcmp(lv_label_get_text(item), text) != 0) lv_label_set_text(item, text);
}

static void render_window() {
  Window &w = windows[currentWindow];
  char text[64];
  if (w.remaining < 0) snprintf(text, sizeof(text), "--%%");
  else snprintf(text, sizeof(text), "%.0f%%", w.remaining);
  set_text(percentLabel, text);
  if (w.reset > 0) {
    time_t resetTime = (time_t)w.reset;
    struct tm local;
    localtime_r(&resetTime, &local);
    strftime(text, sizeof(text), "%m/%d  %H:%M", &local);
  } else snprintf(text, sizeof(text), "--/--  --:--");
  set_text(resetLabel, text);
}

// Runs under the LVGL mutex. BLE callbacks only enqueue complete frames.
static void update(lv_timer_t *) {
  BleFrame frame;
  if (ble_receive(&frame)) {
    cJSON *root = cJSON_Parse(frame.text);
    cJSON *seq = cJSON_GetObjectItemCaseSensitive(root, "s");
    cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "v");
    cJSON *at = cJSON_GetObjectItemCaseSensitive(root, "at");
    cJSON *items = cJSON_GetObjectItemCaseSensitive(root, "w");
    bool valid = number(seq) && seq->valuedouble >= 0 && seq->valuedouble <= 999999 && floor(seq->valuedouble) == seq->valuedouble
      && number(version) && version->valuedouble == 1 && number(at) && at->valuedouble > 0
      && cJSON_IsArray(items) && cJSON_GetArraySize(items) >= 1 && cJSON_GetArraySize(items) <= 2;
    Window next[2];
    int count = cJSON_GetArraySize(items);
    for (int i = 0; valid && i < count; ++i) {
      auto item = cJSON_GetArrayItem(items, i);
      auto r = cJSON_GetObjectItemCaseSensitive(item, "r");
      auto m = cJSON_GetObjectItemCaseSensitive(item, "m");
      auto t = cJSON_GetObjectItemCaseSensitive(item, "t");
      valid = (cJSON_IsNull(r) || (number(r) && r->valuedouble >= 0 && r->valuedouble <= 100))
        && number(m) && m->valuedouble > 0 && m->valuedouble <= 525600
        && (cJSON_IsNull(t) || (number(t) && t->valuedouble > 0 && t->valuedouble < 4102444800.0));
      if (valid) next[i] = {cJSON_IsNull(r) ? -1 : r->valuedouble, m->valueint, cJSON_IsNull(t) ? 0 : t->valuedouble};
    }
    cJSON *volume = cJSON_GetObjectItemCaseSensitive(root, "vol");
    if (volume) valid = valid && number(volume) && volume->valuedouble >= 0
      && volume->valuedouble <= 100 && floor(volume->valuedouble) == volume->valuedouble;
    cJSON *forecast = cJSON_GetObjectItemCaseSensitive(root, "f");
    double probability = -1;
    uint32_t ttl = 0;
    if (forecast && !cJSON_IsNull(forecast)) {
      auto p = cJSON_GetObjectItemCaseSensitive(forecast, "p");
      auto t = cJSON_GetObjectItemCaseSensitive(forecast, "t");
      auto age = cJSON_GetObjectItemCaseSensitive(forecast, "age");
      valid = valid && number(p) && p->valuedouble >= 0 && p->valuedouble <= 100
        && number(t) && t->valuedouble > 0 && number(age) && age->valuedouble >= 0;
      double effectiveAge = age->valuedouble + (frame.live ? 0 : cachedAgeSeconds);
      if (valid && effectiveAge <= 1800) {
        probability = p->valuedouble;
        ttl = (uint32_t)(1800 - effectiveAge) * 1000;
      }
    }
    auto interval = cJSON_GetObjectItemCaseSensitive(root, "i");
    if (interval) valid = valid && number(interval) && interval->valuedouble >= 10
      && interval->valuedouble <= 600 && floor(interval->valuedouble) == interval->valuedouble;
    auto credits = cJSON_GetObjectItemCaseSensitive(root, "rc");
    auto creditCount = cJSON_GetObjectItemCaseSensitive(credits, "n");
    auto creditExpiry = cJSON_GetObjectItemCaseSensitive(credits, "t");
    if (credits && !cJSON_IsNull(credits)) valid = valid && number(creditCount)
      && creditCount->valuedouble >= 0 && creditCount->valuedouble <= 9999
      && floor(creditCount->valuedouble) == creditCount->valuedouble
      && (cJSON_IsNull(creditExpiry) || (number(creditExpiry) && creditExpiry->valuedouble > 0 && creditExpiry->valuedouble < 4102444800.0));
    if (valid) {
      if (frame.live) {
        strlcpy(cachedFrame, frame.text, sizeof(cachedFrame));
        cachedMagic = CACHE_MAGIC;
        cachedAgeSeconds = 0;
        missedCycles = 0;
        syncedThisWake = true;
        sleepRequestedAt = millis();
      }
      char text[64];
      if (number(creditCount)) snprintf(text, sizeof(text), "FULL RESET: %d", creditCount->valueint);
      else snprintf(text, sizeof(text), "FULL RESET: --");
      set_text(creditCountLabel, text);
      if (number(creditExpiry)) {
        time_t when = (time_t)creditExpiry->valuedouble; struct tm local;
        localtime_r(&when, &local);
        char date[32]; strftime(date, sizeof(date), "%m/%d %H:%M", &local);
        snprintf(text, sizeof(text), "Expires %s (UTC+8)", date);
      } else snprintf(text, sizeof(text), number(creditCount) && creditCount->valueint == 0 ? "No resets available" : "Expiry unavailable");
      set_text(creditExpiryLabel, text);
      auto forecastKind = cJSON_GetObjectItemCaseSensitive(forecast, "k");
      bool tiboSignal = number(forecastKind) && forecastKind->valueint == 1;
      set_text(forecastTitleLabel, tiboSignal ? "TIBO SIGNAL" : "48H RESET");
      set_text(forecastNoteLabel, tiboSignal ? "RESET PLANNED" : "estimate");
      syncIntervalMs = interval ? interval->valueint * 1000 : 60000;
      forecastProbability = probability; forecastReceived = millis(); forecastTtl = ttl;
      if (volume) voice_set_volume((uint8_t)volume->valueint);
      cJSON *alert = cJSON_GetObjectItemCaseSensitive(root, "a");
      if (number(alert) && alert->valuedouble > 0 && alert->valuedouble < 4102444800.0) {
        auto kind = cJSON_GetObjectItemCaseSensitive(root, "ak");
        voice_alert((uint32_t)alert->valuedouble, number(kind) && kind->valueint == 1 ? 1 : 0);
      }
      memcpy(windows, next, sizeof(Window) * count);
      windowCount = count; currentWindow = 0;
      lastReceived = lastSwitch = millis();
      render_window();
      Serial.printf("BLE accepted seq=%d remaining=%.1f\n", seq->valueint, windows[0].remaining);
    }
    if (frame.live && number(seq) && seq->valuedouble >= 0 && seq->valuedouble <= 999999) ble_ack(seq->valueint, valid);
    cJSON_Delete(root);
  }
  if (windowCount > 1 && millis() - lastSwitch >= 15000) {
    currentWindow = (currentWindow + 1) % windowCount;
    lastSwitch = millis(); render_window();
  }
  char forecastText[16];
  if (forecastProbability >= 0 && millis() - forecastReceived < forecastTtl
      && millis() - lastReceived < 3 * syncIntervalMs)
    snprintf(forecastText, sizeof(forecastText), "%.0f%%", forecastProbability);
  else snprintf(forecastText, sizeof(forecastText), "--%%");
  set_text(forecastLabel, forecastText);
  char status[40];
  if (!syncedThisWake && missedCycles >= 3) snprintf(status, sizeof(status), "OFFLINE");
  else if (millis() - lastReceived >= 3 * syncIntervalMs) snprintf(status, sizeof(status), "OFFLINE");
  else if (!windowCount) snprintf(status, sizeof(status), "WAITING");
  else if (windowCount > 1) snprintf(status, sizeof(status), "%d/%d", currentWindow + 1, windowCount);
  else status[0] = 0;
  if (strcmp(status, lastStatus) != 0) {
    lv_label_set_text(statusLabel, status);
    strcpy(lastStatus, status);
    Serial.printf("Display status=%s elapsed=%lu interval=%lu\n", status, (unsigned long)(millis()-lastReceived), (unsigned long)syncIntervalMs);
  }
  uint32_t now = millis();
  if (sleepRequestedAt && now - sleepRequestedAt >= FLUSH_GRACE_MS && !voice_busy()) enter_sleep();
  if (now - awakeAt >= AWAKE_TIMEOUT_MS && !voice_busy()) enter_sleep();
}

void user_ui_init(void) {
  lv_obj_t *screen = lv_scr_act();
  lv_obj_set_style_bg_color(screen, lv_color_white(), 0);
  // 200 x 200: app header, two metrics, personal usage-limit reset time.
  auto title = label(screen, "Codex", 4);
  lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
  auto divider = [](int x, int y, int width, int height) {
    lv_obj_t *line = lv_obj_create(lv_scr_act());
    lv_obj_remove_style_all(line);
    lv_obj_set_pos(line, x, y); lv_obj_set_size(line, width, height);
    lv_obj_set_style_bg_color(line, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(line, LV_OPA_COVER, 0);
  };
  divider(8, 30, 184, 1);
  divider(120, 38, 1, 70);
  divider(8, 117, 184, 1);
  auto remaining = label(screen, "REMAINING", 40);
  lv_obj_align(remaining, LV_ALIGN_TOP_MID, -40, 40);
  percentLabel = label(screen, "--%", 63);
  lv_obj_set_style_text_font(percentLabel, &lv_font_montserrat_36, 0);
  lv_obj_align(percentLabel, LV_ALIGN_TOP_MID, -40, 63);
  auto forecastTitle = label(screen, "48H RESET", 40);
  forecastTitleLabel = forecastTitle;
  lv_obj_set_style_text_font(forecastTitle, &lv_font_montserrat_10, 0);
  lv_obj_align(forecastTitle, LV_ALIGN_TOP_MID, 58, 40);
  forecastLabel = label(screen, "--%", 65);
  lv_obj_set_style_text_font(forecastLabel, &lv_font_montserrat_28, 0);
  lv_obj_align(forecastLabel, LV_ALIGN_TOP_MID, 58, 65);
  auto estimate = label(screen, "estimate", 99);
  forecastNoteLabel = estimate;
  lv_obj_set_style_text_font(estimate, &lv_font_montserrat_8, 0);
  lv_obj_align(estimate, LV_ALIGN_TOP_MID, 58, 99);
  label(screen, "NEXT RESET (UTC+8)", 124);
  resetLabel = label(screen, "--/--  --:--", 144);
  lv_obj_set_style_text_font(resetLabel, &lv_font_montserrat_20, 0);
  statusLabel = label(screen, "WAITING", 9);
  lv_obj_align(statusLabel, LV_ALIGN_TOP_RIGHT, -8, 9);
  lv_obj_set_style_text_font(statusLabel, &lv_font_montserrat_10, 0);
  divider(8, 167, 184, 1);
  creditCountLabel = label(screen, "FULL RESET: --", 171);
  creditExpiryLabel = label(screen, "Expiry unavailable", 188);
  lv_obj_set_style_text_font(creditExpiryLabel, &lv_font_montserrat_10, 0);
  setenv("TZ", "CST-8", 1); tzset();
  voice_start();
  ble_start();
  if (cachedMagic == CACHE_MAGIC) {
    ble_restore(cachedFrame);
    update(nullptr);
  }
  lv_timer_create(update, 500, nullptr);
  Serial.println("Codex V2 BLE display ready.");
}
