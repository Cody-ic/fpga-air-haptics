#ifndef HAPTICS_H
#define HAPTICS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifndef HAP_LINE
#define HAP_LINE 8192
#endif
#ifndef HAP_POINTS
#define HAP_POINTS 256
#endif
#ifndef HAP_STROKES
#define HAP_STROKES 32
#endif
#ifndef HAP_PATH_BYTES
#define HAP_PATH_BYTES 1025
#endif
#ifndef HAP_SCAN_BYTES
#define HAP_SCAN_BYTES 4097
#endif
#ifndef HAP_FOCUS_US
#define HAP_FOCUS_US 250
#endif
#ifndef HAP_DEVICE_NAME
#define HAP_DEVICE_NAME "NUCLEO-F411RE"
#endif
#define HAP_CHANNELS 16
#define HAP_STEPS 64
#define HAP_CYCLES 40 /* One millisecond, with four focus updates. */
#define HAP_WORDS (HAP_CYCLES * HAP_STEPS)
#define HAP_MASK_B 0x01f7u
#define HAP_MASK_C 0x00ffu
#define HAP_ADC_SAMPLES 200
#define HAP_ADC_HZ 400000

typedef enum { POINT, LINE_X, LINE_Y, CIRCLE, SQUARE, TRIANGLE, ARROW, CUSTOM } Shape;
typedef enum { IDLE, RUNNING, PAUSED, FAULT } State;
typedef struct { int32_t x, y; } Point;
typedef struct {
    int32_t carrier_hz, phase_steps, cx_um, cy_um, z_um, radius_um;
    int32_t repeat_millihz, mod_hz, level, path_closed, blank_us;
    Shape shape;
    char path_xy_um[HAP_PATH_BYTES], scan_paths[HAP_SCAN_BYTES];
    Point points[HAP_POINTS];
    uint16_t start[HAP_STROKES], count[HAP_STROKES], strokes;
    float length[HAP_STROKES], total_weight;
    bool multi;
    uint16_t channel_mask;
    uint8_t phase_offsets[HAP_CHANNELS];
#ifdef HAP_CACHED_SEGMENTS
    float segment_length[HAP_POINTS], closing_length;
#endif
} Config;

typedef struct {
    uint64_t elapsed_us;
    int32_t x_um, y_um, z_um;
    uint8_t phases[HAP_CHANNELS], stroke;
    bool scan_on, output, drive_on;
} Sample;

typedef struct {
    uint32_t b[HAP_WORDS], c[HAP_WORDS];
    Sample samples[HAP_CYCLES];
} WaveBlock;

typedef struct {
    /* All callbacks run in the foreground. stop must synchronously force LOW. */
    bool (*start)(const Config *, uint64_t elapsed_us);
    void (*stop)(void);
    bool (*readback)(Sample *);
    void (*send)(const char *, size_t);
    void (*service)(void); /* Maintain DMA during long CRC/serialization work. */
    bool (*capture_start)(void);
    int (*capture_poll)(const uint16_t **); /* 0 pending, 1 complete, -1 failed. */
    void (*capture_cancel)(void);
} Hardware;

typedef struct {
    Config config;
    Hardware hw;
    State state;
    bool local, connected, configured;
    uint32_t revision;
    uint64_t sample, now_ms, last_ping_ms, last_state_ms, elapsed_us;
    char boot[48];
    const char *reason;
    Sample latched;
    char line[HAP_LINE + 1];
    size_t used;
    bool dropping;
    uint16_t capture_seq;
    uint32_t capture_revision;
    uint64_t capture_started_ms, capture_last_ms;
    const uint16_t *capture_data;
    const char *capture_error;
    bool capture_tx_running, capture_done, capture_ever;
    uint8_t saved_offsets[HAP_CHANNELS];
    bool calibration_trial;
} Device;

void config_default(Config *);
const char *config_compile(Config *);
void geometry_sample(const Config *, uint64_t us, Sample *, float *remaining_us);
void phase_solve(Sample *);
uint8_t phase_solve_channel(const Sample *, unsigned);
void phase_solve_config(const Config *, Sample *);
void wave_render(const Config *, uint64_t elapsed_us, WaveBlock *);
uint16_t hap_crc(const void *, size_t);
void hap_init(Device *, Hardware, const char *boot);
void hap_feed(Device *, uint8_t);
void hap_poll(Device *, uint64_t now_ms, bool allow_telemetry);
void hap_fault(Device *, const char *reason);
void hap_local_stop(Device *);
void hap_local_button(Device *, bool next);
void hap_toggle_mode(Device *);

#endif
