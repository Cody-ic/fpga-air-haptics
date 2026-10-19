#include "sampler.h"
#include "config.h"
#include "dmadrv.h"
#include "em_cmu.h"
#include "em_gpio.h"
#include "em_iadc.h"
#include "em_prs.h"
#include "em_timer.h"
#include <Arduino.h>

namespace {
bool initialized = false;
unsigned dma_channel;
volatile bool armed = false, completed = false;
volatile uint32_t active_token = 0;
uint32_t words[HAPTICS_CAPTURE_SAMPLES];
uint32_t sampling_hz;
bool dma_done(unsigned, unsigned, void *) {
    TIMER_Enable(TIMER0, false);
    completed = true;
    return false;
}
void drain() {
    while (IADC_getSingleFifoCnt(IADC0))
        IADC_pullSingleFifoData(IADC0);
    IADC_clearInt(IADC0, _IADC_IF_MASK);
}
} // namespace
bool sampler_init() {
    // D0/PC00, TIMER0, one PRS channel and an allocated DMA channel are owned here.
    CMU_ClockEnable(cmuClock_GPIO, true);
    CMU_ClockEnable(cmuClock_PRS, true);
    CMU_ClockEnable(cmuClock_TIMER0, true);
    CMU_ClockSelectSet(cmuClock_IADCCLK, cmuSelect_EM01GRPACLK);
    CMU_ClockEnable(cmuClock_IADC0, true);
    GPIO_PinModeSet(gpioPortC, 0, gpioModeDisabled, 0);
    GPIO->CDBUSALLOC |= GPIO_CDBUSALLOC_CDEVEN0_ADC0;
    IADC_Init_t init = IADC_INIT_DEFAULT;
    IADC_AllConfigs_t configs = IADC_ALLCONFIGS_DEFAULT;
    init.warmup = iadcWarmupKeepWarm;
    init.srcClkPrescale = IADC_calcSrcClkPrescale(IADC0, 20000000, 0);
    configs.configs[0].reference = iadcCfgReferenceInt1V2;
    configs.configs[0].vRef = HAPTICS_ADC_FULL_SCALE_MV;
    configs.configs[0].analogGain = iadcCfgAnalogGain1x;
    configs.configs[0].adcClkPrescale =
        IADC_calcAdcClkPrescale(IADC0, 10000000, 0, iadcCfgModeNormal, init.srcClkPrescale);
    IADC_init(IADC0, &init, &configs);
    IADC_InitSingle_t single = IADC_INITSINGLE_DEFAULT;
    IADC_SingleInput_t input = IADC_SINGLEINPUT_DEFAULT;
    single.dataValidLevel = iadcFifoCfgDvl1;
    single.fifoDmaWakeup = true;
    single.triggerSelect = iadcTriggerSelImmediate;
    single.triggerAction = iadcTriggerActionOnce;
    input.posInput = iadcPosInputPortCPin0;
    IADC_initSingle(IADC0, &single, &input);
    // Share the driver's allocator with the serial drivers; never reset LDMA globally.
    Ecode_t result = DMADRV_Init();
    if (result != ECODE_OK && result != ECODE_EMDRV_DMADRV_ALREADY_INITIALIZED)
        return false;
    if (DMADRV_AllocateChannel(&dma_channel, nullptr) != ECODE_OK)
        return false;
    int prs_channel = PRS_GetFreeChannel(prsTypeSync);
    if (prs_channel < 0)
        return false;
    PRS_ConnectSignal(unsigned(prs_channel), prsTypeSync, prsSignalTIMER0_OF);
    PRS_ConnectConsumer(unsigned(prs_channel), prsTypeSync, prsConsumerIADC0_SINGLETRIGGER);
    TIMER_Init_TypeDef timer = TIMER_INIT_DEFAULT;
    timer.enable = false;
    TIMER_Init(TIMER0, &timer);
    uint32_t clock = CMU_ClockFreqGet(cmuClock_TIMER0);
    uint32_t period = (clock + HAPTICS_CAPTURE_TARGET_HZ / 2) / HAPTICS_CAPTURE_TARGET_HZ;
    if (period < 2)
        return false;
    TIMER_TopSet(TIMER0, period - 1);
    sampling_hz = (clock + period / 2) / period;
    if (sampling_hz < 200000 || sampling_hz > 1000000)
        return false;
    // Prime the analog front end once before accepting captures.
    IADC_command(IADC0, iadcCmdStartSingle);
    uint32_t start = micros();
    while (!IADC_getSingleFifoCnt(IADC0))
        if (uint32_t(micros() - start) > 2000)
            return false;
    drain();
    single.triggerSelect = iadcTriggerSelPrs0SameClk;
    IADC_initSingle(IADC0, &single, &input);
    pinMode(HAPTICS_TRIGGER_PIN, INPUT_PULLDOWN);
    attachInterrupt(digitalPinToInterrupt(HAPTICS_TRIGGER_PIN), sampler_trigger, RISING);
    initialized = true;
    return true;
}
void sampler_cancel() {
    if (!initialized)
        return;
    noInterrupts();
    armed = false;
    completed = false;
    TIMER_Enable(TIMER0, false);
    interrupts();
    DMADRV_StopTransfer(dma_channel);
    IADC_command(IADC0, iadcCmdStopSingle);
    drain();
}
bool sampler_arm(uint32_t token) {
    if (!initialized || armed)
        return false;
    sampler_cancel();
    active_token = token;
    Ecode_t e =
        DMADRV_PeripheralMemory(dma_channel, dmadrvPeripheralSignal_IADC0_IADC_SINGLE, words,
                                (void *)&IADC0->SINGLEFIFODATA, true, HAPTICS_CAPTURE_SAMPLES,
                                dmadrvDataSize4, dma_done, nullptr);
    if (e != ECODE_OK)
        return false;
    // SINGLESTOP disables the queue even with PRS selected. Re-enable it for
    // every window, after DMA is ready and before accepting the external trigger.
    // With the PRS trigger this arms the queue; it does not sample immediately.
    IADC_command(IADC0, iadcCmdStartSingle);
    armed = true;
    return true;
}
void sampler_trigger() {
    if (!armed || completed || (TIMER0->STATUS & TIMER_STATUS_RUNNING))
        return;
    // Interrupt latency shifts the first sample; intervals thereafter are hardware timed.
    // This is not a shared-clock phase measurement.
    TIMER_CounterSet(TIMER0, 0);
    TIMER_Enable(TIMER0, true);
}
bool sampler_take(uint32_t &token, uint16_t *samples, uint32_t &fs, bool &failed) {
    if (!completed)
        return false;
    noInterrupts();
    token = active_token;
    completed = false;
    armed = false;
    interrupts();
    failed = (IADC_getInt(IADC0) & IADC_IF_SINGLEFIFOOF) != 0;
    for (size_t i = 0; i < HAPTICS_CAPTURE_SAMPLES; i++)
        samples[i] = uint16_t(words[i] & 0xfffu);
    fs = sampling_hz;
    return true;
}
