#pragma once
#include "config.h"
#if MG24_USE_BLUETOOTH
#include <Arduino.h>
#include "sl_bt_api.h"
#include "FreeRTOS.h"
#include "queue.h"

namespace mg24 {
struct BleEvent {
    enum Kind : uint8_t { Reset, Ready, Data } kind;
    uint16_t size;
    uint8_t bytes[244];
};
void ble_begin();
bool ble_event(BleEvent &event);
bool ble_failed();
bool ble_send(const char *data, unsigned size, unsigned &sent);
void ble_close();
}
#endif
