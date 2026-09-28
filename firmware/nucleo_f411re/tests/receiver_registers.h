/* Native register model: use actual CMSIS layouts/masks, replace addresses only. */
#include "stm32f411xe.h"
extern RCC_TypeDef test_rcc;
extern GPIO_TypeDef test_gpioa;
extern ADC_TypeDef test_adc;
extern ADC_Common_TypeDef test_adc_common;
extern DMA_TypeDef test_dma;
extern DMA_Stream_TypeDef test_stream;
extern TIM_TypeDef test_tim;
#undef RCC
#undef GPIOA
#undef ADC1
#undef ADC
#undef DMA2
#undef DMA2_Stream0
#undef TIM2
#define RCC (&test_rcc)
#define GPIOA (&test_gpioa)
#define ADC1 (&test_adc)
#define ADC (&test_adc_common)
#define DMA2 (&test_dma)
#define DMA2_Stream0 (&test_stream)
#define TIM2 (&test_tim)
#define __DMB() ((void)0)
