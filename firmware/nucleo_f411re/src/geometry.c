#include "haptics.h"
#include "array_geometry.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

static float distance(Point a, Point b)
{
    float dx = (float)a.x - b.x, dy = (float)a.y - b.y;
    return sqrtf(dx * dx + dy * dy);
}

void config_default(Config *c)
{
    memset(c, 0, sizeof(*c));
    c->channel_mask = 0xffff;
    c->carrier_hz = 40000; c->phase_steps = 64;
    c->z_um = 150000; c->radius_um = 20000;
    c->repeat_millihz = 500; c->mod_hz = 200; c->level = 30;
    c->path_closed = 1; c->blank_us = 2000; c->shape = CIRCLE;
    strcpy(c->path_xy_um, "NONE"); strcpy(c->scan_paths, "NONE");
}

static bool coordinate(const char **input, int32_t *value)
{
    const char *s = *input;
    bool negative = *s == '-';
    if (negative) ++s;
    unsigned count = 0;
    int32_t v = 0;
    while (*s >= '0' && *s <= '9') {
        if (++count > 6) return false;
        v = v * 10 + (*s++ - '0');
    }
    if (!count || v > 300000) return false;
    *value = negative ? -v : v; *input = s;
    return true;
}

const char *config_compile(Config *c)
{
    bool legacy = strcmp(c->path_xy_um, "NONE") != 0;
    c->multi = strcmp(c->scan_paths, "NONE") != 0;
    c->strokes = 0; c->total_weight = 0;
    if ((legacy && c->multi) || (c->shape == CUSTOM && !legacy && !c->multi))
        return "BAD_CONFIG";
    const char *s = c->multi ? c->scan_paths : c->path_xy_um;
    unsigned n = 0;
    if (legacy || c->multi) {
        do {
            unsigned k = c->strokes++;
            if (k >= HAP_STROKES) return "BAD_CONFIG";
            c->start[k] = (uint16_t)n; c->length[k] = 0;
            do {
                if (n >= (c->multi ? HAP_POINTS : 64)) return "BAD_CONFIG";
                Point *p = &c->points[n];
                if (!coordinate(&s, &p->x) || *s++ != ':' || !coordinate(&s, &p->y))
                    return "BAD_CONFIG";
                if (n > c->start[k]) {
                    float len = distance(*p, c->points[n-1]);
                    if (len == 0) return "BAD_CONFIG";
                    c->length[k] += len;
                }
                ++n;
                if (*s != ',') break;
                ++s;
            } while (true);
            c->count[k] = (uint16_t)(n - c->start[k]);
            c->total_weight += fmaxf(100.0f, c->length[k]);
            if (*s != '|' || !c->multi) break;
            ++s;
        } while (true);
        if (*s) return "BAD_CONFIG";
        if (legacy && c->path_closed && n > 1 && distance(c->points[0], c->points[n-1]) == 0)
            return "BAD_CONFIG";
    }
    if (c->shape == CUSTOM && c->multi) {
        double active = 1e9 / c->repeat_millihz - c->strokes * c->blank_us;
        if (active <= 0) return "BAD_CONFIG";
        /* A stroke needs at least two 250 us focus slots. Never silently lose it. */
        for (unsigned i = 0; i < c->strokes; ++i)
            if (active * fmaxf(100, c->length[i]) / c->total_weight < 500)
                return "SCAN_TOO_FAST";
    }
    float lo_x = 0, hi_x = 0, lo_y = 0, hi_y = 0;
    float r = (float)c->radius_um;
    if (c->shape == CUSTOM) {
        lo_x = hi_x = (float)c->points[0].x;
        lo_y = hi_y = (float)c->points[0].y;
        for (unsigned i = 1; i < n; ++i) {
            lo_x = fminf(lo_x, (float)c->points[i].x); hi_x = fmaxf(hi_x, (float)c->points[i].x);
            lo_y = fminf(lo_y, (float)c->points[i].y); hi_y = fmaxf(hi_y, (float)c->points[i].y);
        }
    } else if (c->shape != POINT) {
        hi_x = c->shape == LINE_Y ? 0 : (c->shape == TRIANGLE ? .866f*r : r);
        hi_y = c->shape == LINE_X ? 0 : (c->shape == ARROW ? .7f*r : r);
        lo_x = -hi_x; lo_y = c->shape == TRIANGLE ? -.5f*r : -hi_y;
    }
    if (lo_x+c->cx_um < -100000 || hi_x+c->cx_um > 100000 ||
        lo_y+c->cy_um < -100000 || hi_y+c->cy_um > 100000)
        return "OUT_OF_WORKSPACE";
    return NULL;
}

