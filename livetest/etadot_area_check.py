# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""ETADOT of `--source mixed` on a larger area against MARS's own archived spectral T639 ETADOT (a MARS request without
grid/area, here livetest/fdtest/native.grb, global, one hour, levels 60-137), reproduced both ways:
  exact   exact synthesis of the coefficients at the grid points      vs  --gridding exact
  mars    N360 Gaussian rows + linear in latitude (MARS's own step)   vs  --gridding mars
usage (repository root):  PYTHONPATH=. python livetest/etadot_area_check.py N W S E YYYY-MM-DDTHH native.grb"""
import sys
import time
from datetime import datetime

import eccodes as ec
import numpy as np

from dcfe import arco, spectral
from dcfe.config import Config

N, W, S, E = map(float, sys.argv[1:5]); when = datetime.fromisoformat(sys.argv[5])
coef = {}
with open(sys.argv[6], "rb") as fh:
    while (g := ec.codes_grib_new_from_file(fh)):
        if ec.codes_get(g, "paramId") == 77: coef[ec.codes_get(g, "level")] = ec.codes_get_values(g)
        ec.codes_release(g)
lv = sorted(coef); trunc = spectral.truncation(len(coef[lv[0]]))
st = arco.Store(); out = {}
for gridding in ("exact", "mars"):
    cfg = Config.make(when.strftime("%Y%m%d"), when.strftime("%Y%m%d"), (N, W, S, E), 0.25, (lv[0], lv[-1]), rawdir="/tmp/x", source="mixed", gridding=gridding)
    t0 = time.time(); b = arco.HourBuilder(cfg, st); h = b.build(when); out[gridding] = h
    print(f"{gridding}: {len(h.lat)} x {len(h.lon)} points, {time.time() - t0:.0f} s", flush=True)
lat, lon = h.lat, h.lon
exact = spectral.scalar([coef[k] for k in lv], spectral.Basis(lat, lon, trunc))
rows, Wm = spectral.latitude_rows(lat, 360)
marsl = np.stack([Wm @ f for f in spectral.scalar([coef[k] for k in lv], spectral.Basis(rows, lon, trunc))])
rel = lambda a, r: 100 * np.sqrt(((a - r) ** 2).mean() / (r ** 2).mean())
print(f"\nrms difference in % of the rms of the reference, {len(lat) * len(lon)} points per level")
print("level   gridding exact vs exact T639   gridding mars vs MARS-pipeline T639   (exact vs MARS-pipeline)")
for i, k in enumerate(lv):
    if k in (60, 80, 100, 110, 120, 130, 137):
        print(f"{k:5d}   {rel(out['exact'].fields['ETADOT'][k], exact[i]):14.2f}   {rel(out['mars'].fields['ETADOT'][k], marsl[i]):24.2f}   {rel(exact[i], marsl[i]):16.2f}")
tot = lambda f, r: np.sqrt(np.mean([((f(k, i) - r[i]) ** 2).mean() for i, k in enumerate(lv)]) / np.mean([(r[i] ** 2).mean() for i in range(len(lv))])) * 100
print(f"all levels {lv[0]}-{lv[-1]}: exact {tot(lambda k, i: out['exact'].fields['ETADOT'][k], exact):.2f} %,  mars {tot(lambda k, i: out['mars'].fields['ETADOT'][k], marsl):.2f} %,  "
      f"(MARS-pipeline vs exact T639 {tot(lambda k, i: marsl[i], exact):.2f} %)")
