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
static char serial_reply[8192];
static size_t serial_size;
static uint32_t interrupt_mask;
static bool inject_render_fault;
static void render_with_fault(const Config *, uint64_t, uint16_t *, uint16_t *, uint16_t, Sample *);

#undef GPIOA
#undef GPIOB
#undef TIM1
#undef DMA1
#undef DMA1_Channel2
#undef DMA1_Channel5
#undef IWDG
#undef RCC
#undef DWT
#define GPIOA (&port_a)
#define GPIOB (&port_b)
#define TIM1 (&timer)
#define DMA1 (&dma)
#define DMA1_Channel2 (&channel_a)
#define DMA1_Channel5 (&channel_b)
#define IWDG (&watchdog)
#define RCC (&reset_clock)
#define DWT (&cycle_counter)
#define __get_PRIMASK() interrupt_mask
#define __disable_irq() (interrupt_mask = 1u)
#define __set_PRIMASK(value) (interrupt_mask = (value))
#define __DMB() ((void)0)
#undef NVIC_ClearPendingIRQ
#define NVIC_ClearPendingIRQ(irq) ((void)(irq))

uint32_t SystemCoreClock = 64000000u;
uint32_t HAL_GetTick(void) { return 0; }
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

#define f103_wave_render render_with_fault
#include "../Core/Src/app_f103.c"
#undef f103_wave_render

static void render_with_fault(const Config *c, uint64_t us, uint16_t *b, uint16_t *a,
                              uint16_t idle, Sample *sample)
{
    f103_wave_render(c, us, b, a, idle, sample);
    if (inject_render_fault) {
        inject_render_fault = false;
        fail("UART_RX_ERROR"); /* Simulate an IRQ after actual bank generation. */
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
    port_a.ODR = 0xa5e7u;
    boot_ok = true;
    pending_fault = NULL;
    SystemCoreClock = 64000000u;
    channel_test_internal = false;
    interrupt_mask = 0;
    inject_render_fault = false;
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

static void one_channel(unsigned index)
{
    assert(playing && device.state == RUNNING && device.local);
    assert(device.config.channel_mask == (uint16_t)(1u << index));
    assert(device.config.shape == POINT && !device.config.mod_hz && device.config.level == 100);
    assert(!device.config.cx_um && !device.config.cy_um && device.config.z_um == 150000);
    assert(!strcmp(device.reason, "CHANNEL_TEST_ON"));
    assert(!device.calibration_trial && !device.configured);
    for (unsigned bank = 0; bank < 2; ++bank) {
        assert(samples[bank].phases[index] == 0);
        for (unsigned word = 0; word < F103_HALF_WORDS; ++word) {
            bool high = word >= 64u && word % 64u < 32u;
            uint16_t expected_b = index == 2u || !high ? 0u : (uint16_t)(1u << index);
            uint16_t expected_a = gpioa_idle | (index == 2u && high ? F103_GPIOA_MASK : 0u);
            assert(waves_b[bank][word] == expected_b);
            assert(waves_a[bank][word] == expected_a);
        }
    }
    command(10, "SNAP");
    char mask[32];
    snprintf(mask, sizeof(mask), "channel_mask=%u", 1u << index);
    assert(strstr(serial_reply, mask) && strstr(serial_reply, "reason=CHANNEL_TEST_ON"));
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
        one_channel(index);
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

    fresh(0);
    poll(F103_AUTOSTART_MS);
    dma.ISR = DMA_ISR_TEIF2;
    app_f103_wave_irq();
    assert(!channel_test_pending && !strcmp(pending_fault, "DMA_ERROR"));
    report_fault();
    poll(done);
    off();
    assert(device.state == FAULT && !strcmp(device.reason, "DMA_ERROR"));

    for (unsigned masked = 0; masked < 2; ++masked) {
        fresh(0);
        interrupt_mask = masked;
        inject_render_fault = true;
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
    puts("Channel test: 16 single zero-phase outputs, guarded buffers, deadlines, STOP/fault/reset cancellation passed");
    return 0;
}
