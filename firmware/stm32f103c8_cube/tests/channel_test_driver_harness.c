/* Actual F103 application transitions and DMA buffers, without board timing. */
#include "main.h"
#include "app_f103.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static GPIO_TypeDef port_a, port_b;
static TIM_TypeDef timer;
static DMA_TypeDef dma;
static DMA_Channel_TypeDef channel_a, channel_b;
static IWDG_TypeDef watchdog;
static RCC_TypeDef reset_clock;
static DWT_Type cycle_counter;
static USART_TypeDef serial_port;
static uint32_t virtual_tick, tick_step_ms;
static char serial_reply[8192];
static size_t serial_size;
static uint32_t interrupt_mask;
static bool inject_prepare_fault;
static void geometry_with_fault(const Config *, uint64_t, Sample *, float *);

#undef GPIOA
#undef GPIOB
#undef TIM1
#undef DMA1
#undef DMA1_Channel2
#undef DMA1_Channel5
#undef IWDG
#undef RCC
#undef DWT
#undef USART2
#define GPIOA (&port_a)
#define GPIOB (&port_b)
#define TIM1 (&timer)
#define DMA1 (&dma)
#define DMA1_Channel2 (&channel_a)
#define DMA1_Channel5 (&channel_b)
#define IWDG (&watchdog)
#define RCC (&reset_clock)
#define DWT (&cycle_counter)
#define USART2 (&serial_port)
#define __get_PRIMASK() interrupt_mask
#define __disable_irq() (interrupt_mask = 1u)
#define __set_PRIMASK(value) (interrupt_mask = (value))
#define __DMB() ((void)0)
#undef NVIC_ClearPendingIRQ
#define NVIC_ClearPendingIRQ(irq) ((void)(irq))

uint32_t SystemCoreClock = 64000000u;
uint32_t HAL_GetTick(void)
{
    uint32_t now = virtual_tick;
    virtual_tick += tick_step_ms;
    return now;
}
ADC_HandleTypeDef hadc1;
HAL_StatusTypeDef HAL_ADC_Stop_DMA(ADC_HandleTypeDef *adc)
{ (void)adc; abort(); }
HAL_StatusTypeDef HAL_ADC_Start_DMA(ADC_HandleTypeDef *adc, uint32_t *data, uint32_t length)
{ (void)adc; (void)data; (void)length; abort(); }
HAL_StatusTypeDef HAL_ADCEx_Calibration_Start(ADC_HandleTypeDef *adc)
{ (void)adc; abort(); }
HAL_StatusTypeDef HAL_FLASH_Unlock(void) { abort(); }
HAL_StatusTypeDef HAL_FLASH_Lock(void) { abort(); }
HAL_StatusTypeDef HAL_FLASH_Program(uint32_t type, uint32_t address, uint64_t data)
{ (void)type; (void)address; (void)data; abort(); }
void HAL_GPIO_WritePin(GPIO_TypeDef *port, uint16_t pins, GPIO_PinState state)
{ (void)port; (void)pins; (void)state; abort(); }
void Error_Handler(void) { abort(); }

#define geometry_sample geometry_with_fault
/* A diagnostic build must not depend on either dynamic-wave function. */
#define f103_wave_render forbidden_dynamic_renderer
#define f103_wave_prepare forbidden_dynamic_prepare
#include "../Core/Src/app_f103.c"
#undef f103_wave_render
#undef f103_wave_prepare
#undef geometry_sample

static void geometry_with_fault(const Config *c, uint64_t us, Sample *sample, float *remaining)
{
    geometry_sample(c, us, sample, remaining);
    if (inject_prepare_fault) {
        inject_prepare_fault = false;
        fail("UART_RX_ERROR"); /* Simulate an IRQ before DMA enable. */
    }
}

static void serial_send(const char *bytes, size_t size)
{
    assert(serial_size + size < sizeof(serial_reply));
    memcpy(serial_reply + serial_size, bytes, size);
    serial_size += size;
    serial_reply[serial_size] = 0;
}

