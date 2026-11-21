// Тест ядра плагина на Linux: подставляем фальшивый SCS и гоняем синтетическую поездку.
#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>
#include "scssdk_telemetry.h"
#include "eurotrucks2/scssdk_eut2.h"
#include "eurotrucks2/scssdk_telemetry_eut2.h"
#include "common/scssdk_telemetry_common_channels.h"
#include "common/scssdk_telemetry_common_configs.h"
#include "common/scssdk_telemetry_common_gameplay_events.h"
#include "common/scssdk_telemetry_job_common_channels.h"
#include "common/scssdk_telemetry_trailer_common_channels.h"
#include "common/scssdk_telemetry_truck_common_channels.h"

extern double g_fake_mono;
extern std::string g_test_base;
extern "C" scs_result_t scs_telemetry_init(const scs_u32_t, const scs_telemetry_init_params_t* const);
extern "C" void scs_telemetry_shutdown(void);

struct Ev { scs_event_t e; scs_telemetry_event_callback_t cb; };
static std::vector<Ev> evs;
struct Chn { scs_telemetry_channel_callback_t cb; scs_context_t ctx; };
static std::map<std::string, Chn> chs;

static scs_result_t reg_ev(const scs_event_t e, const scs_telemetry_event_callback_t cb, const scs_context_t) { evs.push_back({e, cb}); return SCS_RESULT_ok; }
static scs_result_t reg_ch(const scs_string_t n, const scs_u32_t, const scs_value_type_t, const scs_u32_t, const scs_telemetry_channel_callback_t cb, const scs_context_t ctx) { chs[n] = {cb, ctx}; return SCS_RESULT_ok; }
static void logf_(const scs_log_type_t, const scs_string_t m) { printf("LOG: %s\n", m); }

static void fire(scs_event_t e, const void* info) { for (auto& x : evs) if (x.e == e) x.cb(e, info, nullptr); }
static void setf(const char* n, float v) { scs_value_t x; x.type = SCS_VALUE_TYPE_float; x.value_float.value = v; chs[n].cb(n, SCS_U32_NIL, &x, chs[n].ctx); }
static void seti(const char* n, int v) { scs_value_t x; x.type = SCS_VALUE_TYPE_s32; x.value_s32.value = v; chs[n].cb(n, SCS_U32_NIL, &x, chs[n].ctx); }
static void setu(const char* n, unsigned v) { scs_value_t x; x.type = SCS_VALUE_TYPE_u32; x.value_u32.value = v; chs[n].cb(n, SCS_U32_NIL, &x, chs[n].ctx); }
static void setb(const char* n, bool v) { scs_value_t x; x.type = SCS_VALUE_TYPE_bool; x.value_bool.value = v; chs[n].cb(n, SCS_U32_NIL, &x, chs[n].ctx); }
static void setpos(double px, double py, double pz, double hd) {
    scs_value_t x; memset(&x, 0, sizeof x); x.type = SCS_VALUE_TYPE_dplacement;
    x.value_dplacement.position.x = px; x.value_dplacement.position.y = py; x.value_dplacement.position.z = pz; x.value_dplacement.orientation.heading = (float)hd;
    chs["truck.world.placement"].cb("truck.world.placement", SCS_U32_NIL, &x, chs["truck.world.placement"].ctx);
}
static void setacc(float ax) { scs_value_t x; x.type = SCS_VALUE_TYPE_fvector; x.value_fvector.x = ax; x.value_fvector.y = 0; x.value_fvector.z = 0; chs["truck.local.acceleration.linear"].cb("truck.local.acceleration.linear", SCS_U32_NIL, &x, chs["truck.local.acceleration.linear"].ctx); }

static scs_value_t sv(const char* s) { scs_value_t v; v.type = SCS_VALUE_TYPE_string; v.value_string.value = s; return v; }
static scs_value_t nv(double d, scs_value_type_t t) { scs_value_t v; memset(&v, 0, sizeof v); v.type = t;
    if (t == SCS_VALUE_TYPE_u32) v.value_u32.value = (unsigned)d; else if (t == SCS_VALUE_TYPE_s64) v.value_s64.value = (long long)d;
    else if (t == SCS_VALUE_TYPE_float) v.value_float.value = (float)d; else if (t == SCS_VALUE_TYPE_bool) v.value_bool.value = d != 0;
    else if (t == SCS_VALUE_TYPE_s32) v.value_s32.value = (int)d; else if (t == SCS_VALUE_TYPE_u64) v.value_u64.value = (unsigned long long)d; return v; }
