#pragma once
#include "analysis.h"
#include <ArduinoJson.h>
#include <cstdio>
#include <cstring>

namespace mg24 {
enum class CaptureState { Running, Done, Overflow, Failed };
struct Sampler {
    virtual ~Sampler() = default;
    virtual bool start() = 0;
    virtual CaptureState poll() = 0;
    virtual void stop() = 0;
    virtual const uint16_t *samples() const = 0;
    virtual double sample_hz() const = 0;
    virtual uint64_t started_us() const = 0;
    virtual uint64_t completed_us() const = 0;
};

class Receiver {
public:
    Receiver(Sampler &sampler, uint32_t boot, const char *transport)
        : sampler_(sampler), boot_(boot), transport_(transport) {}

    void reset() {
        sampler_.stop();
        active_ = false;
        used_ = 0;
        dropping_ = false;
        head_ = tail_ = count_ = offset_ = 0;
        failed_ = false;
    }

    void feed(const char *data, unsigned size, uint64_t now) {
        for (unsigned i = 0; i < size && !failed_; ++i) {
            const char c = data[i];
            if (c == '\n') {
                if (dropping_) error(JsonVariantConst(), "FRAME_TOO_LONG");
                else if (used_) {
                    input_[used_] = 0;
                    command();
                }
                used_ = 0;
                dropping_ = false;
            } else {
                if (!used_) partial_start_ = now;
                if (c == '\r') continue;
                if (!dropping_ && used_ < INPUT_CAPACITY - 1) input_[used_++] = c;
                else dropping_ = true;
            }
        }
    }

    void tick(uint64_t now) {
        if ((used_ || dropping_) && now - partial_start_ > PARTIAL_TIMEOUT_US) {
            used_ = 0;
            dropping_ = false;
            error(JsonVariantConst(), "FRAME_TIMEOUT");
        }
        if (!active_) return;
        const auto state = sampler_.poll();
        if (state == CaptureState::Overflow) finish_error("ADC_OVERFLOW");
        else if (state == CaptureState::Failed) finish_error("ADC_ERROR");
        else if (state == CaptureState::Done) {
            if (sampler_.completed_us() - capture_start_ >= CAPTURE_TIMEOUT_US) {
                finish_error("ADC_TIMEOUT");
                return;
            }
            sampler_.stop();
            active_ = false;
            const Analysis a = analyze(sampler_.samples(), SAMPLE_COUNT, sampler_.sample_hz());
            JsonDocument reply;
            base(reply, capture_id_);
            reply["ok"] = true;
            reply["type"] = "CAPTURE";
            reply["start_us"] = capture_start_;
            reply["end_us"] = sampler_.completed_us();
            reply["sample_hz"] = sampler_.sample_hz();
            reply["count"] = SAMPLE_COUNT;
            reply["adc_bits"] = 12;
            reply["vref_mv_nominal"] = VREF_MV;
            reply["mean_adc"] = a.mean;
            reply["rms_adc"] = a.rms;
            reply["peak40_adc"] = a.peak40;
            reply["rms_mv"] = a.rms * VREF_MV / ADC_MAX;
            reply["peak40_mv"] = a.peak40 * VREF_MV / ADC_MAX;
            reply["min_adc"] = a.minimum;
            reply["max_adc"] = a.maximum;
            reply["adc_rail"] = a.rail;
            reply["valid"] = !a.rail;
            if (raw_) {
                JsonArray raw = reply["samples"].to<JsonArray>();
                for (unsigned i = 0; i < SAMPLE_COUNT; ++i) raw.add(sampler_.samples()[i]);
            }
            enqueue(reply);
        } else if (now >= capture_start_ && now - capture_start_ >= CAPTURE_TIMEOUT_US) finish_error("ADC_TIMEOUT");
    }

