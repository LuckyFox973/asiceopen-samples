/// Metal shader živej krajiny. Kompiluje sa za behu zo zdrojového textu, takže
/// stačia Xcode Command Line Tools (bez Metal kompilátora). Logika je 1:1
/// s tools/animation_preview.py.
enum LiveShader {
    static let source = #"""
#include <metal_stdlib>
using namespace metal;

struct Uniforms {
    float time;
    float fade;
    float scale;
    float cell;
    float2 sizePts;
    float2 drawable;
    float2 fieldSize;
    int theme;
    int pad;
};

struct VOut { float4 position [[position]]; };

vertex VOut vmain(uint vid [[vertex_id]]) {
    float2 p = float2((vid << 1) & 2, vid & 2);
    VOut o;
    o.position = float4(p * 2.0 - 1.0, 0.0, 1.0);
    return o;
}

// ---------------------------------------------------------------- šum (zhodný s CPU)
static float hash2(int x, int y, uint seed) {
    uint h = uint(x) * 374761393u;
    h = h + uint(y) * 668265263u;
    h = h + seed * 2246822519u;
    h = (h ^ (h >> 13)) * 1274126177u;
    h ^= h >> 16;
    return float(h & 0xFFFFFFu) / 16777215.0;
}
static float hashf(float x, float y, uint seed) { return hash2(int(floor(x)), int(floor(y)), seed); }
static float sm(float t) { return t * t * (3.0 - 2.0 * t); }
static float sstep(float a, float b, float x) { return sm(clamp((x - a) / (b - a), 0.0, 1.0)); }
static float vnoise(float x, float y, uint seed) {
    float xf = floor(x), yf = floor(y);
    float tx = sm(x - xf), ty = sm(y - yf);
    int ix = int(xf), iy = int(yf);
    float a = hash2(ix, iy, seed), b = hash2(ix + 1, iy, seed);
    float c = hash2(ix, iy + 1, seed), d = hash2(ix + 1, iy + 1, seed);
    float top = a + (b - a) * tx, bot = c + (d - c) * tx;
    return top + (bot - top) * ty;
}
static float fbm(float x, float y, uint seed, int oct) {
    float s = 0.0, amp = 0.5, fr = 1.0, norm = 0.0;
    for (int o = 0; o < oct; o++) {
        float off = float(o) * 17.31;
        s += amp * vnoise(x * fr + off, y * fr - off, seed);
        norm += amp; amp *= 0.5; fr *= 2.0;
    }
    return s / norm;
}
/// Worley s bodmi krúžiacimi okolo stredov buniek (kaustiky, čipka peny). x = F1, y = F2.
static float2 worleyAnim(float2 p, float cell, uint seed, float t) {
    float cx = floor(p.x / cell), cy = floor(p.y / cell);
    float f1 = 1e9, f2 = 1e9;
    for (int dj = -1; dj <= 1; dj++) {
        for (int di = -1; di <= 1; di++) {
            float i = cx + float(di), j = cy + float(dj);
            float w = 0.35 + 0.3 * hashf(i, j, seed + 5u);
            float ph = hashf(i, j, seed + 6u) * 6.2832;
            float fx = (i + 0.5 + (hashf(i, j, seed) - 0.5) * 0.6 + 0.24 * cos(t * w + ph)) * cell;
            float fy = (j + 0.5 + (hashf(i, j, seed + 1u) - 0.5) * 0.6 + 0.24 * sin(t * w * 1.3 + ph)) * cell;
            float d = length(p - float2(fx, fy));
            if (d < f1) { f2 = f1; f1 = d; } else if (d < f2) { f2 = d; }
        }
    }
    return float2(f1, f2);
}
/// Krátko zablikajúci mäkký bod v každej bunke (odlesky, trblietky snehu).
static float twinkle(float2 p, float cell, uint seed, float t, float prob, float sharp, float r0, float r1) {
    float ci = floor(p.x / cell), cj = floor(p.y / cell);
    if (hashf(ci, cj, seed + 4u) >= prob) return 0.0;
    float2 g = float2((ci + 0.2 + 0.6 * hashf(ci, cj, seed)) * cell, (cj + 0.2 + 0.6 * hashf(ci, cj, seed + 1u)) * cell);
    float fl = pow(max(0.0, sin(t * (1.2 + 1.6 * hashf(ci, cj, seed + 2u)) + hashf(ci, cj, seed + 3u) * 6.2832)), sharp);
    return (1.0 - sstep(r0, r1, length(p - g))) * fl;
}

// ---------------------------------------------------------------- pole (manuálny bilineárny filter)
static float4 field(texture2d<float, access::read> tex, float2 p, float cell, float2 size) {
    float gx = clamp(p.x / cell, 0.0, size.x - 1.001), gy = clamp(p.y / cell, 0.0, size.y - 1.001);
    uint ix = uint(gx), iy = uint(gy);
    float fx = gx - float(ix), fy = gy - float(iy);
    float4 a = tex.read(uint2(ix, iy)), b = tex.read(uint2(ix + 1, iy));
    float4 c = tex.read(uint2(ix, iy + 1)), d = tex.read(uint2(ix + 1, iy + 1));
    return mix(mix(a, b, fx), mix(c, d, fx), fy);
}

// ---------------------------------------------------------------- efekty
constant float WAVE_T = 7.0;
constant float WAVE_L = 34.0;

static float swashProfile(float p) {
    if (p < 0.22) return sin(clamp(p / 0.22, 0.0, 1.0) * 1.5708);
    if (p < 0.70) return cos(clamp((p - 0.22) / 0.48, 0.0, 1.0) * 1.5708);
    return 0.0;
}

static float3 beach(float3 c, float2 p, float4 f, float t, float3 tint) {
    float sdf = f.x, n40 = f.y, n22 = f.z;
    float3 W = float3(1.0);
    float d = max(-sdf, 0.0) + (n40 - 0.5) * 6.0;
    float water = sstep(0.6, -0.6, sdf);
    float fl = 0.5;
    if (water > 0.0) {
        // hladina: pomaly tečúce svetlo
        fl = fbm((p.x + t * 7.0) / 150.0, (p.y - t * 3.0) / 150.0, 901u, 3);
        c *= 1.0 + (fl - 0.5) * 0.10 * sstep(6.0, 40.0, d) * water;
        if (d < 90.0) {
            // kaustiky na plytčine
            if (d < 46.0) {
                float2 w = worleyAnim(p, 24.0, 77u, t * 0.9);
                float web = (1.0 - sstep(0.0, 2.0, w.y - w.x)) * (1.0 - sstep(6.0, 46.0, d)) * sstep(0.0, 5.0, d) * water;
                c = mix(c, W, web * 0.13);
            }
            // vlny bežiace k brehu
            float ph = d / WAVE_L + t / WAVE_T + (n22 - 0.5) * 0.22;
            float fr = fract(ph);
            float qd = min(fr, 1.0 - fr) * WAVE_L;
            float near = 1.0 - sstep(4.0, 55.0, d);
            float width = 1.2 + 4.5 * near;
            float amp = sstep(85.0, 35.0, d) * (0.25 + 0.65 * near);
            float br = sstep(0.32, 0.7, vnoise(p.x / 34.0 + t * 0.25, p.y / 34.0 - t * 0.15, 911u));
            float crest = (1.0 - sstep(width * 0.35, width, qd)) * amp * (0.25 + 0.75 * br);
            float behind = fr < 0.5 ? fr * WAVE_L : 99.0;
            float lace = exp(-behind / 5.0) * amp * sstep(0.55, 0.72, vnoise(p.x / 9.0 + t * 0.2, p.y / 9.0, 912u)) * 0.45;
            c = mix(c, W, clamp(crest * 0.85 + lace, 0.0, 1.0) * water * sstep(1.0, 4.0, d));
        }
        // odlesky slnka
        float g = twinkle(p, 16.0, 931u, t, 0.55, 12.0, 0.2, 1.1) * sstep(20.0, 70.0, d) * (0.4 + 0.6 * sstep(0.45, 0.65, fl));
        c = mix(c, W, g * 0.9 * water);
    }
    // výbeh vody na pláž, pena na čele, mokrý piesok
    float s = max(sdf, 0.0);
    if (sdf > -1.0 && s < 32.0) {
        float pp = fract(t / WAVE_T + (n22 - 0.5) * 0.22);
        float rmax = 13.0 + n40 * 12.0;
        float R = rmax * swashProfile(pp);
        float sheet = sstep(R + 1.2, R - 1.2, s) * (R > 0.3 ? 1.0 : 0.0);
        bool adv = pp < 0.22;
        float zf = (s - R) / (adv ? 1.5 : 1.0);
        float front = exp(-zf * zf) * sstep(0.3, 2.0, R)
                      * (0.7 + 0.3 * vnoise(p.x / 14.0 + t * 0.3, p.y / 14.0, 915u));
        float v = clamp(s / max(rmax, 1.0), 0.0, 1.0);
        float pr = 0.22 + 0.48 * (2.0 / 3.14159265) * acos(v);
        float dt = fract(pp - pr) * WAVE_T;
        float wet = exp(-dt / 2.4) * (s < rmax ? 1.0 : 0.0) * (1.0 - sheet);
        wet = max(wet, (1.0 - sstep(0.0, 5.0, s)) * 0.6) * (sdf > 0.0 ? 1.0 : 0.0);
        c = mix(c, float3(0.863, 0.796, 0.667), wet * 0.6) + wet * 0.03;
        c = mix(c, mix(tint, W, 0.15), sheet * 0.42 * (1.0 - 0.5 * s / max(R, 1.0)));
        float lace2 = 0.0;
        if (!adv && sheet > 0.0) {
            float2 w = worleyAnim(p, 6.0, 91u, t * 0.6);
            lace2 = (1.0 - sstep(0.0, 0.7, w.y - w.x)) * sheet * sstep(R - 8.0, R - 1.0, s) * 0.3;
        }
        c = mix(c, W, clamp(front * 0.95 + lace2, 0.0, 1.0));
    }
    return c;
}

static float3 cloudShadows(float3 c, float2 p, float t, float strength) {
    float cs = fbm((p.x - t * 9.0) / 420.0, (p.y + t * 2.0) / 300.0, 921u, 3);
    return c * (1.0 - sstep(0.55, 0.78, cs) * strength);
}

static float3 wind(float3 c, float2 p, float t, float grass, float canopy, float cloud) {
    if (grass > 0.0) {
        float gust = fbm((p.x - t * 42.0) / 150.0, (p.y + t * 6.0) / 80.0, 941u, 3);
        float ripple = vnoise((p.x - t * 60.0) / 26.0, p.y / 14.0, 942u);
        float light = sstep(0.5, 0.78, gust) * (0.6 + 0.4 * ripple);
        c = c * (1.0 + light * 0.09 * grass) + light * grass * 0.015 * float3(0.6, 0.9, -0.2);
        c *= 1.0 - sstep(0.3, 0.1, gust) * 0.05 * grass;
    }
    if (canopy > 0.0) {
        float sway = fbm((p.x - t * 24.0) / 70.0, (p.y + t * 8.0) / 70.0, 943u, 2);
        c *= 1.0 + (sway - 0.5) * 0.08 * canopy;
    }
    if (cloud > 0.0) {
        float puff = fbm((p.x + t * 4.0) / 90.0, (p.y - t * 3.0) / 90.0, 944u, 2);
        c *= 1.0 + (puff - 0.5) * 0.05 * cloud;
    }
    return c;
}

// ---------------------------------------------------------------- hlavný fragment
fragment float4 fmain(VOut in [[stage_in]],
                      constant Uniforms& u [[buffer(0)]],
                      texture2d<float> baseTex [[texture(0)]],
                      texture2d<float> prevTex [[texture(1)]],
                      texture2d<float, access::read> fieldTex [[texture(2)]]) {
    constexpr sampler smp(filter::linear, address::clamp_to_edge);
    float2 px = in.position.xy;
    float2 uv = px / u.drawable;
    float3 c = mix(prevTex.sample(smp, uv).rgb, baseTex.sample(smp, uv).rgb, u.fade);
    float2 p = float2(px.x / u.scale, u.sizePts.y - px.y / u.scale);        // body, os y hore
    float4 f = field(fieldTex, p, u.cell, u.fieldSize);
    float t = u.time;
    float region = sstep(-1.0, 1.0, f.x);
    switch (u.theme) {
        case 0:   // Pláž
            c = beach(c, p, f, t, float3(0.624, 0.890, 0.855));
            c = cloudShadows(c, p, t, 0.11);
            break;
        case 1:   // Ostrovy
            c = beach(c, p, f, t, float3(0.560, 0.850, 0.830));
            c = wind(c, p, t, sstep(14.0, 18.0, f.x), 0.0, 0.0);
            c = cloudShadows(c, p, t, 0.11);
            break;
        case 2:   // Les a oblaky
            c = wind(c, p, t, f.w * (1.0 - region), (1.0 - f.w) * (1.0 - region), region);
            c = cloudShadows(c, p, t, 0.13);
            break;
        case 3:   // Polia
            c = wind(c, p, t, 1.0, 0.0, 0.0);
            c = cloudShadows(c, p, t, 0.12);
            break;
        case 4:   // Mesto
            c = wind(c, p, t, region, 0.0, 0.0);
            c = cloudShadows(c, p, t, 0.10);
            break;
        default:
            break;
    }
    return float4(clamp(c, 0.0, 1.0), 1.0);
}
"""#
}