static void polyline(const Point *p, unsigned count, float target, float *x, float *y)
{
    *x = (float)p[0].x; *y = (float)p[0].y;
    for (unsigned i = 1; i < count; ++i) {
        float length = distance(p[i-1], p[i]);
        if (length > 0 && target <= length) {
            float f = target / length;
            *x = p[i-1].x + (p[i].x-p[i-1].x)*f;
            *y = p[i-1].y + (p[i].y-p[i-1].y)*f;
            return;
        }
        target -= length;
        *x = (float)p[i].x; *y = (float)p[i].y;
    }
}

void geometry_sample(const Config *c, uint64_t us, Sample *out, float *remaining_us)
{
    /* Integer modulo avoids losing sub-ms precision after long runs. */
    uint64_t phase = ((us % 1000000000u) * (uint32_t)c->repeat_millihz) % 1000000000u;
    float fraction = (float)phase / 1e9f;
    float x = 0, y = 0;
    out->elapsed_us = us; out->scan_on = true; out->stroke = 0; out->output = false; out->drive_on = false;
    *remaining_us = 1e30f;
    if (c->shape == CUSTOM && c->multi) {
        float offset = (float)phase / c->repeat_millihz;
        float active = 1e9f/c->repeat_millihz - c->strokes*c->blank_us;
        for (unsigned i = 0; i < c->strokes; ++i) {
            const Point *p = &c->points[c->start[i]];
            float duration = active * fmaxf(100, c->length[i]) / c->total_weight;
            out->stroke = (uint8_t)i;
            if (offset < duration) {
                polyline(p, c->count[i], offset/duration*c->length[i], &x, &y);
                *remaining_us = duration - offset;
                break;
            }
            offset -= duration;
            if (offset < c->blank_us) {
                Point last = p[c->count[i]-1], next = c->points[c->start[(i+1)%c->strokes]];
                float f = offset/c->blank_us;
                x = last.x+(next.x-last.x)*f; y = last.y+(next.y-last.y)*f;
                out->scan_on = false;
                break;
            }
            offset -= c->blank_us;
        }
    } else if (c->shape == CUSTOM) {
        unsigned count = c->count[0];
        float length = c->length[0];
        float closing = distance(c->points[count-1], c->points[0]);
        float d = fraction * (c->path_closed ? length+closing : 2*length);
        if (!c->path_closed && d > length) d = 2*length-d;
        if (d <= length || !closing) polyline(c->points, count, d, &x, &y);
        else {
            float f = (d-length)/closing;
            x = c->points[count-1].x + (c->points[0].x-c->points[count-1].x)*f;
            y = c->points[count-1].y + (c->points[0].y-c->points[count-1].y)*f;
        }
    } else if (c->shape == CIRCLE) {
        x = c->radius_um * cosf(6.28318530718f*fraction);
        y = c->radius_um * sinf(6.28318530718f*fraction);
    } else if (c->shape == LINE_X || c->shape == LINE_Y) {
        float v = c->radius_um*(1-4*fabsf(fraction-.5f));
        if (c->shape == LINE_X) x = v; else y = v;
    } else if (c->shape != POINT) {
        static const Point square[] = {{-1000,-1000},{1000,-1000},{1000,1000},{-1000,1000},{-1000,-1000}};
        static const Point triangle[] = {{0,1000},{-866,-500},{866,-500},{0,1000}};
        static const Point arrow[] = {{-1000,0},{1000,0},{300,700},{1000,0},{300,-700},{1000,0},{-1000,0}};
        const Point *p = c->shape == SQUARE ? square : (c->shape == TRIANGLE ? triangle : arrow);
        unsigned count = c->shape == SQUARE ? 5 : (c->shape == TRIANGLE ? 4 : 7);
        float length = 0;
        for (unsigned i = 1; i < count; ++i) length += distance(p[i-1], p[i]);
        polyline(p, count, fraction*length, &x, &y);
        x *= c->radius_um/1000.0f; y *= c->radius_um/1000.0f;
    }
    out->x_um = c->cx_um + (int32_t)lroundf(x);
    out->y_um = c->cy_um + (int32_t)lroundf(y); out->z_um = c->z_um;
}

