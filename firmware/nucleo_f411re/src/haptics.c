#include "haptics.h"
#include "array_geometry.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *shapes[] = {"POINT","LINE_X","LINE_Y","CIRCLE","SQUARE","TRIANGLE","ARROW","CUSTOM"};
static const char *states[] = {"IDLE","RUNNING","PAUSED","FAULT"};
#ifdef HAP_SHARED_SCRATCH
/* Parsing/configuration finishes before reply serialization begins. IRQs
 * never use either member, and service callbacks cannot reenter protocol. */
static union { Config configuration; char reply[HAP_LINE+1]; } scratch;
#define candidate scratch.configuration
#define tx scratch.reply
#else
static Config candidate; /* Foreground only; avoid an 8 KB stack allocation. */
static char tx[HAP_LINE+1];
#endif
static size_t tx_used;
static uint32_t telemetry_interval_ms = 500;
static void (*background_service)(void);

static const char *decimal64(uint64_t value, char buffer[21])
{
    char *p = buffer+20;
    *p = 0;
    do { *--p = (char)('0'+value%10u); value /= 10u; } while (value);
    return p;
}

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
    append(" hw_rows=%u hw_cols=%u hw_pitch_um=%u mapping=%s", ARRAY_ROWS, ARRAY_COLS,
           ARRAY_PITCH_UM, ARRAY_EXPLICIT ? "EXPLICIT_XYZ" : "ROW_MAJOR_XY");
    if (ARRAY_EXPLICIT) append(" geometry_id=%s", ARRAY_ID);
}

static void sample_now(Device *d)
{
    if (d->state == RUNNING && d->hw.readback(&d->latched)) return;
    if (d->state != PAUSED && d->state != FAULT) {
        float remaining;
        geometry_sample(&d->config, d->elapsed_us, &d->latched, &remaining);
        phase_solve_config(&d->config, &d->latched);
    }
    d->latched.output = false; d->latched.drive_on = false;
}

