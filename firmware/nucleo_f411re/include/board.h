#ifndef BOARD_H
#define BOARD_H
#include "haptics.h"
void board_init(char boot[48]);
Hardware board_hardware(void);
void board_service(void);
uint64_t board_millis(void);
int board_rx(void);
bool board_command_space(void);
bool board_tx_idle(void);
const char *board_fault(void);
unsigned board_buttons(void);
void board_watchdog_feed(void);
void board_led(bool running);
#endif
