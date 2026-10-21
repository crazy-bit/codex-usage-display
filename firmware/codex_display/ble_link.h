#pragma once
#include <Arduino.h>
struct BleFrame { char text[1024]; };
void ble_start();
bool ble_receive(BleFrame *frame);
bool ble_connected();
void ble_ack(int sequence, bool ok);
