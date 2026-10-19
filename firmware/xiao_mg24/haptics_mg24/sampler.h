#pragma once
#include <stdint.h>
bool sampler_init();
bool sampler_arm(uint32_t token);
void sampler_cancel();
void sampler_trigger();
// Called only from loop(), never publishes protocol data from an interrupt.
bool sampler_take(uint32_t &token, uint16_t *samples, uint32_t &fs_hz, bool &failed);
