#include "receiver_registers.h"
#include "receiver.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

RCC_TypeDef test_rcc;
GPIO_TypeDef test_gpioa;
ADC_TypeDef test_adc;
ADC_Common_TypeDef test_adc_common;
DMA_TypeDef test_dma;
DMA_Stream_TypeDef test_stream;
TIM_TypeDef test_tim;

int main(void)
{
    GPIOA->MODER=0xa5a5a5a5; GPIOA->PUPDR=0x5a5a5a5a;
    uint32_t modes=GPIOA->MODER, pulls=GPIOA->PUPDR;
    assert(receiver_start()); assert(!receiver_start());
    assert(GPIOA->MODER == (modes|3u));
    assert(GPIOA->PUPDR == (pulls&~3u));
    assert(TIM2->PSC==0 && TIM2->ARR==159 && TIM2->CR2==(2u<<4));
    assert(ADC->CCR == (1u<<16));
    assert(ADC1->SMPR2 == 1 && ADC1->SQR1 == 0 && ADC1->SQR3 == 0);
    assert((ADC1->CR2 & (15u<<24)) == (6u<<24));
    assert((ADC1->CR2 & (1u<<9)) == 0); /* Finite ADC DMA requests. */
    assert(DMA2_Stream0->NDTR==200);
    assert(DMA2_Stream0->CR == ((1u<<13)|(1u<<11)|(1u<<10)|1u));
    assert(DMA2->LIFCR == 0x3d); /* Never clear transmitter's Stream1 flags. */
    const uint16_t *out=NULL;
    DMA2->LISR=0;
    assert(receiver_poll(&out)==0 && out==NULL);
    DMA2->LISR=1u<<5; DMA2_Stream0->NDTR=0;
    assert(receiver_poll(&out)==1 && out!=NULL);
    assert(TIM2->CR1==0 && ADC1->CR2==0 && !(DMA2_Stream0->CR&1));
    for (unsigned bit=0;bit<4;++bit) {
        DMA2->LISR=0; ADC1->SR=0; assert(receiver_start());
        if (bit==3) ADC1->SR=1u<<5;
        else DMA2->LISR=1u<<(bit==0?0:bit+1); /* FE, DME, TE. */
        assert(receiver_poll(&out)==-1);
        assert(TIM2->CR1==0 && !(DMA2_Stream0->CR&1));
    }
    DMA2->LISR=0; assert(receiver_start()); receiver_cancel();
    assert(receiver_poll(&out)==-1);
    puts("Receiver register configuration, completion, errors and cancel passed");
    return 0;
}
