#pragma once
#include <cstdint>

namespace mg24 {
template <typename SerialPort>
unsigned usb_send(SerialPort &port, const char *data, unsigned size) {
    // Silicon Labs UARTClass inherits availableForWrite() == 0 from Print:
    // that means writes can block, not that the port cannot transmit.
    // Bound each foreground write to about 2.8 ms at 115200 baud. Sampling
    // and its finite-window DMA completion continue in hardware/interrupts.
    constexpr unsigned chunk = 32;
    if (!size) return 0;
    return port.write(reinterpret_cast<const uint8_t *>(data), size < chunk ? size : chunk);
}
}
