// Yassno ETS2/ATS statistics logger — SCS Telemetry SDK plugin.
//
// Пишет статистику поездок в файлы, пока запущена игра (лаунчер для этого запускать не нужно):
//   %LOCALAPPDATA%\YassnoOverlay\ets2\sessions\<id>.json   — итоги сессии (обновляется каждые ~5 с)
//   %LOCALAPPDATA%\YassnoOverlay\ets2\jobs.jsonl           — по строке на завершённый/отменённый рейс
//   %LOCALAPPDATA%\YassnoOverlay\ets2\events.jsonl         — события: штрафы, дорожные сборы, заправки, удары, ремонт…
//   %LOCALAPPDATA%\YassnoOverlay\ets2\tracks\<id>.csv      — трек: unix_time,x,z,y,км/ч,курс
// Для ATS папка называется "ats". Плагин ничего не читает из лаунчера и ни во что не вмешивается.

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "scssdk_telemetry.h"
#include "eurotrucks2/scssdk_eut2.h"
#include "eurotrucks2/scssdk_telemetry_eut2.h"
#include "amtrucks/scssdk_ats.h"
#include "amtrucks/scssdk_telemetry_ats.h"
#include "common/scssdk_telemetry_common_channels.h"
#include "common/scssdk_telemetry_common_configs.h"
#include "common/scssdk_telemetry_common_gameplay_events.h"
#include "common/scssdk_telemetry_job_common_channels.h"
#include "common/scssdk_telemetry_trailer_common_channels.h"
#include "common/scssdk_telemetry_truck_common_channels.h"

#define PLUGIN_VERSION "1.0.0"
#define SCHEMA 1

// =====================================================================================================================
//  платформа: Windows (поток записи, LOCALAPPDATA) или POSIX (для проверки ядра на Linux)
// =====================================================================================================================
#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

