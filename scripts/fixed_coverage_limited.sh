#!/bin/bash
# Fixed deployment launcher: the package itself does not manage systemd.
set -euo pipefail
[[ "$#" == 2 && "$1" == /* && "$2" =~ ^[0-9a-f]{64}$ ]]
coverage_repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}
systemd-run --user --unit="scpolya-coverage-$(date +%s%N)" --wait --pipe --collect \
 -p CPUQuota=200% -p MemoryMax=8G -p MemorySwapMax=0 \
 -p KillMode=control-group -p RuntimeMaxSec=115s -p TimeoutStopSec=5s \
 --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=OMP_NUM_THREADS=1 --setenv=OPENBLAS_NUM_THREADS=1 \
 --setenv=MKL_NUM_THREADS=1 --setenv=NUMEXPR_NUM_THREADS=1 --setenv=NUMBA_NUM_THREADS=1 --setenv=VECLIB_MAXIMUM_THREADS=1 \
 --working-directory="$coverage_repo" \
  "${SCPOLYA_PYTHON:-$(command -v python3)}" -B \
 "$coverage_repo/scpolya_agent/coverage_diagnostics.py" run --plan "$1" --plan-sha "$2"
