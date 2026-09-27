/* Host-only test adapter. Not linked into the MCU image. */
#include "haptics.h"
#include <string.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif
static Device device;
static WaveBlock block;
static char sent[65536];
static size_t sent_size;
static bool active, start_ok;
static uint64_t started, resume_us;

static bool start(const Config *c, uint64_t us)
{
    (void)c;
    if (!start_ok) return false;
    active = true; started = device.now_ms; resume_us = us;
    return true;
}
static void stop(void) { active = false; }
static bool readback(Sample *s)
{
    if (!active) return false;
    uint64_t us = resume_us+(device.now_ms-started)*1000;
    wave_render(&device.config,us-us%1000,&block);
    *s = block.samples[(us%1000)/25];
    return true;
}
static void send(const char *p, size_t n)
{
    if (sent_size+n+1 < sizeof(sent)) { memcpy(sent+sent_size,p,n); sent_size+=n; sent[sent_size]=0; }
}
API void test_init(void)
{
    sent_size = 0; sent[0] = 0; start_ok = true;
    hap_init(&device,(Hardware){start,stop,readback,send,NULL},"test-boot-1");
}
API const char *test_take(void) { sent[sent_size]=0; sent_size=0; return sent; }
API void test_feed(const uint8_t *data, size_t n, uint64_t now)
{
    hap_poll(&device,now,false);
    for (size_t i=0; i<n; ++i) hap_feed(&device,data[i]);
}
API void test_poll(uint64_t now) { hap_poll(&device,now,true); }
API int test_active(void) { return active; }
API void test_fail_start(void) { start_ok = false; }
API void test_fault(void) { hap_fault(&device,"DMA_UNDERRUN"); }
API void test_button(unsigned button)
{
    if (!button) hap_local_stop(&device);
    else if (button == 3) hap_toggle_mode(&device);
    else hap_local_button(&device,button == 1);
}
API void test_geometry(uint64_t us, Sample *s)
{
    float remaining;
    geometry_sample(&device.config,us,s,&remaining); phase_solve(s);
}
API void test_wave(uint64_t us, WaveBlock *out) { wave_render(&device.config,us,out); }