static double plat_unix() {
    FILETIME ft; GetSystemTimeAsFileTime(&ft);
    unsigned long long t = ((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    return (double)(t - 116444736000000000ULL) / 1e7;
}
static double plat_mono() {
    static LARGE_INTEGER f = {};
    if (!f.QuadPart) QueryPerformanceFrequency(&f);
    LARGE_INTEGER c; QueryPerformanceCounter(&c);
    return (double)c.QuadPart / (double)f.QuadPart;
}
static std::wstring widen(const std::string& s) {
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
    std::wstring w((size_t)n, L'\0');
    if (n) MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}
static std::string plat_base() {
    wchar_t b[MAX_PATH * 2] = {};
    DWORD n = GetEnvironmentVariableW(L"LOCALAPPDATA", b, MAX_PATH * 2);
    if (!n || n >= MAX_PATH * 2) return "";
    int m = WideCharToMultiByte(CP_UTF8, 0, b, (int)n, nullptr, 0, nullptr, nullptr);
    std::string s((size_t)m, '\0');
    WideCharToMultiByte(CP_UTF8, 0, b, (int)n, &s[0], m, nullptr, nullptr);
    return s + "\\YassnoOverlay";
}
static const char SEP = '\\';
static void mkdirs(const std::string& p) {
    std::wstring w = widen(p);
    for (size_t i = 1; i < w.size(); ++i)
        if (w[i] == L'\\') { w[i] = 0; CreateDirectoryW(w.c_str(), nullptr); w[i] = L'\\'; }
    CreateDirectoryW(w.c_str(), nullptr);
}
static void file_replace(const std::string& path, const std::string& data) {
    std::wstring w = widen(path), t = w + L".tmp";
    HANDLE h = CreateFileW(t.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD wr = 0; WriteFile(h, data.data(), (DWORD)data.size(), &wr, nullptr); CloseHandle(h);
    if (wr != data.size() || !MoveFileExW(t.c_str(), w.c_str(), MOVEFILE_REPLACE_EXISTING)) DeleteFileW(t.c_str());
}
static void file_append(const std::string& path, const std::string& data) {
    std::wstring w = widen(path);
    HANDLE h = CreateFileW(w.c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD wr = 0; WriteFile(h, data.data(), (DWORD)data.size(), &wr, nullptr); CloseHandle(h);
}
static void local_time(int& Y, int& M, int& D, int& h, int& m, int& s) {
    SYSTEMTIME t; GetLocalTime(&t); Y = t.wYear; M = t.wMonth; D = t.wDay; h = t.wHour; m = t.wMinute; s = t.wSecond;
}

// Запись идёт в отдельном потоке, чтобы диск не тормозил кадры игры.
struct Job { bool append; std::string path, data; };
static std::vector<Job> g_q;
static CRITICAL_SECTION g_qcs;
static HANDLE g_qev = nullptr, g_qth = nullptr;
static volatile LONG g_qstop = 0;
static DWORD WINAPI writer(LPVOID) {
    for (;;) {
        WaitForSingleObject(g_qev, 500);
        std::vector<Job> local;
        EnterCriticalSection(&g_qcs); local.swap(g_q); LeaveCriticalSection(&g_qcs);
        for (auto& j : local) { if (j.append) file_append(j.path, j.data); else file_replace(j.path, j.data); }
        if (g_qstop) {
            EnterCriticalSection(&g_qcs); bool empty = g_q.empty(); LeaveCriticalSection(&g_qcs);
            if (empty) break;
        }
    }
    return 0;
}
static void io_start() {
    InitializeCriticalSection(&g_qcs);
    g_qev = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    g_qth = CreateThread(nullptr, 0, writer, nullptr, 0, nullptr);
}
static void io_push(bool append, const std::string& path, const std::string& data) {
    EnterCriticalSection(&g_qcs);
    // одинаковые «replace» не копим — достаточно последнего
    if (!append) for (auto& j : g_q) if (!j.append && j.path == path) { j.data = data; LeaveCriticalSection(&g_qcs); SetEvent(g_qev); return; }
    g_q.push_back({ append, path, data });
    LeaveCriticalSection(&g_qcs); SetEvent(g_qev);
}
static void io_stop() {
    if (!g_qth) return;
    InterlockedExchange(&g_qstop, 1); SetEvent(g_qev);
    WaitForSingleObject(g_qth, 4000); CloseHandle(g_qth); g_qth = nullptr;
}
#else   // ---------------------------------------------------------------- POSIX (проверка на Linux)
#include <sys/stat.h>
#include <time.h>
static double plat_unix() { timespec t; clock_gettime(CLOCK_REALTIME, &t); return (double)t.tv_sec + t.tv_nsec / 1e9; }
double g_fake_mono = -1;   // тест может задавать время вручную
static double plat_mono() {
    if (g_fake_mono >= 0) return g_fake_mono;
    timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (double)t.tv_sec + t.tv_nsec / 1e9;
}
std::string g_test_base;
static std::string plat_base() { return g_test_base; }
static const char SEP = '/';
static void mkdirs(const std::string& p) {
    std::string s = p;
    for (size_t i = 1; i < s.size(); ++i) if (s[i] == '/') { s[i] = 0; mkdir(s.c_str(), 0755); s[i] = '/'; }
    mkdir(s.c_str(), 0755);
}
static void local_time(int& Y, int& M, int& D, int& h, int& m, int& s) {
    time_t t = (time_t)plat_unix(); tm tmv; localtime_r(&t, &tmv);
    Y = tmv.tm_year + 1900; M = tmv.tm_mon + 1; D = tmv.tm_mday; h = tmv.tm_hour; m = tmv.tm_min; s = tmv.tm_sec;
}
static void io_start() {}
static void io_push(bool append, const std::string& path, const std::string& data) {
    if (append) { FILE* f = fopen(path.c_str(), "ab"); if (f) { fwrite(data.data(), 1, data.size(), f); fclose(f); } }
    else { std::string t = path + ".tmp"; FILE* f = fopen(t.c_str(), "wb"); if (f) { fwrite(data.data(), 1, data.size(), f); fclose(f); rename(t.c_str(), path.c_str()); } }
}
static void io_stop() {}
#endif

// =====================================================================================================================
//  JSON без привязки к локали
// =====================================================================================================================
static void jnum(std::string& o, double v, int dec) {
    if (!(v == v) || std::isinf(v)) { o += '0'; return; }
    bool neg = v < 0; if (neg) v = -v;
    double p = 1; for (int i = 0; i < dec; ++i) p *= 10;
    double r = std::floor(v * p + 0.5);
    if (r >= 9e15) { o += "0"; return; }
    unsigned long long ip = (unsigned long long)(r / p), fp = (unsigned long long)(r - (double)ip * p);
    char b[40];
    if (neg && r > 0) o += '-';
    snprintf(b, sizeof b, "%llu", ip); o += b;
    if (dec) { snprintf(b, sizeof b, ".%0*llu", dec, fp); o += b; }
}
static void jstr(std::string& o, const std::string& s) {
    o += '"';
    for (unsigned char c : s) {
        if (c == '"') o += "\\\""; else if (c == '\\') o += "\\\\";
        else if (c < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += (char)c;
    }
    o += '"';
}
struct Obj {                     // маленький писатель объектов: {"a":1,"b":"x"}
    std::string s; bool first = true;
    Obj() { s = "{"; }
    void key(const char* k) { if (!first) s += ','; first = false; s += '"'; s += k; s += "\":"; }
    Obj& n(const char* k, double v, int d = 2) { key(k); jnum(s, v, d); return *this; }
    Obj& t(const char* k, const std::string& v) { key(k); jstr(s, v); return *this; }
    Obj& b(const char* k, bool v) { key(k); s += v ? "true" : "false"; return *this; }
    Obj& raw(const char* k, const std::string& v) { key(k); s += v; return *this; }
    std::string done() { return s + "}"; }
};

// =====================================================================================================================
//  значения каналов
// =====================================================================================================================
struct Vals {
    bool have = false;                                   // грузовик доступен (есть значение скорости)
    float speed = 0, rpm = 0, steer = 0, thr = 0, brk = 0, clu = 0, cruise = 0, brake_temp = 0;
    int gear = 0, dgear = 0; unsigned retarder = 0;
    bool park = false, motor = false;
    float fuel = 0, fuel_avg = 0, fuel_range = 0, adblue = 0, oil_t = 0, water_t = 0, oil_p = 0, bat = 0, air = 0;
    bool fuel_warn = false, adblue_warn = false, engine_on = true, electric = true;
    float wear[5] = {0, 0, 0, 0, 0};                     // двигатель, КПП, кабина, шасси, колёса
    float twear[3] = {0, 0, 0};                          // прицеп: кузов, шасси, колёса
    float cargo_dmg = 0, tcargo_dmg = 0;
    float odo = 0, nav_dist = 0, nav_time = 0, limit = 0;
    bool low = false, high = false, wipers = false, hazard = false, lbl = false, rbl = false, lbl_l = false, rbl_l = false;
    bool beacon = false, reverse = false, diff = false, trailer = false, brakelight = false;
    unsigned aux_f = 0, aux_r = 0;
    unsigned game_time = 0; int rest = 0; bool have_rest = false;
    double x = 0, y = 0, z = 0, heading = 0; bool have_pos = false;
    float ax = 0, ay = 0, az = 0;
};
static Vals V;

enum Ch {
    C_SPEED, C_RPM, C_GEAR, C_DGEAR, C_STEER, C_THR, C_BRK, C_CLU, C_CRUISE, C_PARK, C_MOTOR, C_RETARDER, C_BTEMP,
    C_FUEL, C_FUELAVG, C_FUELRANGE, C_FUELWARN, C_ADBLUE, C_ADBLUEWARN, C_OILP, C_OILT, C_WATERT, C_BAT, C_AIR,
    C_ENGINE, C_ELECTRIC, C_LBL, C_RBL, C_HAZARD, C_LLBL, C_LRBL, C_LOW, C_HIGH, C_AUXF, C_AUXR, C_BEACON, C_BRAKELIGHT,
    C_REVERSE, C_WIPERS, C_DIFF, C_WEAR0, C_WEAR1, C_WEAR2, C_WEAR3, C_WEAR4, C_ODO, C_NAVD, C_NAVT, C_LIMIT,
    C_GTIME, C_REST, C_JOBDMG, C_TRAILER, C_TDMG, C_TW0, C_TW1, C_TW2, C_POS, C_ACC
};
struct ChDef { const char* name; scs_value_type_t type; int id; };
static const ChDef CHANNELS[] = {
    {SCS_TELEMETRY_TRUCK_CHANNEL_speed, SCS_VALUE_TYPE_float, C_SPEED},
    {SCS_TELEMETRY_TRUCK_CHANNEL_engine_rpm, SCS_VALUE_TYPE_float, C_RPM},
    {SCS_TELEMETRY_TRUCK_CHANNEL_engine_gear, SCS_VALUE_TYPE_s32, C_GEAR},
    {SCS_TELEMETRY_TRUCK_CHANNEL_displayed_gear, SCS_VALUE_TYPE_s32, C_DGEAR},
    {SCS_TELEMETRY_TRUCK_CHANNEL_input_steering, SCS_VALUE_TYPE_float, C_STEER},
    {SCS_TELEMETRY_TRUCK_CHANNEL_input_throttle, SCS_VALUE_TYPE_float, C_THR},
    {SCS_TELEMETRY_TRUCK_CHANNEL_input_brake, SCS_VALUE_TYPE_float, C_BRK},
    {SCS_TELEMETRY_TRUCK_CHANNEL_input_clutch, SCS_VALUE_TYPE_float, C_CLU},
    {SCS_TELEMETRY_TRUCK_CHANNEL_cruise_control, SCS_VALUE_TYPE_float, C_CRUISE},
    {SCS_TELEMETRY_TRUCK_CHANNEL_parking_brake, SCS_VALUE_TYPE_bool, C_PARK},
    {SCS_TELEMETRY_TRUCK_CHANNEL_motor_brake, SCS_VALUE_TYPE_bool, C_MOTOR},
    {SCS_TELEMETRY_TRUCK_CHANNEL_retarder_level, SCS_VALUE_TYPE_u32, C_RETARDER},
    {SCS_TELEMETRY_TRUCK_CHANNEL_brake_temperature, SCS_VALUE_TYPE_float, C_BTEMP},
    {SCS_TELEMETRY_TRUCK_CHANNEL_fuel, SCS_VALUE_TYPE_float, C_FUEL},
    {SCS_TELEMETRY_TRUCK_CHANNEL_fuel_average_consumption, SCS_VALUE_TYPE_float, C_FUELAVG},
    {SCS_TELEMETRY_TRUCK_CHANNEL_fuel_range, SCS_VALUE_TYPE_float, C_FUELRANGE},
    {SCS_TELEMETRY_TRUCK_CHANNEL_fuel_warning, SCS_VALUE_TYPE_bool, C_FUELWARN},
    {SCS_TELEMETRY_TRUCK_CHANNEL_adblue, SCS_VALUE_TYPE_float, C_ADBLUE},
    {SCS_TELEMETRY_TRUCK_CHANNEL_adblue_warning, SCS_VALUE_TYPE_bool, C_ADBLUEWARN},
    {SCS_TELEMETRY_TRUCK_CHANNEL_oil_pressure, SCS_VALUE_TYPE_float, C_OILP},
    {SCS_TELEMETRY_TRUCK_CHANNEL_oil_temperature, SCS_VALUE_TYPE_float, C_OILT},
    {SCS_TELEMETRY_TRUCK_CHANNEL_water_temperature, SCS_VALUE_TYPE_float, C_WATERT},
    {SCS_TELEMETRY_TRUCK_CHANNEL_battery_voltage, SCS_VALUE_TYPE_float, C_BAT},
    {SCS_TELEMETRY_TRUCK_CHANNEL_brake_air_pressure, SCS_VALUE_TYPE_float, C_AIR},
    {SCS_TELEMETRY_TRUCK_CHANNEL_engine_enabled, SCS_VALUE_TYPE_bool, C_ENGINE},
    {SCS_TELEMETRY_TRUCK_CHANNEL_electric_enabled, SCS_VALUE_TYPE_bool, C_ELECTRIC},
    {SCS_TELEMETRY_TRUCK_CHANNEL_lblinker, SCS_VALUE_TYPE_bool, C_LBL},
    {SCS_TELEMETRY_TRUCK_CHANNEL_rblinker, SCS_VALUE_TYPE_bool, C_RBL},
    {SCS_TELEMETRY_TRUCK_CHANNEL_hazard_warning, SCS_VALUE_TYPE_bool, C_HAZARD},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_lblinker, SCS_VALUE_TYPE_bool, C_LLBL},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_rblinker, SCS_VALUE_TYPE_bool, C_LRBL},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_low_beam, SCS_VALUE_TYPE_bool, C_LOW},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_high_beam, SCS_VALUE_TYPE_bool, C_HIGH},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_aux_front, SCS_VALUE_TYPE_u32, C_AUXF},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_aux_roof, SCS_VALUE_TYPE_u32, C_AUXR},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_beacon, SCS_VALUE_TYPE_bool, C_BEACON},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_brake, SCS_VALUE_TYPE_bool, C_BRAKELIGHT},
    {SCS_TELEMETRY_TRUCK_CHANNEL_light_reverse, SCS_VALUE_TYPE_bool, C_REVERSE},
    {SCS_TELEMETRY_TRUCK_CHANNEL_wipers, SCS_VALUE_TYPE_bool, C_WIPERS},
    {SCS_TELEMETRY_TRUCK_CHANNEL_differential_lock, SCS_VALUE_TYPE_bool, C_DIFF},
    {SCS_TELEMETRY_TRUCK_CHANNEL_wear_engine, SCS_VALUE_TYPE_float, C_WEAR0},
    {SCS_TELEMETRY_TRUCK_CHANNEL_wear_transmission, SCS_VALUE_TYPE_float, C_WEAR1},
    {SCS_TELEMETRY_TRUCK_CHANNEL_wear_cabin, SCS_VALUE_TYPE_float, C_WEAR2},
    {SCS_TELEMETRY_TRUCK_CHANNEL_wear_chassis, SCS_VALUE_TYPE_float, C_WEAR3},
    {SCS_TELEMETRY_TRUCK_CHANNEL_wear_wheels, SCS_VALUE_TYPE_float, C_WEAR4},
    {SCS_TELEMETRY_TRUCK_CHANNEL_odometer, SCS_VALUE_TYPE_float, C_ODO},
    {SCS_TELEMETRY_TRUCK_CHANNEL_navigation_distance, SCS_VALUE_TYPE_float, C_NAVD},
    {SCS_TELEMETRY_TRUCK_CHANNEL_navigation_time, SCS_VALUE_TYPE_float, C_NAVT},
    {SCS_TELEMETRY_TRUCK_CHANNEL_navigation_speed_limit, SCS_VALUE_TYPE_float, C_LIMIT},
    {SCS_TELEMETRY_CHANNEL_game_time, SCS_VALUE_TYPE_u32, C_GTIME},
    {SCS_TELEMETRY_CHANNEL_next_rest_stop, SCS_VALUE_TYPE_s32, C_REST},
    {SCS_TELEMETRY_JOB_CHANNEL_cargo_damage, SCS_VALUE_TYPE_float, C_JOBDMG},
    {SCS_TELEMETRY_TRAILER_CHANNEL_connected, SCS_VALUE_TYPE_bool, C_TRAILER},
    {SCS_TELEMETRY_TRAILER_CHANNEL_cargo_damage, SCS_VALUE_TYPE_float, C_TDMG},
    {SCS_TELEMETRY_TRAILER_CHANNEL_wear_body, SCS_VALUE_TYPE_float, C_TW0},
    {SCS_TELEMETRY_TRAILER_CHANNEL_wear_chassis, SCS_VALUE_TYPE_float, C_TW1},
    {SCS_TELEMETRY_TRAILER_CHANNEL_wear_wheels, SCS_VALUE_TYPE_float, C_TW2},
    {SCS_TELEMETRY_TRUCK_CHANNEL_world_placement, SCS_VALUE_TYPE_dplacement, C_POS},
    {SCS_TELEMETRY_TRUCK_CHANNEL_local_linear_acceleration, SCS_VALUE_TYPE_fvector, C_ACC},
};

