/* PA0 / ADC1_IN0, TIM2 TRGO, DMA2 Stream0 Channel0. No transmit resources used. */
#include "receiver.h"
#include "haptics.h"
#ifdef RECEIVER_REGISTER_TEST
#include "receiver_registers.h"
#else
#include "stm32f411xe.h"
#endif

static uint16_t samples[HAP_ADC_SAMPLES];
static bool active;

void receiver_cancel(void)
{
    TIM2->CR1 = 0;
    ADC1->CR2 = 0;
    DMA2_Stream0->CR &= ~DMA_SxCR_EN;
    while (DMA2_Stream0->CR & DMA_SxCR_EN) {}
    DMA2->LIFCR = 0x3d; /* Stream0 only; preserve transmitter Stream1 flags. */
    ADC1->SR = 0;
    active = false;
}

bool receiver_start(void)
{
    if (active) return false;
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOAEN | RCC_AHB1ENR_DMA2EN;
    RCC->APB1ENR |= RCC_APB1ENR_TIM2EN;
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;
    (void)RCC->APB2ENR;
    receiver_cancel();
    GPIOA->MODER |= 3u; /* PA0 analog; no changes to PA2/3 UART or PA6/7 buttons. */
    GPIOA->PUPDR &= ~3u;
    ADC->CCR = ADC_CCR_ADCPRE_0; /* APB2 64 MHz / 4 = 16 MHz. */
    ADC1->CR1 = 0; /* 12 bit, one regular channel. */
    ADC1->SMPR1 = 0;
    ADC1->SMPR2 = ADC_SMPR2_SMP0_0; /* 15 cycles, total 27/16 MHz < 2.5 us. */
    ADC1->SQR1 = 0; ADC1->SQR2 = 0; ADC1->SQR3 = 0;
    TIM2->CR2 = 0; TIM2->SMCR = 0; TIM2->DIER = 0;
    TIM2->PSC = 0; TIM2->ARR = 159; /* APB1 timer clock 64 MHz / 160. */
    TIM2->EGR = TIM_EGR_UG; TIM2->SR = 0; TIM2->CNT = 0;
    TIM2->CR2 = TIM_CR2_MMS_1; /* Update event as TRGO. */
    DMA2_Stream0->PAR = (uint32_t)(uintptr_t)&ADC1->DR;
    DMA2_Stream0->M0AR = (uint32_t)(uintptr_t)samples;
    DMA2_Stream0->NDTR = HAP_ADC_SAMPLES;
    DMA2_Stream0->FCR = 0;
    DMA2_Stream0->CR = DMA_SxCR_MSIZE_0 | DMA_SxCR_PSIZE_0 | DMA_SxCR_MINC;
    /* Normal mode, low DMA priority. DDS=0 stops ADC DMA requests at 200 words;
       no circular overwrite while the foreground serializes the frozen buffer. */
    __DMB(); DMA2_Stream0->CR |= DMA_SxCR_EN;
    ADC1->CR2 = ADC_CR2_DMA | ADC_CR2_EXTSEL_2 | ADC_CR2_EXTSEL_1 |
                ADC_CR2_EXTEN_0 | ADC_CR2_ADON;
#ifndef RECEIVER_REGISTER_TEST
    uint32_t start = DWT->CYCCNT;
    while ((uint32_t)(DWT->CYCCNT-start) < 640u) {} /* 10 us ADC stabilization. */
#endif
    active = true;
    TIM2->CR1 = TIM_CR1_CEN;
    return true;
}

int receiver_poll(const uint16_t **out)
{
    if (!active) return -1;
    uint32_t status = DMA2->LISR;
    if ((status & (DMA_LISR_TEIF0 | DMA_LISR_DMEIF0 | DMA_LISR_FEIF0)) ||
        (ADC1->SR & ADC_SR_OVR)) {
        receiver_cancel(); return -1;
    }
    if (!(status & DMA_LISR_TCIF0)) return 0;
    if (DMA2_Stream0->NDTR != 0) { receiver_cancel(); return -1; }
    __DMB();
    receiver_cancel();
    *out = samples;
    return 1;
}
