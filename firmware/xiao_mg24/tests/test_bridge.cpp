#include "bridge.h"
#include <assert.h>
#include <fstream>
#include <stdio.h>
#include <string>
#include <vector>
using namespace hap;
std::vector<std::string> fpga, host;
Source last_target = Source::none;
uint32_t armed_token = 0;
bool arm_ok = true;
bool to_fpga(const char *p, size_t n) {
    fpga.emplace_back(p, n);
    return true;
}
bool to_host(Source s, const char *p, size_t n) {
    last_target = s;
    host.emplace_back(p, n);
    return true;
}
bool arm(uint32_t token) {
    armed_token = token;
    return arm_ok;
}
void cancel() {}
Io io = {to_fpga, to_host, arm, cancel};
std::string packet(const std::string &body) {
    char crc[8];
    snprintf(crc, sizeof(crc), "*%04X\n", crc16(body.data(), body.size()));
    return body + crc;
}
void send(Bridge &b, Source source, const std::string &body, uint32_t now = 100) {
    auto p = packet(body);
    b.feed(source, (const uint8_t *)p.data(), p.size(), now);
}
void hello(Bridge &b) {
    send(b, Source::ble, "HAP3 CMD 1 HELLO");
    send(b, Source::fpga, "HAP3 ACK 1 HELLO receiver=MG24 boot=abc simulated=0 adc_capture=1");
}
int main(int argc, char **argv) {
    Bridge b(io);
    hello(b);
    assert(b.ready() && b.owner() == Source::ble);
    auto partial = packet("HAP3 CMD 2 PING");
    size_t count = fpga.size();
    b.feed(Source::ble, (const uint8_t *)partial.data(), 3, 100);
    assert(fpga.size() == count);
    b.feed(Source::ble, (const uint8_t *)partial.data() + 3, partial.size() - 3, 100);
    assert(fpga.size() == count + 1);
    auto bad = partial;
    bad[6] = 'Z';
    b.feed(Source::ble, (const uint8_t *)bad.data(), bad.size(), 100);
    assert(fpga.size() == count + 1);
    send(b, Source::usb, "HAP3 CMD 3 START");
    assert(last_target == Source::usb && host.back().find("code=BUSY") != std::string::npos);
    send(b, Source::ble, "HAP3 CMD 4 CAPTURE");
    assert(b.capture_pending());
    uint32_t token = armed_token;
    send(b, Source::ble, "HAP3 CMD 5 CONFIG");
    assert(host.back().find("ADC_BUSY") != std::string::npos);
    count = fpga.size();
    send(b, Source::ble, "HAP3 CMD 6 PING");
    send(b, Source::ble, "HAP3 CMD 7 STOP");
    assert(fpga.size() == count + 2);
    uint16_t samples[200];
    for (unsigned i = 0; i < 200; i++)
        samples[i] = uint16_t(1000 + i);
    count = host.size();
    b.capture_complete(token, samples, 200, 397959, 1210);
    assert(host.size() == count);
    send(b, Source::fpga,
         "HAP3 ACK 4 CAPTURE_META boot=abc rev=12 uptime_ms=500 simulated=0 tx_running=1");
    assert(!b.capture_pending() &&
           host.back().find("pin=MG24_D0 fs_hz=397959") != std::string::npos);
    assert(host.back().find("rev=12 uptime_ms=500 simulated=0 tx_running=1") != std::string::npos);
    FILE *file = fopen("mg24_capture_fixture.txt", "wb");
    assert(file);
    fwrite(host.back().data(), 1, host.back().size(), file);
    fclose(file);
    send(b, Source::ble, "HAP3 CMD 8 CAPTURE");
    token = armed_token;
    send(b, Source::fpga,
         "HAP3 ACK 8 CAPTURE_META boot=abc rev=13 uptime_ms=600 simulated=0 tx_running=0");
    count = host.size();
    b.capture_complete(token + 1, samples, 200, 400000, 1210);
    assert(host.size() == count && b.capture_pending());
    b.capture_complete(token, samples, 200, 400000, 1210);
    assert(!b.capture_pending());
    send(b, Source::ble, "HAP3 CMD 9 CAPTURE");
    token = armed_token;
    b.tick(1601);
    assert(!b.capture_pending() && host.back().find("ADC_TIMEOUT") != std::string::npos);
    count = host.size();
    b.capture_complete(token, samples, 200, 400000, 1210);
    assert(host.size() == count);
    send(b, Source::ble, "HAP3 CMD 10 CAPTURE");
    token = armed_token;
    send(b, Source::ble, "HAP3 CMD 11 HELLO");
    assert(!b.capture_pending() && !b.ready());
    count = host.size();
    b.capture_complete(token, samples, 200, 400000, 1210);
    assert(host.size() == count);
    send(b, Source::fpga, "HAP3 ACK 11 HELLO receiver=MG24 boot=abc simulated=0");
    send(b, Source::fpga, "HAP3 TEL 0 STATE boot=abc mode=REMOTE");
    count = fpga.size();
    b.disconnect(Source::ble);
    assert(fpga.size() == count + 1 && fpga.back().find(" STOP") != std::string::npos);
    hello(b);
    send(b, Source::fpga, "HAP3 TEL 0 STATE boot=abc mode=LOCAL");
    count = fpga.size();
    b.disconnect(Source::ble);
    assert(fpga.size() == count);
    hello(b);
    send(b, Source::ble, "HAP3 CMD 12 CAPTURE");
    token = armed_token;
    samples[3] = 4096;
    b.capture_complete(token, samples, 200, 400000, 1210);
    assert(host.back().find("ADC_ERROR") != std::string::npos);
    b.disconnect(Source::ble);
    hello(b);
    arm_ok = false;
    send(b, Source::ble, "HAP3 CMD 13 CAPTURE");
    assert(host.back().find("ADC_START_FAILED") != std::string::npos && !b.capture_pending());
    arm_ok = true;
    std::string over(8200, 'x');
    over += '\n';
    count = fpga.size();
    b.feed(Source::ble, (const uint8_t *)over.data(), over.size(), 100);
    assert(fpga.size() == count);
    send(b, Source::ble, "HAP3 CMD 14 PING");
    assert(fpga.size() == count + 1);
    b.disconnect(Source::ble);
    send(b, Source::usb, "HAP3 CMD 15 HELLO");
    send(b, Source::fpga, "HAP3 ACK 15 HELLO boot=abc simulated=0 adc_capture=1");
    assert(!b.ready());
    assert(host.back().find("RECEIVER_MISMATCH") != std::string::npos);
    if (argc > 1) {
        // Replay bytes emitted by the real Verilog UART transmitter. The ADC
        // values below remain a software fixture, never a hardware measurement.
        Bridge rtl(io);
        send(rtl, Source::usb, "HAP3 CMD 1 HELLO");
        std::ifstream log(argv[1]);
        assert(log);
        std::string line;
        bool captured = false;
        while (std::getline(log, line)) {
            line += '\n';
            if (line.find(" CAPTURE_META ") != std::string::npos) {
                assert(rtl.ready());
                send(rtl, Source::usb, "HAP3 CMD 7 CAPTURE");
                for (unsigned i = 0; i < 200; i++)
                    samples[i] = uint16_t(1000 + i);
                rtl.capture_complete(armed_token, samples, 200, 397959, 1210);
            }
            rtl.feed(Source::fpga, (const uint8_t *)line.data(), line.size(), 100);
            if (line.find(" CAPTURE_META ") != std::string::npos) {
                assert(!rtl.capture_pending());
                assert(host.back().find("ACK 7 CAPTURE boot=") != std::string::npos);
                assert(host.back().find("pin=MG24_D0") != std::string::npos);
                FILE *capture = fopen("mg24_rtl_capture_fixture.txt", "wb");
                assert(capture);
                fwrite(host.back().data(), 1, host.back().size(), capture);
                fclose(capture);
                captured = true;
            }
        }
        assert(captured && rtl.ready());
        puts("Verilog UART -> MG24 bridge replay passed (software ADC fixture)");
    }
    puts("MG24 bridge regression passed (fragments, CRC, ownership, capture ordering, "
         "cancellation, timeout, overflow, LOCAL)");
}