static void onChannel(const scs_string_t, const scs_u32_t, const scs_value_t* val, const scs_context_t ctx) {
    int id = (int)(intptr_t)ctx;
    if (!val) {                                              // значение пропало (меню, нет грузовика…)
        if (id == C_SPEED) V.have = false;
        else if (id == C_POS) V.have_pos = false;
        else if (id == C_REST) V.have_rest = false;
        else if (id == C_TRAILER) V.trailer = false;
        return;
    }
    switch (id) {
    case C_SPEED: V.speed = val->value_float.value; V.have = true; break;
    case C_RPM: V.rpm = val->value_float.value; break;
    case C_GEAR: V.gear = val->value_s32.value; break;
    case C_DGEAR: V.dgear = val->value_s32.value; break;
    case C_STEER: V.steer = val->value_float.value; break;
    case C_THR: V.thr = val->value_float.value; break;
    case C_BRK: V.brk = val->value_float.value; break;
    case C_CLU: V.clu = val->value_float.value; break;
    case C_CRUISE: V.cruise = val->value_float.value; break;
    case C_PARK: V.park = val->value_bool.value != 0; break;
    case C_MOTOR: V.motor = val->value_bool.value != 0; break;
    case C_RETARDER: V.retarder = val->value_u32.value; break;
    case C_BTEMP: V.brake_temp = val->value_float.value; break;
    case C_FUEL: V.fuel = val->value_float.value; break;
    case C_FUELAVG: V.fuel_avg = val->value_float.value; break;
    case C_FUELRANGE: V.fuel_range = val->value_float.value; break;
    case C_FUELWARN: V.fuel_warn = val->value_bool.value != 0; break;
    case C_ADBLUE: V.adblue = val->value_float.value; break;
    case C_ADBLUEWARN: V.adblue_warn = val->value_bool.value != 0; break;
    case C_OILP: V.oil_p = val->value_float.value; break;
    case C_OILT: V.oil_t = val->value_float.value; break;
    case C_WATERT: V.water_t = val->value_float.value; break;
    case C_BAT: V.bat = val->value_float.value; break;
    case C_AIR: V.air = val->value_float.value; break;
    case C_ENGINE: V.engine_on = val->value_bool.value != 0; break;
    case C_ELECTRIC: V.electric = val->value_bool.value != 0; break;
    case C_LBL: V.lbl = val->value_bool.value != 0; break;
    case C_RBL: V.rbl = val->value_bool.value != 0; break;
    case C_HAZARD: V.hazard = val->value_bool.value != 0; break;
    case C_LLBL: V.lbl_l = val->value_bool.value != 0; break;
    case C_LRBL: V.rbl_l = val->value_bool.value != 0; break;
    case C_LOW: V.low = val->value_bool.value != 0; break;
    case C_HIGH: V.high = val->value_bool.value != 0; break;
    case C_AUXF: V.aux_f = val->value_u32.value; break;
    case C_AUXR: V.aux_r = val->value_u32.value; break;
    case C_BEACON: V.beacon = val->value_bool.value != 0; break;
    case C_BRAKELIGHT: V.brakelight = val->value_bool.value != 0; break;
    case C_REVERSE: V.reverse = val->value_bool.value != 0; break;
    case C_WIPERS: V.wipers = val->value_bool.value != 0; break;
    case C_DIFF: V.diff = val->value_bool.value != 0; break;
    case C_WEAR0: case C_WEAR1: case C_WEAR2: case C_WEAR3: case C_WEAR4: V.wear[id - C_WEAR0] = val->value_float.value; break;
    case C_ODO: V.odo = val->value_float.value; break;
    case C_NAVD: V.nav_dist = val->value_float.value; break;
    case C_NAVT: V.nav_time = val->value_float.value; break;
    case C_LIMIT: V.limit = val->value_float.value; break;
    case C_GTIME: V.game_time = val->value_u32.value; break;
    case C_REST: V.rest = val->value_s32.value; V.have_rest = true; break;
    case C_JOBDMG: V.cargo_dmg = val->value_float.value; break;
    case C_TRAILER: V.trailer = val->value_bool.value != 0; break;
    case C_TDMG: V.tcargo_dmg = val->value_float.value; break;
    case C_TW0: case C_TW1: case C_TW2: V.twear[id - C_TW0] = val->value_float.value; break;
    case C_POS:
        V.x = val->value_dplacement.position.x; V.y = val->value_dplacement.position.y; V.z = val->value_dplacement.position.z;
        V.heading = val->value_dplacement.orientation.heading * 360.0; V.have_pos = true; break;
    case C_ACC: V.ax = val->value_fvector.x; V.ay = val->value_fvector.y; V.az = val->value_fvector.z; break;
    }
}

