#ifndef F103_FIRMWARE_MODE_H
#define F103_FIRMWARE_MODE_H

#define F103_MODE_BUSINESS 0
#define F103_MODE_CHANNEL_TEST 1
#define F103_MODE_PIN_TEST 2
#define F103_MODE_GROUP_TEST 3

/* =====================================================================
 * 【手动配置区】只改下面的值，保存后点同一个 haptics_f103c8 Run 即可。
 * 所有模式共用 main.c，不需要换源文件、ELF 或烧录配置。
 * ===================================================================== */

/* 模式：0=业务图形，1=逐通道，2=固定单路，3=固定多路。 */
#ifndef F103_FIRMWARE_MODE
#define F103_FIRMWARE_MODE F103_MODE_BUSINESS
#endif

/* >>> 业务：固定中心焦点。坐标单位 um；Z 是距阵列发射面的距离 <<<
 * POINT=固定焦点；也可改为 CIRCLE 等已有基础图形。
 * 默认无调制、满等级，用于先核验各路相位及聚焦；并非触觉效果证明。 */
#ifndef F103_BUSINESS_SHAPE
#define F103_BUSINESS_SHAPE POINT
#endif
#ifndef F103_BUSINESS_X_UM
#define F103_BUSINESS_X_UM 0
#endif
#ifndef F103_BUSINESS_Y_UM
#define F103_BUSINESS_Y_UM 0
#endif
#ifndef F103_BUSINESS_Z_UM
#define F103_BUSINESS_Z_UM 150000
#endif
#ifndef F103_BUSINESS_MOD_HZ
#define F103_BUSINESS_MOD_HZ 0
#endif
#ifndef F103_BUSINESS_LEVEL
#define F103_BUSINESS_LEVEL 100
#endif

/* 启动等待（所有模式）：毫秒。业务自动运行时长：0u=持续；
 * 10000u=运行 10 秒。STOP、故障及看门狗保护仍有效。
 * 运行时长仅约束上电自动启动，串口/按键显式启动遵从原控制流程。 */
#ifndef F103_AUTOSTART_MS
#define F103_AUTOSTART_MS 3000u
#endif
#ifndef F103_AUTO_RUN_LIMIT_MS
#define F103_AUTO_RUN_LIMIT_MS 0u
#endif

/* >>> 单路测试时，只改下一行的通道号 <<<
 * 0=PB0，1=PB1，2=PA8，3..15=PB3..PB15（注意：没有 PB2）。
 * 11=PB11 -> CN1-18 -> PCB CH15；10=PB10 -> CN1-17 -> PCB CH14。
 * 此参数仅在固定通道模式（2）生效。 */
#ifndef F103_PIN_TEST_CHANNEL
#define F103_PIN_TEST_CHANNEL 11
#endif

/* 输出时长：0u=一直输出；如 2000u=输出 2 秒。默认启动前等待 3 秒。
 * STOP、故障或断电仍会停止输出。仅在固定通道模式（2）生效。 */
#ifndef F103_PIN_TEST_ON_MS
#define F103_PIN_TEST_ON_MS 0u
#endif

/* >>> 多路同时测试：只改下面的起始、结束通道（两端都包含） <<<
 * 通道范围 0..15，起始必须 <= 结束。例如 8..13 共输出 6 路：
 * PB8、PB9、PB10、PB11、PB12、PB13；不是只选首尾两路。
 * 各选中通道同时输出同相位 40 kHz、50% 方波，其余通道保持低。
 * 此参数仅在多路模式（3）生效；通道 2 仍对应 PA8，不是 PB2。 */
#ifndef F103_GROUP_TEST_FIRST_CHANNEL
#define F103_GROUP_TEST_FIRST_CHANNEL 8
#endif
#ifndef F103_GROUP_TEST_LAST_CHANNEL
#define F103_GROUP_TEST_LAST_CHANNEL 13
#endif

