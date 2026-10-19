#include "FreeRTOS.h"
#include "app_assert.h"
#include "bridge.h"
#include "config.h"
#include "sampler.h"
#include "sl_bluetooth.h"
#include "task.h"
#include <Arduino.h>
#include <string.h>

#ifndef ARDUINO_SILABS_STACK_BLE_SILABS
#error "Select XIAO MG24 and Protocol stack: BLE (Silabs)"
#endif
static_assert(HAPTICS_CAPTURE_SAMPLES == 200, "HAP3 currently supports exactly 200 samples");

namespace {
// Single producer/single consumer. A complete frame is committed atomically.
template <size_t N> class Queue {
    uint8_t bytes[N];
    volatile uint32_t read_ = 0, write_ = 0;

  public:
    bool push(const uint8_t *p, size_t n) {
        uint32_t w = write_;
        if (n > N - uint32_t(w - read_))
            return false;
        for (size_t i = 0; i < n; i++)
            bytes[(w + i) % N] = p[i];
        __DMB();
        write_ = w + uint32_t(n);
        return true;
    }
    size_t peek(uint8_t *p, size_t n) const {
        uint32_t r = read_, available = write_ - r;
        if (n > available)
            n = available;
        for (size_t i = 0; i < n; i++)
            p[i] = bytes[(r + i) % N];
        return n;
    }
    void consume(size_t n) {
        __DMB();
        read_ += uint32_t(n);
    }
    void clear() {
        noInterrupts();
        read_ = write_;
        interrupts();
    }
};
Queue<16384> fpga_tx;
Queue<8192> usb_tx, ble_tx, ble_rx, fpga_rx, usb_rx;
volatile bool serial_overflow = false;
volatile uint8_t connection = SL_BT_INVALID_CONNECTION_HANDLE;
volatile bool subscribed = false, ble_lost = false, ble_overflow = false;
volatile uint16_t notify_size = 20;
uint16_t rx_handle = 0, tx_handle = 0;
bool send_fpga(const char *p, size_t n) { return fpga_tx.push((const uint8_t *)p, n); }
bool send_host(hap::Source s, const char *p, size_t n) {
    if (s == hap::Source::usb)
        return usb_tx.push((const uint8_t *)p, n);
    if (s == hap::Source::ble && subscribed)
        return ble_tx.push((const uint8_t *)p, n);
    return false;
}
hap::Bridge bridge({send_fpga, send_host, sampler_arm, sampler_cancel});
uint8_t adv_set = 0xff;
bool sampler_ready = false;
const uint8_t name[] = HAPTICS_DEVICE_NAME;
void advertise() {
    if (adv_set == 0xff) {
        app_assert_status(sl_bt_advertiser_create_set(&adv_set));
        app_assert_status(sl_bt_advertiser_set_timing(adv_set, 160, 160, 0, 0));
    }
    app_assert_status(
        sl_bt_legacy_advertiser_generate_data(adv_set, sl_bt_advertiser_general_discoverable));
    app_assert_status(
        sl_bt_legacy_advertiser_start(adv_set, sl_bt_advertiser_connectable_scannable));
}
void gatt_init() {
    uint16_t session, service, gap, device_name;
    app_assert_status(sl_bt_gattdb_new_session(&session));
    const uint8_t gap_uuid[] = {0, 0x18};
    app_assert_status(
        sl_bt_gattdb_add_service(session, sl_bt_gattdb_primary_service, 0, 2, gap_uuid, &gap));
    const sl_bt_uuid_16_t name_uuid = {{0, 0x2a}};
    app_assert_status(sl_bt_gattdb_add_uuid16_characteristic(
        session, gap, SL_BT_GATTDB_CHARACTERISTIC_READ, 0, 0, name_uuid,
        sl_bt_gattdb_fixed_length_value, sizeof(name) - 1, sizeof(name) - 1, name, &device_name));
    app_assert_status(sl_bt_gattdb_start_service(session, gap));
    // Nordic UART Service UUIDs, matching desktop_app and mobile_app defaults.
    const uuid_128 service_uuid = {
        {0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0, 0x93, 0xf3, 0xa3, 0xb5, 1, 0, 0x40, 0x6e}};
    const uuid_128 rx_uuid = {
        {0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0, 0x93, 0xf3, 0xa3, 0xb5, 2, 0, 0x40, 0x6e}};
    const uuid_128 tx_uuid = {
        {0x9e, 0xca, 0xdc, 0x24, 0x0e, 0xe5, 0xa9, 0xe0, 0x93, 0xf3, 0xa3, 0xb5, 3, 0, 0x40, 0x6e}};
    app_assert_status(sl_bt_gattdb_add_service(session, sl_bt_gattdb_primary_service,
                                               SL_BT_GATTDB_ADVERTISED_SERVICE, 16,
                                               service_uuid.data, &service));
    app_assert_status(sl_bt_gattdb_add_uuid128_characteristic(
        session, service,
        SL_BT_GATTDB_CHARACTERISTIC_WRITE | SL_BT_GATTDB_CHARACTERISTIC_WRITE_NO_RESPONSE, 0, 0,
        rx_uuid, sl_bt_gattdb_variable_length_value, 250, 0, nullptr, &rx_handle));
    app_assert_status(sl_bt_gattdb_add_uuid128_characteristic(
        session, service, SL_BT_GATTDB_CHARACTERISTIC_NOTIFY, 0, 0, tx_uuid,
        sl_bt_gattdb_variable_length_value, 250, 0, nullptr, &tx_handle));
    app_assert_status(sl_bt_gattdb_start_service(session, service));
    app_assert_status(sl_bt_gattdb_commit(session));
    uint16_t actual;
    app_assert_status(sl_bt_gatt_set_max_mtu(247, &actual));
}
template <size_t N> void drain_serial(Queue<N> &queue, HardwareSerial &serial) {
    uint8_t buf[16];
    size_t n = queue.peek(buf, sizeof(buf));
    if (n) {
        size_t sent = serial.write(buf, n);
        queue.consume(sent);
    }
}
#if HAPTICS_MG24_MODE == HAPTICS_MODE_BUSINESS
void serial_reader(void *) {
    // The SDK's default hardware RX buffers are only 32 bytes. Drain both UARTs
    // every RTOS tick even while loop() is transmitting or handling Bluetooth.
    static_assert(configTICK_RATE_HZ >= 1000, "Serial reader requires <=1 ms tick");
    for (;;) {
        uint8_t buf[64];
        size_t n = 0;
        while (Serial1.available() && n < sizeof(buf))
            buf[n++] = uint8_t(Serial1.read());
        if (n && !fpga_rx.push(buf, n))
            serial_overflow = true;
        n = 0;
        while (Serial.available() && n < sizeof(buf))
            buf[n++] = uint8_t(Serial.read());
        if (n && !usb_rx.push(buf, n))
            serial_overflow = true;
        vTaskDelay(1);
    }
}
#endif
} // namespace