// =====================================================================================================================
//  накопители
// =====================================================================================================================
#define ACC_FIELDS(X) \
    X(play_s,1) X(drive_s,1) X(idle_s,1) X(engine_off_s,1) X(cruise_s,1) X(night_s,1) \
    X(km,3) X(km_loaded,3) X(km_trailer,3) X(km_night,3) X(km_cruise,3) X(km_reverse,3) X(odo_km,3) \
    X(v_max,1) X(over_s,1) X(over10_s,1) X(over_km,3) X(over_max,1) X(limit_s,1) \
    X(rpm_max,0) X(rpm_t,0) X(redline_s,1) X(thr_t,2) X(brk_t,2) X(full_thr_s,1) X(brake_s,1) \
    X(max_decel,2) X(max_accel,2) X(max_lat,2) X(hard_corner_s,1) \
    X(hard_brakes,0) X(emergency_brakes,0) X(shifts,0) X(retarder_s,1) X(motor_brake_s,1) X(park_s,1) X(park_engages,0) \
    X(brake_temp_max,0) X(oil_temp_max,0) X(water_temp_max,0) \
    X(fuel_used,2) X(fuel_idle,2) X(refuel_l,2) X(refuels,0) X(adblue_used,2) X(adblue_fill,2) X(fuel_warn_s,1) \
    X(low_s,1) X(high_s,1) X(wipers_s,1) X(hazard_s,1) X(beacon_s,1) X(aux_s,1) X(lblink_s,1) X(rblink_s,1) X(blink_toggles,0) \
    X(reverse_s,1) X(diff_s,1) \
    X(dmg_engine,4) X(dmg_trans,4) X(dmg_cabin,4) X(dmg_chassis,4) X(dmg_wheels,4) X(dmg_trailer,4) X(collisions,0) X(repairs,0) \
    X(rests,0) X(climb_m,1) X(descend_m,1) \
    X(fines,0) X(fine_total,0) X(tolls,0) X(toll_total,0) X(ferries,0) X(ferry_total,0) X(trains,0) X(train_total,0)

static const int NH = 16, NG = 28;                       // корзины скорости по 10 км/ч (0..150+), передачи -4..23
struct Acc {
#define F(fld, d) double fld = 0;
    ACC_FIELDS(F)
#undef F
    double hist_s[NH] = {}, hist_km[NH] = {}, gear_s[NG] = {};
    double rest_min = -1, alt_min = 0, alt_max = 0, cargo_dmg_max = 0; bool has_alt = false;
    void reset() { *this = Acc(); }
    void json(std::string& o) const {
        Obj j;
#define F(fld, d) j.n(#fld, this->fld, d);
        ACC_FIELDS(F)
#undef F
        j.n("rest_min", rest_min, 0).n("alt_min", alt_min, 1).n("alt_max", alt_max, 1).n("cargo_dmg_max", cargo_dmg_max, 4);
        std::string a = "[";
        for (int i = 0; i < NH; ++i) { if (i) a += ','; jnum(a, hist_s[i], 1); }
        a += "]"; j.raw("hist_s", a);
        a = "[";
        for (int i = 0; i < NH; ++i) { if (i) a += ','; jnum(a, hist_km[i], 3); }
        a += "]"; j.raw("hist_km", a);
        a = "[";
        for (int i = 0; i < NG; ++i) { if (i) a += ','; jnum(a, gear_s[i], 1); }
        a += "]"; j.raw("gear_s", a);
        o += j.done();
    }
};

// =====================================================================================================================
//  состояние плагина
// =====================================================================================================================
struct TruckInfo {
    std::string id, brand, name, plate, plate_country;
    double fuel_cap = 0, adblue_cap = 0, rpm_limit = 0, gears = 0;
    void json(std::string& o) const {
        Obj j; j.t("id", id).t("brand", brand).t("name", name).t("plate", plate).t("plate_country", plate_country)
            .n("fuel_cap", fuel_cap, 0).n("adblue_cap", adblue_cap, 0).n("rpm_limit", rpm_limit, 0).n("gears", gears, 0);
        o += j.done();
    }
};
struct TrailerInfo {
    std::string id, name, body, chain, accessory; bool present = false;
    void json(std::string& o) const { Obj j; j.t("id", id).t("name", name).t("body", body).t("chain", chain).t("accessory", accessory); o += j.done(); }
};
struct JobInfo {
    bool active = false, loaded = false, special = false, partial = false;
    std::string cargo_id, cargo, src_city, src_comp, dst_city, dst_comp, market;
    double mass = 0, units = 0, income = 0, planned_km = 0, deadline = 0;
    double t0 = 0, game0 = 0, odo0 = 0;                  // старт: unix, игровые минуты, одометр
    std::string truck, plate, trailer;
    bool pending_end = false, need_base = false; double pending_t = 0;
    std::string key() const { return cargo_id + "|" + src_city + "|" + src_comp + "|" + dst_city + "|" + dst_comp; }
};
struct TruckKm { std::string name; double km = 0; };

static struct State {
    bool up = false, paused = true, first_frame = true;
    std::string game, dir, sid, sess_path, track_path, game_label;
    unsigned game_ver = 0, tel_ver = 0;
    double t_start = 0, t_write = 0, t_track = 0, t_mono_prev = -1, paused_s = 0, t_last_move = 0;
    double odo0 = -1, odo_prev = -1, game_min0 = -1;
    float wear0[5]; bool wear0_set = false;
    Acc S, J;
    TruckInfo truck; TrailerInfo trailer; JobInfo job;
    std::vector<TruckKm> trucks;
    std::string track_buf;
    // предыдущие значения для разностей
    bool pv = false;
    float fuel_ref = 0, adblue_ref = 0, refuel_acc = 0; double refuel_t = 0;
    float p_wear[5] = {}, p_twear[3] = {}; int p_gear = 0; bool p_park = false, p_lbl = false, p_rbl = false;
    bool p_trailer = false; int p_rest = 0; double p_x = 0, p_z = 0;
    // торможение
    double a_t0 = 0, a_v0 = 0, a_brk = 0, a_n = 0; bool in_brake = false, in_emerg = false;
    // удары (накапливаем и сбрасываем событием)
    double dm[6] = {}; double dm_t = 0, dm_speed = 0; bool dm_open = false;
    double alt_f = 0, alt_acc = 0; bool alt_init = false;
    bool trailer_seen = false;
} G;

static double now_unix() { return plat_unix(); }

// ---------- запись ----------
static void emit(const char* type, Obj& extra) {
    Obj j; j.n("t", now_unix(), 1).t("s", G.sid).t("e", type).n("game_min", V.game_time, 0).n("odo", V.odo, 1)
        .n("x", V.x, 0).n("z", V.z, 0).n("speed", std::fabs(V.speed) * 3.6, 0);
    std::string s = j.done();
    std::string e = extra.done();
    if (e.size() > 2) { s.pop_back(); s += ','; s += e.substr(1); }
    s += '\n';
    io_push(true, G.dir + SEP + "events.jsonl", s);
}
static void emit0(const char* type) { Obj e; emit(type, e); }

