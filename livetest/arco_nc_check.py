# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""--source arco: are the surface, flux and static GRIB files cut from the ARCO NetCDF files the MARS-route raw data?
Builds one day (downloads 19 NetCDF files of ~50 MB, 6 more for each flux padding day) and compares every message
with the raw files of a MARS-route run of the same box and day (livetest/raw/...).
usage (repository root, netCDF4 + eccodes):  PYTHONPATH=. python livetest/arco_nc_check.py [OUTDIR]"""
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

from dcfe import gribio
from dcfe.arconc import NcDownloader
from dcfe.config import Config

out = Path(sys.argv[1] if len(sys.argv) > 1 else "livetest/raw_arconc")
ref_dir = Path("livetest/raw/N61_W21_S59_E25_g0p25/20230830")
cfg = Config.make("20230830", "20230830", (61, 21, 59, 25), 0.25, (60, 137), rawdir=out, source="arco")
dl = NcDownloader(cfg, log=print)
while dl.step():
    pass


def load(paths):
    d = {}
    for p in paths:
        for r in gribio.scan(p):
            d[(r.param_id, r.valid, r.step_type)] = gribio.values(gribio.read(r))
    return d


mine = load(sorted(out.rglob("*.grb")))
ref = load(sorted(ref_dir.glob("[FSA]*_all.grb")))
stat = defaultdict(list)
for k, v in ref.items():
    if k in mine:
        stat[(k[0], k[2])].append(np.abs(mine[k] - v).max() / (np.abs(v).max() or 1))
print("paramId stepType  n   max relative difference to the MARS-route message")
for k, v in sorted(stat.items()):
    print(f"{k[0]:5d} {k[1]:8s} {len(v):3d}  {max(v):9.2e}")
print("MARS messages without counterpart:", len([k for k in ref if k not in mine]))
