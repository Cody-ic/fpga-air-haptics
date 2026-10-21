#pragma once

// 唯一的手动通信模式开关；修改后重新编译并烧录。
#ifndef MG24_USE_BLUETOOTH
#define MG24_USE_BLUETOOTH 0
#endif
#if MG24_USE_BLUETOOTH != 0 && MG24_USE_BLUETOOTH != 1
#error "MG24_USE_BLUETOOTH must be 0 (USB serial) or 1 (BLE)."
#endif

namespace mg24 {
constexpr unsigned SAMPLE_COUNT = 200;
constexpr unsigned TARGET_SAMPLE_HZ = 400000;
constexpr unsigned SIGNAL_HZ = 40000;
constexpr unsigned ADC_MAX = 4095;
constexpr unsigned VREF_MV = 3300; // 名义值；没有做接收链或参考电压标定。
constexpr unsigned CAPTURE_TIMEOUT_US = 10000;
constexpr unsigned INPUT_CAPACITY = 256;
constexpr unsigned OUTPUT_CAPACITY = 2048;
constexpr unsigned OUTPUT_SLOTS = 4;
constexpr unsigned PARTIAL_TIMEOUT_US = 2000000;
}
