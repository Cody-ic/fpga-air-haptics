/* Reuse the register/protocol fixture, exercising actual application code. */
#define main dynamic_driver_suite_main
#include "wave_driver_harness.c"
#undef main

static void fixed_carrier(void)
{
    assert(playing && static_focus);
    assert(channel_a.CNDTR == HAP_STEPS && channel_b.CNDTR == HAP_STEPS);
    assert(!(channel_a.CCR & (DMA_CCR_HTIE | DMA_CCR_TCIE)));
    assert(!(channel_b.CCR & (DMA_CCR_HTIE | DMA_CCR_TCIE)));
    assert(timer.ARR == 24u && timer.PSC == 0u);
    for (unsigned ch = 0; ch < HAP_CHANNELS; ++ch) {
        unsigned highs = 0, rises = 0;
        bool previous = false;
        unsigned last_rise = 0;
        for (unsigned i = 0; i < HAP_STEPS * 40u; ++i) {
            unsigned slot = i % HAP_STEPS;
            uint16_t logical = waves_b[0][slot] | ((waves_a[0][slot] & 0x100u) ? 4u : 0u);
            bool high = (logical & (1u << ch)) != 0;
            assert(!(waves_b[0][slot] & 4u));
            assert((waves_a[0][slot] & ~0x100u) == (gpioa_idle & ~0x100u));
            if (i < HAP_STEPS && high) ++highs;
            if (i && high && !previous) {
                if (rises) assert(i - last_rise == HAP_STEPS);
                ++rises;
                last_rise = i;
            }
            previous = high;
        }
        assert(highs == ((device.config.channel_mask & (1u << ch)) ? 32u : 0u));
        assert(rises == ((device.config.channel_mask & (1u << ch)) ? 39u + (samples[0].phases[ch] != 0) : 0u));
    }
}

int main(void)
{
    boot_wait();
    startup_schedule(0);
    assert(device.config.shape == F103_BUSINESS_SHAPE);
    assert(device.config.cx_um == F103_BUSINESS_X_UM);
    assert(device.config.cy_um == F103_BUSINESS_Y_UM);
    assert(device.config.z_um == F103_BUSINESS_Z_UM);
    assert(device.config.mod_hz == F103_BUSINESS_MOD_HZ);
    assert(device.config.level == F103_BUSINESS_LEVEL);
    assert(F103_BUSINESS_SHAPE == POINT && F103_BUSINESS_LEVEL == 100 && F103_BUSINESS_MOD_HZ == 0);
    autostart_poll(F103_AUTOSTART_MS - 1u, false);
    assert(!playing);
    uptime_ms = F103_AUTOSTART_MS;
    autostart_poll(uptime_ms, false);
    assert(device.state == RUNNING && device.local);
    fixed_carrier();
#if F103_AUTO_RUN_LIMIT_MS == 0
    assert(!auto_run_pending);
    auto_run_poll(uptime_ms + 60000u);
    assert(playing);
#else
    assert(auto_run_pending);
    auto_run_poll(auto_run_deadline_ms - 1u);
    assert(playing);
#endif
    Sample first, later;
    assert(output_readback(&first));
    uptime_ms += 17u;
    assert(output_readback(&later));
    assert(later.elapsed_us == first.elapsed_us + 17000u);
    assert(!memcmp(first.phases, later.phases, sizeof(first.phases)));
    assert(later.x_um == F103_BUSINESS_X_UM && later.y_um == F103_BUSINESS_Y_UM);
    /* Static output has no producer deadline, even during long replies. */
    refill = true; ready[0] = ready[1] = false; next_us = 1000000;
    service();
    assert(playing && !pending_fault);
    dma.ISR = DMA_ISR_HTIF2 | DMA_ISR_TCIF2;
    app_f103_wave_irq();
    assert(playing && !pending_fault);
    command(1, "HELLO");
    command(2, "SNAP");
    assert(strstr(serial_reply, "firmware_mode=0"));
    assert(strstr(serial_reply, "shape=POINT"));
    assert(strstr(serial_reply, "output=1"));
    command(3, "STOP");
    off(NULL);
    assert(device.state == IDLE && !static_focus && !auto_run_pending);
    command(4, "MODE value=REMOTE");
    configure_point(5);
    command(6, "START");
    fixed_carrier();
    uptime_ms += 13u;
    command(7, "PAUSE");
    assert(device.state == PAUSED && !playing && !static_focus);
    uint64_t resume = device.elapsed_us;
    command(8, "START");
    assert(playing && static_focus && static_resume_us == resume / 25u * 25u);
    command(9, "STOP");
    assert(!playing);
    /* Remote modulation and moving shapes still select the dynamic backend. */
    device.config.mod_hz = 200;
    assert(output_start(&device.config, 0) && !static_focus);
    assert(channel_a.CNDTR == 2u * F103_HALF_WORDS);
    assert(channel_a.CCR & DMA_CCR_HTIE);
    app_f103_shutdown();
    device.config.mod_hz = 0; device.config.shape = CIRCLE;
    assert(output_start(&device.config, 0) && !static_focus);
    app_f103_shutdown();
    device.config.shape = POINT; device.config.level = 30;
    assert(output_start(&device.config, 0) && !static_focus);
    app_f103_shutdown();
    device.config.level = 100; device.config.channel_mask = 4u;
    device.config.phase_offsets[2] = 17;
    assert(output_start(&device.config, 0));
    fixed_carrier();
    for (unsigned error = 0; error < 2; ++error) {
        pending_fault = NULL;
        assert(output_start(&device.config, 0));
        dma.ISR = error ? DMA_ISR_TEIF2 : DMA_ISR_TEIF5;
        app_f103_wave_irq();
        off("DMA_ERROR");
    }
    for (unsigned masked = 0; masked < 2; ++masked) {
        pending_fault = NULL;
        interrupt_mask = masked;
        inject_render_fault = true;
        assert(!output_start(&device.config, 0));
        assert(interrupt_mask == masked);
        off("UART_RX_ERROR");
    }
    interrupt_mask = 0;
    pending_fault = NULL;
    device.state = IDLE; device.local = false;
    configure_point(10);
    command(11, "START");
    assert(playing && static_focus);
    hap_poll(&device, device.last_ping_ms + 3000u, false);
    off(NULL);
    assert(device.state == IDLE && !strcmp(device.reason, "HEARTBEAT_TIMEOUT"));
    puts("F103 business focus: carrier, mode, readback and failure checks passed");
    return 0;
}
