# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""Does `--gridding mars` reproduce the MARS-route model-level fields, and by how much does `--gridding exact` differ?
Builds one hour from ARCO both ways and compares U, V, T, ETADOT with the MARS 0.25 deg raw file (rms, % of the field rms,
levels 60-137).  usage (repository root):  PYTHONPATH=. python livetest/gridding_check.py <MARS ML_all_L060-137.grb> YYYY-MM-DDTHH"""
import sys
from datetime import datetime

import eccodes as ec
import numpy as np

from dcfe import arco
from dcfe.config import Config

path, when = sys.argv[1], datetime.fromisoformat(sys.argv[2])
ref = {}
with open(path, "rb") as fh:
    while (g := ec.codes_grib_new_from_file(fh)):
        if ec.codes_get(g, "validityDate") == int(when.strftime("%Y%m%d")) and ec.codes_get(g, "validityTime") == when.hour * 100:
            ref[(ec.codes_get(g, "paramId"), ec.codes_get(g, "level"))] = (ec.codes_get_values(g), ec.codes_get(g, "Nj"), ec.codes_get(g, "Ni"))
        ec.codes_release(g)
any_ = next(iter(ref.values())); shape = (any_[1], any_[2])
area = (61, 21, 59, 25) if shape == (9, 17) else None
assert area, f"box {shape} not known to this script"
st = arco.Store(); res = {}
for gridding in ("exact", "mars"):
    cfg = Config.make(when.strftime("%Y%m%d"), when.strftime("%Y%m%d"), area, 0.25, (60, 137), rawdir="/tmp/x", source="mixed", gridding=gridding)
    res[gridding] = arco.HourBuilder(cfg, st).build(when)
print("rms difference to the MARS 0.25 deg file in % of the rms of the field, levels 60-137")
print(f"{'field':8s} {'exact':>8s} {'mars':>8s}")
for name in ("U", "V", "T", "ETADOT"):
    pid = {"U": 131, "V": 132, "T": 130, "ETADOT": 77}[name]
    row = []
    for gridding in ("exact", "mars"):
        num = np.mean([((res[gridding].fields[name][k] - ref[(pid, k)][0].reshape(shape)) ** 2).mean() for k in range(60, 138)])
        den = np.mean([(ref[(pid, k)][0] ** 2).mean() for k in range(60, 138)])
        row.append(100 * np.sqrt(num / den))
    print(f"{name:8s} {row[0]:8.3f} {row[1]:8.3f}")
