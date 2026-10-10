#include "app_f103.h"
#include <string.h>

static Sample focus;
static uint64_t origin_us;
static bool valid;
enum { FULL_CARRIER, CARRY_HIGH, NEW_PULSE, CARRIER_PATTERNS };
static uint16_t pattern_b[CARRIER_PATTERNS][64], pattern_a[CARRIER_PATTERNS][64];
static bool pattern_nonzero[CARRIER_PATTERNS];
static uint16_t pattern_mask, pattern_idle;
static Sample future_focus[F103_LOOKAHEAD + 1u];
static volatile unsigned future_read, future_write;
static uint64_t prepare_us;
static unsigned future_channel;
static bool future_started;

void f103_wave_reset(void) { valid = false; future_started = false; future_read = future_write = 0; }

bool f103_wave_prepare(const Config *c)
{
    unsigned next = (future_write + 1u) % (F103_LOOKAHEAD + 1u);
    if (!valid || next == future_read) return false;
    Sample *sample = &future_focus[future_write];
    /* Foreground owns the write cursor; IRQ owns the read cursor. Publish a
     * whole sample only after all 16 phases have finished. */
    if (!future_started) {
        float remaining;
        geometry_sample(c, prepare_us, sample, &remaining);
        sample->scan_on = sample->scan_on && remaining >= HAP_FOCUS_US;
        sample->output = sample->scan_on && c->level > 0;
        future_channel = 0;
        future_started = true;
        return true;
    }
    unsigned channel = future_channel;
    sample->phases[channel] = (uint8_t)((phase_solve_channel(sample, channel)
                                           + c->phase_offsets[channel]) & 63u);
    if (++future_channel == HAP_CHANNELS) {
#if defined(__arm__)
        __asm volatile ("dmb" ::: "memory");
#endif
        future_write = next;
        prepare_us += HAP_FOCUS_US;
        future_started = false;
    }
    return true;
}

bool f103_wave_ready(uint64_t us)
{
    uint64_t target = us - (us - origin_us) % HAP_FOCUS_US;
    return valid && (focus.elapsed_us == target ||
                     (future_read != future_write && future_focus[future_read].elapsed_us == target));
}

/* Newlib-nano's large memcpy/memset are byte loops on this target. Small
 * fixed copies compile to word loads/stores without changing aliasing rules. */
static void copy_carrier(uint16_t *out, const uint16_t *pattern)
{
    for (unsigned slot = 0; slot < 64; slot += 8u)
        memcpy(out + slot, pattern + slot, 8u * sizeof(*out));
}

static __attribute__((noinline)) void fill_carrier(uint16_t *out, uint16_t value)
{
    uint32_t pair = (uint32_t)value | ((uint32_t)value << 16u);
    for (unsigned slot = 0; slot < 64; slot += 16u) {
        memcpy(out + slot, &pair, sizeof(pair));
        memcpy(out + slot + 2u, &pair, sizeof(pair));
        memcpy(out + slot + 4u, &pair, sizeof(pair));
        memcpy(out + slot + 6u, &pair, sizeof(pair));
        memcpy(out + slot + 8u, &pair, sizeof(pair));
        memcpy(out + slot + 10u, &pair, sizeof(pair));
        memcpy(out + slot + 12u, &pair, sizeof(pair));
        memcpy(out + slot + 14u, &pair, sizeof(pair));
    }
}

/* A second contains a whole number of 25 us carriers and density periods.
 * Reducing once keeps every product below 1e9 (mod_hz <= 1000), instead of
 * invoking Cortex-M3 software 64-bit division for every carrier. */
static bool drive_in_second(const Config *c, const Sample *s, uint32_t time_us)
{
    uint32_t cycle = time_us / 25u;
    bool level = ((cycle % 100u) * (unsigned)c->level) % 100u + (unsigned)c->level >= 100;
    bool modulation = !c->mod_hz || (time_us * (unsigned)c->mod_hz) % 1000000u < 500000u;
    return s->scan_on && c->level > 0 && level && modulation;
}

bool f103_drive_on(const Config *c, const Sample *s, uint64_t time_us)
{
    return drive_in_second(c, s, (uint32_t)(time_us % 1000000u));
}

/* POINT + full level + no modulation: solve once, repeat one complete carrier.
 * All selected channels keep their own focusing phase and 32 HIGH slots.
 * Unlike the electrical test pair, this does not force all phases to zero. */
