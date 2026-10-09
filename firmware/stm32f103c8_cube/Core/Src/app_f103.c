#include "app_f103.h"
#include "main.h"
#include "adc.h"
#include "tim.h"
#include "usart.h"
#include <stdio.h>
#include <string.h>

#define RX_SIZE 1536u
#define BOOT_JOURNAL ((volatile uint16_t *)0x0800fc00u)

static Device device;
#if F103_CHANNEL_TEST
/* A fixed high/low pair needs no producer, refill IRQ or low guard window. */
static uint16_t channel_words_b[F103_CHANNEL_DMA_WORDS], channel_words_a[F103_CHANNEL_DMA_WORDS];
static Sample channel_sample;
static uint64_t channel_started_ms;
#else
/* Two ports, two 250 us banks: same 5120-byte waveform budget as before. */
static uint16_t waves_b[2][F103_HALF_WORDS], waves_a[2][F103_HALF_WORDS];
static Sample samples[2];
static uint16_t drive_cycles[2];
static uint64_t bank_us[2], next_us;
static volatile bool refill, ready[2];
static volatile unsigned free_bank;
static const Config *playing_config;
#endif
static uint16_t gpioa_idle;
static volatile bool playing;
static const char *volatile pending_fault;
static bool boot_ok;
static uint8_t rx[RX_SIZE];
static volatile unsigned rx_head, rx_tail;
static uint16_t adc_samples[HAP_ADC_SAMPLES];
static volatile int adc_result;
static bool adc_busy;
static uint32_t last_tick;
static uint64_t uptime_ms;
#if F103_CHANNEL_TEST
static volatile bool channel_test_pending;
static volatile bool channel_test_internal;
static volatile bool channel_test_expired;
static bool channel_test_on;
static unsigned channel_test_index;
static uint64_t channel_test_deadline_ms;
#else
static uint64_t autostart_deadline_ms;
static bool autostart_pending;
static uint64_t auto_run_deadline_ms;
static volatile bool auto_run_pending;
#endif
static uint32_t startup_reset_flags;
volatile uint32_t render_max_cycles;
volatile uint32_t prepare_max_cycles;
static void service(void);

static uint32_t lock(void) { uint32_t p = __get_PRIMASK(); __disable_irq(); return p; }
static void unlock(uint32_t p) { __set_PRIMASK(p); }

void app_f103_shutdown(void)
{
    uint32_t p = lock();
    TIM1->CR1 &= ~TIM_CR1_CEN;
    TIM1->DIER = 0;
    DMA1_Channel5->CCR &= ~DMA_CCR_EN;
    DMA1_Channel2->CCR &= ~DMA_CCR_EN;
    GPIOB->BRR = F103_GPIOB_MASK;
    GPIOA->BRR = F103_GPIOA_MASK;
    DMA1->IFCR = DMA_IFCR_CGIF5 | DMA_IFCR_CGIF2;
    NVIC_ClearPendingIRQ(DMA1_Channel2_IRQn);
    NVIC_ClearPendingIRQ(DMA1_Channel5_IRQn);
    playing = false;
#if F103_CHANNEL_TEST
    if (!channel_test_internal) {
        channel_test_pending = false;
        channel_test_expired = false;
    }
#else
    refill = false;
    ready[0] = ready[1] = false;
    auto_run_pending = false;
#endif
    unlock(p);
}

static void fail(const char *reason)
{
#if F103_CHANNEL_TEST
    channel_test_pending = false; /* IRQ faults override internal transitions too. */
#endif
    app_f103_shutdown();
    pending_fault = reason;
}

void app_f103_wave_irq(void)
{
    uint32_t flags = DMA1->ISR;
    DMA1->IFCR = DMA_IFCR_CGIF5 | DMA_IFCR_CGIF2;
    if (flags & (DMA_ISR_TEIF5 | DMA_ISR_TEIF2)) { fail("DMA_ERROR"); return; }
#if !F103_CHANNEL_TEST
    if (!playing) return;
    /* Port A transfers after port B, so its boundary releases both buffers. */
    unsigned boundaries = flags & (DMA_ISR_HTIF2 | DMA_ISR_TCIF2);
    if (!boundaries) return;
    if (boundaries == (DMA_ISR_HTIF2 | DMA_ISR_TCIF2)) { fail("DMA_UNDERRUN"); return; }
    unsigned active = (flags & DMA_ISR_HTIF2) ? 1u : 0u;
    if (!ready[active] || refill) { fail("DMA_UNDERRUN"); return; }
    unsigned remaining_b = DMA1_Channel5->CNDTR;
    unsigned remaining_a = DMA1_Channel2->CNDTR;
    unsigned actual_b = remaining_b <= F103_HALF_WORDS && remaining_b ? 1u : 0u;
    unsigned actual_a = remaining_a <= F103_HALF_WORDS && remaining_a ? 1u : 0u;
    if (active != actual_b || active != actual_a) { fail("DMA_DESYNC"); return; }
    free_bank = active ^ 1u;
    ready[free_bank] = false;
    __DMB(); refill = true;
    service(); /* Refill is bounded: geometry/phase solving stays in foreground. */
#endif
}