static void fresh(uint32_t flags)
{
    memset(&port_a, 0, sizeof(port_a));
    memset(&port_b, 0, sizeof(port_b));
    memset(&timer, 0, sizeof(timer));
    memset(&dma, 0, sizeof(dma));
    memset(&channel_a, 0, sizeof(channel_a));
    memset(&channel_b, 0, sizeof(channel_b));
    memset(&serial_port, 0, sizeof(serial_port));
    virtual_tick = tick_step_ms = 0;
    uptime_ms = 0;
    last_tick = 0;
    port_a.ODR = 0xa5e7u;
    boot_ok = true;
    pending_fault = NULL;
    SystemCoreClock = 64000000u;
    channel_test_internal = false;
    interrupt_mask = 0;
    inject_prepare_fault = false;
    reset_clock.CSR = flags;
    capture_reset_flags();
    rx_head = rx_tail = 0;
    serial_size = 0;
    hap_init(&device, (Hardware){.start = output_start, .stop = app_f103_shutdown,
             .readback = output_readback, .send = serial_send}, "channel-test-registers");
    startup_schedule(0);
}

static void poll(uint64_t now)
{
    virtual_tick = (uint32_t)now;
    uptime_ms = now;
    last_tick = virtual_tick;
    hap_poll(&device, now, false);
    channel_test_poll(now);
}

static void command(unsigned seq, const char *verb)
{
    char frame[HAP_LINE + 1];
    serial_size = 0;
    int size = snprintf(frame, sizeof(frame), "HAP3 CMD %u %s", seq, verb);
    assert(size > 0 && size < (int)sizeof(frame) - 7);
    uint16_t crc = hap_crc(frame, (size_t)size);
    snprintf(frame + size, sizeof(frame) - (size_t)size, "*%04X\n", crc);
    for (const char *p = frame; *p; ++p) hap_feed(&device, (uint8_t)*p);
}

static void off(void)
{
    assert(!playing && !(timer.CR1 & TIM_CR1_CEN) && !timer.DIER);
    assert(!(channel_a.CCR & DMA_CCR_EN) && !(channel_b.CCR & DMA_CCR_EN));
    assert(port_a.BRR == F103_GPIOA_MASK && port_b.BRR == F103_GPIOB_MASK);
}

#if F103_GROUP_TEST
#define TEST_WAIT_REASON "GROUP_TEST_WAIT"
#define TEST_ON_REASON "GROUP_TEST_ON"
#define TEST_DONE_REASON "GROUP_TEST_DONE"
#elif F103_PIN_TEST
#define TEST_WAIT_REASON "PIN_TEST_WAIT"
#define TEST_ON_REASON "PIN_TEST_ON"
#define TEST_DONE_REASON "PIN_TEST_DONE"
#else
#define TEST_ON_REASON "CHANNEL_TEST_ON"
#endif

