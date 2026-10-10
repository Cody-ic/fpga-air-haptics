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

#if F103_GROUP_TEST && (F103_GROUP_TEST_FIRST_CHANNEL < 0 || F103_GROUP_TEST_FIRST_CHANNEL >= HAP_CHANNELS || \
    F103_GROUP_TEST_LAST_CHANNEL < F103_GROUP_TEST_FIRST_CHANNEL || F103_GROUP_TEST_LAST_CHANNEL >= HAP_CHANNELS)
#error "GroupTest range must satisfy 0 <= FIRST_CHANNEL <= LAST_CHANNEL <= 15 (inclusive)."
#endif
#if F103_GROUP_TEST && defined(F103_GROUP_TEST_MASK)
#error "GroupTest no longer takes a manual mask; set FIRST_CHANNEL and LAST_CHANNEL in firmware_mode.h."
#endif
#if F103_GROUP_TEST && (F103_GROUP_TEST_ON_MS < 0 || F103_GROUP_TEST_ON_MS > 0x7fffffff)
#error "GroupTest duration must be 0 (continuous) or positive and less than 2^31 milliseconds."
#endif

#endif