static void snapshot(Device *d)
{
    sample_now(d);
    const Config *c = &d->config;
    const Sample *s = &d->latched;
    char sample_text[21], uptime_text[21];
    begin("TEL", 0, "STATE");
    append(" boot=%s sample=%s uptime_ms=%s rev=%lu mode=%s state=%s output=%u scan_on=%u stroke_index=%u simulated=0 reason=%s",
        d->boot, decimal64(++d->sample,sample_text), decimal64(d->now_ms,uptime_text),
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
    append(" channel_mask=%u phase_offsets=", c->channel_mask);
    for (unsigned i = 0; i < 16; ++i) append("%s%u", i ? "," : "", c->phase_offsets[i]);
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
    if (d->calibration_trial) {
        memcpy(d->config.phase_offsets, d->saved_offsets, HAP_CHANNELS);
        d->config.channel_mask = 0xffff;
        d->calibration_trial = false; ++d->revision;
        d->configured = false; /* Never resume a continuous test tone implicitly. */
    }
}

void hap_fault(Device *d, const char *reason)
{
    d->hw.stop(); d->state = FAULT; d->reason = reason; d->latched.output = false;
}

void hap_local_stop(Device *d) { stop(d, "LOCAL_STOP"); }

void hap_toggle_mode(Device *d)
{
    if (d->state == IDLE && !d->calibration_trial) { d->local = !d->local; d->last_ping_ms = d->now_ms; }
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
    if (n != 18u+ARRAY_EXPLICIT) return "BAD_CONFIG";
    memset(&candidate, 0, sizeof(candidate));
    candidate.channel_mask = 0xffff;
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
    if (!integer(get(f,n,"hw_rows"),ARRAY_ROWS,ARRAY_ROWS,&v) || !integer(get(f,n,"hw_cols"),ARRAY_COLS,ARRAY_COLS,&v) ||
        !integer(get(f,n,"hw_pitch_um"),ARRAY_PITCH_UM,ARRAY_PITCH_UM,&v) || !get(f,n,"mapping") ||
        strcmp(get(f,n,"mapping"),ARRAY_EXPLICIT ? "EXPLICIT_XYZ" : "ROW_MAJOR_XY")) return "HARDWARE_MISMATCH";
    if (ARRAY_EXPLICIT && (!get(f,n,"geometry_id") || strcmp(get(f,n,"geometry_id"),ARRAY_ID)))
        return "HARDWARE_MISMATCH";
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
#if defined(F103_CHANNEL_TEST) && F103_CHANNEL_TEST
        /* Diagnosis owns its sequence; observing it must not reset a channel. */
        d->connected = true; d->last_ping_ms = d->now_ms;
#else
        /* A new handshake is a new control session, never an implicit resume. */
        if (!d->local) stop(d,"NONE");
        d->configured = false; d->connected = true; d->last_ping_ms = d->now_ms;
#endif
        if (d->capture_seq && d->hw.capture_cancel) d->hw.capture_cancel();
        d->capture_seq = 0;
        begin("ACK",(unsigned)seq,verb);
#if defined(F103_CHANNEL_TEST) && F103_CHANNEL_TEST
        append(" proto=3 device=%s boot=%s simulated=0 hb_ms=3000 profile=CHANNEL_TEST caps=STOP,STATE,PHASE,GEOMETRY max_rows=16 max_cols=16 max_channels=16 max_nodes=64 max_scan_points=%u max_strokes=%u",HAP_DEVICE_NAME,d->boot,HAP_POINTS,HAP_STROKES);
#else
        append(" proto=3 device=%s boot=%s simulated=0 hb_ms=3000 caps=CONFIG,MODE,START,PAUSE,STOP,STATE,PHASE,CUSTOM_XY,SCAN_PATHS,GEOMETRY,CALIBRATION max_rows=16 max_cols=16 max_channels=16 max_nodes=64 max_scan_points=%u max_strokes=%u",HAP_DEVICE_NAME,d->boot,HAP_POINTS,HAP_STROKES);
#endif
        array_fields();
        if (d->hw.capture_start && d->hw.capture_poll && d->hw.capture_cancel)
            append(" adc_capture=1");
        append(" x_min_um=-100000 x_max_um=100000 y_min_um=-100000 y_max_um=100000 z_min_um=20000 z_max_um=300000 carrier_hz=40000 phase_steps=64 focus_hz=%u",1000000u/HAP_FOCUS_US);
#ifdef F103_FIRMWARE_MODE
        append(" fw_id=F103_SERIAL_20261010 firmware_mode=%u", F103_FIRMWARE_MODE);
#if F103_PIN_TEST
        append(" pin_channel=%u on_ms=%lu", F103_PIN_TEST_CHANNEL,
               (unsigned long)F103_PIN_TEST_ON_MS);
#endif
#endif
        send_frame(d); snapshot(d); return;
    }
    if (!d->connected) error = "HANDSHAKE_REQUIRED";
#if defined(F103_CHANNEL_TEST) && F103_CHANNEL_TEST
    else if (strcmp(verb,"PING") && strcmp(verb,"SNAP") && strcmp(verb,"STOP") &&
             strcmp(verb,"GEOMETRY") && strcmp(verb,"CAPTURE")) error = "CHANNEL_TEST_ONLY";
#endif
    else if (!strcmp(verb,"GEOMETRY")) {
        int32_t start, count;
        if (n != 2 || !integer(get(fields,n,"start"),0,15,&start) ||
            !integer(get(fields,n,"count"),1,16,&count) || start+count > 16) error = "BAD_GEOMETRY_RANGE";
        else {
            begin("ACK",(unsigned)seq,verb);
            append(" geometry_id=%s start=%ld count=%ld sound_speed_mm_s=%u elements=", ARRAY_ID,(long)start,(long)count,ARRAY_SOUND_MM_S);
            for (int32_t i=start;i<start+count;++i)
                for (unsigned j=0;j<6;++j) append("%s%ld",j ? "," : (i==start ? "" : "|"),(long)array_elements[i][j]);
            send_frame(d); return;
        }
    }
    else if (!strcmp(verb,"CALIBRATION")) {
        int32_t mask;
        const char *action=get(fields,n,"action"), *offsets=get(fields,n,"offsets"), *id=get(fields,n,"geometry_id");
        uint8_t parsed[HAP_CHANNELS];
        if (d->local) error="LOCAL_CONTROL";
        else if (d->state!=IDLE || d->capture_seq) error="BUSY";
        else if (n!=4 || !action || !offsets || !id || strcmp(id,ARRAY_ID) ||
                 (strcmp(action,"trial") && strcmp(action,"store")) ||
                 !integer(get(fields,n,"mask"),1,65535,&mask)) error="BAD_CALIBRATION";
        else {
            const char *p=offsets;
            for (unsigned i=0;i<HAP_CHANNELS;++i) {
                unsigned value=0, digits=0;
                while (*p>='0' && *p<='9') {
                    value=value*10u+(unsigned)(*p++-'0');
                    if (++digits>2) break;
                }
                if (!digits || digits>2 || value>=64 || (i<15 ? *p!=',' : *p!=0)) { error="BAD_CALIBRATION"; break; }
                parsed[i]=(uint8_t)value; if (i<15) ++p;
            }
            if (!error && !strcmp(action,"trial") && (d->config.shape!=POINT || d->config.mod_hz || d->config.level!=100))
                error="STEADY_POINT_REQUIRED";
            if (!error && !strcmp(action,"store") && mask!=65535) error="BAD_CALIBRATION";
            if (!error) {
                memcpy(d->config.phase_offsets,parsed,HAP_CHANNELS);
                d->config.channel_mask=(uint16_t)mask;
                d->calibration_trial=!strcmp(action,"trial");
                if (!d->calibration_trial) memcpy(d->saved_offsets,parsed,HAP_CHANNELS);
                d->configured=true; ++d->revision;
            }
        }
    }
    else if (!strcmp(verb,"CONFIG")) {
        if (d->local) error = "LOCAL_CONTROL";
        else if (d->state != IDLE) error = "BUSY";
        else {
            error = parse_config(fields,n);
            if (!error) {
                memcpy(candidate.phase_offsets,d->saved_offsets,HAP_CHANNELS);
                d->config = candidate; ++d->revision; d->elapsed_us = 0;
                d->calibration_trial=false;
                d->configured = true; d->reason = "NONE";
            }
        }
    } else if (!strcmp(verb,"MODE")) {
        const char *v = get(fields,n,"value");
        if (d->state != IDLE || d->calibration_trial) error = "BUSY";
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
            char uptime_text[21];
            begin("ACK",d->capture_seq,"CAPTURE");
            append(" boot=%s rev=%lu uptime_ms=%s simulated=0 tx_running=%u pin=PA0 fs_hz=%u bits=12 n=%u raw=",
                   d->boot,(unsigned long)d->capture_revision,decimal64(d->capture_started_ms,uptime_text),
                   d->capture_tx_running,HAP_ADC_HZ,HAP_ADC_SAMPLES);
            for (unsigned i=0; i<HAP_ADC_SAMPLES; ++i) append("%s%u",i?",":"",d->capture_data[i]);
        }
        send_frame(d); d->capture_seq = 0;
        return; /* One outbound frame per idle opportunity; state gets the next one. */
    }
    if (allow_telemetry && d->connected && now_ms-d->last_state_ms >= telemetry_interval_ms)
        snapshot(d);
}