static void enabled_channels(uint16_t expected_mask)
{
    assert(playing && device.state == RUNNING && device.local);
    assert(device.config.channel_mask == expected_mask);
    assert(device.config.shape == POINT && !device.config.mod_hz && device.config.level == 100);
    assert(!device.config.cx_um && !device.config.cy_um && device.config.z_um == 150000);
    assert(!strcmp(device.reason, TEST_ON_REASON));
    assert(!device.calibration_trial && !device.configured);
    assert(timer.PSC == 0 && timer.ARR == 799u && timer.CCR1 == 1u && !timer.CCER);
    assert(timer.CR1 & TIM_CR1_CEN);
    assert(timer.DIER == (TIM_DIER_UDE | TIM_DIER_CC1DE));
    assert(SystemCoreClock / (timer.ARR + 1u) / 2u == 40000u);
    assert(channel_a.CNDTR == 2u && channel_b.CNDTR == 2u);
    assert(channel_a.CMAR == (uint32_t)(uintptr_t)channel_words_a);
    assert(channel_b.CMAR == (uint32_t)(uintptr_t)channel_words_b);
    uint32_t expected_ccr = DMA_CCR_EN | DMA_CCR_DIR | DMA_CCR_CIRC | DMA_CCR_MINC |
        DMA_CCR_PSIZE_0 | DMA_CCR_MSIZE_0 | DMA_CCR_PL | DMA_CCR_TEIE;
    assert(channel_a.CCR == expected_ccr && channel_b.CCR == expected_ccr);
    assert(gpioa_idle == (0xa5e7u & (uint16_t)~F103_GPIOA_MASK));
    for (unsigned i = 0; i < HAP_CHANNELS; ++i) assert(channel_sample.phases[i] == 0);
    for (unsigned word = 0; word < 400; ++word) {
        bool high = (word & 1u) == 0;
        uint16_t expected_b = high ? expected_mask & 0xfffbu : 0u;
        uint16_t expected_a = gpioa_idle | (expected_mask & 4u && high ? (1u << 8u) : 0u);
        assert(channel_words_b[word & 1u] == expected_b);
        assert(channel_words_a[word & 1u] == expected_a);
    }
    uint16_t before_b[2], before_a[2];
    memcpy(before_b, channel_words_b, sizeof(before_b));
    memcpy(before_a, channel_words_a, sizeof(before_a));
    service();
    assert(!memcmp(before_b, channel_words_b, sizeof(before_b)) &&
           !memcmp(before_a, channel_words_a, sizeof(before_a)));
    dma.ISR = DMA_ISR_HTIF2 | DMA_ISR_TCIF2 | DMA_ISR_HTIF5 | DMA_ISR_TCIF5;
    app_f103_wave_irq(); /* Static buffer boundaries never trigger refill. */
    assert(playing && !pending_fault);
    Sample sample;
    for (unsigned remaining = 1; remaining <= 2; ++remaining) {
        channel_a.CNDTR = remaining;
        assert(output_readback(&sample) && sample.output && sample.drive_on);
        assert(sample.x_um == 0 && sample.y_um == 0 && sample.z_um == 150000);
        assert(sample.elapsed_us == (device.now_ms - channel_started_ms) * 1000u);
    }
    command(10, "SNAP");
    char mask[32];
    snprintf(mask, sizeof(mask), "channel_mask=%u", expected_mask);
    assert(strstr(serial_reply, mask) && strstr(serial_reply, TEST_ON_REASON));
}

static void reject_controls(void)
{
    const char *verbs[] = {"START", "PAUSE", "MODE value=REMOTE", "MODE value=LOCAL",
        "CONFIG carrier_hz=40000", "CALIBRATION action=clear",
        "CALIBRATION action=trial mask=1 offsets=0",
        "CALIBRATION action=store mask=65535 offsets=0"};
    Config before = device.config;
    State state = device.state;
    uint32_t revision = device.revision;
    bool pending = channel_test_pending, active = playing;
    const char *reason = device.reason;
    for (unsigned i = 0; i < sizeof(verbs) / sizeof(verbs[0]); ++i) {
        command(30u + i, verbs[i]);
        assert(strstr(serial_reply, "code=CHANNEL_TEST_ONLY"));
        assert(device.state == state && device.revision == revision && device.reason == reason);
        assert(channel_test_pending == pending && playing == active);
        assert(!memcmp(&device.config, &before, sizeof(before)));
        assert(!device.configured && !device.calibration_trial);
    }
}

