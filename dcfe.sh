#!/usr/bin/env bash
# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.
#
# The module list (sites/) and the calc_etadot self-test come from flex_extract's Run/general_callscript.sh
# (L. Haimberger, P. Seibert).

# dcFlexExtract launcher.   ./dcfe.sh setup [--no-fortran] | selftest | plan | download | process | cleanup   (-h for options)
set -euo pipefail
# the exact call is recorded in the run log (<outdir>/logs)
DCFE_CMDLINE="bash $(printf '%q ' "$0" "$@")"; DCFE_CMDLINE=${DCFE_CMDLINE% }; export DCFE_CMDLINE
ROOT=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)

# --- site: DCFE_SITE=csc|local, otherwise guessed from the host name
site=${DCFE_SITE:-}
if [[ -z $site ]]; then
  case "$(hostname -f 2>/dev/null || hostname)" in
    *csc.fi*|*roihu*|*puhti*|*mahti*) site=csc ;;
    *) site=local ;;
  esac
fi
# shellcheck source=/dev/null
source "$ROOT/sites/$site.sh"

# Modules first. The Fortran modules are only loaded when the Fortran step is used.
needs_fortran=0
case "${1:-}" in setup|build|selftest) needs_fortran=1 ;; esac
[[ " $* " == *" --no-fortran "* ]] && needs_fortran=0
prev=""
for a in "$@"; do
  [[ $a == --pressure=spectral || ( $prev == --pressure && $a == spectral ) ]] && needs_fortran=1
  prev=$a
done
site_modules_base
(( needs_fortran )) && site_modules_fortran

PY=${DCFE_PY:-}
[[ -z $PY && -x $ROOT/.venv/bin/python ]] && PY=$ROOT/.venv/bin/python
PY=${PY:-python3}
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
EXE=$ROOT/fortran/calc_etadot

build() { make -C "$ROOT/fortran" -j"$(nproc)" calc_etadot; }

selftest() {
  [[ -x $EXE ]] || build
  local t; t=$(mktemp -d)
  cp "$ROOT"/fortran/selftest/fort.* "$t"/
  local out; out=$(cd "$t" && "$EXE" 2>&1 | tail -1) || true
  local ok=1
  [[ $out == *CONGRATULATIONS* ]] || ok=0
  cmp -s "$t/fort.15" "$ROOT/fortran/selftest/expected.fort.15" || ok=0
  rm -f "$t"/fort.*; rmdir "$t"
  if (( ok )); then echo "calc_etadot works"; else echo "calc_etadot self-test FAILED (modules/ecCodes?). Last line: $out" >&2; return 1; fi
}

# Python packages dcfe needs: import name -> pip requirement. Installed only if the loaded modules lack them.
declare -A PKG=( [eccodes]="eccodes>=2.38" [numpy]="numpy" [numcodecs]="numcodecs" [netCDF4]="netCDF4" [ecmwf.datastores]="ecmwf-datastores-client>=0.6" )

setup_python() {
  echo "python: $(command -v "$PY") ($("$PY" -c 'import sys;print(sys.version.split()[0])'))"
  local missing=() m
  for m in "${!PKG[@]}"; do
    dist=${PKG[$m]%%[<>=]*}
    if v=$("$PY" -c "import ${m}; from importlib import metadata as md; print(md.version('${dist}'))" 2>/dev/null); then
      echo "  $m: found ($v)"
    else
      echo "  $m: NOT found"; missing+=("${PKG[$m]}")
    fi
  done
  if (( ${#missing[@]} == 0 )); then echo "all Python packages are provided by the loaded modules; nothing installed"; return 0; fi
  echo "installing only what the modules do not provide: ${missing[*]}"
  [[ -x $ROOT/.venv/bin/python ]] || "$PY" -m venv --system-site-packages "$ROOT/.venv"
  "$ROOT/.venv/bin/python" -m pip install "${missing[@]}"
  PY=$ROOT/.venv/bin/python
}

case "${1:-}" in
  setup)    # Python packages (modules first), then the Fortran executable (skip with --no-fortran)
    setup_python
    if (( needs_fortran )); then build; selftest; else echo "Fortran step skipped (--no-fortran); --pressure spectral is not available"; fi ;;
  build)    build ;;
  selftest) selftest ;;
  "")       grep -m1 "^# dcFlexExtract launcher" "$0" | cut -c3-; exit 1 ;;
  process)  if (( needs_fortran )) && [[ ! -x $EXE ]]; then build; selftest; fi; exec "$PY" -m dcfe "$@" ;;
  *)        exec "$PY" -m dcfe "$@" ;;
esac