static void service(void)
{
    IWDG->KR = 0xaaaau;
#if F103_CHANNEL_TEST
    /* Formatting/transmission can span the ON deadline. Close the pins here,
     * without reentering protocol or changing a partially serialized config. */
    uint64_t now_ms = uptime_ms + (uint32_t)(HAL_GetTick() - last_tick);
    uint32_t p = lock();
    if (playing && channel_test_pending && channel_test_on && !pending_fault &&
        now_ms >= channel_test_deadline_ms) {
        bool internal = channel_test_internal;
        channel_test_internal = true;
        app_f103_shutdown();
        channel_test_internal = internal;
        channel_test_expired = true;
        device.latched.output = device.latched.drive_on = false;
    }
    unlock(p);
#else
    if (!playing) return;
    if (!refill) {
        /* Finish one complete focus between protocol operations. A single
         * phase per formatted field can starve the producer during ADC replies.
         * Waveform/RX IRQs still preempt this foreground-only calculation. */
        uint32_t started = DWT->CYCCNT;
        for (unsigned step = 0; step < HAP_CHANNELS + 1u; ++step)
            if (!f103_wave_prepare(playing_config)) break;
        uint32_t cycles = DWT->CYCCNT - started;
        if (cycles > prepare_max_cycles) prepare_max_cycles = cycles;
        return;
    }
    if (!f103_wave_ready(next_us)) { fail("FOCUS_UNDERRUN"); return; }
    unsigned bank = free_bank;
    uint32_t started = DWT->CYCCNT;
    Sample sample;
    uint16_t active_cycles;
    f103_wave_render(playing_config, next_us, waves_b[bank], waves_a[bank], gpioa_idle,
                     &sample, &active_cycles);
    uint32_t cycles = DWT->CYCCNT-started;
    if (cycles > render_max_cycles) render_max_cycles = cycles;
    uint32_t p = lock();
    if (playing) {
        samples[bank] = sample; bank_us[bank] = next_us;
        drive_cycles[bank] = active_cycles;
        next_us += F103_HALF_US;
        refill = false;
        __DMB(); ready[bank] = true;
    }
    unlock(p);
#endif
}

