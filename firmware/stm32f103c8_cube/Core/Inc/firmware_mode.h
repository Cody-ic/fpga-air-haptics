#ifndef F103_FIRMWARE_MODE_H
#define F103_FIRMWARE_MODE_H

#define F103_MODE_BUSINESS 0
#define F103_MODE_CHANNEL_TEST 1
#define F103_MODE_PIN_TEST 2

/* =====================================================================
 * 【手动配置区】只改下面的值，保存后点同一个 haptics_f103c8 Run 即可。
 * 所有模式共用 main.c，不需要换源文件、ELF 或烧录配置。
 * ===================================================================== */

/* 模式：0=业务图形，1=逐通道测试，2=固定通道测试（当前默认）。 */
#ifndef F103_FIRMWARE_MODE
#define F103_FIRMWARE_MODE F103_MODE_PIN_TEST
#endif

/* >>> 换测试引脚，只改下一行的 11 <<<
 * 0=PB0，1=PB1，2=PA8，3..15=PB3..PB15（注意：没有 PB2）。
 * 11=PB11 -> CN1-18 -> PCB CH15；10=PB10 -> CN1-17 -> PCB CH14。
 * 此参数仅在固定通道模式（2）生效。 */
#ifndef F103_PIN_TEST_CHANNEL
#define F103_PIN_TEST_CHANNEL 11
#endif

/* 输出时长：0u=一直输出；如 2000u=输出 2 秒。启动前均等待 3 秒。
 * STOP、故障或断电仍会停止输出。仅在固定通道模式（2）生效。 */
#ifndef F103_PIN_TEST_ON_MS
#define F103_PIN_TEST_ON_MS 0u
#endif

/* ===================== 手动配置区结束 ===================== */
/* CLI/native checks may explicitly override the above values with -D. */

#if F103_FIRMWARE_MODE < 0 || F103_FIRMWARE_MODE > 2
#error "F103_FIRMWARE_MODE must be 0 (business), 1 (sequential) or 2 (fixed pin)."
#endif
#if defined(F103_CHANNEL_TEST) || defined(F103_PIN_TEST)
#error "Select F103_FIRMWARE_MODE only; F103_CHANNEL_TEST and F103_PIN_TEST are derived."
#endif

#define F103_CHANNEL_TEST (F103_FIRMWARE_MODE != 0)
#define F103_PIN_TEST (F103_FIRMWARE_MODE == 2)

#endif
