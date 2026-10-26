#pragma once
#include <stdint.h>
void voice_start();
void voice_alert(uint32_t event, uint8_t kind = 0);
void voice_set_volume(uint8_t volume);
bool voice_busy();
