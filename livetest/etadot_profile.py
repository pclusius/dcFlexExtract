# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""Vertical profile of ETADOT (1/s) averaged over the centre 1 x 1 degree (5 x 5 points at 0.25 deg) of the test
box 59-61N 21-25E, 2023-08-30 14 UTC, levels 60-137, from three sources: mixed route (continuity from ARCO), the MARS
0.25 deg file, and the exact synthesis of MARS's native T639 coefficients. Vertical axis: model level, logarithmic.
usage (repository root):  PYTHONPATH=. python livetest/etadot_profile.py [out.png]"""
import sys
from datetime import datetime

import eccodes as ec
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from dcfe import arco, spectral
from dcfe.config import Config

cfg = Config.make("20230830", "20230830", (61, 21, 59, 25), 0.25, (60, 137), rawdir="/tmp/x", source="mixed")
lat, lon = arco.box_axes(cfg)
ic = np.flatnonzero((lat >= 59.5 - 1e-6) & (lat <= 60.5 + 1e-6)); jc = np.flatnonzero((lon >= 22.5 - 1e-6) & (lon <= 23.5 + 1e-6))
levels = np.arange(60, 138)
centre = lambda f: f[np.ix_(ic, jc)].mean()
coef, mars = {}, {}
with open("livetest/fdtest/native.grb", "rb") as fh:
    while (g := ec.codes_grib_new_from_file(fh)):
        if ec.codes_get(g, "paramId") == 77: coef[ec.codes_get(g, "level")] = ec.codes_get_values(g)
        ec.codes_release(g)
with open("livetest/raw/N61_W21_S59_E25_g0p25/20230830/ML_all_L060-137.grb", "rb") as fh:
    while (g := ec.codes_grib_new_from_file(fh)):
        if ec.codes_get(g, "paramId") == 77 and ec.codes_get(g, "dataTime") == 1400:
            mars[ec.codes_get(g, "level")] = ec.codes_get_values(g).reshape(len(lat), len(lon))
        ec.codes_release(g)
syn = spectral.scalar([coef[k] for k in levels], spectral.Basis(lat, lon, spectral.truncation(len(coef[60]))))
ours = arco.HourBuilder(cfg, arco.Store()).build(datetime(2023, 8, 30, 14)).fields["ETADOT"]
series = [("ours: continuity from ARCO", [centre(ours[k]) for k in levels], "#0b6e75", "-"),
          ("MARS 0.25° file", [centre(mars[k]) for k in levels], "#b4540a", "-"),
          ("MARS T639 coefficients, exact synthesis", [centre(syn[i]) for i in range(len(levels))], "#3b4a68", "--")]
fig, ax = plt.subplots(figsize=(6.2, 7.2), dpi=150)
for name, v, c, ls in series:
    ax.plot(np.array(v) * 1e6, levels, color=c, lw=2, ls=ls, label=name)
ax.set_yscale("log"); ax.invert_yaxis()
ticks = [60, 70, 80, 90, 100, 110, 120, 130, 137]
ax.set_yticks(ticks); ax.set_yticklabels([str(t) for t in ticks]); ax.minorticks_off()
ax.axvline(0, color="#8793a6", lw=0.8)
ax.set_xlabel("ETADOT  (10$^{-6}$ s$^{-1}$)"); ax.set_ylabel("model level (log scale, 137 = lowest)")
ax.set_title("ETADOT, mean of the centre 1°×1° (59.5–60.5°N, 22.5–23.5°E)\n2023-08-30 14 UTC, 25 points", fontsize=10)
ax.grid(alpha=0.25); ax.legend(frameon=False, fontsize=9, loc="lower left")
for s in ("top", "right"): ax.spines[s].set_visible(False)
fig.tight_layout(); out = sys.argv[1] if len(sys.argv) > 1 else "livetest/etadot_profile.png"; fig.savefig(out); print("wrote", out)
for name, v, *_ in series: print(f"{name:42s} L60 {v[0]*1e6:7.3f}  L100 {v[40]*1e6:7.3f}  L137 {v[-1]*1e6:7.3f} (1e-6/s)")