/* 多路输出时长：0u=持续；正数=毫秒。默认启动前等待 3 秒。 */
#ifndef F103_GROUP_TEST_ON_MS
#define F103_GROUP_TEST_ON_MS 0u
#endif

/* ===================== 手动配置区结束 ===================== */
/* CLI/native checks may explicitly override the above values with -D. */

#if F103_FIRMWARE_MODE < 0 || F103_FIRMWARE_MODE > 3
#error "F103_FIRMWARE_MODE must be 0 (business), 1 (sequential), 2 (fixed pin) or 3 (group)."
#endif
#if defined(F103_CHANNEL_TEST) || defined(F103_PIN_TEST) || defined(F103_GROUP_TEST) || defined(F103_FIXED_TEST)
#error "Select F103_FIRMWARE_MODE only; F103_CHANNEL_TEST and F103_PIN_TEST are derived."
#endif

#define F103_CHANNEL_TEST (F103_FIRMWARE_MODE != 0)
#define F103_PIN_TEST (F103_FIRMWARE_MODE == 2)
#define F103_GROUP_TEST (F103_FIRMWARE_MODE == 3)
#define F103_FIXED_TEST (F103_PIN_TEST || F103_GROUP_TEST)

#if F103_AUTOSTART_MS < 0 || F103_AUTOSTART_MS > 0x7fffffff || \
    F103_AUTO_RUN_LIMIT_MS < 0 || F103_AUTO_RUN_LIMIT_MS > 0x7fffffff
#error "Startup delay and business run limit must be 0..2147483647 milliseconds."
#endif
#if !F103_CHANNEL_TEST
#if F103_BUSINESS_X_UM < -100000 || F103_BUSINESS_X_UM > 100000 || \
    F103_BUSINESS_Y_UM < -100000 || F103_BUSINESS_Y_UM > 100000 || \
    F103_BUSINESS_Z_UM < 20000 || F103_BUSINESS_Z_UM > 300000
#error "Business focus coordinates exceed the supported workspace."
#endif
#if F103_BUSINESS_LEVEL < 0 || F103_BUSINESS_LEVEL > 100 || \
    F103_BUSINESS_MOD_HZ < 0 || F103_BUSINESS_MOD_HZ > 1000
#error "Business level must be 0..100 and modulation must be 0..1000 Hz."
#endif
#endif

/* 自动生成内部掩码；先验证范围，避免非法参数造成移位溢出。 */
#if F103_GROUP_TEST_FIRST_CHANNEL >= 0 && F103_GROUP_TEST_FIRST_CHANNEL <= 15 && \
    F103_GROUP_TEST_LAST_CHANNEL >= F103_GROUP_TEST_FIRST_CHANNEL && F103_GROUP_TEST_LAST_CHANNEL <= 15
#define F103_GROUP_TEST_EFFECTIVE_MASK \
    ((0xffffu >> (15u - F103_GROUP_TEST_LAST_CHANNEL)) & (0xffffu << F103_GROUP_TEST_FIRST_CHANNEL))
#else
#define F103_GROUP_TEST_EFFECTIVE_MASK 0u
#endif

#if F103_GROUP_TEST
#define F103_FIXED_TEST_MASK F103_GROUP_TEST_EFFECTIVE_MASK
#define F103_FIXED_TEST_ON_MS F103_GROUP_TEST_ON_MS
#define F103_FIXED_TEST_WAIT_REASON "GROUP_TEST_WAIT"
#define F103_FIXED_TEST_ON_REASON "GROUP_TEST_ON"
#define F103_FIXED_TEST_DONE_REASON "GROUP_TEST_DONE"
#else
#define F103_FIXED_TEST_MASK (1u << F103_PIN_TEST_CHANNEL)
#define F103_FIXED_TEST_ON_MS F103_PIN_TEST_ON_MS
#define F103_FIXED_TEST_WAIT_REASON "PIN_TEST_WAIT"
#define F103_FIXED_TEST_ON_REASON "PIN_TEST_ON"
#define F103_FIXED_TEST_DONE_REASON "PIN_TEST_DONE"
#endif

#endif