static void wearsJson(std::string& o, const float* w) {
    Obj j; j.n("engine", w[0], 4).n("trans", w[1], 4).n("cabin", w[2], 4).n("chassis", w[3], 4).n("wheels", w[4], 4); o += j.done();
}
static void writeSession(bool active) {
    double now = now_unix();
    Obj j;
    j.n("schema", SCHEMA, 0).t("plugin", PLUGIN_VERSION).t("id", G.sid).t("game", G.game).n("game_ver", G.game_ver, 0)
        .n("start", G.t_start, 1).n("updated", now, 1).b("active", active).n("paused_s", G.paused_s, 1)
        .n("game_min0", G.game_min0 < 0 ? 0 : G.game_min0, 0).n("game_min", V.game_time, 0)
        .n("odo0", G.odo0 < 0 ? 0 : G.odo0, 3).n("odo", V.odo, 3);
    std::string a;
    G.truck.json(a); j.raw("truck", a); a.clear();
    G.trailer.json(a); j.raw("trailer", a); a.clear();
    a = "["; for (size_t i = 0; i < G.trucks.size(); ++i) { if (i) a += ','; Obj t; t.t("name", G.trucks[i].name).n("km", G.trucks[i].km, 3); a += t.done(); } a += "]";
    j.raw("trucks", a); a.clear();
    if (G.wear0_set) { wearsJson(a, G.wear0); j.raw("wear0", a); a.clear(); }
    wearsJson(a, V.wear); j.raw("wear", a); a.clear();
    {   // текущее состояние
        Obj n;
        n.n("speed", std::fabs(V.speed) * 3.6, 1).n("rpm", V.rpm, 0).n("gear", V.dgear, 0).n("fuel", V.fuel, 1).n("fuel_range", V.fuel_range, 0)
            .n("fuel_avg", V.fuel_avg * 100.0, 1).n("adblue", V.adblue, 1).n("rest", V.have_rest ? V.rest : -1, 0)
            .n("limit", V.limit * 3.6, 0).n("x", V.x, 0).n("y", V.y, 0).n("z", V.z, 0).b("driving", V.have && !G.paused);
        j.raw("now", n.done());
    }
    if (G.job.active) {
        const JobInfo& b = G.job; Obj q;
        q.t("cargo", b.cargo).t("src_city", b.src_city).t("src_comp", b.src_comp).t("dst_city", b.dst_city).t("dst_comp", b.dst_comp)
            .n("mass", b.mass, 0).n("income", b.income, 0).n("planned_km", b.planned_km, 1).n("deadline", b.deadline, 0)
            .n("t0", b.t0, 1).n("game0", b.game0, 0).b("loaded", b.loaded).n("left_km", V.nav_dist / 1000.0, 1).n("cargo_dmg", V.cargo_dmg, 4);
        j.raw("job", q.done());
    }
    a.clear(); G.S.json(a); j.raw("acc", a);
    io_push(false, G.sess_path, j.done());
    G.t_write = plat_mono();
    if (!G.track_buf.empty()) { io_push(true, G.track_path, G.track_buf); G.track_buf.clear(); }
}

static void addTruckKm(double km) {
    std::string nm = G.truck.brand + " " + G.truck.name; if (!G.truck.plate.empty()) nm += " " + G.truck.plate;
    for (auto& t : G.trucks) if (t.name == nm) { t.km += km; return; }
    if (G.trucks.size() < 12) G.trucks.push_back({ nm, km });
}

// ---------- один шаг статистики (вызывается каждый кадр, пока игра не на паузе и есть грузовик) ----------
struct Step {
    double dt, kmh, sp_ms; bool moving, drive, engine_on, night, over, over10, limit_known, cruise, loaded, trailer, reverse;
    double fuel_used, refuel, adblue_used, adblue_fill, climb, descend;
    double dmg[6]; bool repaired, park_engage, shift, blink_toggle, rest_taken;
    double decel_hit;           // 1: жёсткое торможение, 2: экстренное (новое событие в этом шаге)
    double accel_now;           // сглаженное продольное ускорение, если окно закончилось, иначе NAN
};

static void feed(Acc& a, const Step& s) {
    double dt = s.dt;
    a.play_s += dt;
    if (!s.engine_on) a.engine_off_s += dt;
    if (s.night) a.night_s += dt;
    if (s.moving) a.drive_s += dt;
    else if (s.engine_on) a.idle_s += dt;
    if (s.cruise && s.moving) a.cruise_s += dt;
    double dkm = s.kmh * dt / 3600.0;
    if (s.moving) {
        a.km += dkm;
        if (s.loaded) a.km_loaded += dkm;
        if (s.trailer) a.km_trailer += dkm;
        if (s.night) a.km_night += dkm;
        if (s.cruise) a.km_cruise += dkm;
        if (s.reverse) a.km_reverse += dkm;
        int hb = std::min(NH - 1, (int)(s.kmh / 10.0));
        a.hist_s[hb] += dt; a.hist_km[hb] += dkm;
        if (s.limit_known) { a.limit_s += dt; if (s.over) { a.over_s += dt; a.over_km += dkm; } if (s.over10) a.over10_s += dt; }
        a.rpm_t += V.rpm * dt; a.thr_t += V.thr * dt; a.brk_t += V.brk * dt;
        if (V.thr > 0.95f) a.full_thr_s += dt;
        if (V.brk > 0.05f) a.brake_s += dt;
        if (std::fabs(V.ax) > 3.0 && s.kmh > 20) a.hard_corner_s += dt;
    }
    a.v_max = std::max(a.v_max, s.kmh);
    if (a.limit_s > 0 && s.over) a.over_max = std::max(a.over_max, s.kmh - V.limit * 3.6);
    a.rpm_max = std::max(a.rpm_max, (double)V.rpm);
    if (G.truck.rpm_limit > 0 && V.rpm > 0.92 * G.truck.rpm_limit) a.redline_s += dt;
    int gi = std::max(0, std::min(NG - 1, V.gear + 4));
    if (s.moving || V.gear != 0) a.gear_s[gi] += dt;
    if (s.shift) a.shifts += 1;
    a.max_lat = std::max(a.max_lat, (double)std::fabs(V.ax));
    if (!std::isnan(s.accel_now)) {
        if (s.accel_now > 0) a.max_accel = std::max(a.max_accel, s.accel_now);
        else a.max_decel = std::max(a.max_decel, -s.accel_now);
    }
    if (s.decel_hit >= 1) a.hard_brakes += 1;
    if (s.decel_hit >= 2) a.emergency_brakes += 1;
    if (V.retarder > 0) a.retarder_s += dt;
    if (V.motor) a.motor_brake_s += dt;
    if (V.park) a.park_s += dt;
    if (s.park_engage) a.park_engages += 1;
    a.brake_temp_max = std::max(a.brake_temp_max, (double)V.brake_temp);
    a.oil_temp_max = std::max(a.oil_temp_max, (double)V.oil_t);
    a.water_temp_max = std::max(a.water_temp_max, (double)V.water_t);
    a.fuel_used += s.fuel_used;
    if (!s.moving) a.fuel_idle += s.fuel_used;
    if (s.refuel > 0) { a.refuel_l += s.refuel; a.refuels += 1; }
    a.adblue_used += s.adblue_used;
    a.adblue_fill += s.adblue_fill;
    if (V.fuel_warn) a.fuel_warn_s += dt;
    if (V.low) a.low_s += dt;
    if (V.high) a.high_s += dt;
    if (V.wipers) a.wipers_s += dt;
    if (V.hazard) a.hazard_s += dt;
    if (V.beacon) a.beacon_s += dt;
    if (V.aux_f || V.aux_r) a.aux_s += dt;
    if (V.lbl_l) a.lblink_s += dt;
    if (V.rbl_l) a.rblink_s += dt;
    if (s.blink_toggle) a.blink_toggles += 1;
    if (s.reverse) a.reverse_s += dt;
    if (V.diff) a.diff_s += dt;
    a.dmg_engine += s.dmg[0]; a.dmg_trans += s.dmg[1]; a.dmg_cabin += s.dmg[2]; a.dmg_chassis += s.dmg[3]; a.dmg_wheels += s.dmg[4]; a.dmg_trailer += s.dmg[5];
    if (s.repaired) a.repairs += 1;
    if (s.rest_taken) a.rests += 1;
    if (V.have_rest && (a.rest_min < 0 || V.rest < a.rest_min)) a.rest_min = V.rest;
    a.climb_m += s.climb; a.descend_m += s.descend;
    a.cargo_dmg_max = std::max(a.cargo_dmg_max, (double)std::max(V.cargo_dmg, V.tcargo_dmg));
    if (V.have_pos) {
        if (!a.has_alt) { a.has_alt = true; a.alt_min = a.alt_max = V.y; }
        a.alt_min = std::min(a.alt_min, V.y); a.alt_max = std::max(a.alt_max, V.y);
    }
}

