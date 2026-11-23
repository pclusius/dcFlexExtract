# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""PROTOTYPE: ERA5 model-level fields from the Google ARCO-ERA5 Zarr stores, no MARS, no queue.

Reads one hour from gs://gcp-public-data-arco-era5/co/ over plain HTTPS, builds T, U, V (from spectral
T, vorticity, divergence), LNSP (spectral) and Q (reduced Gaussian N320) on a regular lat/lon box and, if
a reference GRIB is given, compares them with it.

Needs only: numpy, numcodecs (blosc; both are part of any zarr install). eccodes is optional and used
only for --ref. Nothing is written except --out/arco_<date>_<hh>.npz.

Example (the case in livetest/, reference = the old flex_extract hourly file):
    python arco_proto.py --date 2023-08-30 --hour 14 --levels 60-137 --box 61 59 21 25 \
        --ref ../../FE/ERA5/20230830/EA02523083014 --out /tmp/arco
Quick network/timing check without any maths:  add --fetch-only
"""
import argparse, json, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
from numcodecs import Blosc

ROOT = "https://storage.googleapis.com/gcp-public-data-arco-era5/co"
WIND, MOIST, SURF = "model-level-wind.zarr-v2", "model-level-moisture.zarr-v2", "single-level-surface.zarr-v2"
A_EARTH = 6371229.0            # IFS Earth radius (m)
N_TRUNC = 639                  # T639
_codec = Blosc()
_stats = {"bytes": 0, "n": 0}


def get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                b = r.read()
            _stats["bytes"] += len(b); _stats["n"] += 1
            return b
        except Exception as e:                                   # network hiccup: retry, then give up
            if i == tries - 1:
                raise RuntimeError(f"{url}: {e}")
            time.sleep(1 + 2 * i)


def chunk(store, var, *idx):
    """One Zarr v2 chunk = one (time, level) field; lz4/blosc-compressed float32 (or float64/int64)."""
    meta = json.loads(get(f"{ROOT}/{store}/{var}/.zarray"))
    raw = _codec.decode(get(f"{ROOT}/{store}/{var}/" + ".".join(str(i) for i in idx)))
    return np.frombuffer(raw, dtype=meta["dtype"])


def time_index(store, when):
    t = chunk(store, "time", 0)                                   # hours since 1900-01-01 (int64)
    h = int((when - datetime(1900, 1, 1, tzinfo=timezone.utc)).total_seconds() // 3600)
    i = int(np.searchsorted(t, h))
    if i >= t.size or t[i] != h:
        last = datetime(1900, 1, 1, tzinfo=timezone.utc).timestamp() + int(t[-1]) * 3600
        sys.exit(f"{when:%Y-%m-%d %H} UTC is not in {store} (last time there: "
                 f"{datetime.fromtimestamp(last, timezone.utc):%Y-%m-%d %H} UTC). Use MARS from then onwards.")
    return i


# --------------------------------------------------------------------------- spectral synthesis
def sh_layout(N=N_TRUNC):
    """ARCO/cfgrib order: m = 0..N outer, n = m..N inner, (re, im) interleaved -> index arrays."""
    m = np.concatenate([np.full(N + 1 - k, k) for k in range(N + 1)])
    n = np.concatenate([np.arange(k, N + 1) for k in range(N + 1)])
    return m, n


def to_matrix(flat, lay, N=N_TRUNC, extra=2):
    """flat (2*ncoef,) -> complex C[n, m] of shape (N+extra, N+1)."""
    m, n = lay
    C = np.zeros((N + extra, N + 1), complex)
    C[n, m] = flat[0::2] + 1j * flat[1::2]
    return C


def legendre(lat_deg, N=N_TRUNC, sign=1):
    """Fully normalised associated Legendre functions Pbar[n, m] (n up to N+1), IFS normalisation
    (integral over mu = 2, so Pbar_00 = 1, Pbar_10 = sqrt(3) mu), at each latitude. sign=-1 adds (-1)^m.
    Returns P (nlat, N+2, N+1) and cos(lat)."""
    mu = np.sin(np.radians(lat_deg)); c = np.cos(np.radians(lat_deg))
    nl = len(mu)
    P = np.zeros((nl, N + 2, N + 1))
    mm = np.arange(N + 1)
    pmm = np.ones((nl,))
    diag = np.zeros((nl, N + 1))
    diag[:, 0] = 1.0
    for k in range(1, N + 1):
        diag[:, k] = diag[:, k - 1] * c * np.sqrt((2 * k + 1) / (2.0 * k))
    for k in range(N + 1):
        P[:, k, k] = diag[:, k] * (sign ** k)
    for k in range(N + 1):                                          # n = k + 1 ... via the 3-term recurrence
        n = k + 1
        if n <= N + 1:
            P[:, n, k] = np.sqrt(2 * k + 3.0) * mu * P[:, k, k]
    for n in range(2, N + 2):
        k = np.arange(0, min(n - 1, N + 1))                         # m = 0..n-2
        k = k[k <= N]
        a = np.sqrt((4.0 * n * n - 1) / (n * n - k * k))
        b = np.sqrt(((n - 1.0) ** 2 - k * k) / (4.0 * (n - 1) ** 2 - 1))
        P[:, n, k] = a * (mu[:, None] * P[:, n - 1, k] - b * P[:, n - 2, k])
    return P, c


def eps(N=N_TRUNC):
    n = np.arange(N + 2)[:, None].astype(float); m = np.arange(N + 1)[None, :].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.sqrt(np.where(n > 0, (n * n - m * m) / (4 * n * n - 1), 0.0))
    return np.nan_to_num(e)


def synth(F, lon_deg, weights):
    """F[lat, m] complex Fourier coefficients -> values[lat, lon]."""
    lam = np.radians(lon_deg)
    E = np.exp(1j * np.outer(np.arange(F.shape[1]), lam))         # (m, lon)
    return np.real((F * weights) @ E)


def scalar_field(C, P, lon, w):
    """sum_n C[n,m] Pbar[n,m] per latitude -> (lat, m); then Fourier sum."""
    F = np.einsum("nm,lnm->lm", C[:P.shape[1] - 1 + 1][:P.shape[1]], P)
    return synth(F, lon, w)


def wind_fields(vo, d, P, cosl, lon, w, e):
    """u, v on the box from spectral vorticity and divergence (matrices C[n,m], n up to N+1)."""
    N = P.shape[2] - 1
    n = np.arange(P.shape[1])[:, None].astype(float); m = np.arange(N + 1)[None, :].astype(float)
    nn1 = n * (n + 1); nn1[0] = 1.0
    psi = -A_EARTH ** 2 * vo / nn1; chi = -A_EARTH ** 2 * d / nn1
    psi[0] = 0; chi[0] = 0
    # H[n,m] = (1-mu^2) dPbar/dmu = -n eps[n+1,m] Pbar[n+1,m] + (n+1) eps[n,m] Pbar[n-1,m]
    H = np.zeros_like(P)
    nmax = P.shape[1] - 1
    H[:, :nmax] = -n[:nmax] * e[1:nmax + 1] * P[:, 1:nmax + 1]
    H[:, 1:] += (n[1:] + 1) * e[1:nmax + 1] * P[:, :nmax]
    Fu = 1j * m * np.einsum("nm,lnm->lm", chi, P) - np.einsum("nm,lnm->lm", psi, H)
    Fv = 1j * m * np.einsum("nm,lnm->lm", psi, P) + np.einsum("nm,lnm->lm", chi, H)
    ucos = synth(Fu, lon, w) / A_EARTH; vcos = synth(Fv, lon, w) / A_EARTH
    return ucos / cosl[:, None], vcos / cosl[:, None]



def dleg(P, e):
    """H[n,m] = (1-mu^2) dPbar/dmu = cos(lat) dPbar/dlat, from the Pbar table (n up to N+1)."""
    n = np.arange(P.shape[1])[:, None].astype(float); nmax = P.shape[1] - 1
    H = np.zeros_like(P)
    H[:, :nmax] = -n[:nmax] * e[1:nmax + 1] * P[:, 1:nmax + 1]
    H[:, 1:] += (n[1:] + 1) * e[1:nmax + 1] * P[:, :nmax]
    return H


def etadot_continuity(D, U, V, lnsp, A, B, P, cosl, lon, w, e):
    """H(k+1/2) = eta_dot * dp/deta at the interface BELOW model level k (what calc_etadot writes), k = 1..137,
    from mass continuity, all inside the box:
        S_k = div(V_k dp_k) = dp_k D_k + dB_k ps (V_k . grad ln ps),   dp_k = dA_k + dB_k ps
        H(k+1/2) = B(k+1/2) * sum_all S - sum_{j<=k} S_j                (H = 0 at the top and at the ground)
    D, U, V: {level: values(lat, lon)} (D for ALL 137 levels, U/V for every level with dB != 0);
    lnsp: spectral matrix C[n, m]; A, B: half-level coefficients (138)."""
    Hm = dleg(P, e); m = np.arange(P.shape[2])[None, :]
    ps = np.exp(synth(np.einsum("nm,lnm->lm", lnsp, P), lon, w))
    gx = synth(1j * m * np.einsum("nm,lnm->lm", lnsp, P), lon, w) / (A_EARTH * cosl[:, None])
    gy = synth(np.einsum("nm,lnm->lm", lnsp, Hm), lon, w) / (A_EARTH * cosl[:, None])
    S, cum = {}, 0.0
    for k in range(1, 138):
        dA, dB = A[k] - A[k - 1], B[k] - B[k - 1]
        S[k] = (dA + dB * ps) * D[k] + (dB * ps * (U[k] * gx + V[k] * gy) if dB != 0 else 0.0)
    tot = sum(S.values()); out = {}
    for k in range(1, 138):
        cum = cum + S[k]
        out[k] = B[k] * tot - cum
    return out, ps


# ---- batched versions: many levels at once, the Legendre sums as BLAS matrix products (100x faster than einsum)
class Basis:
    """Legendre tables of a latitude list, arranged for matmul: Pm[m, lat, n] and Hm[m, lat, n]."""
    def __init__(self, lat_deg, lon_deg, sign=1):
        P, self.cosl = legendre(lat_deg, sign=sign)
        self.Pm = np.ascontiguousarray(P.transpose(2, 0, 1))
        self.Hm = np.ascontiguousarray(dleg(P, eps()).transpose(2, 0, 1)); del P
        self.m = np.arange(self.Pm.shape[0]); self.w = np.where(self.m == 0, 1.0, 2.0)
        self.E = np.exp(1j * np.outer(self.m, np.radians(lon_deg))) * self.w[:, None]     # (m, lon), weights included
        self.lay = sh_layout()


def stack_matrices(flats, lay, N=N_TRUNC, extra=2):
    """list of flat coefficient vectors -> complex array (L, n, m)"""
    m, n = lay; F = np.asarray(flats, dtype=np.float64)
    C = np.zeros((F.shape[0], N + extra, N + 1), complex)
    C[:, n, m] = F[:, 0::2] + 1j * F[:, 1::2]
    return C


def _sum_n(C, Tm):
    """sum_n C[l, n, m] T[lat, n, m] -> (l, lat, m) complex; one real matmul per m for all levels."""
    L = C.shape[0]
    X = np.ascontiguousarray(np.concatenate([C.real, C.imag]).transpose(2, 1, 0))     # (m, n, 2L)
    R = Tm @ X                                                                         # (m, lat, 2L)
    return (R[..., :L] + 1j * R[..., L:]).transpose(2, 1, 0)


def scalar_batch(flats, bs):
    C = stack_matrices(flats, bs.lay)
    return np.real(_sum_n(C, bs.Pm) @ bs.E)                                            # (L, lat, lon)


def wind_batch(vo_flats, d_flats, bs):
    vo, d = stack_matrices(vo_flats, bs.lay), stack_matrices(d_flats, bs.lay)
    n = np.arange(vo.shape[1])[None, :, None].astype(float); nn1 = n * (n + 1); nn1[:, 0] = 1.0
    psi = -A_EARTH ** 2 * vo / nn1; chi = -A_EARTH ** 2 * d / nn1; psi[:, 0] = 0; chi[:, 0] = 0
    L = vo.shape[0]; both = np.concatenate([chi, psi])
    SP = _sum_n(both, bs.Pm); SH = _sum_n(both, bs.Hm); im = 1j * bs.m[None, None, :]
    Fu = im * SP[:L] - SH[L:]; Fv = im * SP[L:] + SH[:L]
    c = bs.cosl[None, :, None] * A_EARTH
    return np.real(Fu @ bs.E) / c, np.real(Fv @ bs.E) / c


# --------------------------------------------------------------------------- reduced Gaussian (q)
class ReducedGG:
    def __init__(self, lat, lon):
        self.lat, self.lon = lat, lon
        starts = np.flatnonzero(np.r_[True, np.diff(lat) != 0]); self.starts = starts
        self.rows = lat[starts]; self.cnt = np.diff(np.r_[starts, lat.size])

    def interp(self, vals, tlat, tlon):
        out = np.empty((len(tlat), len(tlon)))
        for a, la in enumerate(tlat):
            j = np.searchsorted(-self.rows, -la)                      # rows are north -> south
            j0 = int(np.clip(j - 1, 0, len(self.rows) - 2)); j1 = j0 + 1
            wl = (self.rows[j0] - la) / (self.rows[j0] - self.rows[j1])
            row = []
            for jj in (j0, j1):
                s, c = self.starts[jj], self.cnt[jj]
                x = np.r_[self.lon[s:s + c], self.lon[s] + 360.0]; y = np.r_[vals[s:s + c], vals[s]]
                row.append(np.interp(np.mod(tlon, 360.0), x, y))
            out[a] = (1 - wl) * row[0] + wl * row[1]
        return out


# --------------------------------------------------------------------------- reference GRIB
def read_ref(path, tlat, tlon, wanted):
    import eccodes as ec
    out = {}
    with open(path, "rb") as fh:
        while (g := ec.codes_grib_new_from_file(fh)):
            key = (ec.codes_get(g, "paramId"), ec.codes_get(g, "level"))
            if key in wanted and ec.codes_get(g, "gridType") == "regular_ll":
                ni, nj = ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj")
                la0 = ec.codes_get(g, "latitudeOfFirstGridPointInDegrees"); lo0 = ec.codes_get(g, "longitudeOfFirstGridPointInDegrees")
                dj = ec.codes_get(g, "jDirectionIncrementInDegrees"); di = ec.codes_get(g, "iDirectionIncrementInDegrees")
                lo0 = lo0 - 360 if lo0 > 180 else lo0
                v = ec.codes_get_values(g).reshape(nj, ni)
                j = np.round((la0 - tlat) / dj).astype(int); i = np.round((tlon - lo0) / di).astype(int) % ni
                out[key] = v[np.ix_(j, i)]
            ec.codes_release(g)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default="2023-08-30"); ap.add_argument("--hour", type=int, default=14)
    ap.add_argument("--levels", default="60-137"); ap.add_argument("--box", nargs=4, type=float, default=[61, 59, 21, 25],
                    metavar=("N", "S", "W", "E")); ap.add_argument("--step", type=float, default=0.25)
    ap.add_argument("--workers", type=int, default=16); ap.add_argument("--out", default=".")
    ap.add_argument("--ref"); ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--cloud", action="store_true", help="also fetch CLWC and CIWC (N320, interpolated like Q)")
    ap.add_argument("--etadot", action="store_true", help="also compute eta_dot*dp/deta from continuity (fetches d for all 137 levels)")
    ap.add_argument("--legendre-sign", type=int, default=1, choices=(1, -1), help="1 = no Condon-Shortley phase")
    a = ap.parse_args()
    lo, hi = (int(x) for x in a.levels.split("-")); levels = list(range(lo, hi + 1))
    when = datetime.fromisoformat(a.date).replace(hour=a.hour, tzinfo=timezone.utc)
    N, S, W, E = a.box
    tlat = np.arange(N, S - 1e-9, -a.step); tlon = np.arange(W, E + 1e-9, a.step)
    os.makedirs(a.out, exist_ok=True)

    t0 = time.time()
    iw, im, isf = time_index(WIND, when), time_index(MOIST, when), time_index(SURF, when)
    print(f"time index wind/moist/surface: {iw}/{im}/{isf}   ({time.time() - t0:.1f}s)")
    # hybrid is stored one value per chunk: check that chunk L-1 really is model level L (first and last)
    assert chunk(WIND, "hybrid", 0)[0] == 1 and chunk(WIND, "hybrid", 136)[0] == 137, "level order"
    pool = ThreadPoolExecutor(a.workers)
    jobs = {("lnsp", 1): pool.submit(chunk, SURF, "lnsp", isf, 0)}
    for L in levels:
        for v in ("t", "d", "vo"):
            jobs[(v, L)] = pool.submit(chunk, WIND, v, iw, L - 1, 0)
        jobs[("q", L)] = pool.submit(chunk, MOIST, "q", im, L - 1, 0)
        if a.cloud:
            for v in ("clwc", "ciwc"):
                jobs[(v, L)] = pool.submit(chunk, MOIST, v, im, L - 1, 0)
    pv = json.loads(get(f"{ROOT}/{SURF}/.zmetadata"))["metadata"]["lnsp/.zattrs"]["GRIB_pv"];  # only the consolidated metadata has it
    Ahl, Bhl = np.array(pv[:138]), np.array(pv[138:])
    need_uv = [k for k in range(1, 138) if Bhl[k] != Bhl[k - 1]] if a.etadot else []
    if a.etadot:
        print(f"etadot: d needed on all 137 levels, u/v on levels {need_uv[0]}-{need_uv[-1]} (where dB != 0)")
        for L in range(1, 138):
            if ("d", L) not in jobs: jobs[("d", L)] = pool.submit(chunk, WIND, "d", iw, L - 1, 0)
        for L in need_uv:
            if ("vo", L) not in jobs: jobs[("vo", L)] = pool.submit(chunk, WIND, "vo", iw, L - 1, 0)
    lat_q = pool.submit(chunk, MOIST, "latitude", 0); lon_q = pool.submit(chunk, MOIST, "longitude", 0)
    data = {k: f.result() for k, f in jobs.items()}
    lat_q, lon_q = lat_q.result(), lon_q.result()
    dt = time.time() - t0
    print(f"fetched {_stats['n']} objects, {_stats['bytes'] / 1e6:.0f} MB in {dt:.1f}s = {_stats['bytes'] / 1e6 / dt:.1f} MB/s "
          f"with {a.workers} workers")
    for k in (("t", levels[0]), ("d", levels[0]), ("vo", levels[0]), ("q", levels[0]), ("lnsp", 1)):
        v = data[k]; print(f"  {k}: {v.size} values, min {v.min():.4g} max {v.max():.4g}, finite {np.isfinite(v).all()}")
    if a.fetch_only:
        return

    t1 = time.time()
    lay = sh_layout(); P, cosl = legendre(tlat, sign=a.legendre_sign); e = eps(); w = np.where(np.arange(N_TRUNC + 1) == 0, 1.0, 2.0)
    print(f"Legendre functions for {len(tlat)} latitudes: {time.time() - t1:.1f}s")
    res = {}
    Pn = P[:, :N_TRUNC + 2]
    def sc(flat): return scalar_field(to_matrix(flat, lay), Pn, tlon, w)
    res[(152, 1)] = sc(data[("lnsp", 1)])
    gg = ReducedGG(lat_q, lon_q)
    for L in levels:
        res[(130, L)] = sc(data[("t", L)])
        res[(131, L)], res[(132, L)] = wind_fields(to_matrix(data[("vo", L)], lay), to_matrix(data[("d", L)], lay), Pn, cosl, tlon, w, e)
        res[(133, L)] = gg.interp(data[("q", L)].astype(float), tlat, tlon)
        if a.cloud:
            res[(246, L)] = gg.interp(data[("clwc", L)].astype(float), tlat, tlon)
            res[(247, L)] = gg.interp(data[("ciwc", L)].astype(float), tlat, tlon)
    if a.etadot:
        t2 = time.time(); Cl = to_matrix(data[("lnsp", 1)], lay)
        D = {L: sc(data[("d", L)]) for L in range(1, 138)}
        UV = {L: (res[(131, L)], res[(132, L)]) if (131, L) in res else
              wind_fields(to_matrix(data[("vo", L)], lay), to_matrix(data[("d", L)], lay), Pn, cosl, tlon, w, e) for L in need_uv}
        H, ps = etadot_continuity(D, {k: v[0] for k, v in UV.items()}, {k: v[1] for k, v in UV.items()}, Cl, Ahl, Bhl, Pn, cosl, tlon, w, e)
        for L in levels: res[(77, L)] = H[L]
        print(f"etadot from continuity: {time.time() - t2:.1f}s; H at the ground (level 137): max |H| {abs(H[137]).max():.2e} Pa/s")
    print(f"synthesis: {time.time() - t1:.1f}s")
    out = os.path.join(a.out, f"arco_{when:%Y%m%d_%H}.npz")
    np.savez(out, lat=tlat, lon=tlon, **{f"p{k[0]}_L{k[1]}": v for k, v in res.items()})
    print("wrote", out)
    res[(152, 1)]  # lnsp is in ln(Pa)
    if a.ref:
        ref = read_ref(a.ref, tlat, tlon, set(res))
        names = {130: "T", 131: "U", 132: "V", 133: "Q", 152: "LNSP", 77: "H", 246: "CLWC", 247: "CIWC"}
        print(f"compare with {a.ref}: {len(ref)} reference fields found")
        for pid in (152, 130, 131, 132, 133, 77, 246, 247):
            ks = sorted(k for k in res if k[0] == pid and k in ref)
            if not ks:
                print(f"  {names[pid]}: no reference"); continue
            d = np.array([np.abs(res[k] - ref[k]).max() for k in ks]); r = np.array([np.abs(ref[k]).max() for k in ks])
            rms = np.sqrt(np.mean([np.mean((res[k] - ref[k]) ** 2) for k in ks]))
            print(f"  {names[pid]:5s} levels {ks[0][1]}-{ks[-1][1]}: max |diff| {d.max():.4g} (at level {ks[int(d.argmax())][1]}), "
                  f"rms {rms:.4g}, field max {r.max():.4g}")


if __name__ == "__main__":
    main()