static scs_named_value_t at(const char* n, scs_value_t v) { scs_named_value_t a; a.name = n; a.index = SCS_U32_NIL; a.value = v; return a; }

int main(int argc, char** argv) {
    g_test_base = argc > 1 ? argv[1] : "/tmp/yassno_test";
    scs_telemetry_init_params_v100_t p; memset(&p, 0, sizeof p);
    p.common.game_id = SCS_GAME_ID_EUT2; p.common.game_version = SCS_TELEMETRY_EUT2_GAME_VERSION_CURRENT; p.common.log = logf_;
    p.register_for_event = reg_ev; p.register_for_channel = reg_ch;
    g_fake_mono = 1000.0;
    if (scs_telemetry_init(SCS_TELEMETRY_VERSION_1_01, &p) != SCS_RESULT_ok) { printf("init failed\n"); return 1; }
    printf("каналов зарегистрировано: %zu, событий: %zu\n", chs.size(), evs.size());

    // конфигурации
    {
        scs_named_value_t a[] = { at("brand", sv("Volvo")), at("name", sv("FH")), at("id", sv("volvo.fh")), at("license.plate", sv("AB 123")),
            at("fuel.capacity", nv(700, SCS_VALUE_TYPE_float)), at("rpm.limit", nv(2300, SCS_VALUE_TYPE_float)), at("gears.forward", nv(12, SCS_VALUE_TYPE_u32)), {nullptr, 0, {}} };
        scs_telemetry_configuration_t c; c.id = "truck"; c.attributes = a; fire(SCS_TELEMETRY_EVENT_configuration, &c);
        scs_named_value_t b[] = { at("id", sv("boat")), at("name", sv("Boat trailer")), at("body.type", sv("lowloader")), {nullptr, 0, {}} };
        scs_telemetry_configuration_t d; d.id = "trailer"; d.attributes = b; fire(SCS_TELEMETRY_EVENT_configuration, &d);
        scs_named_value_t j[] = { at("cargo.id", sv("boat")), at("cargo", sv("Roskoshnaya")), at("cargo.mass", nv(9000, SCS_VALUE_TYPE_float)),
            at("source.city", sv("Bayonne")), at("source.company", sv("Port")), at("destination.city", sv("Valladolid")), at("destination.company", sv("Depot \"A\"")),
            at("income", nv(12345, SCS_VALUE_TYPE_u64)), at("planned_distance.km", nv(400, SCS_VALUE_TYPE_u32)), at("cargo.loaded", nv(1, SCS_VALUE_TYPE_bool)),
            at("delivery.time", nv(99999, SCS_VALUE_TYPE_u32)), {nullptr, 0, {}} };
        scs_telemetry_configuration_t e; e.id = "job"; e.attributes = j; fire(SCS_TELEMETRY_EVENT_configuration, &e);
    }
    fire(SCS_TELEMETRY_EVENT_started, nullptr);
    setb("truck.engine.enabled", true); setb("trailer.connected", true);
    float fuel = 500, odo = 1000; double x = 0, z = 0; float wear_cab = 0;
    setf("truck.fuel.amount", fuel); setf("truck.odometer", odo); setu("game.time", 8 * 60); seti("rest.stop", 600);
    float speed = 0; setf("truck.speed", 0); setpos(x, 50, z, 0.25);
    const double dt = 1.0 / 60;
    int frames = 0;
    for (double t = 0; t < 320; t += dt, ++frames) {
        float lim = 22.2f; setf("truck.navigation.speed.limit", lim);
        float thr = 0, brk = 0; float target = speed;
        if (t < 30) { target = 25.0f * (float)(t / 30); thr = 0.8f; }
        else if (t < 90) { target = 25.0f; thr = 0.3f; setf("truck.cruise_control", 25.0f); }      // круиз 90 км/ч при лимите 80
        else if (t < 93) { setf("truck.cruise_control", 0); target = 25.0f - (t - 90) * 6.7f; brk = 1; }  // экстренное торможение
        else if (t < 120) { target = 5.0f + (float)((t - 93) / 27) * 17; thr = 0.6f; }
        else if (t < 150) { target = 22.0f; thr = 0.2f; }
        else if (t < 160) { target = std::max(0.0f, 22.0f - (float)(t - 150) * 2.2f); brk = 0.4f; }  // мягко до остановки
        else if (t < 250) { target = 0; }
        else if (t < 300) { target = 12; thr = 0.5f; }
        else { target = std::max(0.0f, 12.0f - (float)(t - 300) * 1.2f); brk = 0.3f; }
        speed = target;
        setf("truck.speed", speed); setf("truck.input.throttle", thr); setf("truck.input.brake", brk);
        seti("truck.engine.gear", speed > 18 ? 12 : speed > 8 ? 8 : speed > 0.5 ? 4 : 0);
        setf("truck.engine.rpm", 600 + speed * 60);
        setb("truck.light.beam.low", true);
        setb("truck.wipers", t > 100 && t < 140);
        setf("truck.fuel.amount", fuel);
        if (speed > 0.5) fuel -= (float)(0.0075 * dt * speed / 25.0 * 2); else fuel -= (float)(0.0006 * dt);
        if (t > 248 && t < 248 + dt) { fuel += 100; }
        odo += (float)(speed * dt / 1000.0); setf("truck.odometer", odo);
        x += speed * dt; z += 0.1 * speed * dt; setpos(x, 50 + 10 * std::sin(t / 40), z, 0.25);
        setacc(t > 120 && t < 125 ? 4.0f : 0.0f);
        setu("game.time", 8 * 60 + (unsigned)(t / 3));
        if (t >= 200 && t < 200 + 3 * dt) { wear_cab += 0.01f; setf("truck.wear.cabin", wear_cab); }
        g_fake_mono += dt;
        fire(SCS_TELEMETRY_EVENT_frame_start, nullptr);
        if (frames == 150 * 60) {
            scs_named_value_t a[] = { at("fine.offence", sv("speeding_camera")), at("fine.amount", nv(500, SCS_VALUE_TYPE_s64)), {nullptr, 0, {}} };
            scs_telemetry_gameplay_event_t g; g.id = "player.fined"; g.attributes = a; fire(SCS_TELEMETRY_EVENT_gameplay, &g);
        }
        if (frames == 100 * 60) {
            scs_named_value_t a[] = { at("pay.amount", nv(12, SCS_VALUE_TYPE_s64)), {nullptr, 0, {}} };
            scs_telemetry_gameplay_event_t g; g.id = "player.tollgate.paid"; g.attributes = a; fire(SCS_TELEMETRY_EVENT_gameplay, &g);
        }
        if (frames == 310 * 60) {
            scs_named_value_t a[] = { at("revenue", nv(12000, SCS_VALUE_TYPE_s64)), at("earned.xp", nv(250, SCS_VALUE_TYPE_s32)),
                at("cargo.damage", nv(0.02, SCS_VALUE_TYPE_float)), at("distance.km", nv(398, SCS_VALUE_TYPE_float)), at("delivery.time", nv(500, SCS_VALUE_TYPE_u32)),
                at("auto.park.used", nv(0, SCS_VALUE_TYPE_bool)), at("auto.load.used", nv(0, SCS_VALUE_TYPE_bool)), {nullptr, 0, {}} };
            scs_telemetry_gameplay_event_t g; g.id = "job.delivered"; g.attributes = a; fire(SCS_TELEMETRY_EVENT_gameplay, &g);
            scs_telemetry_configuration_t e; scs_named_value_t none[] = { {nullptr, 0, {}} }; e.id = "job"; e.attributes = none; fire(SCS_TELEMETRY_EVENT_configuration, &e);
        }
    }
    scs_telemetry_shutdown();
    printf("кадров: %d\n", frames);
    return 0;
}
