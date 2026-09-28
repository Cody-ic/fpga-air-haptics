#ifndef RECEIVER_H
#define RECEIVER_H
#include <stdbool.h>
#include <stdint.h>
bool receiver_start(void);
int receiver_poll(const uint16_t **samples);
void receiver_cancel(void);
#endif