    const char *output(unsigned &size) const {
        size = count_ ? lengths_[head_] - offset_ : 0;
        return count_ ? output_[head_] + offset_ : nullptr;
    }
    void consume(unsigned size) {
        if (!count_) return;
        offset_ += size;
        if (offset_ >= lengths_[head_]) {
            head_ = (head_ + 1) % OUTPUT_SLOTS;
            --count_;
            offset_ = 0;
        }
    }
    bool failed() const { return failed_; }
    bool active() const { return active_; }

private:
    Sampler &sampler_;
    uint32_t boot_, capture_id_ = 0;
    const char *transport_;
    bool active_ = false, raw_ = false, dropping_ = false, failed_ = false;
    uint64_t capture_start_ = 0, partial_start_ = 0;
    char input_[INPUT_CAPACITY] = {};
    unsigned used_ = 0;
    char output_[OUTPUT_SLOTS][OUTPUT_CAPACITY] = {};
    unsigned lengths_[OUTPUT_SLOTS] = {}, head_ = 0, tail_ = 0, count_ = 0, offset_ = 0;

    void base(JsonDocument &doc, JsonVariantConst id) const {
        doc["proto"] = "MG24_RX1";
        doc["id"] = id;
        doc["boot"] = boot_;
        doc["simulated"] = false;
    }
    void base(JsonDocument &doc, uint32_t id) const {
        doc["proto"] = "MG24_RX1";
        doc["id"] = id;
        doc["boot"] = boot_;
        doc["simulated"] = false;
    }
    void enqueue(const JsonDocument &doc) {
        const auto size = measureJson(doc);
        if (count_ == OUTPUT_SLOTS || size + 2 > OUTPUT_CAPACITY || doc.overflowed()) {
            sampler_.stop();
            active_ = false;
            failed_ = true;
            return;
        }
        const auto written = serializeJson(doc, output_[tail_], OUTPUT_CAPACITY);
        output_[tail_][written] = '\n';
        lengths_[tail_] = written + 1;
        tail_ = (tail_ + 1) % OUTPUT_SLOTS;
        ++count_;
    }
    void error(JsonVariantConst id, const char *reason) {
        JsonDocument reply;
        base(reply, id);
        reply["ok"] = false;
        reply["error"] = reason;
        enqueue(reply);
    }
    void finish_error(const char *reason) {
        sampler_.stop();
        active_ = false;
        JsonDocument id;
        id.set(capture_id_);
        error(id.as<JsonVariantConst>(), reason);
    }
    void command() {
        JsonDocument request;
        if (deserializeJson(request, input_) || !request.is<JsonObject>()
            || !request["id"].is<uint32_t>() || !request["cmd"].is<const char *>()) {
            error(JsonVariantConst(), "BAD_REQUEST");
            return;
        }
        JsonVariantConst id = request["id"];
        const char *cmd = request["cmd"];
        if (!std::strcmp(cmd, "INFO")) {
            // An INFO handshake establishes a clean logical USB/BLE session.
            reset();
            JsonDocument reply;
            base(reply, id);
            reply["ok"] = true;
            reply["type"] = "INFO";
            reply["device"] = "TouchSee-MG24-RX";
            reply["transport"] = transport_;
            reply["sample_hz"] = sampler_.sample_hz();
            reply["count"] = SAMPLE_COUNT;
            reply["input"] = "A0/PC00";
            reply["adc_bits"] = 12;
            reply["vref_mv_nominal"] = VREF_MV;
            reply["capabilities"] = "INFO,CAPTURE,RAW,STOP";
            enqueue(reply);
        } else if (!std::strcmp(cmd, "STOP")) {
            if (active_) finish_error("CANCELLED");
            JsonDocument reply;
            base(reply, id);
            reply["ok"] = true;
            reply["type"] = "STOP";
            enqueue(reply);
        } else if (!std::strcmp(cmd, "CAPTURE")) {
            if (!request["raw"].isNull() && !request["raw"].is<bool>()) {
                error(id, "BAD_REQUEST");
            } else if (active_) error(id, "BUSY");
            else if (!sampler_.start()) error(id, "ADC_ERROR");
            else {
                active_ = true;
                capture_id_ = id.as<uint32_t>();
                capture_start_ = sampler_.started_us();
                raw_ = request["raw"] | false;
            }
        } else error(id, "UNKNOWN_COMMAND");
    }
};
}
