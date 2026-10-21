#include <Arduino.h>
#include "lvgl.h"
#include "user_config.h"
#include "user_app.h"
#include "src/power/board_power_bsp.h"
#include "ble_link.h"
#include "voice_alert.h"
#include "cJSON.h"
#include <time.h>
#include <math.h>

epaper_driver_display *driver = nullptr;
static board_power_bsp_t *power = nullptr;
static lv_obj_t *percentLabel, *resetLabel, *statusLabel;
struct Window { double remaining; int minutes; double reset; };
static Window windows[2];
static int windowCount = 0, currentWindow = 0;
static uint32_t lastReceived = 0, lastSwitch = 0;
static char lastStatus[40] = "";

void user_app_init(void) {
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
  driver->EPD_Clear();
  driver->EPD_DisplayPartBaseImage();
  driver->EPD_Init_Partial();
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
    if (valid) {
      if (volume) voice_set_volume((uint8_t)volume->valueint);
      cJSON *alert = cJSON_GetObjectItemCaseSensitive(root, "a");
      if (number(alert) && alert->valuedouble > 0 && alert->valuedouble < 4102444800.0) voice_alert((uint32_t)alert->valuedouble);
      memcpy(windows, next, sizeof(Window) * count);
      windowCount = count; currentWindow = 0;
      lastReceived = lastSwitch = millis();
      render_window();
      Serial.printf("BLE accepted seq=%d remaining=%.1f\n", seq->valueint, windows[0].remaining);
    }
    if (number(seq) && seq->valuedouble >= 0 && seq->valuedouble <= 999999) ble_ack(seq->valueint, valid);
    cJSON_Delete(root);
  }
  if (windowCount > 1 && millis() - lastSwitch >= 15000) {
    currentWindow = (currentWindow + 1) % windowCount;
    lastSwitch = millis(); render_window();
  }
  char status[40];
  if (!windowCount) snprintf(status, sizeof(status), "Waiting for data");
  else if (millis() - lastReceived >= 300000) snprintf(status, sizeof(status), "Data expired");
  else if (windowCount > 1) snprintf(status, sizeof(status), "%d/%d", currentWindow + 1, windowCount);
  else status[0] = 0;
  if (strcmp(status, lastStatus) != 0) {
    lv_label_set_text(statusLabel, status);
    strcpy(lastStatus, status);
  }
}

void user_ui_init(void) {
  lv_obj_t *screen = lv_scr_act();
  lv_obj_set_style_bg_color(screen, lv_color_white(), 0);
  label(screen, "REMAINING", 24);
  lv_obj_t *percent = lv_label_create(screen);
  percentLabel = percent;
  lv_label_set_text(percent, "--%");
  lv_obj_set_style_text_color(percent, lv_color_black(), 0);
  lv_obj_set_style_text_font(percent, &lv_font_montserrat_48, 0);
  lv_obj_align(percent, LV_ALIGN_TOP_MID, 0, 49);

  label(screen, "RESETS AT", 119);
  resetLabel = label(screen, "--/--  --:--", 143);
  lv_obj_set_style_text_font(resetLabel, &lv_font_montserrat_20, 0);
  statusLabel = label(screen, "Waiting for data", 180);
  setenv("TZ", "CST-8", 1); tzset();
  voice_start();
  ble_start();
  lv_timer_create(update, 500, nullptr);
  Serial.println("Codex V2 BLE display ready.");
}
