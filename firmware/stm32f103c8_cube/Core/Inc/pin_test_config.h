#ifndef F103_PIN_TEST_CONFIG_H
#define F103_PIN_TEST_CONFIG_H

/* PinTest is an additional diagnostic profile; ChannelTest stays sequential. */
#ifndef F103_PIN_TEST
#define F103_PIN_TEST 0
#endif

/* Logical channel: 11 = PB11 -> CN1-18 -> PCB CH15.
 * Channels 0/1/3..15 map to PB0/PB1/PB3..PB15; channel 2 maps to PA8. */
#ifndef F103_PIN_TEST_CHANNEL
#define F103_PIN_TEST_CHANNEL 11
#endif

/* 0 = continuous output until STOP/fault; positive values limit the burst in ms.
 * Both start after the existing 3-second startup delay. */
#ifndef F103_PIN_TEST_ON_MS
#define F103_PIN_TEST_ON_MS 0u
#endif

#if F103_PIN_TEST && !F103_CHANNEL_TEST
#error "PinTest requires the diagnostic backend (F103_CHANNEL_TEST=1)."
#endif
#if F103_PIN_TEST && (F103_PIN_TEST_CHANNEL < 0 || F103_PIN_TEST_CHANNEL >= HAP_CHANNELS)
#error "PinTest channel must be 0..15; channel 2 uses PA8, not PB2."
#endif
#if F103_PIN_TEST && (F103_PIN_TEST_ON_MS < 0 || F103_PIN_TEST_ON_MS > 0x7fffffff)
#error "PinTest duration must be 0 (continuous) or positive and less than 2^31 milliseconds."
#endif

#endif