static void resetBaseline() { G.pv = false; G.a_n = 0; G.in_brake = G.in_emerg = false; }

static void step(double dt) {
    Step s{};
    s.dt = dt; s.sp_ms = std::fabs(V.speed); s.kmh = s.sp_ms * 3.6;
    s.moving = s.sp_ms > 0.5; s.engine_on = V.engine_on;
    s.night = (V.game_time % 1440) < 360 || (V.game_time % 1440) >= 1260;
    s.limit_known = V.limit > 0.1f && s.moving;
    s.over = s.limit_known && s.kmh > V.limit * 3.6 + 3.0;
    s.over10 = s.limit_known && s.kmh > V.limit * 3.6 + 10.0;
    s.cruise = V.cruise > 0.1f;
    s.loaded = G.job.active && G.job.loaded;
    s.trailer = V.trailer; s.reverse = V.speed < -0.5f;
    s.accel_now = NAN;
    for (double& d : s.dmg) d = 0;

    if (G.pv) {
        // уровень топлива меняется за кадр меньше, чем нужно для порога, поэтому считаем от опорного значения:
        // расход копится, пока разница не дойдёт до 0,02 л; рост уровня — заправка, её закрываем после 2 с без прироста
        double d = V.fuel - G.fuel_ref;
        if (d < -0.02) { if (d > -5) s.fuel_used = -d; G.fuel_ref = V.fuel; }
        else if (d > 0.02) { if (d < 2000) { G.refuel_acc += (float)d; G.refuel_t = plat_mono(); } G.fuel_ref = V.fuel; }
        d = V.adblue - G.adblue_ref;
        if (d < -0.02) { if (d > -5) s.adblue_used = -d; G.adblue_ref = V.adblue; }
        else if (d > 0.02) { if (d < 500) s.adblue_fill = d; G.adblue_ref = V.adblue; }
        if (G.refuel_acc > 0 && plat_mono() - G.refuel_t > 2.0) {
            if (G.refuel_acc >= 1.0f) s.refuel = G.refuel_acc;
            G.refuel_acc = 0;
        }
        for (int i = 0; i < 5; ++i) {
            d = V.wear[i] - G.p_wear[i];
            if (d > 0.00015) s.dmg[i] = d;
            else if (d < -0.004) s.repaired = true;
        }
        if (V.trailer && G.p_trailer) {
            for (int i = 0; i < 3; ++i) { d = V.twear[i] - G.p_twear[i]; if (d > 0.00015) s.dmg[5] += d; }
        }
        s.park_engage = V.park && !G.p_park;
        s.shift = V.gear != G.p_gear;
        s.blink_toggle = (V.lbl && !G.p_lbl) || (V.rbl && !G.p_rbl);
        s.rest_taken = V.have_rest && G.p_rest < V.rest - 30;                   // «выспался»: резкий прирост запаса
    }
    // высота: фильтр + мёртвая зона, чтобы шум не превращался в километры набора
    if (V.have_pos) {
        if (!G.alt_init) { G.alt_f = V.y; G.alt_init = true; }
        else {
            double nf = G.alt_f + (V.y - G.alt_f) * std::min(1.0, dt * 2.0);
            if (s.moving && std::fabs(nf - G.alt_f) < 2.0) G.alt_acc += nf - G.alt_f;
            G.alt_f = nf;
            if (G.alt_acc > 1.0) { s.climb = G.alt_acc; G.alt_acc = 0; }
            else if (G.alt_acc < -1.0) { s.descend = -G.alt_acc; G.alt_acc = 0; }
        }
    }
    // ускорение по скорости: окно ≥0.25 с
    double tnow = plat_mono();
    if (G.a_n == 0) { G.a_t0 = tnow; G.a_v0 = V.speed; G.a_brk = 0; G.a_n = 1; }
    G.a_brk = std::max(G.a_brk, (double)V.brk);
    if (tnow - G.a_t0 >= 0.25) {
        double a = (V.speed - G.a_v0) / (tnow - G.a_t0);
        if (s.kmh > 3 || std::fabs(G.a_v0) > 1) s.accel_now = a;
        double decel = -a;
        if (G.a_brk > 0.4) {
            if (decel > 3.5 && !G.in_brake) { s.decel_hit = std::max(s.decel_hit, 1.0); G.in_brake = true; }
            if (decel > 6.0 && !G.in_emerg) { s.decel_hit = 2; G.in_emerg = true; }
        }
        if (decel < 1.5) { G.in_brake = false; G.in_emerg = false; }
        G.a_n = 0;
    }

    // события: удары (серия приращений износа → одно событие), заправка, ремонт
    double dsum = 0; for (int i = 0; i < 6; ++i) dsum += s.dmg[i];
    bool hit = dsum > 0;
    if (hit) {
        if (!G.dm_open) { G.dm_open = true; G.dm_speed = s.kmh; for (double& q : G.dm) q = 0; }
        for (int i = 0; i < 6; ++i) G.dm[i] += s.dmg[i];
        G.dm_t = tnow;
    }
    if (G.dm_open && tnow - G.dm_t > 1.5) {
        G.dm_open = false;
        Obj e; e.n("speed_hit", G.dm_speed, 0).n("engine", G.dm[0], 4).n("trans", G.dm[1], 4).n("cabin", G.dm[2], 4)
            .n("chassis", G.dm[3], 4).n("wheels", G.dm[4], 4).n("trailer", G.dm[5], 4);
        emit("damage", e);
        G.S.collisions += 1; if (G.job.active) G.J.collisions += 1;
    }
    if (s.refuel > 0) { Obj e; e.n("liters", s.refuel, 1).n("fuel", V.fuel, 1); emit("refuel", e); }
    if (s.repaired) emit0("repair");
    if (s.rest_taken) { Obj e; e.n("rest", V.rest, 0); emit("rest", e); }

    if (G.job.active && G.job.need_base) { G.job.game0 = V.game_time; G.job.odo0 = V.odo; G.job.need_base = false; }
    feed(G.S, s);
    if (G.job.active) feed(G.J, s);

    // одометр и километры по грузовикам
    if (G.odo0 < 0) G.odo0 = V.odo;
    if (G.odo_prev >= 0) {
        double d = V.odo - G.odo_prev;
        if (d > 0 && d < 0.5) { G.S.odo_km += d; if (G.job.active) G.J.odo_km += d; }
    }
    G.odo_prev = V.odo;
    if (s.moving) addTruckKm(s.kmh * dt / 3600.0);

    // трек: точка раз в 2 с в движении (и при остановке — одна)
    if (V.have_pos) {
        bool mv = s.moving, due = tnow - G.t_track >= 2.0;
        if (due && (mv || tnow - G.t_last_move < 3.0)) {
            std::string ln; jnum(ln, now_unix(), 0); ln += ','; jnum(ln, V.x, 1); ln += ','; jnum(ln, V.z, 1); ln += ','; jnum(ln, V.y, 1); ln += ',';
            jnum(ln, s.kmh, 0); ln += ','; jnum(ln, V.heading, 0); ln += '\n';
            G.track_buf += ln; G.t_track = tnow;
        }
        if (mv) G.t_last_move = tnow;
    }

    // запомнить для следующего кадра
    if (!G.pv) { G.fuel_ref = V.fuel; G.adblue_ref = V.adblue; G.refuel_acc = 0; }
    for (int i = 0; i < 5; ++i) G.p_wear[i] = V.wear[i];
    for (int i = 0; i < 3; ++i) G.p_twear[i] = V.twear[i];
    G.p_gear = V.gear; G.p_park = V.park; G.p_lbl = V.lbl; G.p_rbl = V.rbl; G.p_trailer = V.trailer; G.p_rest = V.rest; G.pv = true;
    if (!G.wear0_set) { for (int i = 0; i < 5; ++i) G.wear0[i] = V.wear[i]; G.wear0_set = true; }
    if (G.game_min0 < 0) G.game_min0 = V.game_time;
}