static bool output_start(const Config *c, uint64_t us)
{
    app_f103_shutdown();
#if F103_CHANNEL_TEST
    if (!channel_test_internal || !c->channel_mask ||
        (c->channel_mask & (c->channel_mask - 1u)) || c->shape != POINT ||
        c->mod_hz || c->level != 100 || c->cx_um || c->cy_um || c->z_um != 150000)
        return false;
#if F103_PIN_TEST
    if (c->channel_mask != (uint16_t)(1u << F103_PIN_TEST_CHANNEL)) return false;
#endif
#endif
    if (pending_fault || !boot_ok || SystemCoreClock != 64000000u) return false;
    /* Keep button pull-ups and every non-array GPIOA output latch unchanged. */
    gpioa_idle = (uint16_t)GPIOA->ODR & (uint16_t)~F103_GPIOA_MASK;
#if F103_CHANNEL_TEST
    (void)us;
    float remaining;
    geometry_sample(c, 0, &channel_sample, &remaining);
    /* Electrical phase is fixed by the static pair, independent of distance. */
    memset(channel_sample.phases, 0, sizeof(channel_sample.phases));
    channel_sample.output = channel_sample.scan_on = channel_sample.drive_on = true;
    channel_started_ms = device.now_ms;
    channel_words_b[0] = c->channel_mask & F103_GPIOB_MASK;
    channel_words_b[1] = 0;
    channel_words_a[0] = gpioa_idle | ((c->channel_mask & 4u) ? F103_GPIOA_MASK : 0u);
    channel_words_a[1] = gpioa_idle;
    TIM1->PSC = 0; TIM1->ARR = F103_CHANNEL_TIMER_ARR; TIM1->RCR = 0;
#else
    playing_config = c;
    us = us/25u*25u;
    f103_wave_reset();
    for (unsigned i = 0; i < 2; ++i) {
        bank_us[i] = us+i*F103_HALF_US;
        f103_wave_render(c, bank_us[i], waves_b[i], waves_a[i], gpioa_idle,
                         &samples[i], &drive_cycles[i]);
    }
    for (unsigned step = 0; step < F103_LOOKAHEAD * (HAP_CHANNELS + 1u); ++step)
        f103_wave_prepare(c);
    next_us = us+2u*F103_HALF_US;
    ready[0] = ready[1] = true;
    TIM1->PSC = 0; TIM1->ARR = 24; TIM1->RCR = 0;
#endif
    TIM1->CCMR1 = 0; /* Internal CH1 timing only; PA8 remains a GPIO. */
    TIM1->CCER = 0;
    TIM1->CR2 &= ~TIM_CR2_CCDS;
    TIM1->CCR1 = 1; /* Port A request one 64 MHz timer tick after port B. */
    TIM1->EGR = TIM_EGR_UG; TIM1->SR = 0; TIM1->CNT = TIM1->ARR;
    DMA1_Channel5->CPAR = (uint32_t)&GPIOB->ODR;
#if F103_CHANNEL_TEST
    DMA1_Channel5->CMAR = (uint32_t)channel_words_b;
    DMA1_Channel5->CNDTR = F103_CHANNEL_DMA_WORDS;
#else
    DMA1_Channel5->CMAR = (uint32_t)waves_b;
    DMA1_Channel5->CNDTR = 2u*F103_HALF_WORDS;
#endif
    DMA1_Channel5->CCR = DMA_CCR_DIR | DMA_CCR_CIRC | DMA_CCR_MINC |
        DMA_CCR_PSIZE_0 | DMA_CCR_MSIZE_0 | DMA_CCR_PL |
        DMA_CCR_TEIE;
    DMA1_Channel2->CPAR = (uint32_t)&GPIOA->ODR;
#if F103_CHANNEL_TEST
    DMA1_Channel2->CMAR = (uint32_t)channel_words_a;
    DMA1_Channel2->CNDTR = F103_CHANNEL_DMA_WORDS;
#else
    DMA1_Channel2->CMAR = (uint32_t)waves_a;
    DMA1_Channel2->CNDTR = 2u*F103_HALF_WORDS;
#endif
    DMA1_Channel2->CCR = DMA_CCR_DIR | DMA_CCR_CIRC | DMA_CCR_MINC |
        DMA_CCR_PSIZE_0 | DMA_CCR_MSIZE_0 | DMA_CCR_PL |
        DMA_CCR_TEIE;
#if !F103_CHANNEL_TEST
    DMA1_Channel2->CCR |= DMA_CCR_HTIE | DMA_CCR_TCIE;
#endif
    uint32_t p = lock();
    /* A UART/DMA fault may have arrived while the output data was prepared. */
    if (pending_fault || !boot_ok || SystemCoreClock != 64000000u) {
        app_f103_shutdown();
        unlock(p);
        return false;
    }
    __DMB();
    playing = true;
    DMA1_Channel5->CCR |= DMA_CCR_EN;
    DMA1_Channel2->CCR |= DMA_CCR_EN;
    TIM1->DIER = TIM_DIER_UDE | TIM_DIER_CC1DE;
    TIM1->CR1 = TIM_CR1_CEN;
    unlock(p);
    return true;
}

static bool output_readback(Sample *out)
{
    uint32_t p = lock();
#if F103_CHANNEL_TEST
    if (!playing && !channel_test_expired) { unlock(p); return false; }
    *out = channel_sample;
    /* This is a commanded digital envelope, not a measured pin or sound level. */
    uint64_t now_ms = uptime_ms + (uint32_t)(HAL_GetTick() - last_tick);
    out->elapsed_us = (now_ms - channel_started_ms) * 1000u;
    out->output = out->drive_on = playing;
    unlock(p);
#else
    if (!playing) { unlock(p); return false; }
    unsigned remaining = DMA1_Channel2->CNDTR;
    unsigned transferred = 2u*F103_HALF_WORDS-remaining;
    /* CNDTR points after the last transferred sample. */
    unsigned last = transferred ? transferred-1u : 0u;
    unsigned bank = last/F103_HALF_WORDS;
    unsigned cycle = (last%F103_HALF_WORDS)/64u;
    *out = samples[bank];
    bool drive_on = (drive_cycles[bank] >> cycle) & 1u;
    unlock(p);
    out->drive_on = drive_on;
#endif
    return true;
}

