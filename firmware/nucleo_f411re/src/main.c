#include "board.h"
static Device device;

int main(void)
{
    char boot[48];
    board_init(boot);
    hap_init(&device,board_hardware(),boot);
    for (;;) {
        board_service();
        const char *fault = board_fault();
        if (fault) {
            hap_fault(&device,fault);
            device.used = 0; device.dropping = true;
        }
        hap_poll(&device,board_millis(),false);
        /* Process at most one byte before checking the refill deadline again. */
        if (board_command_space()) {
            int byte = board_rx();
            if (byte >= 0) hap_feed(&device,(uint8_t)byte);
        }
        unsigned buttons = board_buttons();
        if (buttons & 1) hap_local_stop(&device);
        else {
            if (buttons & 2) hap_local_button(&device,true);
            if (buttons & 4) hap_local_button(&device,false);
        }
        if (buttons & 8) hap_toggle_mode(&device);
        hap_poll(&device,board_millis(),board_tx_idle());
        board_led(device.state == RUNNING);
        board_watchdog_feed();
    }
}
