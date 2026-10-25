# Source and redistribution notices

- TEAMMATE-authored code follows the repository root MIT license.
- The six Flutter Atlas runner files are byte-identical to files already in this
  main baseline. Their Sony/Flutter/Samsung BSD notices are retained; the upstream
  license is included at app/atlas/runner/LICENSE.
  Source: https://github.com/sony/flutter-elinux/blob/main/LICENSE
- Atlas CMake scaffold files also match previously public TEAMMATE source blobs.
  No private LG SDK, engine, toolchain, PDF, plugin implementation or binary is
  vendored. Builds use an externally supplied licensed Atlas environment.
- Python probe, manual-trial and air-quality logic/fixtures are minimal TEAMMATE
  snapshots from the ThinQ work. Their hashes are in PROVENANCE.json; the relevant
  source is already public in PR #65. Self-contained copies preserve the isolated
  Docker build and Python import boundary without importing the whole PR.
- libcurl/OpenSSL/sdbus-c++/Python and Flutter dependencies are referenced only;
  no dependency source or SDK wheel is included. Binary packages stay local,
  excluded from Git; distribution of those packages needs its own license review.
