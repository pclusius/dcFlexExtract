# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""Finite-difference ETADOT test. Same continuity formula as arco_proto.etadot_continuity, but with horizontal
derivatives taken by centred differences on the 0.25 deg grid instead of in spectral space. It answers: is the
~3-10% difference between ARCO-continuity H and the MARS eta_dot caused by MARS's gridded fields (then H computed
from MARS-gridded D, U, V, LNSP should agree with MARS eta_dot much better) or by the IFS eta_dot itself?

  --source arco   D, U, V, LNSP synthesised from the ARCO spectral data on the SAME grid as the MARS request
                  (58-62N 19-27E): measures the error of the finite-difference scheme alone (reference: spectral H)
  --source mars   D (155), U, V, LNSP read from livetest/fdtest/fd.grb (see arco_mars_fd.py)
Both are compared inside the test box (59-61N 21-25E) with the eta_dot of the hourly file --ref (MARS, ground start).
Needs numpy, eccodes (and numcodecs for --source arco).
usage:  PYTHONPATH=livetest python livetest/arco_fd_test.py --source arco|mars [--ref livetest/out_ground/EA02523083014]"""
import argparse, json, sys
import numpy as np

A_EARTH = 6371229.0


def fd_H(D, U, V, lnsp, lat, lon, A, B):
    """D, U, V: {level: (nlat, nlon)}, lnsp (nlat, nlon), lat descending, lon ascending (deg).
    Returns {k: H(k+1/2)} for all levels, valid in the interior (edges use one-sided differences)."""
    la = np.radians(lat); dla = np.gradient(la); dlo = np.gradient(np.radians(lon))
    cos = np.cos(la)[:, None]
    gx = np.gradient(lnsp, axis=1) / dlo[None, :] / (A_EARTH * cos)
    gy = np.gradient(lnsp, axis=0) / dla[:, None] / A_EARTH
    ps = np.exp(lnsp)
    S = {}
    for k in range(1, 138):
        dA, dB = A[k] - A[k - 1], B[k] - B[k - 1]
        S[k] = (dA + dB * ps) * D[k] + (dB * ps * (U[k] * gx + V[k] * gy) if dB != 0 else 0.0)
    tot = sum(S.values()); out = {}; cum = 0.0
    for k in range(1, 138):
        cum = cum + S[k]; out[k] = B[k] * tot - cum
    return out


def load_mars(path):
    import eccodes as ec
    D, U, V, ln = {}, {}, {}, None; lat = lon = pv = None
    with open(path, "rb") as fh:
        while (g := ec.codes_grib_new_from_file(fh)):
            pid, lev = ec.codes_get(g, "paramId"), ec.codes_get(g, "level")
            ni, nj = ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj")
            v = ec.codes_get_values(g).reshape(nj, ni)
            if lat is None:
                la0 = ec.codes_get(g, "latitudeOfFirstGridPointInDegrees"); lo0 = ec.codes_get(g, "longitudeOfFirstGridPointInDegrees")
                lat = la0 - 0.25 * np.arange(nj); lon = lo0 + 0.25 * np.arange(ni); pv = ec.codes_get_array(g, "pv")
            {155: D, 131: U, 132: V}.get(pid, {}).__setitem__(lev, v) if pid in (155, 131, 132) else None
            if pid == 152: ln = v
            ec.codes_release(g)
    lon = np.where(lon > 180, lon - 360, lon)
    return D, U, V, ln, lat, lon, pv[:138], pv[138:]


def load_arco(lat, lon, hour_utc="2023-08-30T14"):
    import arco_proto as ap
    from datetime import datetime, timezone
    from concurrent.futures import ThreadPoolExecutor
    when = datetime.fromisoformat(hour_utc).replace(tzinfo=timezone.utc)
    iw, isf = ap.time_index(ap.WIND, when), ap.time_index(ap.SURF, when)
    pv = json.loads(ap.get(f"{ap.ROOT}/{ap.SURF}/.zmetadata"))["metadata"]["lnsp/.zattrs"]["GRIB_pv"]
    pool = ThreadPoolExecutor(16)
    jd = {L: pool.submit(ap.chunk, ap.WIND, "d", iw, L - 1, 0) for L in range(1, 138)}
    jv = {L: pool.submit(ap.chunk, ap.WIND, "vo", iw, L - 1, 0) for L in range(1, 138)}
    jl = pool.submit(ap.chunk, ap.SURF, "lnsp", isf, 0)
    lay = ap.sh_layout(); P, cosl = ap.legendre(lat); e = ap.eps(); w = np.where(np.arange(ap.N_TRUNC + 1) == 0, 1.0, 2.0)
    D, U, V = {}, {}, {}
    for L in range(1, 138):
        Cd, Cv = ap.to_matrix(jd[L].result(), lay), ap.to_matrix(jv[L].result(), lay)
        D[L] = ap.scalar_field(Cd, P, lon, w); U[L], V[L] = ap.wind_fields(Cv, Cd, P, cosl, lon, w, e)
    Cl = ap.to_matrix(jl.result(), lay)
    ln = ap.scalar_field(Cl, P, lon, w)
    Hs, _ = ap.etadot_continuity(D, U, V, Cl, np.array(pv[:138]), np.array(pv[138:]), P, cosl, lon, w, e)
    return D, U, V, ln, np.array(pv[:138]), np.array(pv[138:]), Hs


ap_ = argparse.ArgumentParser()
ap_.add_argument("--source", choices=("arco", "mars"), required=True)
ap_.add_argument("--fd", default="livetest/fdtest/fd.grb")
ap_.add_argument("--ref", default="livetest/out_ground/EA02523083014")
a = ap_.parse_args()
lat = np.arange(62, 58 - 1e-9, -0.25); lon = np.arange(19, 27 + 1e-9, 0.25)
Hs = None
if a.source == "mars":
    D, U, V, ln, lat, lon, A, B = load_mars(a.fd)
else:
    D, U, V, ln, A, B, Hs = load_arco(lat, lon)
H = fd_H(D, U, V, ln, lat, lon, A, B)
# test box inside the grid
j0 = int(round((lat[0] - 61) / 0.25)); i0 = int(round((21 - lon[0]) / 0.25))
sl = (slice(j0, j0 + 9), slice(i0, i0 + 17))
tl = np.arange(61, 59 - 1e-9, -0.25); tn = np.arange(21, 25 + 1e-9, 0.25)
import eccodes as ec
ref = {}
with open(a.ref, "rb") as fh:
    while (g := ec.codes_grib_new_from_file(fh)):
        if ec.codes_get(g, "paramId") == 77:
            ni, nj = ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj")
            la0 = ec.codes_get(g, "latitudeOfFirstGridPointInDegrees"); lo0 = ec.codes_get(g, "longitudeOfFirstGridPointInDegrees")
            lo0 = lo0 - 360 if lo0 > 180 else lo0
            jj = np.round((la0 - tl) / 0.25).astype(int); ii = np.round((tn - lo0) / 0.25).astype(int) % ni
            ref[ec.codes_get(g, "level")] = ec.codes_get_values(g).reshape(nj, ni)[np.ix_(jj, ii)]
        ec.codes_release(g)
print(f"source {a.source}: finite-difference H vs MARS eta_dot (test box, interior of the request box)")
print("level  rms(MARS)  rms(FD-MARS)  max|FD-MARS|" + ("   rms(FD-spectral)" if Hs else ""))
for L in (60, 70, 80, 90, 100, 110, 120, 130):
    f = H[L][sl]; r = ref[L]
    line = f"{L:5d} {np.sqrt((r**2).mean()):10.4f} {np.sqrt(((f - r)**2).mean()):12.4f} {abs(f - r).max():12.4f}"
    if Hs: line += f"   {np.sqrt(((f - Hs[L][sl])**2).mean()):14.5f}"
    print(line)
tot = lambda x: np.sqrt(np.mean([((x[L][sl] - ref[L]) ** 2).mean() for L in range(60, 138)]))
print(f"rms over levels 60-137, FD vs MARS: {tot(H):.4f} Pa/s" + (f"; spectral (ARCO) vs MARS: {tot({L: Hs[L][sl] for L in Hs} if False else Hs):.4f}" if Hs else ""))
