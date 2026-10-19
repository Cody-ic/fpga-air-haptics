#pragma once
#include <stddef.h>
#include <stdint.h>

namespace hap {
constexpr size_t max_frame = 8192;
enum class Source : uint8_t { none, usb, ble, fpga };
struct Io {
    bool (*to_fpga)(const char *, size_t);
    bool (*to_host)(Source, const char *, size_t);
    bool (*arm_adc)(uint32_t);
    void (*cancel_adc)();
};
uint16_t crc16(const char *, size_t);
// Input buffers keep partial frames local: never forward a partial host command.
class Bridge {
  public:
    explicit Bridge(Io io) : io_(io) {}
    void feed(Source, const uint8_t *, size_t, uint32_t now_ms);
    void tick(uint32_t now_ms);
    void disconnect(Source);
    void capture_complete(uint32_t token, const uint16_t *, size_t, uint32_t fs_hz,
                          uint32_t full_scale_mv);
    void capture_failed(uint32_t token);
    Source owner() const { return owner_; }
    bool ready() const { return ready_; }
    bool capture_pending() const { return pending_; }

  private:
    struct Line {
        char data[max_frame + 1]{};
        size_t size = 0;
        bool dropping = false;
    } lines_[3];
    Io io_;
    Source owner_ = Source::none;
    bool ready_ = false, local_ = false, mode_known_ = false, pending_ = false, have_meta_ = false,
         have_samples_ = false;
    uint16_t hello_seq_ = 0, capture_seq_ = 0;
    uint32_t token_ = 0, capture_started_ = 0, last_host_ = 0, fs_ = 0, scale_ = 0;
    char boot_[33]{}, meta_boot_[33]{};
    uint32_t meta_rev_ = 0, meta_uptime_ = 0;
    bool meta_running_ = false;
    uint16_t samples_[200]{};
    void frame(Source, const char *, size_t, uint32_t);
    void cancel_capture();
    void publish_capture();
    void error(Source, uint16_t, const char *, const char *);
};
} // namespace hap
