#include "haptics.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *shapes[] = {"POINT","LINE_X","LINE_Y","CIRCLE","SQUARE","TRIANGLE","ARROW","CUSTOM"};
static const char *states[] = {"IDLE","RUNNING","PAUSED","FAULT"};
static Config candidate; /* Foreground only; avoid an 8 KB stack allocation. */
static char tx[HAP_LINE+1];
static size_t tx_used;
static uint32_t telemetry_interval_ms = 500;
static void (*background_service)(void);

uint16_t hap_crc(const void *bytes, size_t length)
{
    const uint8_t *p = bytes;
    uint16_t crc = 0xffff;
    while (length--) {
        if (!(length & 63u) && background_service) background_service();
        crc ^= (uint16_t)*p++ << 8;
        for (unsigned i = 0; i < 8; ++i)
            crc = (uint16_t)((crc<<1) ^ ((crc&0x8000) ? 0x1021 : 0));
    }
    return crc;
}

static void append(const char *format, ...)
{
    if (tx_used >= HAP_LINE-6) return;
    va_list args;
    va_start(args, format);
    int n = vsnprintf(tx+tx_used, HAP_LINE-5-tx_used, format, args);
    va_end(args);
    if (n < 0 || (size_t)n >= HAP_LINE-5-tx_used) tx_used = HAP_LINE;
    else tx_used += (size_t)n;
    if (background_service) background_service();
}

static void begin(const char *kind, unsigned seq, const char *verb)
{
    tx_used = 0;
    append("HAP3 %s %u %s", kind, seq, verb);
}

static void send_frame(Device *d)
{
    if (tx_used > HAP_LINE-6) { hap_fault(d, "TX_OVERFLOW"); return; }
    uint16_t crc = hap_crc(tx, tx_used);
    int n = snprintf(tx+tx_used, sizeof(tx)-tx_used, "*%04X\n", crc);
    tx_used += (size_t)n;
    d->hw.send(tx, tx_used);
}

static void array_fields(void)
{
    append(" hw_rows=4 hw_cols=4 hw_pitch_um=11000 mapping=ROW_MAJOR_XY");
}

static void sample_now(Device *d)
{
    if (d->state == RUNNING && d->hw.readback(&d->latched)) return;
    if (d->state != PAUSED && d->state != FAULT) {
        float remaining;
        geometry_sample(&d->config, d->elapsed_us, &d->latched, &remaining);
        phase_solve(&d->latched);
    }
    d->latched.output = false; d->latched.drive_on = false;
}

static void snapshot(Device *d)
{
    sample_now(d);
    const Config *c = &d->config;
    const Sample *s = &d->latched;
    begin("TEL", 0, "STATE");
    append(" boot=%s sample=%llu uptime_ms=%llu rev=%lu mode=%s state=%s output=%u scan_on=%u stroke_index=%u simulated=0 reason=%s",
        d->boot, (unsigned long long)++d->sample, (unsigned long long)d->now_ms,
        (unsigned long)d->revision, d->local ? "LOCAL" : "REMOTE", states[d->state],
        d->state == RUNNING && s->output, s->scan_on, s->stroke, d->reason);
    append(" carrier_hz=%ld phase_steps=%ld cx_um=%ld cy_um=%ld z_um=%ld radius_um=%ld repeat_millihz=%ld mod_hz=%ld level=%ld shape=%s path_xy_um=%s path_closed=%ld scan_paths=%s blank_us=%ld",
        (long)c->carrier_hz, (long)c->phase_steps, (long)c->cx_um, (long)c->cy_um,
        (long)c->z_um, (long)c->radius_um, (long)c->repeat_millihz, (long)c->mod_hz,
        (long)c->level, shapes[c->shape], c->path_xy_um, (long)c->path_closed,
        c->scan_paths, (long)c->blank_us);
    array_fields();
    append(" drive_on=%u",d->state == RUNNING && s->drive_on);
    append(" fx_um=%ld fy_um=%ld fz_um=%ld phases=", (long)s->x_um, (long)s->y_um, (long)s->z_um);
    for (unsigned i = 0; i < 16; ++i) append("%s%u", i ? "," : "", s->phases[i]);
    send_frame(d);
    /* Leave half the 115200-baud link for commands/ACKs, including long paths. */
    telemetry_interval_ms = (uint32_t)((tx_used*20000u+115199u)/115200u);
    if (telemetry_interval_ms < 100) telemetry_interval_ms = 100;
    if (d->state != RUNNING && telemetry_interval_ms < 500) telemetry_interval_ms = 500;
    d->last_state_ms = d->now_ms;
}