void phase_solve(Sample *s)
{
    for (unsigned i = 0; i < HAP_CHANNELS; ++i) {
        float dx = s->x_um/1000.0f - array_elements[i][0]/1000000.0f;
        float dy = s->y_um/1000.0f - array_elements[i][1]/1000000.0f;
        float z = s->z_um/1000.0f - array_elements[i][2]/1000000.0f;
        float cycles = -sqrtf(dx*dx+dy*dy+z*z)*(40000.0f/ARRAY_SOUND_MM_S);
        float fraction = cycles-floorf(cycles);
        s->phases[i] = (uint8_t)((unsigned)(fraction*64+.5f)&63);
    }
}

void phase_solve_config(const Config *c, Sample *s)
{
    phase_solve(s);
    for (unsigned i = 0; i < HAP_CHANNELS; ++i)
        s->phases[i] = (uint8_t)((s->phases[i]+c->phase_offsets[i])&63u);
}

void wave_render(const Config *c, uint64_t elapsed_us, WaveBlock *block)
{
    static const uint8_t pins_b[8] = {0,1,2,4,5,6,7,8};
    uint32_t pattern_b[64], pattern_c[64];
    Sample sample = {0};
    for (unsigned cycle = 0; cycle < HAP_CYCLES; ++cycle) {
        uint64_t time = elapsed_us+cycle*25u;
        if (!(cycle%10)) {
            float remaining;
            geometry_sample(c, time, &sample, &remaining);
            /* Blank the complete slot if it could straddle a transfer. */
            sample.scan_on = sample.scan_on && remaining >= 250;
            phase_solve_config(c, &sample);
            /* Two edges per channel; avoid a 64 x 16 inner loop at 4 kHz. */
            uint16_t edges_b[64] = {0}, edges_c[64] = {0};
            unsigned b = 0, cc = 0;
            for (unsigned i = 0; i < 16; ++i) {
                if (!(c->channel_mask & (1u<<i))) continue;
                unsigned phase = sample.phases[i], rise = (64-phase)&63, fall = (96-phase)&63;
                unsigned bit = 1u << (i < 8 ? pins_b[i] : i-8);
                uint16_t *edges = i < 8 ? edges_b : edges_c;
                edges[rise] ^= (uint16_t)bit; edges[fall] ^= (uint16_t)bit;
                if (phase < 32) { if (i < 8) b |= bit; else cc |= bit; }
            }
            for (unsigned slot = 0; slot < 64; ++slot) {
                if (slot) { b ^= edges_b[slot]; cc ^= edges_c[slot]; }
                pattern_b[slot] = b | ((HAP_MASK_B^b)<<16);
                pattern_c[slot] = cc | ((HAP_MASK_C^cc)<<16);
            }
        }
        uint64_t carrier_index = time/25u;
        /* Pulse density is a drive-level control, not calibrated acoustic amplitude. */
        bool level_on = ((carrier_index%100u)*(unsigned)c->level)%100u + (unsigned)c->level >= 100;
        bool mod_on = !c->mod_hz || ((time%1000000u)*(unsigned)c->mod_hz)%1000000u < 500000u;
        sample.output = sample.scan_on && c->level > 0;
        sample.drive_on = sample.output && level_on && mod_on;
        /* Retain the focus slot's timestamp: PAUSE/START must resume this exact
           latched position, not a later unsolved point inside the carrier group. */
        block->samples[cycle] = sample;
        for (unsigned slot = 0; slot < 64; ++slot) {
            unsigned index = cycle*64+slot;
            block->b[index] = sample.drive_on ? pattern_b[slot] : HAP_MASK_B<<16;
            block->c[index] = sample.drive_on ? pattern_c[slot] : HAP_MASK_C<<16;
        }
    }
}
