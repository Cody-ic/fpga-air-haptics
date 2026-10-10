#include "app_f103.h"
#include <string.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif
static Device device;
static char sent[16384];
static size_t sent_size;
static uint16_t words[F103_HALF_WORDS], words_a[F103_HALF_WORDS], captured[HAP_ADC_SAMPLES];
static uint16_t active_cycles;
static bool active, capturing;
static uint64_t resume_us, started_ms;

static bool start(const Config *c, uint64_t us)
{
    (void)c;
    active = true; resume_us = us; started_ms = device.now_ms;
    f103_wave_reset();
    return true;
}
static void stop(void) { active = false; }
static bool readback(Sample *s)
{
    if (!active) return false;
    uint64_t us = resume_us+(device.now_ms-started_ms)*1000u;
    f103_wave_reset();
    uint64_t bank_us = us-us%F103_HALF_US;
    f103_wave_render(&device.config, bank_us, words, words_a, 0xe0u, s, &active_cycles);
    s->drive_on = (active_cycles >> ((us-bank_us)/25u)) & 1u;
    return true;
}
static void send(const char *p, size_t n)
{
    if (sent_size+n+1 < sizeof(sent)) { memcpy(sent+sent_size,p,n); sent_size+=n; sent[sent_size]=0; }
}
static bool capture_start(void)
{
    capturing = true;
    for (unsigned i=0; i<HAP_ADC_SAMPLES; ++i) captured[i] = (uint16_t)(600+i%10);
    return true;
}
static int capture_poll(const uint16_t **out) { capturing=false; *out=captured; return 1; }
static void capture_cancel(void) { capturing=false; }

API void test_init(void)
{
    active=false; capturing=false; sent_size=0; sent[0]=0;
    f103_wave_reset();
    hap_init(&device,(Hardware){start,stop,readback,send,NULL,capture_start,capture_poll,capture_cancel},"f103-test-boot");
}
API void test_feed(const char *p, size_t n, uint64_t now)
{
    hap_poll(&device,now,false);
    while (n--) hap_feed(&device,(uint8_t)*p++);
}
API const char *test_take(void) { sent[sent_size]=0; sent_size=0; return sent; }
API void test_poll(uint64_t now) { hap_poll(&device,now,true); }
API int test_active(void) { return active; }
API void test_local_start(void)
{
    hap_toggle_mode(&device);
    hap_local_button(&device, false);
}
API unsigned test_half_words(void) { return F103_HALF_WORDS; }
API unsigned test_half_us(void) { return F103_HALF_US; }
API void test_reset_wave(void) { f103_wave_reset(); }
API void test_phase(Sample *sample) { phase_solve_config(&device.config, sample); }
API void test_prepare_wave(void) { f103_wave_prepare(&device.config); }
API int test_wave_ready(uint64_t us) { return f103_wave_ready(us); }
API void test_geometry(uint64_t us, Sample *sample)
{
    float remaining;
    geometry_sample(&device.config, us, sample, &remaining);
}
API void test_wave(uint64_t us, uint16_t *out_b, uint16_t *out_a, uint16_t idle, Sample *s)
{
    f103_wave_render(&device.config,us,out_b,out_a,idle,s,&active_cycles);
}
API unsigned test_active_cycles(void) { return active_cycles; }
API void test_static_focus(uint64_t us, uint16_t *out_b, uint16_t *out_a, uint16_t idle, Sample *s)
{
    f103_wave_static_focus(&device.config, us, out_b, out_a, idle, s);
}