void hap_init(Device *d, Hardware hw, const char *boot)
{
    memset(d, 0, sizeof(*d));
    d->hw = hw; d->reason = "NONE";
    background_service = hw.service;
    snprintf(d->boot, sizeof(d->boot), "%s", boot);
    config_default(&d->config);
    (void)config_compile(&d->config);
    d->hw.stop();
}

static void stop(Device *d, const char *reason)
{
    d->hw.stop(); d->state = IDLE; d->elapsed_us = 0; d->reason = reason;
    d->latched.output = false;
}

void hap_fault(Device *d, const char *reason)
{
    d->hw.stop(); d->state = FAULT; d->reason = reason; d->latched.output = false;
}

void hap_local_stop(Device *d) { stop(d, "LOCAL_STOP"); }

void hap_toggle_mode(Device *d)
{
    if (d->state == IDLE) { d->local = !d->local; d->last_ping_ms = d->now_ms; }
}

void hap_local_button(Device *d, bool next)
{
    if (!d->local) return;
    if (next && d->state == IDLE) {
        candidate = d->config;
        candidate.shape = (Shape)((candidate.shape+1)%7);
        const char *error = config_compile(&candidate);
        if (error) { d->reason = error; return; }
        d->config = candidate; ++d->revision; d->reason = "NONE";
    } else if (!next && d->state == RUNNING) {
        sample_now(d); d->elapsed_us = d->latched.elapsed_us;
        d->hw.stop(); d->state = PAUSED; d->latched.output = false;
    } else if (!next && (d->state == IDLE || d->state == PAUSED)) {
        if (d->hw.start(&d->config,d->elapsed_us)) { d->state = RUNNING; d->reason = "NONE"; }
        else hap_fault(d,"OUTPUT_START_FAILED");
    }
}

typedef struct { char *key, *value; } Field;
static const char *get(Field *fields, unsigned n, const char *key)
{
    for (unsigned i = 0; i < n; ++i)
        if (!strcmp(fields[i].key, key)) return fields[i].value;
    return NULL;
}

static bool integer(const char *s, int32_t low, int32_t high, int32_t *out)
{
    if (!s || !*s) return false;
    bool negative = *s == '-';
    if (negative || *s == '+') ++s;
    if (!*s) return false;
    int64_t n = 0;
    while (*s) {
        if (*s < '0' || *s > '9') return false;
        n = n*10+(*s++-'0');
        if (n > 2147483648LL) return false;
    }
    if (negative) n = -n;
    if (n < low || n > high) return false;
    *out = (int32_t)n;
    return true;
}

static const char *parse_config(Field *f, unsigned n)
{
    if (n != 18) return "BAD_CONFIG";
    memset(&candidate, 0, sizeof(candidate));
#define READ(name, lo, hi) if (!integer(get(f,n,#name),lo,hi,&candidate.name)) return "BAD_CONFIG"
    READ(carrier_hz,40000,40000); READ(phase_steps,64,64);
    READ(cx_um,-100000,100000); READ(cy_um,-100000,100000);
    READ(z_um,20000,300000); READ(radius_um,0,80000);
    READ(repeat_millihz,10,200000); READ(mod_hz,0,1000); READ(level,0,100);
    READ(path_closed,0,1); READ(blank_us,100,100000);
#undef READ
    const char *s = get(f,n,"shape"), *p = get(f,n,"path_xy_um"), *paths = get(f,n,"scan_paths");
    if (!s || !p || !paths || strlen(p) >= sizeof(candidate.path_xy_um) || strlen(paths) >= sizeof(candidate.scan_paths))
        return "BAD_CONFIG";
    unsigned i;
    for (i = 0; i < 8 && strcmp(s,shapes[i]); ++i) {}
    if (i == 8) return "BAD_CONFIG";
    candidate.shape = (Shape)i;
    strcpy(candidate.path_xy_um,p); strcpy(candidate.scan_paths,paths);
    int32_t v;
    if (!integer(get(f,n,"hw_rows"),4,4,&v) || !integer(get(f,n,"hw_cols"),4,4,&v) ||
        !integer(get(f,n,"hw_pitch_um"),11000,11000,&v) || !get(f,n,"mapping") ||
        strcmp(get(f,n,"mapping"),"ROW_MAJOR_XY")) return "HARDWARE_MISMATCH";
    return config_compile(&candidate);
}

