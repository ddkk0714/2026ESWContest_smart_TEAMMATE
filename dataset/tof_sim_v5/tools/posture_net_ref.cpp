// posture_net_ref.cpp — PostureNet(tofsim/model.py) 의 의존성 없는 C++ 순전파.
//
// Pi 4 hub/atlas 서비스(AI Native OS Headless: numpy·torch·컴파일러 없음)에 그대로 옮길 수 있게
// 표준 라이브러리만 쓴다. export.py 가 낸 posture_net.bin 을 읽고, golden.txt 의 입력을 넣어
// PyTorch logits 와 맞춰 본 뒤 프레임당 시간을 잰다.
//
//   g++ -O2 -std=c++17 tools/posture_net_ref.cpp -o tools/posture_net_ref
//   tools/posture_net_ref <export>/posture_net.bin <export>/golden.txt
//
// posture_net.bin : "TOFPN001" + int32 H, W, classes, hidden + float32(LE) 가중치 (export.py BIN_ORDER 순서)
// golden.txt      : "golden T H W classes hidden" 다음 프레임마다
//                   "frame t reset r" / "dist <H*W>" / "status <H*W>" / "logits <classes>"
//
// 보드에서 쓸 것은 tof::PostureNet 하나다: load() → 세션 시작에 reset() → 프레임마다 step().
// 센서가 여러 개면 센서마다 PostureNet 하나씩 (각자 GRU 상태를 가진다). 가중치는 같은 파일.
// 입력 규약(model_card.json 과 같음): dist 는 mm (int16), status 는 센서 상태값(uint8),
// row-major, row 0 = 위, col 0 = 센서 기준 왼쪽. 유효 zone = status ∈ {5,6,9,10} 이고 dist >= 0.
// step() 은 10 Hz 로 불러야 한다 (학습 프레임률). 센서 스트림이 끊겼다 돌아오면 reset().

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace tof {

constexpr float kMaxMm = 4000.0f;

inline bool zone_valid(int16_t dist, uint8_t status) {
    return dist >= 0 && (status == 5 || status == 6 || status == 9 || status == 10);
}

class PostureNet {
public:
    int H = 0, W = 0, classes = 0, hidden = 0;

    bool load(const std::string& path, std::string* err = nullptr) {
        std::ifstream f(path, std::ios::binary);
        if (!f) { if (err) *err = "cannot open " + path; return false; }
        char magic[8];
        f.read(magic, 8);
        if (!f || std::memcmp(magic, "TOFPN001", 8) != 0) { if (err) *err = "bad magic"; return false; }
        int32_t hdr[4];
        f.read(reinterpret_cast<char*>(hdr), sizeof hdr);
        if (!f) { if (err) *err = "short header"; return false; }
        H = hdr[0]; W = hdr[1]; classes = hdr[2]; hidden = hdr[3];
        H2 = (H - 1) / 2 + 1; W2 = (W - 1) / 2 + 1;           // conv 3x3, stride 2, pad 1
        const size_t n_fc_in = 32 * 4 * 4;                        // AdaptiveAvgPool2d((4,4)) 뒤 flatten
        const size_t sizes[14] = {
            16u * 2 * 9, 16, 32u * 16 * 9, 32, 32u * 32 * 9, 32,
            (size_t)hidden * n_fc_in, (size_t)hidden,
            (size_t)3 * hidden * hidden, (size_t)3 * hidden * hidden, (size_t)3 * hidden, (size_t)3 * hidden,
            (size_t)classes * hidden, (size_t)classes};
        size_t total = 0;
        for (size_t s : sizes) total += s;
        w_.resize(total);
        f.read(reinterpret_cast<char*>(w_.data()), total * sizeof(float));
        if (!f) { if (err) *err = "short weights"; return false; }
        const float* p = w_.data();
        const float** dst[14] = {&c1w, &c1b, &c2w, &c2b, &c3w, &c3b, &fcw, &fcb, &wih, &whh, &bih, &bhh, &hw, &hb};
        for (int i = 0; i < 14; ++i) { *dst[i] = p; p += sizes[i]; }
        x_.assign((size_t)2 * H * W, 0.f);
        a1_.assign((size_t)16 * H * W, 0.f);
        a2_.assign((size_t)32 * H2 * W2, 0.f);
        a3_.assign((size_t)32 * H2 * W2, 0.f);
        p_.assign(n_fc_in, 0.f);
        f_.assign(hidden, 0.f);
        g_.assign((size_t)3 * hidden, 0.f);
        gh_.assign((size_t)3 * hidden, 0.f);
        reset();
        return true;
    }

    // 세션 시작, 센서 스트림 끊김 뒤 — GRU 상태를 0 으로
    void reset() { h_.assign(hidden, 0.f); }

    // 프레임 하나 -> logits[classes] (argmax 가 자세). dist/status 는 H*W row-major.
    void step(const int16_t* dist, const uint8_t* status, float* logits) {
        const int n = H * W;
        for (int i = 0; i < n; ++i) {
            bool v = zone_valid(dist[i], status[i]);
            x_[i] = v ? (float)dist[i] / kMaxMm : 0.f;       // channel 0: 거리/4000
            x_[n + i] = v ? 1.f : 0.f;                        // channel 1: 유효 마스크
        }
        conv3x3(x_.data(), 2, H, W, c1w, c1b, 16, 1, a1_.data());        // -> 16×H×W
        conv3x3(a1_.data(), 16, H, W, c2w, c2b, 32, 2, a2_.data());      // -> 32×H2×W2
        conv3x3(a2_.data(), 32, H2, W2, c3w, c3b, 32, 1, a3_.data());    // -> 32×H2×W2
        adaptive_avg_pool(a3_.data(), 32, H2, W2, 4, 4, p_.data());      // -> 32×4×4
        linear(p_.data(), 32 * 16, fcw, fcb, hidden, f_.data());
        for (int i = 0; i < hidden; ++i) f_[i] = relu(f_[i]);
        gru_step();
        linear(h_.data(), hidden, hw, hb, classes, logits);
    }

    const std::vector<float>& state() const { return h_; }

private:
    int H2 = 0, W2 = 0;
    std::vector<float> w_;
    const float *c1w, *c1b, *c2w, *c2b, *c3w, *c3b, *fcw, *fcb, *wih, *whh, *bih, *bhh, *hw, *hb;
    std::vector<float> h_, x_, a1_, a2_, a3_, p_, f_, g_, gh_;

    static float relu(float v) { return v > 0.f ? v : 0.f; }
    static float sigmoid(float v) { return 1.f / (1.f + std::exp(-v)); }

    // PyTorch Conv2d(cin, cout, 3, stride, padding=1) + ReLU. w: [cout][cin][3][3]
    static void conv3x3(const float* in, int cin, int h, int w, const float* wt, const float* b, int cout,
                        int stride, float* out) {
        const int ho = (h - 1) / stride + 1, wo = (w - 1) / stride + 1;
        for (int o = 0; o < cout; ++o)
            for (int y = 0; y < ho; ++y)
                for (int x = 0; x < wo; ++x) {
                    float acc = b[o];
                    for (int i = 0; i < cin; ++i) {
                        const float* wi = wt + ((size_t)o * cin + i) * 9;
                        const float* ii = in + (size_t)i * h * w;
                        for (int ky = 0; ky < 3; ++ky) {
                            int yy = y * stride + ky - 1;
                            if (yy < 0 || yy >= h) continue;
                            for (int kx = 0; kx < 3; ++kx) {
                                int xx = x * stride + kx - 1;
                                if (xx < 0 || xx >= w) continue;
                                acc += wi[ky * 3 + kx] * ii[yy * w + xx];
                            }
                        }
                    }
                    out[((size_t)o * ho + y) * wo + x] = relu(acc);
                }
    }

    // PyTorch AdaptiveAvgPool2d((oh, ow)): 구간 [floor(i*h/oh), ceil((i+1)*h/oh))
    static void adaptive_avg_pool(const float* in, int c, int h, int w, int oh, int ow, float* out) {
        for (int ch = 0; ch < c; ++ch)
            for (int y = 0; y < oh; ++y) {
                int y0 = (y * h) / oh, y1 = ((y + 1) * h + oh - 1) / oh;
                for (int x = 0; x < ow; ++x) {
                    int x0 = (x * w) / ow, x1 = ((x + 1) * w + ow - 1) / ow;
                    float acc = 0.f;
                    for (int yy = y0; yy < y1; ++yy)
                        for (int xx = x0; xx < x1; ++xx) acc += in[((size_t)ch * h + yy) * w + xx];
                    out[((size_t)ch * oh + y) * ow + x] = acc / (float)((y1 - y0) * (x1 - x0));
                }
            }
    }

    // y = W x + b, W: [nout][nin]
    static void linear(const float* x, int nin, const float* wt, const float* b, int nout, float* y) {
        for (int o = 0; o < nout; ++o) {
            const float* row = wt + (size_t)o * nin;
            float acc = b[o];
            for (int i = 0; i < nin; ++i) acc += row[i] * x[i];
            y[o] = acc;
        }
    }

    // PyTorch nn.GRU 한 스텝. 게이트 순서 r, z, n.
    //   r = σ(W_ir x + b_ir + W_hr h + b_hr)
    //   z = σ(W_iz x + b_iz + W_hz h + b_hz)
    //   n = tanh(W_in x + b_in + r ⊙ (W_hn h + b_hn))
    //   h' = (1 − z) ⊙ n + z ⊙ h
    void gru_step() {
        linear(f_.data(), hidden, wih, bih, 3 * hidden, g_.data());
        linear(h_.data(), hidden, whh, bhh, 3 * hidden, gh_.data());
        for (int i = 0; i < hidden; ++i) {
            float r = sigmoid(g_[i] + gh_[i]);
            float z = sigmoid(g_[hidden + i] + gh_[hidden + i]);
            float nn = std::tanh(g_[2 * hidden + i] + r * gh_[2 * hidden + i]);
            h_[i] = (1.f - z) * nn + z * h_[i];
        }
    }
};

}  // namespace tof

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: %s posture_net.bin golden.txt\n", argv[0]);
        return 2;
    }
    tof::PostureNet net;
    std::string err;
    if (!net.load(argv[1], &err)) { std::fprintf(stderr, "load failed: %s\n", err.c_str()); return 1; }
    std::printf("model: %dx%d input, %d classes, hidden %d\n", net.H, net.W, net.classes, net.hidden);

    std::ifstream g(argv[2]);
    if (!g) { std::fprintf(stderr, "cannot open %s\n", argv[2]); return 1; }
    std::string tok;
    int T, H, W, C, hid;
    g >> tok >> T >> H >> W >> C >> hid;
    if (tok != "golden" || H != net.H || W != net.W || C != net.classes || hid != net.hidden) {
        std::fprintf(stderr, "golden header mismatch\n");
        return 1;
    }
    const int n = H * W;
    std::vector<int16_t> dist((size_t)T * n);
    std::vector<uint8_t> status((size_t)T * n);
    std::vector<int> reset(T);
    std::vector<float> want((size_t)T * C), got(C);
    for (int t = 0; t < T; ++t) {
        int tt, v;
        g >> tok >> tt >> tok >> reset[t] >> tok;
        for (int i = 0; i < n; ++i) { g >> v; dist[(size_t)t * n + i] = (int16_t)v; }
        g >> tok;
        for (int i = 0; i < n; ++i) { g >> v; status[(size_t)t * n + i] = (uint8_t)v; }
        g >> tok;
        for (int c = 0; c < C; ++c) g >> want[(size_t)t * C + c];
        if (!g) { std::fprintf(stderr, "golden parse error at frame %d\n", t); return 1; }
    }

    double max_diff = 0.0;
    int argmax_mismatch = 0;
    for (int t = 0; t < T; ++t) {
        if (reset[t]) net.reset();
        net.step(&dist[(size_t)t * n], &status[(size_t)t * n], got.data());
        int a = 0, b = 0;
        for (int c = 0; c < C; ++c) {
            double d = std::fabs((double)got[c] - (double)want[(size_t)t * C + c]);
            if (d > max_diff) max_diff = d;
            if (got[c] > got[a]) a = c;
            if (want[(size_t)t * C + c] > want[(size_t)t * C + b]) b = c;
        }
        if (a != b) ++argmax_mismatch;
    }
    std::printf("golden: %d frames, max |logit diff| = %.3g, argmax mismatches = %d\n", T, max_diff, argmax_mismatch);

    // 시간: 골든 프레임을 반복해 1초 이상 돌린다
    using clock = std::chrono::steady_clock;
    net.reset();
    long frames = 0;
    auto t0 = clock::now();
    double elapsed = 0.0;
    while (elapsed < 1.0) {
        for (int t = 0; t < T; ++t) net.step(&dist[(size_t)t * n], &status[(size_t)t * n], got.data());
        frames += T;
        elapsed = std::chrono::duration<double>(clock::now() - t0).count();
    }
    std::printf("timing: %.1f us/frame (%ld frames, 1 thread)\n", elapsed / frames * 1e6, frames);
    const bool ok = max_diff < 1e-4 && argmax_mismatch == 0;
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
