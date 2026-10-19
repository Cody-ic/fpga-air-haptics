#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${BUILD_DIR:-/tmp/haptics-mg24-host-tests}"
mkdir -p "$out"
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -I"$root/haptics_mg24" \
    "$root/haptics_mg24/bridge.cpp" "$root/tests/test_bridge.cpp" -o "$out/test_bridge"
cd "$out"
./test_bridge "$@"
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -I"$root/tests/sampler_stubs" \
    -I"$root/haptics_mg24" "$root/haptics_mg24/sampler.cpp" "$root/tests/test_sampler.cpp" \
    -o "$out/test_sampler"
./test_sampler
