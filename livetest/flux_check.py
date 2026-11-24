# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""How well do the hourly flux fields written for FLEXPART represent the hourly accumulations they come from?
FLEXPART interpolates every field linearly in time between two files (values are rates at the file time). For each
flux this compares, over a run of hours, the hourly mean m(T) = a(T)/unit of the accumulation a(T) over (T-1h, T]
(GRIB stepRange 'k-1 - k', i.e. each message is already an hourly accumulation) with what FLEXPART would integrate
from the file values: 0.5*(r(T-1) + r(T)). Candidate file values r(T):
  disagg   dcfe default (flex_extract's dapoly/darain from 4 neighbouring hours)
  end      a(T)/unit, the mean of the hour that ENDS at T (no use of neighbours)
  start    a(T+1)/unit, the mean of the hour that STARTS at T
  mid      0.5 (a(T)+a(T+1))/unit, mean of the two hours around T
usage (repository root):  PYTHONPATH=. python livetest/flux_check.py <rawdir>"""
import sys
from datetime import timedelta

import numpy as np

from dcfe import era5, gribio
from dcfe.fluxes import FLUX_PARAMS, disaggregate, to_rate
from dcfe.rawindex import RawIndex

ix = RawIndex.from_dir(sys.argv[1])
print(f"{'flux':6s} {'candidate':8s} {'hours':>5s}  {'rms(r_int - m)/rms(m)':>22s}  {'bias of 0.5(r0+r1) vs m':>24s}  {'total over run':>15s}  {'negatives':>9s}")
for name in era5.FLUX_ORDER:
    pid = era5.PARAM_ID[name]; rain = FLUX_PARAMS[pid]
    ts = sorted(t for (p, t) in ix.flux if p == pid)
    series = {t: to_rate(gribio.values(gribio.read(ix.flux[(pid, t)])).astype(float), pid) for t in ts}
    # longest contiguous hourly run
    runs, cur = [], [ts[0]]
    for a, b in zip(ts, ts[1:]):
        if b - a == timedelta(hours=1): cur.append(b)
        else: runs.append(cur); cur = [b]
    runs.append(cur); run = max(runs, key=len)
    mean_hour = {t: series[t] for t in run}                       # m(T): mean rate over the hour ending at T
    dis = disaggregate(mean_hour, rain)
    cands = {"disagg": dis, "end": mean_hour,
             "start": {t: mean_hour[t + timedelta(hours=1)] for t in run[:-1]},
             "mid": {t: 0.5 * (mean_hour[t] + mean_hour[t + timedelta(hours=1)]) for t in run[:-1]}}
    for cn, r in cands.items():
        Ts = [t for t in run[2:-2] if (t - timedelta(hours=1)) in r and t in r]       # interior hours only
        err, ref, tot_i, tot_m, neg, n = [], [], 0.0, 0.0, 0, 0
        for t in Ts:
            I = 0.5 * (r[t - timedelta(hours=1)] + r[t])
            if name == "SSR": I = 0.5 * (np.maximum(r[t - timedelta(hours=1)], 0) + np.maximum(r[t], 0))      # FLEXPART clips ssr < 0
            if rain: I = 0.5 * (np.maximum(r[t - timedelta(hours=1)], 0) + np.maximum(r[t], 0))              # and precipitation < 0
            m = mean_hour[t]
            err.append(((I - m) ** 2).mean()); ref.append((m ** 2).mean()); tot_i += I.mean(); tot_m += m.mean()
            neg += int((r[t] < 0).sum()); n += r[t].size
        print(f"{name:6s} {cn:8s} {len(Ts):5d}  {100*np.sqrt(np.mean(err)/np.mean(ref)):21.1f}%  {100*(tot_i/tot_m-1):23.2f}%  {tot_i:15.4g}  {100*neg/n:8.2f}%")