// ---------- рейсы ----------
static double numOf(const scs_value_t* v) {
    if (!v) return 0;
    switch (v->type) {
    case SCS_VALUE_TYPE_u32: return v->value_u32.value;
    case SCS_VALUE_TYPE_s32: return v->value_s32.value;
    case SCS_VALUE_TYPE_u64: return (double)v->value_u64.value;
    case SCS_VALUE_TYPE_s64: return (double)v->value_s64.value;
    case SCS_VALUE_TYPE_float: return v->value_float.value;
    case SCS_VALUE_TYPE_double: return v->value_double.value;
    case SCS_VALUE_TYPE_bool: return v->value_bool.value ? 1 : 0;
    default: return 0;
    }
}
static std::string strOf(const scs_value_t* v) {
    if (v && v->type == SCS_VALUE_TYPE_string && v->value_string.value) return v->value_string.value;
    return "";
}

static void finishJob(const char* status, const scs_telemetry_gameplay_event_t* ev) {
    if (!G.job.active) return;
    const JobInfo& b = G.job;
    Obj j;
    j.n("schema", SCHEMA, 0).t("session", G.sid).t("status", status).b("partial", b.partial)
        .n("t0", b.t0, 1).n("t1", now_unix(), 1).n("game0", b.game0, 0).n("game1", V.game_time, 0).n("odo0", b.odo0, 3).n("odo1", V.odo, 3)
        .t("cargo", b.cargo).t("cargo_id", b.cargo_id).n("mass", b.mass, 0).n("units", b.units, 0)
        .t("src_city", b.src_city).t("src_comp", b.src_comp).t("dst_city", b.dst_city).t("dst_comp", b.dst_comp)
        .n("income", b.income, 0).n("planned_km", b.planned_km, 1).n("deadline", b.deadline, 0).b("special", b.special).t("market", b.market)
        .t("truck", b.truck).t("plate", b.plate).t("trailer", b.trailer);
    if (ev) {
        Obj r;
        for (const scs_named_value_t* a = ev->attributes; a && a->name; ++a) {
            std::string n = a->name;
            if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_revenue) r.n("revenue", numOf(&a->value), 0);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_earned_xp) r.n("xp", numOf(&a->value), 0);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_cargo_damage) r.n("cargo_damage", numOf(&a->value), 4);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_distance_km) r.n("distance_km", numOf(&a->value), 1);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_delivery_time) r.n("delivery_min", numOf(&a->value), 0);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_auto_park_used) r.b("auto_park", numOf(&a->value) != 0);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_auto_load_used) r.b("auto_load", numOf(&a->value) != 0);
            else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_cancel_penalty) r.n("penalty", numOf(&a->value), 0);
        }
        j.raw("result", r.done());
    }
    std::string acc; G.J.json(acc); j.raw("acc", acc);
    io_push(true, G.dir + SEP + "jobs.jsonl", j.done() + "\n");
    Obj e; e.t("status", status).t("cargo", b.cargo).t("src", b.src_city).t("dst", b.dst_city);
    emit(std::strcmp(status, "delivered") == 0 ? "job_delivered" : "job_end", e);
    G.job.active = false; G.job.pending_end = false;
    G.J.reset();
    writeSession(true);
}

static void onConfig(const scs_telemetry_configuration_t* c) {
    std::string id = c->id;
    if (id == SCS_TELEMETRY_CONFIG_truck) {
        TruckInfo t;
        for (const scs_named_value_t* a = c->attributes; a->name; ++a) {
            std::string n = a->name;
            if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_id) t.id = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_brand) t.brand = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_name) t.name = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_license_plate) t.plate = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_license_plate_country) t.plate_country = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_fuel_capacity) t.fuel_cap = numOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_adblue_capacity) t.adblue_cap = numOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_rpm_limit) t.rpm_limit = numOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_forward_gear_count) t.gears = numOf(&a->value);
        }
        bool changed = t.id != G.truck.id || t.plate != G.truck.plate || t.name != G.truck.name;
        G.truck = t;
        if (changed) { resetBaseline(); G.wear0_set = false; G.odo_prev = -1; if (!G.first_frame) emit0("truck"); }
        if (!t.brand.empty()) G.game_label = t.brand + " " + t.name;
    } else if (id == SCS_TELEMETRY_CONFIG_trailer) {
        TrailerInfo t;
        for (const scs_named_value_t* a = c->attributes; a->name; ++a) {
            std::string n = a->name;
            if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_id) t.id = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_name) t.name = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_body_type) t.body = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_chain_type) t.chain = strOf(&a->value);
            else if (n == SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo_accessory_id) t.accessory = strOf(&a->value);
        }
        t.present = !t.id.empty();
        G.trailer = t;
        resetBaseline();
    } else if (id == SCS_TELEMETRY_CONFIG_job) {
        JobInfo n; bool has = false;
        for (const scs_named_value_t* a = c->attributes; a->name; ++a) {
            std::string k = a->name; has = true;
            if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo_id) n.cargo_id = strOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo) n.cargo = strOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo_mass) n.mass = numOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_cargo_unit_count) n.units = numOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_destination_city) n.dst_city = strOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_destination_company) n.dst_comp = strOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_source_city) n.src_city = strOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_source_company) n.src_comp = strOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_income) n.income = numOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_delivery_time) n.deadline = numOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_planned_distance_km) n.planned_km = numOf(&a->value);
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_is_cargo_loaded) n.loaded = numOf(&a->value) != 0;
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_special_job) n.special = numOf(&a->value) != 0;
            else if (k == SCS_TELEMETRY_CONFIG_ATTRIBUTE_job_market) n.market = strOf(&a->value);
        }
        if (has && (!n.cargo_id.empty() || !n.cargo.empty())) {
            if (G.job.active && G.job.key() == n.key()) {                      // тот же рейс: обновилась загрузка/сроки
                G.job.loaded = n.loaded; G.job.income = n.income; G.job.deadline = n.deadline; G.job.pending_end = false;
                if (n.planned_km > 0) G.job.planned_km = n.planned_km;
            } else {
                G.job = n;
                G.job.active = true; G.job.t0 = now_unix(); G.job.game0 = V.game_time; G.job.odo0 = V.odo; G.job.need_base = true;
                G.job.partial = G.first_frame;                                 // рейс уже шёл до запуска игры/плагина
                G.job.truck = G.truck.brand + " " + G.truck.name; G.job.plate = G.truck.plate; G.job.trailer = G.trailer.name;
                G.J.reset();
                Obj e; e.t("cargo", n.cargo).t("src", n.src_city).t("dst", n.dst_city).n("income", n.income, 0).n("planned_km", n.planned_km, 1).b("partial", G.job.partial);
                emit("job_start", e);
            }
        } else if (G.job.active) {                                             // рейс пропал: ждём событие «доставлено/отменено» до 3 с
            G.job.pending_end = true; G.job.pending_t = plat_mono();
        }
    }
}