static bool value_char(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
        (c && strchr("_,.?:+|-",c));
}

static void process(Device *d)
{
    char *line = d->line;
    size_t length = d->used;
    if (length && line[length-1] == '\n') --length;
    if (length && line[length-1] == '\r') --length;
    if (length < 18 || line[length-5] != '*') return;
    unsigned crc = 0;
    for (size_t i = length-4; i < length; ++i) {
        char c = line[i];
        unsigned v = c >= '0' && c <= '9' ? (unsigned)(c-'0') :
            c >= 'A' && c <= 'F' ? (unsigned)(c-'A'+10) :
            c >= 'a' && c <= 'f' ? (unsigned)(c-'a'+10) : 16;
        if (v > 15) return;
        crc = (crc<<4)|v;
    }
    if (hap_crc(line,length-5) != crc) return;
    line[length-5] = 0;
    char *tokens[40]; unsigned count = 0;
    tokens[count++] = line;
    for (char *s = line; *s; ++s) {
        if (*s == ' ') {
            if (s == line || s[-1] == 0 || !s[1] || count >= 40) return;
            *s = 0; tokens[count++] = s+1;
        }
    }
    int32_t seq;
    if (count < 4 || strcmp(tokens[0],"HAP3") || strcmp(tokens[1],"CMD") ||
        !integer(tokens[2],1,65535,&seq)) return;
    const char *verb = tokens[3];
    if (!*verb || strlen(verb) > 32) return;
    for (const char *s = verb; *s; ++s) if (!value_char(*s)) return;
    Field fields[36];
    unsigned n = count-4;
    for (unsigned i = 0; i < n; ++i) {
        char *key = tokens[i+4], *eq = strchr(key,'=');
        if (!eq || eq == key || !eq[1] || *key < 'a' || *key > 'z') return;
        *eq = 0;
        for (char *s = key; *s; ++s)
            if (!((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == '_')) return;
        for (char *s = eq+1; *s; ++s) if (!value_char(*s)) return;
        for (unsigned j = 0; j < i; ++j) if (!strcmp(key,fields[j].key)) return;
        fields[i] = (Field){key,eq+1};
    }
    const char *error = NULL;
    if (!strcmp(verb,"HELLO") && !n) {
        /* A new handshake is a new control session, never an implicit resume. */
        if (!d->local) stop(d,"NONE");
        d->configured = false; d->connected = true; d->last_ping_ms = d->now_ms;
        if (d->capture_seq && d->hw.capture_cancel) d->hw.capture_cancel();
        d->capture_seq = 0;
        begin("ACK",(unsigned)seq,verb);
        append(" proto=3 device=NUCLEO-F411RE boot=%s simulated=0 hb_ms=3000 caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,CUSTOM_XY,SCAN_PATHS max_rows=4 max_cols=4 max_channels=16 max_nodes=64 max_scan_points=256 max_strokes=32",d->boot);
        array_fields();
        if (d->hw.capture_start && d->hw.capture_poll && d->hw.capture_cancel)
            append(" adc_capture=1");
        append(" x_min_um=-100000 x_max_um=100000 y_min_um=-100000 y_max_um=100000 z_min_um=20000 z_max_um=300000 carrier_hz=40000 phase_steps=64 focus_hz=4000");
        send_frame(d); snapshot(d); return;
    }
    if (!d->connected) error = "HANDSHAKE_REQUIRED";
    else if (!strcmp(verb,"CONFIG")) {
        if (d->local) error = "LOCAL_CONTROL";
        else if (d->state != IDLE) error = "BUSY";
        else {
            error = parse_config(fields,n);
            if (!error) {
                d->config = candidate; ++d->revision; d->elapsed_us = 0;
                d->configured = true; d->reason = "NONE";
            }
        }
    } else if (!strcmp(verb,"MODE")) {
        const char *v = get(fields,n,"value");
        if (d->state != IDLE) error = "BUSY";
        else if (n != 1 || !v || (strcmp(v,"LOCAL") && strcmp(v,"REMOTE"))) error = "BAD_MODE";
        else { d->local = !strcmp(v,"LOCAL"); d->last_ping_ms = d->now_ms; }
    } else if (n) error = "BAD_FIELDS";
    else if (!strcmp(verb,"CAPTURE")) {
        if (!d->hw.capture_start || !d->hw.capture_poll || !d->hw.capture_cancel)
            error = "UNSUPPORTED";
        else if (d->capture_seq) error = "ADC_BUSY";
        else if (d->capture_ever && d->now_ms-d->capture_last_ms < 200)
            error = "ADC_RATE_LIMIT";
        else if (!d->hw.capture_start()) error = "ADC_START_FAILED";
        else {
            d->capture_seq = (uint16_t)seq; d->capture_started_ms = d->now_ms;
            d->capture_last_ms = d->now_ms; d->capture_ever = true;
            d->capture_revision = d->revision; d->capture_tx_running = d->state == RUNNING;
            d->capture_data = NULL; d->capture_error = NULL; d->capture_done = false;
            return; /* Reply only after the complete DMA window; never block control. */
        }
    }
    else if (!strcmp(verb,"PING")) d->last_ping_ms = d->now_ms;
    else if (!strcmp(verb,"STOP")) stop(d,"NONE");
    else if (!strcmp(verb,"START")) {
        if (d->local) error = "LOCAL_CONTROL";
        else if (d->state != IDLE && d->state != PAUSED) error = "BUSY";
        else if (!d->configured) error = "CONFIG_REQUIRED";
        else if (!d->hw.start(&d->config,d->elapsed_us)) {
            hap_fault(d,"OUTPUT_START_FAILED"); error = "OUTPUT_START_FAILED";
        } else { d->state = RUNNING; d->reason = "NONE"; d->last_ping_ms = d->now_ms; }
    } else if (!strcmp(verb,"PAUSE")) {
        if (d->local) error = "LOCAL_CONTROL";
        else if (d->state != RUNNING) error = "NOT_RUNNING";
        else {
            sample_now(d); d->elapsed_us = d->latched.elapsed_us;
            d->hw.stop(); d->state = PAUSED; d->latched.output = false;
        }
    } else if (strcmp(verb,"SNAP")) error = "UNKNOWN_COMMAND";
    if (error) { begin("ERR",(unsigned)seq,verb); append(" code=%s",error); send_frame(d); }
    else {
        begin("ACK",(unsigned)seq,verb); append(" applied=1 rev=%lu",(unsigned long)d->revision); send_frame(d);
        if (strcmp(verb,"PING")) snapshot(d);
    }
}

void hap_feed(Device *d, uint8_t byte)
{
    if (d->dropping) { if (byte == '\n') d->dropping = false; return; }
    if (d->used >= HAP_LINE || !byte || byte > 127) {
        d->used = 0; d->dropping = byte != '\n'; return;
    }
    d->line[d->used++] = (char)byte;
    if (byte == '\n') { d->line[d->used] = 0; process(d); d->used = 0; }
}

void hap_poll(Device *d, uint64_t now_ms, bool allow_telemetry)
{
    d->now_ms = now_ms;
    if (!d->local && (d->state == RUNNING || d->state == PAUSED) && now_ms-d->last_ping_ms >= 3000)
        stop(d,"HEARTBEAT_TIMEOUT");
    if (d->capture_seq && !d->capture_done) {
        int result = d->hw.capture_poll(&d->capture_data);
        if (result < 0) d->capture_error = "ADC_ERROR";
        else if (!result && now_ms-d->capture_started_ms >= 20) {
            d->hw.capture_cancel(); d->capture_error = "ADC_TIMEOUT";
        }
        if (result || d->capture_error) d->capture_done = true;
    }
    if (allow_telemetry && d->capture_seq && d->capture_done) {
        if (d->capture_error) {
            begin("ERR",d->capture_seq,"CAPTURE"); append(" code=%s",d->capture_error);
        } else {
            begin("ACK",d->capture_seq,"CAPTURE");
            append(" boot=%s rev=%lu uptime_ms=%llu simulated=0 tx_running=%u pin=PA0 fs_hz=%u bits=12 n=%u raw=",
                   d->boot,(unsigned long)d->capture_revision,(unsigned long long)d->capture_started_ms,
                   d->capture_tx_running,HAP_ADC_HZ,HAP_ADC_SAMPLES);
            for (unsigned i=0; i<HAP_ADC_SAMPLES; ++i) append("%s%u",i?",":"",d->capture_data[i]);
        }
        send_frame(d); d->capture_seq = 0;
        return; /* One outbound frame per idle opportunity; state gets the next one. */
    }
    if (allow_telemetry && d->connected && now_ms-d->last_state_ms >= telemetry_interval_ms)
        snapshot(d);
}
