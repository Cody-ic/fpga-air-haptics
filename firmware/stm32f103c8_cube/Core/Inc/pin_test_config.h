#ifndef F103_PIN_TEST_CONFIG_H
#define F103_PIN_TEST_CONFIG_H

/* 参数已集中到 firmware_mode.h 顶部的【手动配置区】。
 * 此文件只校验参数，不要在此新增另一份通道/时长定义。 */
#include "firmware_mode.h"

#if F103_PIN_TEST && (F103_PIN_TEST_CHANNEL < 0 || F103_PIN_TEST_CHANNEL >= HAP_CHANNELS)
#error "PinTest channel must be 0..15; channel 2 uses PA8, not PB2."
#endif
#if F103_PIN_TEST && (F103_PIN_TEST_ON_MS < 0 || F103_PIN_TEST_ON_MS > 0x7fffffff)
#error "PinTest duration must be 0 (continuous) or positive and less than 2^31 milliseconds."
#endif

#endif
