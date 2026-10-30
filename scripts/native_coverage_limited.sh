#!/bin/bash
# One fixed native-v3 preparation process; no arbitrary operation dispatch.
set -euo pipefail
[[ "$#" == 1 && "$1" == /* ]]
adapter_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}
systemd-run --user --unit="scpolya-native-coverage-$(date +%s%N)" --wait --pipe --collect \
 -p CPUQuota=200% -p MemoryMax=8G -p MemorySwapMax=0 -p RuntimeMaxSec=115s -p TimeoutStopSec=5s -p KillMode=control-group \
 --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=OMP_NUM_THREADS=1 --setenv=OPENBLAS_NUM_THREADS=1 \
 --setenv=MKL_NUM_THREADS=1 --setenv=NUMEXPR_NUM_THREADS=1 --setenv=NUMBA_NUM_THREADS=1 --setenv=VECLIB_MAXIMUM_THREADS=1 \
 --working-directory="$adapter_repo" \
  "${SCPOLYA_PYTHON:-$(command -v python3)}" -B \
 "$adapter_repo/scpolya_agent/native_coverage.py" prepare --request "$1"
