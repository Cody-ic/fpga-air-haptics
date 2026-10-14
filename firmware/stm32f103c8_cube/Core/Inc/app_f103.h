#ifndef APP_F103_H
#define APP_F103_H

#include "haptics.h"

#define F103_HALF_CYCLES 10u
#define F103_HALF_WORDS (F103_HALF_CYCLES * HAP_STEPS)
#define F103_HALF_US (F103_HALF_CYCLES * 25u)
#define F103_GPIOB_MASK 0xfffbu /* PB2/BOOT1 is not an array output. */
#define F103_GPIOA_MASK (1u << 8u) /* Logical channel 2 uses exposed PA8. */
#define F103_AUTOSTART_MS 3000u
#define F103_LOOKAHEAD 4u

void app_f103_init(void);
void app_f103_poll(void);
void app_f103_wave_irq(void);
void app_f103_uart_irq(void);
void app_f103_shutdown(void);
void f103_wave_reset(void);
bool f103_wave_prepare(const Config *);
bool f103_wave_ready(uint64_t);
void f103_wave_render(const Config *, uint64_t, uint16_t *, uint16_t *, uint16_t, Sample *);
bool f103_drive_on(const Config *, const Sample *, uint64_t);

#endif
