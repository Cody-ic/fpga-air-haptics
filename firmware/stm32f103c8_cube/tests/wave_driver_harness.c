/* Exercise the actual driver with CMSIS register types and simulated DMA events.
 * This checks control/failure handling, not bus timing or physical waveforms. */
#include "main.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static GPIO_TypeDef port_a, port_b;
static TIM_TypeDef timer;
static DMA_TypeDef dma;
static DMA_Channel_TypeDef channel_a, channel_b;
static IWDG_TypeDef watchdog;
static DWT_Type cycle_counter;
static unsigned cleared_irqs;
static char serial_reply[8192];
static size_t serial_size;

static void serial_send(const char *bytes, size_t size)
{
    assert(serial_size + size < sizeof(serial_reply));
    memcpy(serial_reply + serial_size, bytes, size);
    serial_size += size;
    serial_reply[serial_size] = 0;
}

#undef GPIOA
#undef GPIOB
#undef TIM1
#undef DMA1
#undef DMA1_Channel2
#undef DMA1_Channel5
#undef IWDG
#undef DWT
#define GPIOA (&port_a)
#define GPIOB (&port_b)
#define TIM1 (&timer)
#define DMA1 (&dma)
#define DMA1_Channel2 (&channel_a)
#define DMA1_Channel5 (&channel_b)
#define IWDG (&watchdog)
#define DWT (&cycle_counter)
#define __get_PRIMASK() 0u
#define __disable_irq() ((void)0)
#define __set_PRIMASK(value) ((void)(value))
#define __DMB() ((void)0)
#undef NVIC_ClearPendingIRQ
#define NVIC_ClearPendingIRQ(irq) (cleared_irqs |= 1u << (unsigned)(irq))

uint32_t SystemCoreClock = 64000000u;
uint32_t HAL_GetTick(void) { return 0; }
/* ADC, Flash and initialization are outside this register-only test. */
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

/* Keep tests on the same start, shutdown, readback and IRQ code as firmware. */
#include "../Core/Src/app_f103.c"

static void fresh(void)
{
    memset(&port_a, 0, sizeof(port_a));
    memset(&port_b, 0, sizeof(port_b));
    memset(&timer, 0, sizeof(timer));
    memset(&dma, 0, sizeof(dma));
    memset(&channel_a, 0, sizeof(channel_a));
    memset(&channel_b, 0, sizeof(channel_b));
    port_a.ODR = 0xa5e7u;
    pending_fault = NULL;
    boot_ok = true;
    SystemCoreClock = 64000000u;
    cleared_irqs = 0;
    serial_size = 0;
    hap_init(&device, (Hardware){.start = output_start, .stop = app_f103_shutdown,
             .readback = output_readback, .send = serial_send}, "dma-register-test");
    device.config.level = 100;
    device.config.mod_hz = 0;
    assert(output_start(&device.config, 0));
}

static void boot_wait(void)
{
    fresh();
    app_f103_shutdown();
    device.state = IDLE;
    device.local = device.connected = false;
    rx_head = rx_tail = 0;
    autostart_deadline_ms = F103_AUTOSTART_MS;
    autostart_pending = true;
}

static void off(const char *reason)
{
    assert(!playing);
    assert(!(channel_a.CCR & DMA_CCR_EN));
    assert(!(channel_b.CCR & DMA_CCR_EN));
    assert(!(timer.CR1 & TIM_CR1_CEN));
    assert(!timer.DIER);
    assert(port_a.BRR == (1u << 8u));
    assert(port_b.BRR == 0xfffbu);
    assert(cleared_irqs & (1u << DMA1_Channel2_IRQn));
    assert(cleared_irqs & (1u << DMA1_Channel5_IRQn));
    if (reason) assert(pending_fault && !strcmp(pending_fault, reason));
}

