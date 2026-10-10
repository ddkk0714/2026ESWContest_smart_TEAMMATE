// Runs the firmware posture estimator (firmware/.../preprocess/tof/TofPosture) on
// frames piped in from Python, so the rule-based baseline can be scored on the same
// synthetic sessions as the learned model.
//
// stdin : uint8 width, uint8 height, then frames of
//         uint32 t_ms | int16 distance[w*h] | uint8 status[w*h]
//         t_ms == 0xFFFFFFFF starts a new session (estimator reset, no output).
// stdout: one uint8 per frame = (uint8)TofPosture
//
// Build: g++ -std=gnu++14 -O2 -I../../firmware/esp32_sensor_node/src rule_replay.cpp
//        ../../firmware/esp32_sensor_node/src/preprocess/tof/TofPosture.cpp -o rule_replay
#include <cstdio>
#include <cstdint>
#include <memory>
#include <vector>

#include "preprocess/tof/TofPosture.h"

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

using namespace deskmate;

int main() {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  uint8_t wh[2];
  if (fread(wh, 1, 2, stdin) != 2) return 1;
  const size_t z = (size_t)wh[0] * wh[1];
  std::vector<int16_t> dist(z);
  std::vector<uint8_t> status(z);
  std::unique_ptr<TofPostureEstimator> est(new TofPostureEstimator());
  uint32_t t;
  while (fread(&t, 4, 1, stdin) == 1) {
    if (t == 0xFFFFFFFFu) {
      est->reset();
      continue;
    }
    if (fread(dist.data(), 2, z, stdin) != z || fread(status.data(), 1, z, stdin) != z) return 2;
    TofFrameView v;
    v.width = wh[0];
    v.height = wh[1];
    v.distance_mm = dist.data();
    v.status = status.data();
    est->update(v, t);
    const uint8_t p = (uint8_t)est->features().posture;
    fwrite(&p, 1, 1, stdout);
  }
  fflush(stdout);
  return 0;
}
