#include "ble_link.h"
#include <atomic>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "user_config.h"

static const char *SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
static const char *RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";
static const char *TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";
static QueueHandle_t incoming;
static BLECharacteristic *tx;
static BLEServer *bleServer;
static std::atomic<uint32_t> activityAt{0};
static std::atomic<bool> connected{false};
static BleFrame partial = {};
static size_t length = 0;
static bool discard = false;
static uint32_t lastChunk = 0;
static uint32_t frameSequence = UINT32_MAX;
static uint16_t expectedOffset = 0;

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *) override { Serial.println("BLE connected"); activityAt = millis(); connected = true; length = 0; discard = false; frameSequence = UINT32_MAX; expectedOffset = 0; }
  void onDisconnect(BLEServer *) override {
    connected = false;
    length = 0;
    discard = false;
    Serial.println("BLE disconnected; advertising recovery scheduled");
  }
};
class RxCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *characteristic) override {
    String data = characteristic->getValue();
    if (data.length() <= 6) return;
    uint32_t sequence = 0;
    for (int i=0; i<4; ++i) sequence |= (uint32_t)(uint8_t)data[i] << (8*i);
    uint16_t offset = (uint8_t)data[4] | ((uint16_t)(uint8_t)data[5] << 8);
    if (sequence != frameSequence || millis() - lastChunk > 5000) {
      if (offset != 0) return;
      frameSequence = sequence; expectedOffset = 0; length = 0; discard = false;
      partial.live = true;
    }
    if (offset != expectedOffset) return; // Duplicate or out-of-order packet.
    expectedOffset += data.length() - 6;
    lastChunk = millis();
    activityAt = lastChunk;
    for (size_t i = 6; i < data.length(); ++i) {
      char c = data[i];
      if (c == '\n') {
        if (!discard && length) {
          partial.text[length] = 0;
          xQueueOverwrite(incoming, &partial);
        }
        length = 0; discard = false;
      } else if (!discard) {
        if (length < sizeof(partial.text) - 1 && c != 0) partial.text[length++] = c;
        else discard = true;
      }
    }
  }
};
// Keep GAP operations out of the disconnect callback. A periodic refresh also
// recovers an advertising state that the stack reports as started but is silent.
static void maintain_link(void *) {
  uint32_t lastRefresh = millis(), disconnectRequested = 0;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint32_t now = millis();
    if (connected.load()) {
      if (now - activityAt.load() > 240000 && now - disconnectRequested > 10000) {
        Serial.println("BLE idle connection; requesting disconnect");
        bleServer->disconnect(bleServer->getConnId());
        disconnectRequested = now;
      }
      continue;
    }
    if (bleServer->getConnectedCount() != 0) continue;
    auto advertising = BLEDevice::getAdvertising();
    if (!advertising->isAdvertising() || now - lastRefresh >= 30000) {
      advertising->stop();
      vTaskDelay(pdMS_TO_TICKS(250));
      if (!connected.load() && bleServer->getConnectedCount() == 0) {
        advertising->start();
        Serial.printf("BLE advertising recovered; free heap=%u\n", ESP.getFreeHeap());
      }
      lastRefresh = millis();
    }
  }
}

void ble_start() {
  incoming = xQueueCreate(1, sizeof(BleFrame));
  assert(incoming);
  BLEDevice::init("CodexDisplay");
  BLEServer *server = BLEDevice::createServer();
  bleServer = server;
  server->setCallbacks(new ServerCallbacks());
  BLEService *service = server->createService(SERVICE);
  tx = service->createCharacteristic(TX_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  tx->addDescriptor(new BLE2902());
  auto rx = service->createCharacteristic(RX_UUID, BLECharacteristic::PROPERTY_WRITE);
  rx->setCallbacks(new RxCallbacks());
  service->start();
  auto advertising = BLEDevice::getAdvertising();
  advertising->addServiceUUID(SERVICE);
  advertising->setScanResponse(true);
  BLEDevice::startAdvertising();
  Serial.println("BLE advertising: CodexDisplay");
  xTaskCreate(maintain_link, "ble-recovery", 4096, nullptr, 1, nullptr);
}
bool ble_receive(BleFrame *frame) { return xQueueReceive(incoming, frame, 0) == pdTRUE; }
void ble_restore(const char *text) {
  if (!text || !*text) return;
  BleFrame restored = {};
  restored.live = false;
  strlcpy(restored.text, text, sizeof(restored.text));
  xQueueOverwrite(incoming, &restored);
}
bool ble_connected() { return connected.load(); }
void ble_ack(int sequence, bool ok) {
  char message[32];
  // GPIO4 is connected to the midpoint of the board's 1:1 battery divider.
  // Send a separate compact notification so the desktop can retain the last
  // voltage observed before a battery-side disconnect.
  unsigned batteryMv = analogReadMilliVolts(BAT_ADC_PIN) * 2U;
  Serial.printf("Battery=%umV\n", batteryMv);
  snprintf(message, sizeof(message), "{\"b\":%u}", batteryMv);
  tx->setValue(String(message));
  if (connected) tx->notify();
  vTaskDelay(pdMS_TO_TICKS(20));
  snprintf(message, sizeof(message), "{\"s\":%d,\"ok\":%d}", sequence, ok ? 1 : 0);
  tx->setValue(String(message));
  if (connected) tx->notify();
}
