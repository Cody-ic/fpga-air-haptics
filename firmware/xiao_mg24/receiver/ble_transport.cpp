#include "ble_transport.h"
#if MG24_USE_BLUETOOTH
#include <cstring>
#ifndef ARDUINO_SILABS_STACK_BLE_SILABS
#error "Select BLE (Silabs) under Tools > Protocol stack."
#endif

namespace mg24 {
static QueueHandle_t events;
static volatile bool failed = false;
static uint16_t tx_handle = 0, rx_handle = 0;
static volatile uint8_t connection = SL_BT_INVALID_CONNECTION_HANDLE;
static volatile uint16_t packet_size = 20;
static uint8_t advertising_handle = 0xff;
static bool advertising_created = false;
static const uint8_t name[] = "TouchSee-MG24-RX";

static void check(sl_status_t status) { if (status != SL_STATUS_OK) failed = true; }
static void push(BleEvent::Kind kind, const uint8_t *data = nullptr, unsigned size = 0) {
    BleEvent event{};
    event.kind = kind;
    event.size = size;
    if (size > sizeof(event.bytes)) { failed = true; return; }
    if (size) std::memcpy(event.bytes, data, size);
    if (!events || xQueueSend(events, &event, 0) != pdTRUE) failed = true;
}
void ble_begin() { events = xQueueCreate(8, sizeof(BleEvent)); if (!events) failed = true; }
bool ble_event(BleEvent &event) { return events && xQueueReceive(events, &event, 0) == pdTRUE; }
bool ble_failed() { return failed; }
void ble_close() {
    const auto handle = connection;
    if (handle != SL_BT_INVALID_CONNECTION_HANDLE) sl_bt_connection_close(handle);
    if (events) xQueueReset(events);
    // Runtime queue overflow is recoverable after resetting the connection.
    failed = !events;
}
bool ble_send(const char *data, unsigned size, unsigned &sent) {
    sent = 0;
    const auto handle = connection;
    if (handle == SL_BT_INVALID_CONNECTION_HANDLE) return false;
    const unsigned chunk = size < packet_size ? size : packet_size;
    const auto status = sl_bt_gatt_server_send_notification(handle, tx_handle, chunk,
                                                           reinterpret_cast<const uint8_t *>(data));
    if (status == SL_STATUS_OK) { sent = chunk; return true; }
    if (status == SL_STATUS_NO_MORE_RESOURCE || status == SL_STATUS_WOULD_BLOCK) return true;
    return false;
}
static void advertise() {
    if (!advertising_created) {
        check(sl_bt_advertiser_create_set(&advertising_handle));
        check(sl_bt_advertiser_set_timing(advertising_handle, 160, 160, 0, 0));
        advertising_created = true;
    }
    check(sl_bt_legacy_advertiser_generate_data(advertising_handle, sl_bt_advertiser_general_discoverable));
    check(sl_bt_legacy_advertiser_start(advertising_handle, sl_bt_advertiser_connectable_scannable));
}
static void database() {
    uint16_t session, gap_service, name_handle, service;
    check(sl_bt_gattdb_new_session(&session));
    const uint8_t gap_uuid[] = {0x00, 0x18};
    check(sl_bt_gattdb_add_service(session, sl_bt_gattdb_primary_service,
        SL_BT_GATTDB_ADVERTISED_SERVICE, sizeof(gap_uuid), gap_uuid, &gap_service));
    const sl_bt_uuid_16_t name_uuid = {.data = {0x00, 0x2a}};
    check(sl_bt_gattdb_add_uuid16_characteristic(session, gap_service,
        SL_BT_GATTDB_CHARACTERISTIC_READ, 0, 0, name_uuid, sl_bt_gattdb_fixed_length_value,
        sizeof(name) - 1, sizeof(name) - 1, name, &name_handle));
    check(sl_bt_gattdb_start_service(session, gap_service));
    // Nordic UART service: 6e400001 / RX 002 / TX 003, little-endian UUID bytes.
    const uuid_128 service_uuid = {.data = {0x9e,0xca,0xdc,0x24,0x0e,0xe5,0xa9,0xe0,0x93,0xf3,0xa3,0xb5,0x01,0x00,0x40,0x6e}};
    const uuid_128 rx_uuid = {.data = {0x9e,0xca,0xdc,0x24,0x0e,0xe5,0xa9,0xe0,0x93,0xf3,0xa3,0xb5,0x02,0x00,0x40,0x6e}};
    const uuid_128 tx_uuid = {.data = {0x9e,0xca,0xdc,0x24,0x0e,0xe5,0xa9,0xe0,0x93,0xf3,0xa3,0xb5,0x03,0x00,0x40,0x6e}};
    check(sl_bt_gattdb_add_service(session, sl_bt_gattdb_primary_service,
        SL_BT_GATTDB_ADVERTISED_SERVICE, sizeof(service_uuid), service_uuid.data, &service));
    check(sl_bt_gattdb_add_uuid128_characteristic(session, service,
        SL_BT_GATTDB_CHARACTERISTIC_WRITE | SL_BT_GATTDB_CHARACTERISTIC_WRITE_NO_RESPONSE,
        0, 0, rx_uuid, sl_bt_gattdb_variable_length_value, 244, 0, nullptr, &rx_handle));
    check(sl_bt_gattdb_add_uuid128_characteristic(session, service,
        SL_BT_GATTDB_CHARACTERISTIC_NOTIFY, 0, 0, tx_uuid,
        sl_bt_gattdb_variable_length_value, 244, 0, nullptr, &tx_handle));
    check(sl_bt_gattdb_start_service(session, service));
    check(sl_bt_gattdb_commit(session));
}
}

void sl_bt_on_event(sl_bt_msg_t *evt) {
    using namespace mg24;
    switch (SL_BT_MSG_ID(evt->header)) {
    case sl_bt_evt_system_boot_id:
        database();
        advertise();
        break;
    case sl_bt_evt_connection_opened_id:
        if (connection != SL_BT_INVALID_CONNECTION_HANDLE) {
            sl_bt_connection_close(evt->data.evt_connection_opened.connection);
            break;
        }
        connection = evt->data.evt_connection_opened.connection;
        packet_size = 20;
        push(BleEvent::Reset);
        break;
    case sl_bt_evt_connection_closed_id:
        if (evt->data.evt_connection_closed.connection != connection) break;
        connection = SL_BT_INVALID_CONNECTION_HANDLE;
        push(BleEvent::Reset);
        advertise();
        break;
    case sl_bt_evt_gatt_mtu_exchanged_id: {
        const auto size = evt->data.evt_gatt_mtu_exchanged.mtu - 3;
        packet_size = size < 244 ? size : 244;
        break;
    }
    case sl_bt_evt_gatt_server_characteristic_status_id: {
        const auto &status = evt->data.evt_gatt_server_characteristic_status;
        if (status.characteristic == tx_handle && status.status_flags == sl_bt_gatt_server_client_config) {
            push(status.client_config_flags == sl_bt_gatt_server_notification ? BleEvent::Ready : BleEvent::Reset);
        }
        break;
    }
    case sl_bt_evt_gatt_server_attribute_value_id: {
        const auto &value = evt->data.evt_gatt_server_attribute_value;
        if (value.connection == connection && value.attribute == rx_handle)
            push(BleEvent::Data, value.value.data, value.value.len);
        break;
    }
    default: break;
    }
}
#else
#include "sl_bt_api.h"
// Both builds use the same board-stack setting. USB mode deliberately handles
// no radio application events and never creates a service or starts advertising.
void sl_bt_on_event(sl_bt_msg_t *) {}
#endif
