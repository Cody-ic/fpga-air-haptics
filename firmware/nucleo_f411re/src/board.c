#include "board.h"
#include "receiver.h"
#include "stm32f411xe.h"
#include <stdio.h>

#define RX_SIZE 2048u
#define TX_SIZE 16384u
#define DMA_ERRORS_1 (DMA_LISR_TEIF1 | DMA_LISR_DMEIF1 | DMA_LISR_FEIF1)
#define DMA_ERRORS_5 (DMA_HISR_TEIF5 | DMA_HISR_DMEIF5 | DMA_HISR_FEIF5)

uint32_t SystemCoreClock = 64000000;
static WaveBlock waves[3];
static volatile unsigned bank_buffer[2];
static volatile unsigned spare_buffer, free_buffer;
static volatile bool ready, need_render, playing;
static volatile const char *pending_fault;
static const Config *playing_config;
static bool boot_ok;
static uint64_t next_time_us;
static volatile uint32_t ticks_ms;
static uint8_t rx[RX_SIZE], tx_bytes[TX_SIZE];
static volatile unsigned rx_head, rx_tail, tx_head, tx_tail;
/* Inspect in the debugger: measured CPU time, not an acoustic measurement. */
volatile uint32_t render_max_cycles;

static uint32_t lock(void) { uint32_t p = __get_PRIMASK(); __disable_irq(); return p; }
static void unlock(uint32_t p) { __set_PRIMASK(p); }

static void output_stop(void)
{
    uint32_t p = lock();
    TIM1->CR1 = 0; TIM1->DIER = 0;
    DMA2_Stream1->CR &= ~DMA_SxCR_EN;
    DMA2_Stream5->CR &= ~DMA_SxCR_EN;
    while ((DMA2_Stream1->CR | DMA2_Stream5->CR)&DMA_SxCR_EN) {}
    GPIOB->BSRR = HAP_MASK_B<<16; GPIOC->BSRR = HAP_MASK_C<<16;
    playing = false; ready = false; need_render = false;
    DMA2->LIFCR = 0x00000f40; DMA2->HIFCR = 0x00000f40;
    NVIC_ClearPendingIRQ(DMA2_Stream1_IRQn); NVIC_ClearPendingIRQ(DMA2_Stream5_IRQn);
    unlock(p);
}

static void fail(const char *reason) { output_stop(); pending_fault = reason; }

void DMA2_Stream5_IRQHandler(void)
{
    if (DMA2->HISR & DMA_ERRORS_5) fail("DMA_ERROR");
    DMA2->HIFCR = 0x00000f40;
}

void DMA2_Stream1_IRQHandler(void)
{
    uint32_t status = DMA2->LISR;
    DMA2->LIFCR = 0x00000f40;
    if ((status & DMA_ERRORS_1) || (DMA2->HISR & DMA_ERRORS_5)) { fail("DMA_ERROR"); return; }
    if (!(status & DMA_LISR_TCIF1) || !playing) return;
    unsigned active = !!(DMA2_Stream1->CR & DMA_SxCR_CT);
    if (active != !!(DMA2_Stream5->CR & DMA_SxCR_CT)) { fail("DMA_DESYNC"); return; }
    /* Stop while the next bank is still valid, before any stale bank can repeat. */
    if (!ready) { fail("DMA_UNDERRUN"); return; }
    unsigned inactive = active^1u;
    unsigned old = bank_buffer[inactive], next = spare_buffer;
    if (inactive) {
        DMA2_Stream5->M1AR = (uint32_t)waves[next].b;
        DMA2_Stream1->M1AR = (uint32_t)waves[next].c;
    } else {
        DMA2_Stream5->M0AR = (uint32_t)waves[next].b;
        DMA2_Stream1->M0AR = (uint32_t)waves[next].c;
    }
    bank_buffer[inactive] = next;
    free_buffer = old; ready = false;
    __DMB(); need_render = true;
}

