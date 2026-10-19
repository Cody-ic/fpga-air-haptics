#include "sampler.h"
#include "sampler_stubs/hardware.h"
#include <assert.h>
#include <stdio.h>
namespace hw {
Adc adc;
Timer timer;
Gpio gpio;
bool immediate = true, fifo = false, dma_active = false, dma_ok = true;
uint32_t flags = 0, sample_base = 500;
unsigned count = 0, length = 0;
uint32_t *destination;
bool (*callback)(unsigned, unsigned, void *);
void periods(unsigned n) {
    for (unsigned i = 0; i < n; i++) {
        if (!(timer.STATUS & TIMER_STATUS_RUNNING))
            continue;
        if (!(adc.STATUS & IADC_STATUS_SINGLEQEN))
            continue;
        if (!dma_active) {
            flags |= IADC_IF_SINGLEFIFOOF;
            continue;
        }
        destination[count] = sample_base + count;
        if (++count == length) {
            dma_active = false;
            callback(0, 0, nullptr);
        }
    }
}
} // namespace hw
int main() {
    uint16_t data[200];
    uint32_t token = 0, fs = 0;
    bool failed = false;
    assert(sampler_init());
    assert(hw::timer.top == 97); // 39 MHz / 98 is reported, not fabricated 400 kHz.
    assert(sampler_arm(1));
    assert(hw::adc.STATUS & IADC_STATUS_SINGLEQEN);
    hw::periods(200);
    assert(!sampler_take(token, data, fs, failed)); // Still waiting for the external trigger.
    sampler_trigger();
    hw::periods(199);
    assert(!sampler_take(token, data, fs, failed));
    hw::periods(1);
    assert(sampler_take(token, data, fs, failed));
    assert(token == 1 && fs == 397959 && !failed);
    assert(data[0] == 500 && data[199] == 699);
    assert(!hw::timer.STATUS && !sampler_take(token, data, fs, failed));
    assert(sampler_arm(2));
    sampler_trigger();
    hw::periods(37);
    sampler_cancel();
    hw::periods(400);
    assert(!sampler_take(token, data, fs, failed));
    assert(!hw::dma_active && !hw::adc.STATUS && !hw::timer.STATUS);
    hw::sample_base = 2000;
    assert(sampler_arm(3));
    sampler_trigger();
    hw::periods(200);
    assert(sampler_take(token, data, fs, failed));
    assert(token == 3 && data[0] == 2000 && data[199] == 2199 && !failed);
    hw::dma_ok = false;
    assert(!sampler_arm(4));
    assert(!hw::adc.STATUS && !hw::timer.STATUS);
    hw::dma_ok = true;
    assert(sampler_arm(5));
    sampler_trigger();
    hw::flags |= IADC_IF_SINGLEFIFOOF;
    hw::periods(200);
    assert(sampler_take(token, data, fs, failed) && token == 5 && failed);
    puts("MG24 sampler lifecycle passed (queue enable, trigger, exact window, cancel/rearm, DMA "
         "failure, overflow)");
}