int main(void)
{
    fresh();
    assert(channel_a.CCR & DMA_CCR_EN);
    assert(channel_b.CCR & DMA_CCR_EN);
    assert(channel_a.CPAR == (uint32_t)(uintptr_t)&port_a.ODR);
    assert(channel_b.CPAR == (uint32_t)(uintptr_t)&port_b.ODR);
    assert(channel_a.CNDTR == 2u * F103_HALF_WORDS);
    assert(channel_b.CNDTR == channel_a.CNDTR);
    assert(timer.DIER == (TIM_DIER_UDE | TIM_DIER_CC1DE));
    assert(timer.ARR == 24 && timer.CCR1 == 1 && !timer.CCER);
    assert(!(channel_b.CCR & (DMA_CCR_HTIE | DMA_CCR_TCIE)));
    for (unsigned bank = 0; bank < 2; ++bank) {
        for (unsigned word = 0; word < F103_HALF_WORDS; ++word) {
            assert(!(waves_b[bank][word] & 4u));
            assert((waves_a[bank][word] & ~0x100u) == (0xa5e7u & ~0x100u));
        }
    }
    app_f103_shutdown();
    off(NULL);

    for (unsigned error = 0; error < 2; ++error) {
        fresh();
        dma.ISR = error ? DMA_ISR_TEIF2 : DMA_ISR_TEIF5;
        app_f103_wave_irq();
        off("DMA_ERROR");
        assert(!output_start(&device.config, 0));
    }

    fresh();
    /* The earlier B port boundary alone must not release either bank. */
    dma.ISR = DMA_ISR_HTIF5;
    app_f103_wave_irq();
    assert(!refill && ready[0] && ready[1]);
    channel_b.CNDTR = F103_HALF_WORDS - 1u;
    channel_a.CNDTR = F103_HALF_WORDS;
    dma.ISR = DMA_ISR_HTIF2;
    app_f103_wave_irq();
    assert(playing && !refill && free_bank == 0 && ready[0]);
    assert(!refill && ready[0] && bank_us[0] == 2u * F103_HALF_US);
    channel_b.CNDTR = 2u * F103_HALF_WORDS - 1u;
    channel_a.CNDTR = 2u * F103_HALF_WORDS;
    dma.ISR = DMA_ISR_TCIF2;
    app_f103_wave_irq();
    assert(playing && !refill && free_bank == 1 && ready[1]);
    assert(!refill && ready[1] && bank_us[1] == 3u * F103_HALF_US);
    assert(f103_wave_ready(1000));
    channel_b.CNDTR = F103_HALF_WORDS - 1u;
    channel_a.CNDTR = F103_HALF_WORDS;
    dma.ISR = DMA_ISR_HTIF2;
    app_f103_wave_irq();
    assert(playing && !refill && bank_us[0] == 1000);
    assert(f103_wave_ready(2000));
    assert(!f103_wave_ready(5000));
    /* Reply formatting may offer only one background call per focus. That
     * call must finish all channels, rather than slowly drain the lookahead. */
    service();
    for (unsigned boundary = 0; boundary < 40; ++boundary) {
        bool half = (boundary & 1u) != 0;
        channel_b.CNDTR = half ? F103_HALF_WORDS - 1u : 2u * F103_HALF_WORDS - 1u;
        channel_a.CNDTR = half ? F103_HALF_WORDS : 2u * F103_HALF_WORDS;
        dma.ISR = half ? DMA_ISR_HTIF2 : DMA_ISR_TCIF2;
        app_f103_wave_irq();
        assert(playing && !refill);
        if ((boundary & 3u) == 3u) service();
    }

    fresh();
    channel_a.CNDTR = F103_HALF_WORDS;
    channel_b.CNDTR = F103_HALF_WORDS;
    dma.ISR = DMA_ISR_HTIF2;
    app_f103_wave_irq();
    /* An unready active bank must turn off both ports. */
    ready[0] = false;
    channel_a.CNDTR = 2u * F103_HALF_WORDS;
    channel_b.CNDTR = 2u * F103_HALF_WORDS;
    dma.ISR = DMA_ISR_TCIF2;
    app_f103_wave_irq();
    off("DMA_UNDERRUN");

    fresh();
    next_us = 6000;
    channel_a.CNDTR = F103_HALF_WORDS;
    channel_b.CNDTR = F103_HALF_WORDS;
    dma.ISR = DMA_ISR_HTIF2;
    app_f103_wave_irq();
    off("FOCUS_UNDERRUN");

    fresh();
    dma.ISR = DMA_ISR_HTIF2 | DMA_ISR_TCIF2;
    app_f103_wave_irq();
    off("DMA_UNDERRUN");

    fresh();
    channel_a.CNDTR = F103_HALF_WORDS;
    channel_b.CNDTR = F103_HALF_WORDS + 1u;
    dma.ISR = DMA_ISR_HTIF2;
    app_f103_wave_irq();
    off("DMA_DESYNC");

    fresh();
    Sample sample;
    channel_a.CNDTR = 2u * F103_HALF_WORDS - 1u;
    assert(output_readback(&sample) && !sample.drive_on);
    channel_a.CNDTR = 2u * F103_HALF_WORDS - 65u;
    assert(output_readback(&sample) && sample.drive_on);
    boot_ok = false;
    assert(!output_start(&device.config, 0));
    off(NULL);
    boot_wait();
    autostart_poll(F103_AUTOSTART_MS - 1u, false);
    assert(!playing && device.state == IDLE && !device.local);
    autostart_poll(F103_AUTOSTART_MS, false);
    assert(playing && device.state == RUNNING && device.local && !autostart_pending);
    hap_local_button(&device, false);
    assert(!playing && device.state == PAUSED);
    autostart_poll(F103_AUTOSTART_MS + 10000u, false);
    assert(!playing && device.state == PAUSED);
    hap_local_button(&device, false);
    assert(playing && device.state == RUNNING);
    hap_local_stop(&device);
    autostart_poll(F103_AUTOSTART_MS + 20000u, false);
    assert(!playing && device.state == IDLE);

    boot_wait();
    hap_local_stop(&device);
    autostart_poll(1000, true);
    autostart_poll(F103_AUTOSTART_MS, false);
    assert(!playing && !autostart_pending);

    boot_wait();
    rx_head = 1; /* UART data not yet parsed. */
    autostart_poll(F103_AUTOSTART_MS, false);
    rx_tail = rx_head;
    autostart_poll(F103_AUTOSTART_MS + 1000u, false);
    assert(!playing && !device.local && !autostart_pending);

    boot_wait();
    device.connected = true; /* A host handshake arrived during the countdown. */
    autostart_poll(F103_AUTOSTART_MS, false);
    assert(!playing && !device.local && !autostart_pending);

    boot_wait();
    pending_fault = "ADC_CALIBRATION_ERROR";
    autostart_poll(F103_AUTOSTART_MS, false);
    assert(!playing && !autostart_pending);

    boot_wait();
    boot_ok = false;
    autostart_poll(F103_AUTOSTART_MS, false);
    assert(!playing && !autostart_pending);

    boot_wait();
    SystemCoreClock = 8000000u;
    autostart_poll(F103_AUTOSTART_MS, false);
    assert(!playing && device.state == FAULT && !autostart_pending);
    SystemCoreClock = 64000000u;
    autostart_poll(F103_AUTOSTART_MS + 1000u, false);
    assert(!playing && device.state == FAULT);
    boot_wait();
    const char *hello = "HAP3 CMD 1 HELLO*F6AD\n";
    device.local = true;
    rx_head = (unsigned)strlen(hello);
    memcpy(rx, hello, rx_head);
    pending_fault = "DMA_UNDERRUN";
    report_fault();
    assert(device.state == FAULT && !device.dropping && rx_tail == 0);
    while (rx_tail != rx_head) hap_feed(&device, rx[rx_tail++]);
    assert(device.connected && strstr(serial_reply, "ACK 1 HELLO"));
    assert(strstr(serial_reply, "state=FAULT") && strstr(serial_reply, "DMA_UNDERRUN"));

    boot_wait();
    hap_feed(&device, 'H');
    pending_fault = "DMA_ERROR";
    report_fault();
    assert(device.used == 1 && !device.dropping);
    for (const char *p = hello + 1; *p; ++p) hap_feed(&device, (uint8_t)*p);
    assert(device.connected && strstr(serial_reply, "ACK 1 HELLO"));

    boot_wait();
    hap_feed(&device, 'H');
    rx_head = 4;
    pending_fault = "UART_RX_ERROR";
    report_fault();
    assert(rx_tail == rx_head && device.used == 0 && device.dropping);
    hap_feed(&device, '\n');
    for (const char *p = hello; *p; ++p) hap_feed(&device, (uint8_t)*p);
    assert(device.connected && strstr(serial_reply, "ACK 1 HELLO"));
    puts("Dual-port DMA, failures, first-HELLO recovery, readback and delayed startup passed");
    return 0;
}