#if !F103_FIXED_TEST
int main(void)
{
    fresh(0);
    off();
    assert(channel_test_pending && device.config.channel_mask == 0);
    command(1, "HELLO");
    assert(channel_test_pending && !playing);
    assert(strstr(serial_reply, "profile=CHANNEL_TEST") &&
           strstr(serial_reply, "caps=STOP,STATE,PHASE,GEOMETRY"));
    reject_controls();
    poll(F103_AUTOSTART_MS - 1u);
    off();
    for (unsigned index = 0; index < HAP_CHANNELS; ++index) {
        uint64_t start = F103_AUTOSTART_MS + index * (F103_CHANNEL_ON_MS + F103_CHANNEL_GAP_MS);
        poll(start);
        enabled_channels((uint16_t)(1u << index));
        if (index == 0 || index == HAP_CHANNELS - 1u) reject_controls();
        assert(device.revision == index * 2u + 1u);
        command(2, "HELLO");
        command(3, "PING");
        assert(channel_test_pending && playing);
        poll(start + F103_CHANNEL_ON_MS - 1u);
        assert(playing);
        poll(start + F103_CHANNEL_ON_MS);
        off();
        assert(device.state == IDLE && !device.config.channel_mask);
        assert(device.revision == index * 2u + 2u);
        assert(!strcmp(device.reason, "CHANNEL_TEST_GAP"));
        if (!index) reject_controls();
        poll(start + F103_CHANNEL_ON_MS + F103_CHANNEL_GAP_MS - 1u);
        off();
    }
    uint64_t done = F103_AUTOSTART_MS + HAP_CHANNELS * (F103_CHANNEL_ON_MS + F103_CHANNEL_GAP_MS);
    poll(done);
    off();
    assert(!channel_test_pending && device.state == IDLE && !strcmp(device.reason, "CHANNEL_TEST_DONE"));
    reject_controls();
    poll(done + 100000u);
    off();

    fresh(0);
    command(1, "HELLO");
    poll(F103_AUTOSTART_MS);
    uint64_t expiry = channel_test_deadline_ms;
    Config before_expiry = device.config;
    uint32_t before_revision = device.revision;
    virtual_tick = (uint32_t)(expiry - 1u);
    service();
    assert(playing && !channel_test_expired);
    /* Actual send_bytes() calls service for each TX byte while poll's cached
     * uptime stays at startup. Advancing the virtual tick crosses the limit. */
    serial_port.SR = USART_SR_TXE;
    tick_step_ms = 1;
    send_bytes("abcd", 4);
    tick_step_ms = 0;
    off();
    assert(channel_test_pending && channel_test_on && channel_test_expired);
    assert(device.state == RUNNING && device.revision == before_revision);
    assert(!memcmp(&device.config, &before_expiry, sizeof(before_expiry)));
    assert(!device.latched.output && !device.latched.drive_on);
    Sample expired_sample;
    assert(output_readback(&expired_sample) && !expired_sample.output && !expired_sample.drive_on);
    assert(expired_sample.elapsed_us >= F103_CHANNEL_ON_MS * 1000u);
    uint64_t after_send = uptime_ms + (uint32_t)(virtual_tick - last_tick);
    channel_test_poll(device.now_ms); /* Complete GAP despite a stale timestamp. */
    assert(!channel_test_expired && channel_test_pending && !channel_test_on);
    assert(device.state == IDLE && !strcmp(device.reason, "CHANNEL_TEST_GAP"));
    assert(channel_test_deadline_ms == after_send + F103_CHANNEL_GAP_MS);
    poll(channel_test_deadline_ms - 1u);
    off();
    virtual_tick = (uint32_t)(channel_test_deadline_ms + 100u);
    service(); /* Servicing expired GAP must never start the next channel. */
    off();
    poll(channel_test_deadline_ms + 100u);
    enabled_channels(2u);

    for (unsigned canceled = 0; canceled < 2; ++canceled) {
        fresh(0);
        command(1, "HELLO");
        poll(F103_AUTOSTART_MS);
        virtual_tick = (uint32_t)channel_test_deadline_ms;
        service();
        assert(channel_test_expired && channel_test_pending);
        if (canceled) {
            fail("UART_RX_ERROR");
            report_fault();
        } else command(50, "STOP");
        assert(!channel_test_expired && !channel_test_pending);
        channel_test_poll(done);
        service();
        off();
        assert(device.state == (canceled ? FAULT : IDLE));
    }

    fresh(0);
    uint64_t wrap_start = (uint64_t)UINT32_MAX - 1000u;
    poll(wrap_start);
    interrupt_mask = 1;
    virtual_tick = (uint32_t)(channel_test_deadline_ms - 1u);
    service();
    assert(playing && !channel_test_expired && interrupt_mask == 1);
    virtual_tick = (uint32_t)channel_test_deadline_ms;
    service();
    off();
    assert(channel_test_expired && channel_test_pending && interrupt_mask == 1);

    for (unsigned stage = 0; stage < 3; ++stage) {
        fresh(0);
        command(1, "HELLO");
        if (stage) poll(F103_AUTOSTART_MS);
        if (stage == 2) poll(F103_AUTOSTART_MS + F103_CHANNEL_ON_MS);
        command(2, "STOP");
        assert(!channel_test_pending && device.state == IDLE);
        off();
        poll(done + 100000u);
        off();
        command(3, "START");
        command(4, "MODE value=REMOTE");
        command(5, "CALIBRATION action=store mask=65535 offsets=0");
        off();
        assert(!channel_test_pending && !device.configured && !device.calibration_trial);
    }

    for (unsigned port = 0; port < 2; ++port) {
        fresh(0);
        poll(F103_AUTOSTART_MS);
        dma.ISR = port ? DMA_ISR_TEIF5 : DMA_ISR_TEIF2;
        app_f103_wave_irq();
        assert(!channel_test_pending && !strcmp(pending_fault, "DMA_ERROR"));
        report_fault();
        poll(done);
        off();
        assert(device.state == FAULT && !strcmp(device.reason, "DMA_ERROR"));
    }

    for (unsigned masked = 0; masked < 2; ++masked) {
        fresh(0);
        interrupt_mask = masked;
        inject_prepare_fault = true;
        poll(F103_AUTOSTART_MS);
        assert(!channel_test_pending && !strcmp(pending_fault, "UART_RX_ERROR"));
        assert(interrupt_mask == masked); /* Preserve caller's PRIMASK on failure. */
        report_fault();
        poll(done);
        off();
        assert(device.state == FAULT && !strcmp(device.reason, "UART_RX_ERROR"));
    }

    fresh(0);
    interrupt_mask = 1;
    poll(F103_AUTOSTART_MS);
    assert(playing && interrupt_mask == 1); /* Preserve an already masked caller. */
    app_f103_shutdown();
    assert(interrupt_mask == 1);

    fresh(0);
    channel_test_internal = true;
    fail("UART_RX_ERROR"); /* Even a fault during an internal transition aborts. */
    channel_test_internal = false;
    assert(!channel_test_pending);
    report_fault();
    poll(done);
    off();

    fresh(0);
    SystemCoreClock = 8000000u;
    poll(F103_AUTOSTART_MS);
    assert(!channel_test_pending && !strcmp(pending_fault, "OUTPUT_START_FAILED"));
    report_fault();
    SystemCoreClock = 64000000u;
    poll(done);
    off();

    for (unsigned i = 0; i < 2; ++i) {
        fresh(i ? RCC_CSR_WWDGRSTF : RCC_CSR_IWDGRSTF);
        assert(!channel_test_pending && !strcmp(pending_fault, "WATCHDOG_RESET"));
        report_fault();
        command(1, "HELLO");
        assert(device.state == FAULT && !strcmp(device.reason, "WATCHDOG_RESET"));
        command(2, "STOP");
        poll(done);
        off();
        assert(!channel_test_pending && device.state == IDLE);
    }

    fresh(0);
    pending_fault = "ADC_CALIBRATION_ERROR";
    startup_schedule(0);
    assert(!channel_test_pending);
    report_fault();
    poll(done);
    off();
    fresh(0);
    boot_ok = false;
    startup_schedule(0);
    poll(done);
    off();

    fresh(0);
    device.config.shape = POINT;
    device.config.mod_hz = 0;
    device.config.level = 100;
    device.config.channel_mask = 1;
    assert(!output_start(&device.config, 0)); /* No manual permission. */
    assert(!channel_test_pending);
    off();
    for (unsigned invalid = 0; invalid < 6; ++invalid) {
        fresh(0);
        channel_test_internal = true;
        device.config.shape = POINT;
        device.config.mod_hz = 0;
        device.config.level = 100;
        device.config.channel_mask = 1;
        if (invalid == 0) device.config.channel_mask = 0xffff;
        if (invalid == 1) device.config.channel_mask = 0;
        if (invalid == 2) device.config.shape = CIRCLE;
        if (invalid == 3) device.config.mod_hz = 200;
        if (invalid == 4) device.config.level = 30;
        if (invalid == 5) device.config.cx_um = 1000;
        assert(!output_start(&device.config, 0));
        channel_test_internal = false;
        off();
    }
    puts("Channel test: static DMA pairs/config, TX deadline/wrap, STOP/fault/reset cancellation passed");
    return 0;
}
#else
int main(void)
{
    const uint16_t target = (uint16_t)F103_FIXED_TEST_MASK;
#if F103_GROUP_TEST
    /* Build the expected inclusive range independently of the macro shifts. */
    uint16_t expected_range=0;
    for (unsigned channel=F103_GROUP_TEST_FIRST_CHANNEL;channel<=F103_GROUP_TEST_LAST_CHANNEL;++channel)
        expected_range |= (uint16_t)(1u << channel);
    assert(target==expected_range);
#endif
    uint64_t late = F103_AUTOSTART_MS + (uint64_t)F103_FIXED_TEST_ON_MS + 100000u;
#if F103_FIXED_TEST_ON_MS > 0
    uint64_t done = F103_AUTOSTART_MS + (uint64_t)F103_FIXED_TEST_ON_MS;
#endif
    fresh(0);
    assert(channel_test_pending && !strcmp(device.reason,TEST_WAIT_REASON));
    command(1,"HELLO");
    char selected[48], duration[48];
#if F103_GROUP_TEST
    snprintf(selected,sizeof(selected),"group_mask=%u",target);
    char first[48], last[48];
    snprintf(first,sizeof(first),"group_first=%u",F103_GROUP_TEST_FIRST_CHANNEL);
    snprintf(last,sizeof(last),"group_last=%u",F103_GROUP_TEST_LAST_CHANNEL);
    assert(strstr(serial_reply,first) && strstr(serial_reply,last));
#else
    snprintf(selected,sizeof(selected),"pin_channel=%u",F103_PIN_TEST_CHANNEL);
#endif
    snprintf(duration,sizeof(duration),"on_ms=%lu",(unsigned long)F103_FIXED_TEST_ON_MS);
    assert(strstr(serial_reply,selected) && strstr(serial_reply,duration));
    reject_controls();
    poll(F103_AUTOSTART_MS-1u);
    off();
    poll(F103_AUTOSTART_MS);
    enabled_channels(target);
    assert(device.revision==1);
#if F103_FIXED_TEST_ON_MS > 0
    assert(channel_test_deadline_ms==done);
#endif
    command(2,"HELLO");
    command(3,"PING");
    reject_controls();
#if F103_FIXED_TEST_ON_MS > 0
    /* Timed profiles still close at their deadline while sending serial data. */
    poll(done-1u);
    service();
    assert(playing);
    serial_port.SR=USART_SR_TXE;
    tick_step_ms=1;
    send_bytes("abcd",4);
    tick_step_ms=0;
    off();
    assert(channel_test_expired && channel_test_pending && device.revision==1);
    assert(device.config.channel_mask==target);
    Sample sample;
    assert(output_readback(&sample) && !sample.output && !sample.drive_on);
    channel_test_poll(device.now_ms);
    assert(!channel_test_pending && !channel_test_expired && !channel_test_on);
    assert(device.state==IDLE && device.revision==2 && !device.config.channel_mask);
    assert(!strcmp(device.reason,TEST_DONE_REASON));
    reject_controls();
    poll(late);
    off();
#else
    /* The default never ends after the old 10-second burst or late polls. */
    poll(F103_AUTOSTART_MS + 10000u);
    enabled_channels(target);
    poll(late);
    enabled_channels(target);
    assert(channel_test_pending && channel_test_on && !channel_test_expired);
    assert(device.revision==1);
    serial_port.SR=USART_SR_TXE;
    tick_step_ms=1;
    send_bytes("abcd",4);
    tick_step_ms=0;
    assert(playing && !channel_test_expired && device.revision==1);

    /* Cross the actual 32-bit HAL tick boundary in service(), while the
     * foreground's extended uptime remains just before the wrap. */
    poll((uint64_t)UINT32_MAX - 1000u);
    enabled_channels(target);
    interrupt_mask=1;
    virtual_tick=500u;
    service();
    assert(playing && !channel_test_expired && interrupt_mask==1);
    uint64_t after_wrap=uptime_ms + (uint32_t)(virtual_tick-last_tick);
    assert(after_wrap > UINT32_MAX);
    channel_test_poll(after_wrap);
    assert(playing && channel_test_pending && channel_test_on);
    assert(device.revision==1);
    interrupt_mask=0;
    poll(after_wrap);
    enabled_channels(target);
    poll((uint64_t)UINT32_MAX * 2u + 100000u);
    enabled_channels(target);
#endif

    /* STOP in WAIT, ON and late/deferred completion never restarts the configured outputs. */
    for (unsigned stage=0;stage<3;++stage) {
        fresh(0);
        command(1,"HELLO");
        if (stage) poll(F103_AUTOSTART_MS);
        if (stage==2) {
#if F103_FIXED_TEST_ON_MS > 0
            virtual_tick=(uint32_t)done;
            service();
            assert(channel_test_expired);
#else
            poll((uint64_t)UINT32_MAX+100000u);
            assert(playing);
#endif
        }
        command(2,"STOP");
        assert(!channel_test_pending && !channel_test_expired);
#if F103_FIXED_TEST_ON_MS > 0
        poll(late);
#else
        poll(stage==2 ? (uint64_t)UINT32_MAX+200000u : late);
#endif
        off();
        assert(device.state==IDLE);
    }
    for (unsigned port=0;port<2;++port) {
        fresh(0);
        poll(F103_AUTOSTART_MS);
        dma.ISR=port ? DMA_ISR_TEIF5 : DMA_ISR_TEIF2;
        app_f103_wave_irq();
        assert(!channel_test_pending && !strcmp(pending_fault,"DMA_ERROR"));
        report_fault();
        poll(late);
        off();
        assert(device.state==FAULT);
    }
    for (unsigned masked=0;masked<2;++masked) {
        fresh(0);
        interrupt_mask=masked;
        inject_prepare_fault=true;
        poll(F103_AUTOSTART_MS);
        assert(!channel_test_pending && !strcmp(pending_fault,"UART_RX_ERROR"));
        assert(interrupt_mask==masked);
        off();
    }
    for (unsigned watchdog_kind=0;watchdog_kind<2;++watchdog_kind) {
        fresh(watchdog_kind ? RCC_CSR_WWDGRSTF : RCC_CSR_IWDGRSTF);
        assert(!channel_test_pending && !strcmp(pending_fault,"WATCHDOG_RESET"));
        report_fault();
        command(1,"HELLO");
        command(2,"STOP");
        poll(late);
        off();
    }
    fresh(0);
    boot_ok=false;
    poll(F103_AUTOSTART_MS);
    off();
    assert(!channel_test_pending);
    fresh(0);
    device.config.shape=POINT;
    device.config.mod_hz=0;
    device.config.level=100;
    device.config.channel_mask=target;
    assert(!output_start(&device.config,0)); /* Cannot bypass mode ownership. */
    off();
    fresh(0);
    channel_test_internal=true;
    device.config.shape=POINT;
    device.config.mod_hz=0;
    device.config.level=100;
    device.config.channel_mask=(uint16_t)(target ^ 1u);
    assert(!output_start(&device.config,0)); /* Only the configured mask is allowed. */
    channel_test_internal=false;
    off();
    for (unsigned invalid=0;invalid<5;++invalid) {
        fresh(0);
        channel_test_internal=true;
        device.config.shape=POINT;
        device.config.mod_hz=0;
        device.config.level=100;
        device.config.channel_mask=target;
        if (invalid==0) device.config.channel_mask=0;
        if (invalid==1) device.config.shape=CIRCLE;
        if (invalid==2) device.config.mod_hz=200;
        if (invalid==3) device.config.level=30;
        if (invalid==4) device.config.cx_um=1000;
        assert(!output_start(&device.config,0));
        channel_test_internal=false;
        off();
    }
#if F103_FIXED_TEST_ON_MS > 0
    printf("FixedTest mask 0x%04X: static pair, timed burst, TX deadline and STOP/fault isolation passed\n",target);
#else
    printf("FixedTest mask 0x%04X: static pair, continuous output/tick wrap and STOP/fault isolation passed\n",target);
#endif
    return 0;
}
#endif