static void dma_setup(DMA_Stream_TypeDef *stream, volatile uint32_t *port,
                      uint32_t *a, uint32_t *b, bool tc_irq)
{
    stream->PAR = (uint32_t)port;
    stream->M0AR = (uint32_t)a; stream->M1AR = (uint32_t)b;
    stream->NDTR = HAP_WORDS;
    stream->FCR = DMA_SxFCR_FEIE; /* Direct mode, single transfers to GPIO BSRR. */
    stream->CR = (6u<<DMA_SxCR_CHSEL_Pos) | DMA_SxCR_PL | DMA_SxCR_DBM |
        DMA_SxCR_MSIZE_1 | DMA_SxCR_PSIZE_1 | DMA_SxCR_MINC | DMA_SxCR_DIR_0 |
        DMA_SxCR_CIRC | DMA_SxCR_TEIE | DMA_SxCR_DMEIE | (tc_irq ? DMA_SxCR_TCIE : 0);
}

static bool output_start(const Config *c, uint64_t us)
{
    output_stop();
    if (pending_fault || !boot_ok) return false;
    playing_config = c;
    /* Resume at the next complete carrier boundary. */
    us = (us/25u)*25u;
    for (unsigned i = 0; i < 3; ++i) wave_render(c,us+i*1000u,&waves[i]);
    bank_buffer[0] = 0; bank_buffer[1] = 1; spare_buffer = 2;
    ready = true; need_render = false; next_time_us = us+3000;
    TIM1->PSC = 0; TIM1->ARR = 24; TIM1->RCR = 0; TIM1->CCR1 = 1;
    TIM1->CCMR1 = 0; TIM1->CCER = TIM_CCER_CC1E;
    TIM1->EGR = TIM_EGR_UG; TIM1->SR = 0; TIM1->CNT = 24;
    dma_setup(DMA2_Stream5,&GPIOB->BSRR,waves[0].b,waves[1].b,false);
    dma_setup(DMA2_Stream1,&GPIOC->BSRR,waves[0].c,waves[1].c,true);
    __DMB();
    DMA2_Stream5->CR |= DMA_SxCR_EN; DMA2_Stream1->CR |= DMA_SxCR_EN;
    playing = true;
    TIM1->DIER = TIM_DIER_UDE | TIM_DIER_CC1DE;
    TIM1->CR1 = TIM_CR1_CEN;
    return true;
}

void board_service(void)
{
    if (!playing || !need_render) return;
    unsigned index = free_buffer;
    uint32_t start = DWT->CYCCNT;
    wave_render(playing_config,next_time_us,&waves[index]);
    uint32_t cycles = DWT->CYCCNT-start;
    if (cycles > render_max_cycles) render_max_cycles = cycles;
    uint32_t p = lock();
    if (playing) {
        next_time_us += 1000;
        spare_buffer = index; need_render = false;
        __DMB(); ready = true;
    }
    unlock(p);
}

static bool output_readback(Sample *out)
{
    uint32_t p = lock();
    if (!playing) { unlock(p); return false; }
    /* DMA continues while IRQs are masked. Retry across a hardware bank switch. */
    unsigned bank, after, remaining;
    do {
        bank = !!(DMA2_Stream1->CR & DMA_SxCR_CT);
        remaining = DMA2_Stream1->NDTR;
        after = !!(DMA2_Stream1->CR & DMA_SxCR_CT);
    } while (bank != after);
    unsigned transferred = HAP_WORDS-remaining;
    unsigned cycle = transferred ? (transferred-1)/64 : 0;
    if (cycle >= HAP_CYCLES) cycle = HAP_CYCLES-1;
    *out = waves[bank_buffer[bank]].samples[cycle];
    unlock(p);
    return true;
}

static void uart_send(const char *bytes, size_t length)
{
    if (length >= TX_SIZE-((tx_head-tx_tail)&(TX_SIZE-1))) { fail("TX_OVERFLOW"); return; }
    unsigned head = tx_head;
    for (size_t i = 0; i < length; ++i) { tx_bytes[head] = (uint8_t)bytes[i]; head = (head+1)&(TX_SIZE-1); }
    uint32_t p = lock();
    __DMB(); tx_head = head; USART2->CR1 |= USART_CR1_TXEIE;
    unlock(p);
}

