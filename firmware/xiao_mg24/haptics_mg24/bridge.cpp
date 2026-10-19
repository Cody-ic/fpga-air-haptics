#include "bridge.h"
#include "config.h"
#include <limits.h>
#include <stdio.h>
#include <string.h>

namespace hap {
uint16_t crc16(const char *p, size_t n) {
    uint16_t crc = 0xffff;
    for (size_t i = 0; i < n; i++) {
        crc ^= uint16_t(uint8_t(p[i])) << 8;
        for (unsigned k = 0; k < 8; k++)
            crc = (crc & 0x8000) ? uint16_t((crc << 1) ^ 0x1021) : uint16_t(crc << 1);
    }
    return crc;
}
namespace {
bool uint_value(const char *p, size_t n, uint32_t &v) {
    if (!n)
        return false;
    v = 0;
    for (size_t i = 0; i < n; i++) {
        if (p[i] < '0' || p[i] > '9' || v > (UINT32_MAX - unsigned(p[i] - '0')) / 10)
            return false;
        v = v * 10 + unsigned(p[i] - '0');
    }
    return true;
}
struct View {
    char kind[4]{}, verb[20]{};
    uint16_t seq = 0;
    const char *fields = nullptr;
    const char *end = nullptr;
    bool parse(const char *p, size_t n) {
        if (n < 20 || n > max_frame || p[n - 1] != '\n')
            return false;
        if (n >= 2 && p[n - 2] == '\r')
            n--;
        const char *star = p + n - 6;
        if (*star != '*' || memcmp(p, "HAP3 ", 5))
            return false;
        unsigned checksum = 0;
        for (unsigned i = 1; i <= 4; i++) {
            unsigned char c = star[i];
            unsigned digit;
            if (c >= '0' && c <= '9')
                digit = c - '0';
            else if (c >= 'A' && c <= 'F')
                digit = c - 'A' + 10;
            else if (c >= 'a' && c <= 'f')
                digit = c - 'a' + 10;
            else
                return false;
            checksum = (checksum << 4) | digit;
        }
        if (crc16(p, size_t(star - p)) != checksum)
            return false;
        const char *q = p + 5, *s = static_cast<const char *>(memchr(q, ' ', star - q));
        if (!s || s - q != 3)
            return false;
        memcpy(kind, q, 3);
        if (strcmp(kind, "CMD") && strcmp(kind, "ACK") && strcmp(kind, "TEL") &&
            strcmp(kind, "ERR"))
            return false;
        q = s + 1;
        s = static_cast<const char *>(memchr(q, ' ', star - q));
        uint32_t number;
        if (!s || !uint_value(q, size_t(s - q), number) || number > 65535)
            return false;
        seq = uint16_t(number);
        q = s + 1;
        s = static_cast<const char *>(memchr(q, ' ', star - q));
        if (!s)
            s = star;
        if (s == q || s - q >= int(sizeof(verb)))
            return false;
        for (const char *v = q; v < s; v++)
            if (!((*v >= 'A' && *v <= 'Z') || *v == '_'))
                return false;
        memcpy(verb, q, size_t(s - q));
        fields = (s == star) ? star : s + 1;
        end = star;
        return true;
    }
    bool field(const char *name, char *out, size_t size) const {
        size_t key = strlen(name);
        bool found = false;
        for (const char *p = fields; p < end;) {
            const char *next = static_cast<const char *>(memchr(p, ' ', end - p));
            if (!next)
                next = end;
            if (size_t(next - p) > key && memcmp(p, name, key) == 0 && p[key] == '=') {
                size_t len = size_t(next - p) - key - 1;
                if (found || !len || len >= size)
                    return false;
                memcpy(out, p + key + 1, len);
                out[len] = 0;
                found = true;
            }
            p = next + 1;
        }
        return found;
    }
    bool number(const char *name, uint32_t &v) const {
        char buf[12];
        return field(name, buf, sizeof(buf)) && uint_value(buf, strlen(buf), v);
    }
};
size_t finish(char *out, size_t used, size_t capacity) {
    if (used + 6 >= capacity)
        return 0;
    int n = snprintf(out + used, capacity - used, "*%04X\n", crc16(out, used));
    return n == 6 ? used + 6 : 0;
}
} // namespace
void Bridge::error(Source source, uint16_t seq, const char *verb, const char *code) {
    char out[128];
    int n = snprintf(out, sizeof(out), "HAP3 ERR %u %s code=%s", seq, verb, code);
    if (n > 0 && size_t(n) < sizeof(out)) {
        size_t size = finish(out, size_t(n), sizeof(out));
        if (size)
            io_.to_host(source, out, size);
    }
}
void Bridge::cancel_capture() {
    io_.cancel_adc();
    pending_ = false;
    have_meta_ = false;
    have_samples_ = false;
    token_++;
}
void Bridge::disconnect(Source source) {
    if (source != owner_)
        return;
    cancel_capture();
    if (ready_ && mode_known_ && !local_) {
        char stop[64];
        const char *body = "HAP3 CMD 65535 STOP";
        memcpy(stop, body, strlen(body));
        size_t n = finish(stop, strlen(body), sizeof(stop));
        io_.to_fpga(stop, n);
    }
    owner_ = Source::none;
    ready_ = false;
    mode_known_ = false;
    for (auto &line : lines_) {
        line.size = 0;
        line.dropping = false;
    }
}
void Bridge::feed(Source source, const uint8_t *data, size_t n, uint32_t now) {
    if (source == Source::none)
        return;
    Line &line = lines_[source == Source::usb ? 0 : source == Source::ble ? 1 : 2];
    for (size_t i = 0; i < n; i++) {
        uint8_t c = data[i];
        if (line.dropping) {
            if (c == '\n')
                line.dropping = false;
            continue;
        }
        if (line.size >= max_frame) {
            line.size = 0;
            line.dropping = c != '\n';
            continue;
        }
        line.data[line.size++] = char(c);
        if (c == '\n') {
            line.data[line.size] = 0;
            frame(source, line.data, line.size, now);
            line.size = 0;
        }
    }
}
void Bridge::frame(Source source, const char *raw, size_t n, uint32_t now) {
    View f;
    if (!f.parse(raw, n))
        return;
    if (source == Source::fpga) {
        if (!strcmp(f.verb, "CAPTURE_META")) {
            if (!pending_ || strcmp(f.kind, "ACK") || f.seq != capture_seq_)
                return;
            uint32_t running, simulated;
            if (!f.field("boot", meta_boot_, sizeof(meta_boot_)) || strcmp(meta_boot_, boot_) ||
                !f.number("rev", meta_rev_) || !f.number("uptime_ms", meta_uptime_) ||
                !f.number("tx_running", running) || running > 1 ||
                !f.number("simulated", simulated) || simulated != 0) {
                capture_failed(token_);
                return;
            }
            meta_running_ = running != 0;
            have_meta_ = true;
            publish_capture();
            return;
        }
        if (owner_ == Source::none)
            return;
        if (!strcmp(f.verb, "HELLO") && !strcmp(f.kind, "ACK") && f.seq == hello_seq_) {
            char receiver[16];
            uint32_t simulated;
            if (!f.field("receiver", receiver, sizeof(receiver)) || strcmp(receiver, "MG24") ||
                !f.field("boot", boot_, sizeof(boot_)) || !f.number("simulated", simulated) ||
                simulated != 0) {
                error(owner_, f.seq, "HELLO", "RECEIVER_MISMATCH");
                ready_ = false;
                return;
            }
            ready_ = true;
        }
        if (!strcmp(f.verb, "STATE") && !strcmp(f.kind, "TEL")) {
            char value[33];
            if (f.field("boot", value, sizeof(value)) && strcmp(value, boot_)) {
                ready_ = false;
                cancel_capture();
            }
            if (f.field("mode", value, sizeof(value))) {
                if (!strcmp(value, "LOCAL") || !strcmp(value, "REMOTE")) {
                    local_ = !strcmp(value, "LOCAL");
                    mode_known_ = true;
                }
            }
        }
        if (pending_ && f.seq == capture_seq_ && !strcmp(f.verb, "CAPTURE") &&
            !strcmp(f.kind, "ERR"))
            cancel_capture();
        if (!io_.to_host(owner_, raw, n))
            disconnect(owner_);
        return;
    }
    if (strcmp(f.kind, "CMD"))
        return;
    if (owner_ != Source::none && owner_ != source) {
        error(source, f.seq, f.verb, "BUSY");
        return;
    }
    if (!strcmp(f.verb, "HELLO")) {
        owner_ = source;
        last_host_ = now;
        ready_ = false;
        mode_known_ = false;
        hello_seq_ = f.seq;
        cancel_capture();
    } else if (!ready_) {
        error(source, f.seq, f.verb, "HANDSHAKE_NEEDED");
        return;
    }
    last_host_ = now;
    if (!strcmp(f.verb, "CAPTURE")) {
        if (f.fields != f.end) {
            error(source, f.seq, f.verb, "BAD_FIELDS");
            return;
        }
        if (pending_) {
            error(source, f.seq, f.verb, "ADC_BUSY");
            return;
        }
        token_++;
        if (!io_.arm_adc(token_)) {
            error(source, f.seq, f.verb, "ADC_START_FAILED");
            return;
        }
        pending_ = true;
        capture_seq_ = f.seq;
        capture_started_ = now;
        have_meta_ = false;
        have_samples_ = false;
    } else if (pending_ && strcmp(f.verb, "PING") && strcmp(f.verb, "SNAP") &&
               strcmp(f.verb, "STOP") && strcmp(f.verb, "HELLO")) {
        error(source, f.seq, f.verb, "ADC_BUSY");
        return;
    }
    if (!io_.to_fpga(raw, n)) {
        error(source, f.seq, f.verb, "LINK_OVERFLOW");
        disconnect(source);
    }
}
void Bridge::tick(uint32_t now) {
    if (pending_ && uint32_t(now - capture_started_) > HAPTICS_CAPTURE_TIMEOUT_MS) {
        error(owner_, capture_seq_, "CAPTURE", "ADC_TIMEOUT");
        cancel_capture();
    }
    if (owner_ != Source::none && uint32_t(now - last_host_) > 5000)
        disconnect(owner_);
}
void Bridge::capture_failed(uint32_t token) {
    if (pending_ && token == token_) {
        error(owner_, capture_seq_, "CAPTURE", "ADC_ERROR");
        cancel_capture();
    }
}
void Bridge::capture_complete(uint32_t token, const uint16_t *data, size_t n, uint32_t fs,
                              uint32_t scale) {
    if (!pending_ || token != token_ || have_samples_)
        return;
    if (n != 200 || fs < 200000 || fs > 1000000 || scale < 1000 || scale > 1250) {
        capture_failed(token);
        return;
    }
    for (size_t i = 0; i < n; i++)
        if (data[i] > 4095) {
            capture_failed(token);
            return;
        }
    memcpy(samples_, data, sizeof(samples_));
    fs_ = fs;
    scale_ = scale;
    have_samples_ = true;
    publish_capture();
}
void Bridge::publish_capture() {
    if (!pending_ || !have_meta_ || !have_samples_)
        return;
    char out[1400];
    int pos =
        snprintf(out, sizeof(out),
                 "HAP3 ACK %u CAPTURE boot=%s rev=%lu uptime_ms=%lu simulated=0 tx_running=%u "
                 "pin=MG24_D0 fs_hz=%lu bits=12 n=200 full_scale_mv=%lu raw=",
                 capture_seq_, meta_boot_, (unsigned long)meta_rev_, (unsigned long)meta_uptime_,
                 unsigned(meta_running_), (unsigned long)fs_, (unsigned long)scale_);
    for (size_t i = 0; i < 200; i++)
        pos += snprintf(out + pos, sizeof(out) - size_t(pos), "%s%u", i ? "," : "", samples_[i]);
    size_t n = finish(out, size_t(pos), sizeof(out));
    if (!n || !io_.to_host(owner_, out, n)) {
        disconnect(owner_);
        return;
    }
    cancel_capture();
}
} // namespace hap
