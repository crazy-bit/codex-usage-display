#pragma once
#include <Arduino.h>
struct BleFrame { bool live; char text[1024]; };
void ble_start();
void ble_restore(const char *text);
bool ble_receive(BleFrame *frame);
bool ble_connected();
void ble_ack(int sequence, bool ok);
