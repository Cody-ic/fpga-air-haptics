#pragma once
#include <stddef.h>
#include <stdint.h>

// Behavioral peripheral model for sampler.cpp, not an electrical simulation.
// A PRS event may convert only while SINGLEQEN is set by SINGLESTART.
namespace hw {
struct Adc {
    uint32_t SINGLEFIFODATA = 0, STATUS = 0;
};
struct Timer {
    uint32_t STATUS = 0, top = 0;
};
struct Gpio {
    uint32_t CDBUSALLOC = 0;
};
extern Adc adc;
extern Timer timer;
extern Gpio gpio;
extern bool immediate, fifo, dma_active, dma_ok;
extern uint32_t flags, sample_base;
extern unsigned count, length;
extern uint32_t *destination;
extern bool (*callback)(unsigned, unsigned, void *);
void periods(unsigned n);
} // namespace hw
#define IADC0 (&hw::adc)
#define TIMER0 (&hw::timer)
#define GPIO (&hw::gpio)
enum { D0, D2 = 2, INPUT_PULLDOWN, RISING };
enum {
    cmuClock_GPIO,
    cmuClock_PRS,
    cmuClock_TIMER0,
    cmuClock_IADCCLK,
    cmuSelect_EM01GRPACLK,
    cmuClock_IADC0,
    gpioPortC,
    gpioModeDisabled,
    iadcWarmupKeepWarm,
    iadcCfgReferenceInt1V2,
    iadcCfgAnalogGain1x,
    iadcCfgModeNormal,
    iadcFifoCfgDvl1,
    iadcTriggerSelImmediate,
    iadcTriggerActionOnce,
    iadcPosInputPortCPin0,
    prsTypeSync,
    prsSignalTIMER0_OF,
    prsConsumerIADC0_SINGLETRIGGER,
    iadcTriggerSelPrs0SameClk,
    dmadrvPeripheralSignal_IADC0_IADC_SINGLE,
    dmadrvDataSize4,
    iadcCmdStartSingle,
    iadcCmdStopSingle
};
constexpr uint32_t GPIO_CDBUSALLOC_CDEVEN0_ADC0 = 1;
constexpr uint32_t TIMER_STATUS_RUNNING = 1, IADC_STATUS_SINGLEQEN = 1;
constexpr uint32_t IADC_IF_SINGLEFIFOOF = 2, _IADC_IF_MASK = 0xffffffff;
using Ecode_t = int;
constexpr Ecode_t ECODE_OK = 0, ECODE_EMDRV_DMADRV_ALREADY_INITIALIZED = 3;
struct IADC_Init_t {
    int warmup = 0;
    uint32_t srcClkPrescale = 0;
};
struct IADC_Config {
    int reference = 0, vRef = 0, analogGain = 0;
    uint32_t adcClkPrescale = 0;
};
struct IADC_AllConfigs_t {
    IADC_Config configs[1];
};
struct IADC_InitSingle_t {
    int dataValidLevel = 0, triggerSelect = iadcTriggerSelImmediate, triggerAction = 0;
    bool fifoDmaWakeup = false;
};
struct IADC_SingleInput_t {
    int posInput = 0;
};
struct TIMER_Init_TypeDef {
    bool enable = false;
};
#define IADC_INIT_DEFAULT                                                                          \
    {                                                                                              \
    }
#define IADC_ALLCONFIGS_DEFAULT                                                                    \
    {                                                                                              \
    }
#define IADC_INITSINGLE_DEFAULT                                                                    \
    {                                                                                              \
    }
#define IADC_SINGLEINPUT_DEFAULT                                                                   \
    {                                                                                              \
    }
#define TIMER_INIT_DEFAULT                                                                         \
    {                                                                                              \
    }
inline void noInterrupts() {}
inline void interrupts() {}
inline void pinMode(int, int) {}
inline int digitalPinToInterrupt(int pin) { return pin; }
inline void attachInterrupt(int, void (*)(), int) {}
inline uint32_t micros() {
    static uint32_t us = 0;
    return us += 10;
}
inline void CMU_ClockEnable(int, bool) {}
inline void CMU_ClockSelectSet(int, int) {}
inline uint32_t CMU_ClockFreqGet(int) { return 39000000; }
inline void GPIO_PinModeSet(int, int, int, int) {}
inline uint32_t IADC_calcSrcClkPrescale(hw::Adc *, uint32_t, int) { return 1; }
inline uint32_t IADC_calcAdcClkPrescale(hw::Adc *, uint32_t, int, int, uint32_t) { return 1; }
inline void IADC_init(hw::Adc *, const IADC_Init_t *, const IADC_AllConfigs_t *) {}
inline void IADC_initSingle(hw::Adc *adc, const IADC_InitSingle_t *single,
                            const IADC_SingleInput_t *) {
    hw::immediate = single->triggerSelect == iadcTriggerSelImmediate;
    adc->STATUS = 0;
}
inline void IADC_command(hw::Adc *adc, int cmd) {
    if (cmd == iadcCmdStopSingle)
        adc->STATUS = 0;
    else {
        adc->STATUS = IADC_STATUS_SINGLEQEN;
        if (hw::immediate)
            hw::fifo = true;
    }
}
inline unsigned IADC_getSingleFifoCnt(hw::Adc *) { return hw::fifo ? 1 : 0; }
inline uint32_t IADC_pullSingleFifoData(hw::Adc *) {
    hw::fifo = false;
    return 0;
}
inline void IADC_clearInt(hw::Adc *, uint32_t) { hw::flags = 0; }
inline uint32_t IADC_getInt(hw::Adc *) { return hw::flags; }
inline int PRS_GetFreeChannel(int) { return 0; }
inline void PRS_ConnectSignal(unsigned, int, int) {}
inline void PRS_ConnectConsumer(unsigned, int, int) {}
inline void TIMER_Enable(hw::Timer *t, bool on) { t->STATUS = on ? TIMER_STATUS_RUNNING : 0; }
inline void TIMER_Init(hw::Timer *t, const TIMER_Init_TypeDef *i) { TIMER_Enable(t, i->enable); }
inline void TIMER_TopSet(hw::Timer *t, uint32_t top) { t->top = top; }
inline void TIMER_CounterSet(hw::Timer *, int) {}
inline Ecode_t DMADRV_Init() { return ECODE_OK; }
inline Ecode_t DMADRV_AllocateChannel(unsigned *channel, void *) {
    *channel = 0;
    return ECODE_OK;
}
inline void DMADRV_StopTransfer(unsigned) { hw::dma_active = false; }
inline Ecode_t DMADRV_PeripheralMemory(unsigned, int, void *dst, void *, bool, unsigned n, int,
                                       bool (*cb)(unsigned, unsigned, void *), void *) {
    if (!hw::dma_ok)
        return 1;
    hw::destination = static_cast<uint32_t *>(dst);
    hw::count = 0;
    hw::length = n;
    hw::callback = cb;
    hw::dma_active = true;
    return ECODE_OK;
}
