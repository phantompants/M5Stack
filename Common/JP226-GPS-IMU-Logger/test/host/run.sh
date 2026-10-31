#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
task_test_dir=$(mktemp -d /tmp/jp226-obd-tests.XXXXXX)
trap 'rm -rf "$task_test_dir"' EXIT
c++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -Iinclude src/ObdProtocol.cpp test/host/test_obd_protocol.cpp \
  -o "$task_test_dir/protocol"
c++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -Itest/host/fakes -Iinclude src/ObdProtocol.cpp src/ObdReader.cpp \
  test/host/test_obd_reader.cpp -o "$task_test_dir/reader"
c++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -Itest/host/fakes -Iinclude src/WifiSetupPage.cpp test/host/test_wifi_setup.cpp \
  -o "$task_test_dir/wifi"
c++ -std=c++17 -Wall -Wextra -Werror -fsanitize=address,undefined \
  -Iinclude src/ObdProtocol.cpp src/ObdCsv.cpp test/host/test_obd_csv.cpp \
  -o "$task_test_dir/csv"
# LeakSanitizer cannot run under the sandbox's ptrace; address/UB checks stay on.
ASAN_OPTIONS=detect_leaks=0 "$task_test_dir/protocol"
ASAN_OPTIONS=detect_leaks=0 "$task_test_dir/reader"
ASAN_OPTIONS=detect_leaks=0 "$task_test_dir/wifi"
ASAN_OPTIONS=detect_leaks=0 "$task_test_dir/csv"