static void onEvent(const scs_event_t ev, const void* info, const scs_context_t) {
    if (ev == SCS_TELEMETRY_EVENT_paused) { G.paused = true; resetBaseline(); }
    else if (ev == SCS_TELEMETRY_EVENT_started) { G.paused = false; G.t_mono_prev = -1; resetBaseline(); }
    else if (ev == SCS_TELEMETRY_EVENT_configuration) onConfig((const scs_telemetry_configuration_t*)info);
    else if (ev == SCS_TELEMETRY_EVENT_gameplay) {
        const scs_telemetry_gameplay_event_t* g = (const scs_telemetry_gameplay_event_t*)info;
        std::string id = g->id;
        if (id == SCS_TELEMETRY_GAMEPLAY_EVENT_job_delivered) finishJob("delivered", g);
        else if (id == SCS_TELEMETRY_GAMEPLAY_EVENT_job_cancelled) finishJob("cancelled", g);
        else {
            double amount = 0; std::string off, src, dst;
            for (const scs_named_value_t* a = g->attributes; a && a->name; ++a) {
                std::string n = a->name;
                if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_fine_amount || n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_pay_amount) amount = numOf(&a->value);
                else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_fine_offence) off = strOf(&a->value);
                else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_source_name) src = strOf(&a->value);
                else if (n == SCS_TELEMETRY_GAMEPLAY_EVENT_ATTRIBUTE_target_name) dst = strOf(&a->value);
            }
            Acc* tgt[2] = { &G.S, G.job.active ? &G.J : nullptr };
            Obj e;
            const char* type = nullptr;
            if (id == SCS_TELEMETRY_GAMEPLAY_EVENT_player_fined) { type = "fine"; e.t("offence", off).n("amount", amount, 0); for (Acc* a : tgt) if (a) { a->fines += 1; a->fine_total += amount; } }
            else if (id == SCS_TELEMETRY_GAMEPLAY_EVENT_player_tollgate_paid) { type = "toll"; e.n("amount", amount, 0); for (Acc* a : tgt) if (a) { a->tolls += 1; a->toll_total += amount; } }
            else if (id == SCS_TELEMETRY_GAMEPLAY_EVENT_player_use_ferry) { type = "ferry"; e.t("from", src).t("to", dst).n("amount", amount, 0); for (Acc* a : tgt) if (a) { a->ferries += 1; a->ferry_total += amount; } }
            else if (id == SCS_TELEMETRY_GAMEPLAY_EVENT_player_use_train) { type = "train"; e.t("from", src).t("to", dst).n("amount", amount, 0); for (Acc* a : tgt) if (a) { a->trains += 1; a->train_total += amount; } }
            if (type) emit(type, e);
        }
    }
}

static void onFrameStart(const scs_event_t, const void*, const scs_context_t) {
    double t = plat_mono();
    if (G.job.pending_end && t - G.job.pending_t > 3.0) { finishJob("ended", nullptr); }
    if (G.t_mono_prev < 0) { G.t_mono_prev = t; return; }
    double dt = t - G.t_mono_prev; G.t_mono_prev = t;
    if (dt <= 0) return;
    if (dt > 0.5) { resetBaseline(); dt = 0; }                // загрузка/сворачивание: не считаем как езду
    if (G.paused) { G.paused_s += std::min(dt, 1.0); }
    else if (V.have && dt > 0) {
        if (V.trailer != G.p_trailer && G.pv) { Obj e; emit(V.trailer ? "trailer_attach" : "trailer_detach", e); resetBaseline(); }
        step(dt);
        G.first_frame = false;
    }
    if (t - G.t_write >= 5.0) writeSession(true);
}

// =====================================================================================================================
//  точки входа SCS
// =====================================================================================================================
static scs_log_t g_log = nullptr;

extern "C" {
#ifdef _WIN32
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
SCSAPI_RESULT scs_telemetry_init(const scs_u32_t version, const scs_telemetry_init_params_t* const params) {
    if (version != SCS_TELEMETRY_VERSION_1_00 && version != SCS_TELEMETRY_VERSION_1_01) return SCS_RESULT_unsupported;
    const scs_telemetry_init_params_v100_t* p = (const scs_telemetry_init_params_v100_t*)params;
    g_log = p->common.log;
    std::string gid = p->common.game_id ? p->common.game_id : "";
    G = State();                                            // чистое состояние (на случай повторной инициализации)
    V = Vals();
    if (gid == SCS_GAME_ID_EUT2) G.game = "ets2"; else if (gid == SCS_GAME_ID_ATS) G.game = "ats"; else return SCS_RESULT_unsupported;
    G.game_ver = p->common.game_version; G.tel_ver = version;
    std::string base = plat_base();
    if (base.empty()) { if (g_log) g_log(SCS_LOG_TYPE_error, "[Yassno stats] нет LOCALAPPDATA"); return SCS_RESULT_generic_error; }
    G.dir = base + SEP + G.game;
    mkdirs(G.dir + SEP + "sessions"); mkdirs(G.dir + SEP + "tracks");
    int Y, M, D, h, m, sec; local_time(Y, M, D, h, m, sec);
    char sid[40]; snprintf(sid, sizeof sid, "%04d%02d%02d-%02d%02d%02d", Y, M, D, h, m, sec);
    G.sid = sid;
    G.sess_path = G.dir + SEP + "sessions" + SEP + G.sid + ".json";
    G.track_path = G.dir + SEP + "tracks" + SEP + G.sid + ".csv";
    G.t_start = now_unix(); G.t_write = plat_mono();
    io_start();

    bool ok = p->register_for_event(SCS_TELEMETRY_EVENT_paused, onEvent, nullptr) == SCS_RESULT_ok;
    ok = ok && p->register_for_event(SCS_TELEMETRY_EVENT_started, onEvent, nullptr) == SCS_RESULT_ok;
    ok = ok && p->register_for_event(SCS_TELEMETRY_EVENT_configuration, onEvent, nullptr) == SCS_RESULT_ok;
    ok = ok && p->register_for_event(SCS_TELEMETRY_EVENT_gameplay, onEvent, nullptr) == SCS_RESULT_ok;
    ok = ok && p->register_for_event(SCS_TELEMETRY_EVENT_frame_start, onFrameStart, nullptr) == SCS_RESULT_ok;
    if (!ok) { if (g_log) g_log(SCS_LOG_TYPE_error, "[Yassno stats] не удалось подписаться на события"); io_stop(); return SCS_RESULT_generic_error; }
    int bad = 0;
    for (const ChDef& c : CHANNELS)
        if (p->register_for_channel(c.name, SCS_U32_NIL, c.type, SCS_TELEMETRY_CHANNEL_FLAG_no_value, onChannel, (void*)(intptr_t)c.id) != SCS_RESULT_ok) ++bad;
    G.up = true; G.paused = true;
    emit0("session_start");
    writeSession(true);
    if (g_log) {
        char b[160]; snprintf(b, sizeof b, "[Yassno stats] %s v%s: пишу статистику в %s (недоступных каналов: %d)", G.game.c_str(), PLUGIN_VERSION, G.dir.c_str(), bad);
        g_log(SCS_LOG_TYPE_message, b);
    }
    return SCS_RESULT_ok;
}

#ifdef _WIN32
__declspec(dllexport)
#else
__attribute__((visibility("default")))
#endif
SCSAPI_VOID scs_telemetry_shutdown(void) {
    if (!G.up) return;
    G.up = false;
    if (G.job.active) { /* незавершённый рейс остаётся в сессии как активный; при следующем запуске он придёт как partial */ }
    if (G.dm_open) {
        Obj e; e.n("speed_hit", G.dm_speed, 0).n("engine", G.dm[0], 4).n("cabin", G.dm[2], 4).n("chassis", G.dm[3], 4).n("trailer", G.dm[5], 4);
        emit("damage", e);
    }
    emit0("session_end");
    writeSession(false);
    io_stop();
}
}

#ifdef _WIN32
BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) { return TRUE; }
#endif