void sl_bt_on_event(sl_bt_msg_t *event) {
    switch (SL_BT_MSG_ID(event->header)) {
    case sl_bt_evt_system_boot_id:
        gatt_init();
        advertise();
        break;
    case sl_bt_evt_connection_opened_id:
        if (connection != SL_BT_INVALID_CONNECTION_HANDLE) {
            sl_bt_connection_close(event->data.evt_connection_opened.connection);
            break;
        }
        connection = event->data.evt_connection_opened.connection;
        notify_size = 20;
        subscribed = false;
        sl_bt_connection_set_parameters(connection, 6, 12, 0, 200, 0, 0xffff);
        break;
    case sl_bt_evt_connection_closed_id:
        if (event->data.evt_connection_closed.connection == connection) {
            connection = SL_BT_INVALID_CONNECTION_HANDLE;
            subscribed = false;
            ble_lost = true;
            advertise();
        }
        break;
    case sl_bt_evt_gatt_mtu_exchanged_id:
        notify_size = uint16_t(min(unsigned(event->data.evt_gatt_mtu_exchanged.mtu - 3), 250u));
        break;
    case sl_bt_evt_gatt_server_characteristic_status_id: {
        auto &s = event->data.evt_gatt_server_characteristic_status;
        if (s.characteristic == tx_handle && s.status_flags == sl_bt_gatt_server_client_config) {
            subscribed = (s.client_config_flags & sl_bt_gatt_server_notification) != 0;
            if (!subscribed)
                ble_lost = true;
        }
        break;
    }
    case sl_bt_evt_gatt_server_attribute_value_id: {
        auto &v = event->data.evt_gatt_server_attribute_value;
        if (v.attribute == rx_handle && subscribed && !ble_rx.push(v.value.data, v.value.len))
            ble_overflow = true;
        break;
    }
    default:
        break;
    }
}
void app_setup() {
    Serial.begin(HAPTICS_HOST_BAUD);
    Serial1.begin(HAPTICS_FPGA_BAUD);
    pinMode(LED_BUILTIN, OUTPUT);
    digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
    // Failed initialization leaves all capture requests explicitly failing.
    sampler_ready = sampler_init();
#if HAPTICS_MG24_MODE == HAPTICS_MODE_BUSINESS
    if (xTaskCreate(serial_reader, "hap-rx", 512, nullptr, configMAX_PRIORITIES - 2, nullptr) !=
        pdPASS)
        serial_overflow = true;
#endif
}
void app_loop() {
    uint16_t samples[200];
    uint32_t token, rate;
    bool failed;
#if HAPTICS_MG24_MODE == HAPTICS_MODE_ADC_TEST
    static uint32_t previous = 0;
    static bool waiting = false;
    if (waiting && uint32_t(millis() - previous) > HAPTICS_CAPTURE_TIMEOUT_MS) {
        sampler_cancel();
        waiting = false;
        Serial.println("MG24_ADC_TEST error=ADC_TIMEOUT");
    }
    if (uint32_t(millis() - previous) >= 2000) {
        previous = millis();
        if (sampler_ready && sampler_arm(previous)) {
            waiting = true;
            sampler_trigger();
        } else {
            Serial.println("MG24_ADC_TEST error=ADC_START_FAILED");
        }
    }
    if (sampler_take(token, samples, rate, failed)) {
        waiting = false;
        Serial.printf("MG24_ADC_TEST fs_hz=%lu full_scale_mv=%u error=%u raw=", (unsigned long)rate,
                      HAPTICS_ADC_FULL_SCALE_MV, unsigned(failed));
        for (unsigned i = 0; i < 200; i++)
            Serial.printf("%s%u", i ? "," : "", samples[i]);
        Serial.println();
    }
#else
    if (ble_lost || ble_overflow) {
        noInterrupts();
        bool overflow = ble_overflow;
        ble_lost = false;
        ble_overflow = false;
        interrupts();
        ble_rx.clear();
        ble_tx.clear();
        bridge.disconnect(hap::Source::ble);
        if (overflow && connection != SL_BT_INVALID_CONNECTION_HANDLE)
            sl_bt_connection_close(connection);
    }
    uint8_t buf[128];
    size_t n = 0;
    if (serial_overflow) {
        serial_overflow = false;
        fpga_rx.clear();
        usb_rx.clear();
        bridge.disconnect(bridge.owner());
        if (connection != SL_BT_INVALID_CONNECTION_HANDLE)
            sl_bt_connection_close(connection);
    }
    n = fpga_rx.peek(buf, sizeof(buf));
    fpga_rx.consume(n);
    if (n)
        bridge.feed(hap::Source::fpga, buf, n, millis());
    n = usb_rx.peek(buf, sizeof(buf));
    usb_rx.consume(n);
    if (n)
        bridge.feed(hap::Source::usb, buf, n, millis());
    n = ble_rx.peek(buf, sizeof(buf));
    if (n) {
        ble_rx.consume(n);
        bridge.feed(hap::Source::ble, buf, n, millis());
    }
    if (sampler_take(token, samples, rate, failed)) {
        if (failed)
            bridge.capture_failed(token);
        else
            bridge.capture_complete(token, samples, 200, rate, HAPTICS_ADC_FULL_SCALE_MV);
    }
    bridge.tick(millis());
    drain_serial(fpga_tx, Serial1);
    drain_serial(usb_tx, Serial);
    if (subscribed) {
        uint8_t data[250];
        n = ble_tx.peek(data, notify_size);
        if (n) {
            sl_status_t status =
                sl_bt_gatt_server_send_notification(connection, tx_handle, n, data);
            if (status == SL_STATUS_OK)
                ble_tx.consume(n);
            else if (status != SL_STATUS_NO_MORE_RESOURCE && status != SL_STATUS_ALLOCATION_FAILED)
                sl_bt_connection_close(connection);
        }
    }
    if (bridge.ready())
        digitalWrite(LED_BUILTIN, LED_BUILTIN_ACTIVE);
    else
        digitalWrite(LED_BUILTIN, LED_BUILTIN_INACTIVE);
#endif
    delay(1);
}
