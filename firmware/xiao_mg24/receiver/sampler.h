#pragma once
#include "receiver_core.h"
#include <Arduino.h>
#include "em_cmu.h"
#include "em_iadc.h"
#include "em_gpio.h"
#include "dmadrv.h"

namespace mg24 {
// Single foreground owner; the DMA callback only stops hardware and sets flags.
class HardwareSampler final : public Sampler {
public:
    bool begin() {
        CMU_ClockEnable(cmuClock_GPIO, true);
        CMU_ClockEnable(cmuClock_IADC0, true);
        CMU_ClockSelectSet(cmuClock_IADCCLK, cmuSelect_FSRCO);
        GPIO_PinModeSet(gpioPortC, 0, gpioModeDisabled, 0);
        GPIO->CDBUSALLOC |= GPIO_CDBUSALLOC_CDEVEN0_ADC0;
        IADC_reset(IADC0);
        IADC_Init_t init = IADC_INIT_DEFAULT;
        IADC_AllConfigs_t configs = IADC_ALLCONFIGS_DEFAULT;
        init.warmup = iadcWarmupKeepWarm;
        init.srcClkPrescale = IADC_calcSrcClkPrescale(IADC0, 20000000, 0);
        auto &cfg = configs.configs[0];
        cfg.reference = iadcCfgReferenceVddx;
        cfg.vRef = VREF_MV;
        cfg.analogGain = iadcCfgAnalogGain1x;
        cfg.osrHighSpeed = iadcCfgOsrHighSpeed2x;
        cfg.adcClkPrescale = IADC_calcAdcClkPrescale(IADC0, 10000000, 0,
                                                   iadcCfgModeNormal, init.srcClkPrescale);
        // IADC_TIMER counts CLK_SRC_ADC, before the separate conversion-clock
        // prescaler (EFR32xG24 reference manual, IADC_TIMER). Dividing by
        // adcClkPrescale here would double the real trigger rate on this board.
        const uint32_t timer_clock = CMU_ClockFreqGet(cmuClock_IADCCLK)
            / (init.srcClkPrescale + 1);
        const uint32_t timer_cycles = (timer_clock + TARGET_SAMPLE_HZ / 2) / TARGET_SAMPLE_HZ;
        if (timer_cycles < 12 || timer_cycles > 65535) return false;
        init.timerCycles = timer_cycles;
        sample_hz_ = static_cast<double>(timer_clock) / timer_cycles;
        IADC_init(IADC0, &init, &configs);
        IADC_InitSingle_t single = IADC_INITSINGLE_DEFAULT;
        single.triggerSelect = iadcTriggerSelTimer;
        single.triggerAction = iadcTriggerActionOnce;
        single.dataValidLevel = iadcFifoCfgDvl1;
        single.fifoDmaWakeup = true;
        IADC_SingleInput_t input = IADC_SINGLEINPUT_DEFAULT;
        input.posInput = iadcPosInputPortCPin0;
        input.negInput = iadcNegInputGnd;
        IADC_initSingle(IADC0, &single, &input);
        DMADRV_Init();
        initialized_ = DMADRV_AllocateChannel(&channel_, nullptr) == ECODE_EMDRV_DMADRV_OK;
        return initialized_;
    }
    bool start() override {
        if (!initialized_) return false;
        stop();
        const uint32_t stopping = micros();
        while (IADC0->STATUS & (IADC_STATUS_TIMERACTIVE | IADC_STATUS_SINGLEQEN
                               | IADC_STATUS_CONVERTING | IADC_STATUS_SINGLEWRITEPENDING)) {
            if (uint32_t(micros() - stopping) > 1000) return false;
        }
        while (IADC_getSingleFifoCnt(IADC0)) IADC_pullSingleFifoResult(IADC0);
        IADC_clearInt(IADC0, _IADC_IF_MASK);
        done_ = false;
        start_low_ = micros();
        capture_epoch_ = next_epoch_ + uint32_t(start_low_ - uint32_t(next_epoch_));
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
        descriptor_ = (LDMA_Descriptor_t)LDMA_DESCRIPTOR_LINKREL_P2M_WORD(&IADC0->SINGLEFIFODATA, dma_, SAMPLE_COUNT, 0);
#pragma GCC diagnostic pop
        descriptor_.xfer.link = 0; // Finite window: never link back and overwrite completed samples.
        LDMA_TransferCfg_t transfer = LDMA_TRANSFER_CFG_PERIPHERAL(ldmaPeripheralSignal_IADC0_IADC_SINGLE);
        if (DMADRV_LdmaStartTransfer(channel_, &transfer, &descriptor_, complete, this)
            != ECODE_EMDRV_DMADRV_OK) return false;
        IADC_command(IADC0, iadcCmdStartSingle);
        IADC_command(IADC0, iadcCmdEnableTimer);
        return true;
    }
    CaptureState poll() override {
        if (IADC_getInt(IADC0) & IADC_IF_SINGLEFIFOOF) return CaptureState::Overflow;
        if (IADC_getInt(IADC0) & (IADC_IF_POLARITYERR | IADC_IF_PORTALLOCERR | IADC_IF_EM23ABORTERROR))
            return CaptureState::Failed;
        if (!done_) return CaptureState::Running;
        __DMB();
        for (unsigned i = 0; i < SAMPLE_COUNT; ++i) samples_[i] = dma_[i] & ADC_MAX;
        return CaptureState::Done;
    }
    void stop() override {
        if (!initialized_) return;
        IADC_command(IADC0, iadcCmdDisableTimer);
        IADC_command(IADC0, iadcCmdStopSingle);
        DMADRV_StopTransfer(channel_);
    }
    const uint16_t *samples() const override { return samples_; }
    double sample_hz() const override { return sample_hz_; }
    uint64_t started_us() const override { return capture_epoch_; }
    uint64_t completed_us() const override { return capture_epoch_ + uint32_t(done_low_ - start_low_); }
    void set_epoch(uint64_t now) { next_epoch_ = now; }
private:
    bool initialized_ = false;
    unsigned channel_ = 0;
    volatile bool done_ = false;
    volatile uint32_t done_low_ = 0;
    uint32_t start_low_ = 0;
    uint64_t capture_epoch_ = 0;
    uint64_t next_epoch_ = 0;
    double sample_hz_ = 0;
    uint32_t dma_[SAMPLE_COUNT] = {};
    uint16_t samples_[SAMPLE_COUNT] = {};
    LDMA_Descriptor_t descriptor_ = {};
    static bool complete(unsigned, unsigned, void *context) {
        auto *self = static_cast<HardwareSampler *>(context);
        IADC_command(IADC0, iadcCmdDisableTimer);
        IADC_command(IADC0, iadcCmdStopSingle);
        self->done_low_ = micros();
        self->done_ = true;
        return false;
    }
};
}