void app_f103_uart_irq(void)
{
    uint32_t status = USART2->SR;
    if (status & (USART_SR_RXNE | USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) {
        uint8_t byte = (uint8_t)USART2->DR;
        if (status & (USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) {
            fail("UART_RX_ERROR"); return;
        }
        unsigned head = rx_head+1u;
        if (head == RX_SIZE) head = 0;
        if (head == rx_tail) { fail("UART_RX_OVERFLOW"); return; }
        rx[rx_head] = byte; __DMB(); rx_head = head;
    }
}

static void send_bytes(const char *bytes, size_t n)
{
    uint32_t started = HAL_GetTick();
    while (n--) {
        while (!(USART2->SR & USART_SR_TXE)) {
            service();
            if ((uint32_t)(HAL_GetTick()-started) > 500u) { fail("UART_TX_TIMEOUT"); return; }
        }
        USART2->DR = (uint8_t)*bytes++;
        service();
    }
}

static void capture_cancel(void)
{
    TIM3->CR1 &= ~TIM_CR1_CEN;
    if (adc_busy) (void)HAL_ADC_Stop_DMA(&hadc1);
    adc_busy = false;
}

static bool capture_start(void)
{
    if (adc_busy) return false;
    TIM3->CR1 &= ~TIM_CR1_CEN;
    TIM3->CNT = 0; TIM3->SR = 0;
    adc_result = 0; adc_busy = true;
    if (HAL_ADC_Start_DMA(&hadc1, (uint32_t *)adc_samples, HAP_ADC_SAMPLES) != HAL_OK) {
        capture_cancel(); return false;
    }
    __HAL_DMA_DISABLE_IT(hadc1.DMA_Handle, DMA_IT_HT);
    TIM3->CR1 |= TIM_CR1_CEN;
    return true;
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *adc)
{
    if (adc == &hadc1) { TIM3->CR1 &= ~TIM_CR1_CEN; adc_result = 1; }
}

void HAL_ADC_ErrorCallback(ADC_HandleTypeDef *adc)
{
    if (adc == &hadc1) { TIM3->CR1 &= ~TIM_CR1_CEN; adc_result = -1; }
}

static int capture_poll(const uint16_t **out)
{
    if (!adc_busy) return -1;
    int result = adc_result;
    if (result) { capture_cancel(); *out = adc_samples; }
    return result;
}

static bool boot_identity(char boot[48])
{
    unsigned slot = 0;
    while (slot < 512u && BOOT_JOURNAL[slot] != 0xffffu) ++slot;
    if (slot == 512u || HAL_FLASH_Unlock() != HAL_OK) return false;
    HAL_StatusTypeDef result = HAL_FLASH_Program(FLASH_TYPEPROGRAM_HALFWORD,
                                               (uint32_t)&BOOT_JOURNAL[slot], 0);
    HAL_FLASH_Lock();
    if (result != HAL_OK || BOOT_JOURNAL[slot] != 0) return false;
    const uint32_t *uid = (const uint32_t *)UID_BASE;
    snprintf(boot, 48, "%08lx%08lx%08lx-%04x", (unsigned long)uid[0],
             (unsigned long)uid[1], (unsigned long)uid[2], slot);
    return true;
}

static unsigned buttons(uint64_t now)
{
    static unsigned raw_prev, stable;
    static uint64_t changed, stop_pressed;
    static bool long_press;
    unsigned raw = (~GPIOA->IDR >> 5u)&7u;
    if (raw != raw_prev) { raw_prev = raw; changed = now; }
    unsigned events = 0;
    if (now-changed >= 25u && raw != stable) {
        events = raw & ~stable;
        if (events & 1u) { stop_pressed = now; long_press = false; }
        stable = raw;
    }
    if ((stable&1u) && !long_press && now-stop_pressed >= 1500u) {
        long_press = true; events |= 8u;
    }
    return events;
}

#if !F103_CHANNEL_TEST
static void autostart_poll(uint64_t now_ms, bool user_action)
{
    if (!autostart_pending) return;
    /* A command, button or fault takes precedence over this one-time startup. */
    if (user_action || rx_head != rx_tail || device.connected || device.local ||
        device.state != IDLE || !boot_ok || pending_fault) {
        autostart_pending = false;
        return;
    }
    if (now_ms < autostart_deadline_ms) return;
    autostart_pending = false; /* STOP, PAUSE and faults never schedule a retry. */
    hap_toggle_mode(&device);
    hap_local_button(&device, false);
    if (playing && device.state == RUNNING) {
        auto_run_deadline_ms = now_ms + F103_AUTO_RUN_LIMIT_MS;
        auto_run_pending = true;
    }
}

static void auto_run_poll(uint64_t now_ms)
{
    if (!auto_run_pending || now_ms < auto_run_deadline_ms) return;
    /* Every explicit stop, pause, fault or new start cancels this deadline. */
    hap_local_stop(&device);
    device.reason = "AUTO_RUN_LIMIT";
}
#else
static void channel_test_poll(uint64_t now_ms)
{
    if (!channel_test_pending) return;
    if (!boot_ok || pending_fault || device.state == FAULT) {
        channel_test_pending = false;
        app_f103_shutdown();
        return;
    }
    if (!channel_test_expired && now_ms < channel_test_deadline_ms) return;
    /* service() may have closed the output while this poll's timestamp was
     * cached before serial transmission. Give GAP its full foreground delay. */
    uint64_t current_ms = uptime_ms + (uint32_t)(HAL_GetTick() - last_tick);
    if (now_ms < current_ms) now_ms = current_ms;
    /* The permission spans only our synchronous off/configure/start transition. */
    channel_test_internal = true;
    app_f103_shutdown();
    channel_test_expired = false;
    device.state = IDLE;
    device.elapsed_us = 0;
    device.latched.output = device.latched.drive_on = false;
    if (channel_test_on) {
        channel_test_on = false;
        device.config.channel_mask = 0;
        ++device.revision;
#if F103_PIN_TEST
        channel_test_pending = false;
        device.reason = "PIN_TEST_DONE";
#else
        device.reason = "CHANNEL_TEST_GAP";
        channel_test_deadline_ms = now_ms + F103_CHANNEL_GAP_MS;
#endif
    } else if (channel_test_index == HAP_CHANNELS) {
        channel_test_pending = false;
        device.reason = "CHANNEL_TEST_DONE";
    } else {
        config_default(&device.config);
        device.config.shape = POINT;
        device.config.cx_um = device.config.cy_um = 0;
        device.config.z_um = 150000;
        device.config.mod_hz = 0;
        device.config.level = 100;
#if F103_PIN_TEST
        device.config.channel_mask = (uint16_t)(1u << F103_PIN_TEST_CHANNEL);
#else
        device.config.channel_mask = (uint16_t)(1u << channel_test_index);
#endif
        const char *error = config_compile(&device.config);
        if (error) fail(error);
        else {
            /* No focusing/phase solver in the single-channel electrical test. */
            ++device.revision;
            if (output_start(&device.config, 0)) {
                device.state = RUNNING;
                channel_test_on = true;
                ++channel_test_index;
#if F103_PIN_TEST
                device.reason = "PIN_TEST_ON";
                channel_test_deadline_ms = now_ms + F103_PIN_TEST_ON_MS;
#else
                device.reason = "CHANNEL_TEST_ON";
                channel_test_deadline_ms = now_ms + F103_CHANNEL_ON_MS;
#endif
            } else if (!pending_fault) fail("OUTPUT_START_FAILED");
        }
    }
    channel_test_internal = false;
}
#endif

static void capture_reset_flags(void)
{
    startup_reset_flags = RCC->CSR;
    RCC->CSR |= RCC_CSR_RMVF; /* Clear sticky reset flags only after capturing. */
}

static void startup_schedule(uint64_t now_ms)
{
    /* Preserve boot-journal/ADC faults instead of replacing their diagnosis. */
    if (!pending_fault && (startup_reset_flags & (RCC_CSR_IWDGRSTF | RCC_CSR_WWDGRSTF))) {
        pending_fault = "WATCHDOG_RESET";
        /* LOCAL HELLO preserves FAULT; the host must send STOP to acknowledge. */
        device.local = true;
    }
#if F103_CHANNEL_TEST
    channel_test_on = false;
    channel_test_expired = false;
    channel_test_index = 0;
    channel_test_internal = false;
    channel_test_deadline_ms = now_ms + F103_AUTOSTART_MS;
    channel_test_pending = boot_ok && !pending_fault;
    device.local = true; /* Observing the test via HELLO must not stop it. */
    device.config.channel_mask = 0;
    if (channel_test_pending) {
#if F103_PIN_TEST
        device.reason = "PIN_TEST_WAIT";
#else
        device.reason = "CHANNEL_TEST_WAIT";
#endif
    }
#else
    autostart_deadline_ms = now_ms + F103_AUTOSTART_MS;
    autostart_pending = boot_ok && !pending_fault;
#endif
}

void app_f103_init(void)
{
    capture_reset_flags();
    app_f103_shutdown();
    /* 50 MHz GPIO mode for 2.56 MHz DMA slot writes; keep SWD on PA13/PA14. */
    GPIOB->CRL = 0x33333433u; GPIOB->CRH = 0x33333333u; /* PB2 stays floating input. */
    GPIOA->CRH = (GPIOA->CRH & ~0xfu) | 0x3u; /* PA8 only; preserve SWD. */
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0; DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    DBGMCU->CR |= DBGMCU_CR_DBG_IWDG_STOP | DBGMCU_CR_DBG_TIM1_STOP | DBGMCU_CR_DBG_TIM3_STOP;
    char boot[48] = "BOOT-JOURNAL-ERROR";
    boot_ok = boot_identity(boot);
    if (!boot_ok) pending_fault = "BOOT_JOURNAL_ERROR";
    if (HAL_ADCEx_Calibration_Start(&hadc1) != HAL_OK) pending_fault = "ADC_CALIBRATION_ERROR";
    USART2->CR1 |= USART_CR1_RXNEIE;
    NVIC_SetPriority(DMA1_Channel5_IRQn, 1);
    NVIC_SetPriority(DMA1_Channel2_IRQn, 1);
    NVIC_SetPriority(USART2_IRQn, 0); /* RX must preempt a >87 us waveform refill. */
    NVIC_SetPriority(DMA1_Channel1_IRQn, 2);
    NVIC_EnableIRQ(USART2_IRQn);
    /* Starting IWDG enables LSI; its clock is needed to clear update flags. */
    IWDG->KR = 0xccccu;
    IWDG->KR = 0x5555u; IWDG->PR = 4u; IWDG->RLR = 1000u;
    uint32_t watchdog_started = HAL_GetTick();
    while (IWDG->SR) {
        if ((uint32_t)(HAL_GetTick()-watchdog_started) >= 100u) Error_Handler();
    }
    IWDG->KR = 0xaaaau;
    hap_init(&device, (Hardware){output_start, app_f103_shutdown, output_readback,
        send_bytes, service, capture_start, capture_poll, capture_cancel}, boot);
    startup_schedule(HAL_GetTick());
}

static void report_fault(void)
{
    uint32_t p = lock();
    const char *fault = pending_fault; pending_fault = NULL;
    /* Output faults do not damage UART data. Preserve queued complete frames
     * and partially assembled commands so the first HELLO can report FAULT. */
    bool damaged_rx = fault && (!strcmp(fault, "UART_RX_ERROR") ||
                                !strcmp(fault, "UART_RX_OVERFLOW"));
    bool partial_rx = device.used != 0 || device.dropping;
    if (damaged_rx) rx_tail = rx_head;
    unlock(p);
    if (fault) {
        hap_fault(&device, fault);
        if (damaged_rx) {
            device.used = 0;
            device.dropping = partial_rx;
        }
    }
}

void app_f103_poll(void)
{
    service();
    uint32_t tick = HAL_GetTick();
    uptime_ms += (uint32_t)(tick-last_tick); last_tick = tick;
    report_fault();
#if !F103_CHANNEL_TEST
    auto_run_poll(uptime_ms);
#endif
    hap_poll(&device, uptime_ms, false);
    bool command_received = rx_head != rx_tail;
    if (command_received) {
        uint8_t byte = rx[rx_tail];
        unsigned tail = rx_tail+1u;
        rx_tail = tail == RX_SIZE ? 0 : tail;
        hap_feed(&device, byte);
    }
    unsigned event = buttons(uptime_ms);
    if (event&1u) hap_local_stop(&device);
#if F103_CHANNEL_TEST
    channel_test_poll(uptime_ms);
#else
    else {
        if (event&2u) hap_local_button(&device, true);
        if (event&4u) hap_local_button(&device, false);
    }
    if (event&8u) hap_toggle_mode(&device);
    autostart_poll(uptime_ms, command_received || event);
#endif
    /* Reply serialization blocks foreground parsing, but continuously services DMA. */
    hap_poll(&device, uptime_ms, rx_head == rx_tail);
    HAL_GPIO_WritePin(LED_GPIO_Port, LED_Pin, device.state == RUNNING ? GPIO_PIN_RESET : GPIO_PIN_SET);
    service();
}