void f103_wave_static_focus(const Config *c, uint64_t us, uint16_t *words_b,
                            uint16_t *words_a, uint16_t gpioa_idle, Sample *sample)
{
    float remaining;
    geometry_sample(c, us, sample, &remaining);
    phase_solve_config(c, sample);
    sample->output = sample->scan_on && c->level > 0;
    sample->drive_on = sample->output;
    gpioa_idle &= (uint16_t)~F103_GPIOA_MASK;
    for (unsigned slot = 0; slot < HAP_STEPS; ++slot) {
        uint16_t bits = 0;
        for (unsigned channel = 0; channel < HAP_CHANNELS; ++channel) {
            if (sample->output && (c->channel_mask & (1u << channel)) &&
                ((slot + sample->phases[channel]) & 63u) < 32u)
                bits |= (uint16_t)(1u << channel);
        }
        words_b[slot] = bits & F103_GPIOB_MASK;
        words_a[slot] = gpioa_idle | ((bits & 4u) ? F103_GPIOA_MASK : 0u);
    }
}

void f103_wave_render(const Config *c, uint64_t us, uint16_t *words_b,
                      uint16_t *words_a, uint16_t gpioa_idle, Sample *sample,
                      uint16_t *active_cycles)
{
    if (!valid) origin_us = us;
    uint64_t focus_us = us - (us - origin_us) % HAP_FOCUS_US;
    bool new_focus = !valid || focus.elapsed_us != focus_us;
    if (new_focus) {
        if (future_read != future_write && future_focus[future_read].elapsed_us == focus_us) {
            focus = future_focus[future_read];
            future_read = (future_read + 1u) % (F103_LOOKAHEAD + 1u);
        } else {
            float remaining;
            geometry_sample(c, focus_us, &focus, &remaining);
            focus.scan_on = focus.scan_on && remaining >= HAP_FOCUS_US;
            phase_solve_config(c, &focus);
            focus.output = focus.scan_on && c->level > 0;
            future_read = future_write = 0;
            future_started = false;
            prepare_us = focus_us + HAP_FOCUS_US;
        }
        valid = true;
    }
    *sample = focus;
    gpioa_idle &= (uint16_t)~F103_GPIOA_MASK;
    if (new_focus || pattern_mask != c->channel_mask || pattern_idle != gpioa_idle) {
        uint16_t edges[64] = {0};
        uint16_t bits = 0, carry = 0;
        memset(pattern_nonzero, 0, sizeof(pattern_nonzero));
        for (unsigned i = 0; i < HAP_CHANNELS; ++i) {
            if (!(c->channel_mask & (1u << i))) continue;
            unsigned phase = sample->phases[i];
            edges[(64u-phase)&63u] ^= (uint16_t)(1u << i);
            edges[(96u-phase)&63u] ^= (uint16_t)(1u << i);
            if (phase < 32u) bits |= (uint16_t)(1u << i);
            /* Only phases 1..31 have a HIGH pulse that crosses slot 0.
             * That prefix belongs to the preceding cycle's enable decision. */
            if (phase && phase < 32u) {
                carry |= (uint16_t)(1u << i);
            }
        }
        for (unsigned slot = 0; slot < 64; ++slot) {
            if (slot) {
                bits ^= edges[slot];
                carry &= (uint16_t)~edges[slot];
            }
            uint16_t variants[CARRIER_PATTERNS] = {bits, carry, bits & (uint16_t)~carry};
            for (unsigned kind = 0; kind < CARRIER_PATTERNS; ++kind) {
                uint16_t value = variants[kind];
                pattern_b[kind][slot] = value & F103_GPIOB_MASK;
                pattern_a[kind][slot] = gpioa_idle | ((value & (1u << 2u)) ? F103_GPIOA_MASK : 0u);
                pattern_nonzero[kind] |= value != 0;
            }
        }
        pattern_mask = c->channel_mask;
        pattern_idle = gpioa_idle;
    }
    uint32_t time_us = (uint32_t)(us % 1000000u);
    uint16_t active = 0;
    bool previous_on = false;
    for (unsigned cycle = 0; cycle < F103_HALF_CYCLES; ++cycle) {
        uint16_t *out_b = words_b + cycle*64u;
        uint16_t *out_a = words_a + cycle*64u;
        /* The guard clears old phase/gate state. Thereafter a pulse starts
         * only at that channel's natural rising edge, never by restoring an
         * already-HIGH prefix at the global cycle boundary. Normal density/
         * modulation changes finish HIGH pulses at their natural falling edge.
         * The next guard, STOP and faults may still cut a pulse short. */
        bool on = cycle && drive_in_second(c, sample, time_us);
        if (cycle && (on || previous_on)) {
            unsigned kind = on ? (previous_on ? FULL_CARRIER : NEW_PULSE) : CARRY_HIGH;
            copy_carrier(out_b, pattern_b[kind]);
            copy_carrier(out_a, pattern_a[kind]);
            if (pattern_nonzero[kind]) active |= (uint16_t)(1u << cycle);
        } else {
            fill_carrier(out_b, 0);
            fill_carrier(out_a, gpioa_idle);
        }
        previous_on = on;
        time_us += 25u;
        if (time_us >= 1000000u) time_us -= 1000000u;
    }
    if (active_cycles) *active_cycles = active;
}
