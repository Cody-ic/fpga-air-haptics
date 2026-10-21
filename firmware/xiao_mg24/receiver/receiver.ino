#include "config.h"
#include "sampler.h"
#include "ble_transport.h"
#include "psa/crypto.h"

static mg24::HardwareSampler sampler;
static mg24::Receiver *receiver = nullptr;
static bool ready = !MG24_USE_BLUETOOTH;

static uint64_t clock_us() {
    static uint32_t previous = 0;
    static uint64_t high = 0;
    const uint32_t low = micros();
    if (low < previous) high += uint64_t(1) << 32;
    previous = low;
    return high | low;
}

void setup() {
#if MG24_USE_BLUETOOTH
    mg24::ble_begin();
#else
    Serial.begin(115200);
#endif
    uint32_t boot = 0;
    if (psa_crypto_init() != PSA_SUCCESS
        || psa_generate_random(reinterpret_cast<uint8_t *>(&boot), sizeof(boot)) != PSA_SUCCESS) {
        // No false reusable boot identity if the hardware random source fails.
        return;
    }
    sampler.begin();
    static mg24::Receiver instance(sampler, boot, MG24_USE_BLUETOOTH ? "BLE" : "USB");
    receiver = &instance;
}

void loop() {
    if (!receiver) { delay(10); return; }
    const auto now = clock_us();
    sampler.set_epoch(now);
#if MG24_USE_BLUETOOTH
    mg24::BleEvent event{};
    while (mg24::ble_event(event)) {
        if (event.kind == mg24::BleEvent::Reset) { receiver->reset(); ready = false; }
        else if (event.kind == mg24::BleEvent::Ready) { receiver->reset(); ready = true; }
        else if (ready) receiver->feed(reinterpret_cast<const char *>(event.bytes), event.size, now);
    }
#else
    for (unsigned i = 0; i < 256 && Serial.available(); ++i) {
        const char c = Serial.read();
        receiver->feed(&c, 1, now);
    }
#endif
    if (ready) receiver->tick(clock_us());
    unsigned size = 0;
    const char *data = receiver->output(size);
    static uint64_t progress_us = 0;
    if (!size) progress_us = now;
    else if (ready) {
        unsigned sent = 0;
#if MG24_USE_BLUETOOTH
        if (!mg24::ble_send(data, size, sent)) { mg24::ble_close(); receiver->reset(); ready = false; }
#else
        const int available = Serial.availableForWrite();
        if (available > 0) sent = Serial.write(reinterpret_cast<const uint8_t *>(data),
                                              size < unsigned(available) ? size : unsigned(available));
#endif
        receiver->consume(sent);
        if (sent) progress_us = now;
    }
    if (receiver->failed() || (size && now - progress_us > 3000000)
#if MG24_USE_BLUETOOTH
        || mg24::ble_failed()
#endif
        ) {
#if MG24_USE_BLUETOOTH
        mg24::ble_close();
        ready = false;
#endif
        receiver->reset();
    }
    // Yield to the radio/RTOS without sleeping through the 0.5 ms acquisition.
    yield();
}
