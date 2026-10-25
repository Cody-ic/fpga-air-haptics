#include "receiver_core.h"
#include "usb_transport.h"
#include <iostream>
#include <string>
#include <cstdlib>

struct FakeSampler : mg24::Sampler {
    uint16_t data[mg24::SAMPLE_COUNT] = {};
    mg24::CaptureState state = mg24::CaptureState::Running;
    bool started = false;
    bool start() override { started = true; state = mg24::CaptureState::Running; return true; }
    mg24::CaptureState poll() override { return state; }
    void stop() override { started = false; }
    const uint16_t *samples() const override { return data; }
    double sample_hz() const override { return 400000; }
    uint64_t started_us() const override { return 1000; }
    uint64_t completed_us() const override { return 1500; }
};

int main(int argc, char **argv) {
    if (argc > 1 && std::string(argv[1]) == "usb-send") {
        struct UnbufferedSerial {
            std::string received;
            unsigned calls = 0, maximum = 0;
            int availableForWrite() { return 0; } // Silicon Labs inherits Print's default.
            unsigned write(const uint8_t *data, unsigned size) {
                ++calls;
                if (size > maximum) maximum = size;
                received.append(reinterpret_cast<const char *>(data), size);
                return size;
            }
        } port;
        const std::string reply(200, 'x');
        unsigned sent = 0;
        for (unsigned i = 0; i < 200 && sent < reply.size(); ++i)
            sent += mg24::usb_send(port, reply.data() + sent, reply.size() - sent);
        std::cout << (port.received == reply) << ' ' << port.maximum << ' ' << port.calls << '\n';
        return 0;
    }
    if (argc > 1 && std::string(argv[1]) == "analyze") {
        uint16_t samples[mg24::SAMPLE_COUNT];
        double fs;
        std::cin >> fs;
        for (auto &sample : samples) { unsigned value; std::cin >> value; sample = value; }
        const auto a = mg24::analyze(samples, mg24::SAMPLE_COUNT, fs);
        std::cout.precision(12);
        std::cout << a.mean << ' ' << a.rms << ' ' << a.peak40 << ' ' << a.rail << '\n';
        return 0;
    }
    FakeSampler sampler;
    for (unsigned i = 0; i < mg24::SAMPLE_COUNT; ++i)
        sampler.data[i] = std::lround(1000 + 200 * std::sin(2 * 3.141592653589793 * i / 10));
    mg24::Receiver receiver(sampler, 12345, "TEST");
    uint64_t now = 1000;
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line == "!done") sampler.state = mg24::CaptureState::Done;
        else if (line == "!overflow") sampler.state = mg24::CaptureState::Overflow;
        else if (line == "!reset") receiver.reset();
        else if (line == "!timeout") now += 2000001;
        else if (line == "!partial") receiver.feed("{\"id\":1", 7, now);
        else if (line == "!burst") {
            const char *request = "{\"id\":9,\"cmd\":\"CAPTURE\"}\n";
            for (unsigned i = 0; i < 6; ++i) receiver.feed(request, std::strlen(request), now);
        }
        else if (line.rfind("!advance ", 0) == 0) now += std::stoul(line.substr(9));
        else if (line.rfind("!chunk ", 0) == 0) {
            std::string raw = line.substr(7) + "\n";
            for (char c : raw) receiver.feed(&c, 1, now);
        } else {
            line += '\n';
            receiver.feed(line.data(), line.size(), now);
        }
        receiver.tick(now);
        unsigned size;
        while (const char *output = receiver.output(size)) {
            std::cout.write(output, size);
            receiver.consume(size);
        }
        if (receiver.failed()) std::cout << "FAILED\n";
    }
}