void USART2_IRQHandler(void)
{
    uint32_t status = USART2->SR;
    if (status & (USART_SR_RXNE | USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) {
        uint8_t byte = (uint8_t)USART2->DR;
        if (status & (USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) fail("SERIAL_RX_ERROR");
        else {
            unsigned next = (rx_head+1)&(RX_SIZE-1);
            if (next == rx_tail) fail("SERIAL_RX_OVERFLOW");
            else { rx[rx_head] = byte; __DMB(); rx_head = next; }
        }
    }
    if ((status & USART_SR_TXE) && (USART2->CR1 & USART_CR1_TXEIE)) {
        if (tx_head == tx_tail) USART2->CR1 &= ~USART_CR1_TXEIE;
        else { USART2->DR = tx_bytes[tx_tail]; tx_tail = (tx_tail+1)&(TX_SIZE-1); }
    }
}

int board_rx(void)
{
    if (rx_head == rx_tail) return -1;
    unsigned byte = rx[rx_tail]; rx_tail = (rx_tail+1)&(RX_SIZE-1);
    return (int)byte;
}
bool board_command_space(void) { return TX_SIZE-((tx_head-tx_tail)&(TX_SIZE-1)) > HAP_LINE+1024; }
bool board_tx_idle(void) { return tx_head == tx_tail; }
const char *board_fault(void)
{
    uint32_t p = lock();
    const char *reason = (const char *)pending_fault;
    pending_fault = NULL;
    if (reason) rx_tail = rx_head;
    unlock(p); return reason;
}

void SysTick_Handler(void) { ++ticks_ms; }
uint64_t board_millis(void)
{
    static uint32_t last;
    static uint64_t epoch;
    uint32_t now = ticks_ms;
    if (now < last) epoch += 0x100000000ULL;
    last = now; return epoch+now;
}

unsigned board_buttons(void)
{
    static unsigned stable, previous;
    static uint64_t changed;
    static bool long_sent;
    unsigned raw = ((GPIOC->IDR & (1u<<13)) ? 0u : 1u) |
        ((GPIOA->IDR & (1u<<6)) ? 0u : 2u) | ((GPIOA->IDR & (1u<<7)) ? 0u : 4u);
    uint64_t now = board_millis();
    if (raw != previous) { previous = raw; changed = now; }
    if (!(raw & 1)) long_sent = false;
    if (now-changed < 30) return 0;
    unsigned pressed = raw & ~stable; stable = raw;
    if ((raw & 1) && !long_sent && now-changed >= 1000) { pressed |= 8; long_sent = true; }
    return pressed;
}
void board_watchdog_feed(void) { IWDG->KR = 0xaaaa; }
void board_led(bool running) { GPIOA->BSRR = running ? 1u<<5 : 1u<<21; }

static void gpio_output(GPIO_TypeDef *port, unsigned mask)
{
    port->BSRR = mask<<16;
    for (unsigned i = 0; i < 16; ++i) if (mask & (1u<<i)) {
        port->MODER = (port->MODER & ~(3u<<(2*i))) | (1u<<(2*i));
        port->OSPEEDR = (port->OSPEEDR & ~(3u<<(2*i))) | (2u<<(2*i));
    }
}

static bool boot_identity(char boot[48])
{
    /* Sector 7 is reserved by the linker. One irreversible word per boot;
       no sector erase in firmware, so power interruption cannot reuse an ID. */
    volatile uint32_t *journal = (volatile uint32_t *)0x08060000;
    unsigned slot = 0;
    while (slot < 32768 && journal[slot] != 0xffffffffu) ++slot;
    if (slot == 32768) return false;
    FLASH->KEYR = 0x45670123; FLASH->KEYR = 0xcdef89ab;
    while (FLASH->SR & FLASH_SR_BSY) {}
    FLASH->SR = FLASH_SR_EOP | FLASH_SR_WRPERR | FLASH_SR_PGAERR | FLASH_SR_PGPERR | FLASH_SR_PGSERR;
    FLASH->CR = FLASH_CR_PSIZE_1 | FLASH_CR_PG;
    journal[slot] = 0;
    __DSB();
    while (FLASH->SR & FLASH_SR_BSY) {}
    FLASH->CR = FLASH_CR_LOCK;
    if (journal[slot] != 0) return false;
    const uint32_t *uid = (const uint32_t *)UID_BASE;
    snprintf(boot,48,"%08lx%08lx%08lx-%08x",(unsigned long)uid[0],(unsigned long)uid[1],(unsigned long)uid[2],slot);
    return true;
}

void board_init(char boot[48])
{
    /* HSI -> PLL (16 MHz / 16 * 256 / 4): 64 MHz. No dependency on ST-LINK MCO. */
    SCB->CPACR |= 0xfu<<20; __DSB(); __ISB();
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_GPIOBEN | RCC_AHB1ENR_GPIOCEN | RCC_AHB1ENR_DMA2EN;
    RCC->APB1ENR |= RCC_APB1ENR_PWREN | RCC_APB1ENR_USART2EN;
    RCC->APB2ENR |= RCC_APB2ENR_TIM1EN;
    (void)RCC->AHB1ENR;
    gpio_output(GPIOB,HAP_MASK_B); gpio_output(GPIOC,HAP_MASK_C); gpio_output(GPIOA,1u<<5);
    PWR->CR |= PWR_CR_VOS;
    /* Keep ART data cache off until the boot journal has been programmed/read back. */
    FLASH->ACR = FLASH_ACR_LATENCY_2WS | FLASH_ACR_PRFTEN;
    RCC->PLLCFGR = 16u | (256u<<6) | RCC_PLLCFGR_PLLP_0 | (4u<<24);
    RCC->CR |= RCC_CR_PLLON;
    while (!(RCC->CR & RCC_CR_PLLRDY)) {}
    RCC->CFGR = RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_SW_PLL;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL) {}
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0; DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    boot_ok = boot_identity(boot);
    if (!boot_ok) {
        snprintf(boot,48,"BOOT-JOURNAL-ERROR"); pending_fault = "BOOT_JOURNAL_ERROR";
    }
    FLASH->ACR |= FLASH_ACR_ICEN | FLASH_ACR_DCEN;
    GPIOA->MODER = (GPIOA->MODER & ~((3u<<4)|(3u<<6))) | (2u<<4)|(2u<<6);
    GPIOA->AFR[0] = (GPIOA->AFR[0] & ~((15u<<8)|(15u<<12))) | (7u<<8)|(7u<<12);
    GPIOA->PUPDR |= (1u<<6)|(1u<<12)|(1u<<14); /* RX and two external buttons. */
    GPIOC->PUPDR |= 1u<<26;
    USART2->BRR = 278; /* 32 MHz APB1 / 115200, oversampling 16. */
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE;
    NVIC_SetPriority(DMA2_Stream1_IRQn,0); NVIC_SetPriority(DMA2_Stream5_IRQn,0);
    NVIC_EnableIRQ(DMA2_Stream1_IRQn); NVIC_EnableIRQ(DMA2_Stream5_IRQn);
    NVIC_SetPriority(USART2_IRQn,3); NVIC_EnableIRQ(USART2_IRQn);
    SysTick_Config(SystemCoreClock/1000); NVIC_SetPriority(SysTick_IRQn,2);
    RCC->CSR |= RCC_CSR_LSION;
    while (!(RCC->CSR & RCC_CSR_LSIRDY)) {}
    IWDG->KR = 0x5555; IWDG->PR = 4; IWDG->RLR = 1000;
    while (IWDG->SR) {}
    IWDG->KR = 0xcccc; IWDG->KR = 0xaaaa;
}

Hardware board_hardware(void)
{
    return (Hardware){output_start,output_stop,output_readback,uart_send,board_service,
                      receiver_start,receiver_poll,receiver_cancel};
}

void Default_Handler(void)
{
    output_stop();
    for (;;) {} /* Independent watchdog resets into an output-off boot. */
}
