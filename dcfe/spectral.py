# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""Spherical-harmonic synthesis on a lat-lon box, and ETADOT from mass continuity. Pure numpy, no I/O.

Conventions (IFS / ECMWF GRIB `sh` data; verified against ECMWF's own gridded surface pressure to 0.004 Pa):
  * coefficients are stored m = 0..N outer, n = m..N inner, (real, imag) interleaved;
  * f(lat, lon) = sum_m w_m Re[ exp(i m lon) sum_n c_nm Pbar_n^m(sin lat) ],  w_0 = 1, w_m = 2 for m > 0;
  * Pbar is fully normalised with integral over mu = 2 (Pbar_00 = 1, Pbar_10 = sqrt(3) mu), no Condon-Shortley phase.
The field is evaluated at the requested points only (the box); the Legendre sums are batched over model levels so
that numpy hands them to BLAS. Winds follow from vorticity and divergence through the stream function and the
velocity potential, so derivatives are exact, with no stencil and no boundary effects.
"""
from __future__ import annotations

import numpy as np

A_EARTH = 6371229.0            # radius used by the IFS (m)


def truncation(ncoef_values: int) -> int:
    """N of a triangular truncation from the length of a flat (re, im) vector: (N+1)(N+2) values."""
    n = int(round((-3 + np.sqrt(1 + 4 * ncoef_values)) / 2))
    if (n + 1) * (n + 2) != ncoef_values:
        raise ValueError(f"{ncoef_values} values are not a triangular spectral truncation")
    return n


def sh_layout(N: int):
    """index arrays (m, n) of the stored coefficients, in storage order"""
    m = np.concatenate([np.full(N + 1 - k, k) for k in range(N + 1)])
    n = np.concatenate([np.arange(k, N + 1) for k in range(N + 1)])
    return m, n


def legendre(lat_deg, N: int) -> np.ndarray:
    """Pbar[lat, n, m], n up to N+1 (one more than the data, needed for the latitude derivative)."""
    mu = np.sin(np.radians(lat_deg)); c = np.cos(np.radians(lat_deg))
    nl = len(mu)
    P = np.zeros((nl, N + 2, N + 1))
    diag = np.ones((nl, N + 1))
    for k in range(1, N + 1):
        diag[:, k] = diag[:, k - 1] * c * np.sqrt((2 * k + 1) / (2.0 * k))
    for k in range(N + 1):
        P[:, k, k] = diag[:, k]
        P[:, k + 1, k] = np.sqrt(2 * k + 3.0) * mu * diag[:, k]
    for n in range(2, N + 2):
        k = np.arange(0, min(n - 1, N + 1))
        a = np.sqrt((4.0 * n * n - 1) / (n * n - k * k))
        b = np.sqrt(((n - 1.0) ** 2 - k * k) / (4.0 * (n - 1) ** 2 - 1))
        P[:, n, k] = a * (mu[:, None] * P[:, n - 1, k] - b * P[:, n - 2, k])
    return P


def _eps(N: int) -> np.ndarray:
    n = np.arange(N + 2)[:, None].astype(float); m = np.arange(N + 1)[None, :].astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.sqrt(np.where(n > 0, (n * n - m * m) / (4 * n * n - 1), 0.0))
    return np.nan_to_num(e)


def _dleg(P: np.ndarray, e: np.ndarray) -> np.ndarray:
    """H[n,m] = (1-mu^2) dPbar/dmu = cos(lat) dPbar/dlat"""
    n = np.arange(P.shape[1])[:, None].astype(float); nmax = P.shape[1] - 1
    H = np.zeros_like(P)
    H[:, :nmax] = -n[:nmax] * e[1:nmax + 1] * P[:, 1:nmax + 1]
    H[:, 1:] += (n[1:] + 1) * e[1:nmax + 1] * P[:, :nmax]
    return H


class Basis:
    """Everything that depends on the grid points but not on the data: Legendre tables arranged for matmul
    (Pm[m, lat, n], Hm[m, lat, n]) and the longitude factors. Memory ~ 2 * 8 * nlat * N^2 bytes."""

    def __init__(self, lat_deg, lon_deg, N: int):
        self.N = N; self.lat = np.asarray(lat_deg, float); self.lon = np.asarray(lon_deg, float)
        P = legendre(self.lat, N)
        self.cosl = np.cos(np.radians(self.lat))
        self.Pm = np.ascontiguousarray(P.transpose(2, 0, 1))
        self.Hm = np.ascontiguousarray(_dleg(P, _eps(N)).transpose(2, 0, 1)); del P
        self.m = np.arange(N + 1); w = np.where(self.m == 0, 1.0, 2.0)
        self.E = np.exp(1j * np.outer(self.m, np.radians(self.lon))) * w[:, None]      # (m, lon), weights included
        self.lay = sh_layout(N)


def stack(flats, bs: Basis) -> np.ndarray:
    """flat coefficient vectors (L, (N+1)(N+2)) -> complex C[l, n, m] (n has N+2 rows, the last is zero)"""
    F = np.asarray(flats, dtype=np.float64)
    if F.shape[1] != (bs.N + 1) * (bs.N + 2):
        raise ValueError(f"expected T{bs.N} coefficients, got {F.shape[1]} values")
    m, n = bs.lay
    C = np.zeros((F.shape[0], bs.N + 2, bs.N + 1), complex)
    C[:, n, m] = F[:, 0::2] + 1j * F[:, 1::2]
    return C


def _sum_n(C: np.ndarray, Tm: np.ndarray) -> np.ndarray:
    """sum_n C[l,n,m] T[m,lat,n] -> (l, lat, m), one real matrix product per m for all levels at once"""
    L = C.shape[0]
    X = np.ascontiguousarray(np.concatenate([C.real, C.imag]).transpose(2, 1, 0))     # (m, n, 2L)
    R = Tm @ X                                                                         # (m, lat, 2L)
    return (R[..., :L] + 1j * R[..., L:]).transpose(2, 1, 0)


def scalar(flats, bs: Basis) -> np.ndarray:
    """spectral scalar fields -> values (L, lat, lon)"""
    return np.real(_sum_n(stack(flats, bs), bs.Pm) @ bs.E)


def winds(vo_flats, d_flats, bs: Basis):
    """u, v (m/s) as (L, lat, lon) from spectral vorticity and divergence"""
    vo, d = stack(vo_flats, bs), stack(d_flats, bs)
    n = np.arange(vo.shape[1])[None, :, None].astype(float); nn1 = n * (n + 1); nn1[:, 0] = 1.0
    psi = -A_EARTH ** 2 * vo / nn1; chi = -A_EARTH ** 2 * d / nn1
    psi[:, 0] = 0; chi[:, 0] = 0
    L = vo.shape[0]; both = np.concatenate([chi, psi])
    SP = _sum_n(both, bs.Pm); SH = _sum_n(both, bs.Hm); im = 1j * bs.m[None, None, :]
    Fu = im * SP[:L] - SH[L:]          # u cos(lat) * a  =  d chi / d lon  -  cos(lat) d psi / d lat
    Fv = im * SP[L:] + SH[:L]          # v cos(lat) * a  =  d psi / d lon  +  cos(lat) d chi / d lat
    c = bs.cosl[None, :, None] * A_EARTH
    return np.real(Fu @ bs.E) / c, np.real(Fv @ bs.E) / c


def gradient_scalar(flat, bs: Basis):
    """(f, df/dx, df/dy) of one spectral scalar on the box, x east and y north in metres"""
    C = stack([flat], bs)
    SP = _sum_n(C, bs.Pm)[0]
    f = np.real(SP @ bs.E)
    gx = np.real((1j * bs.m[None, :] * SP) @ bs.E) / (A_EARTH * bs.cosl[:, None])
    gy = np.real(_sum_n(C, bs.Hm)[0] @ bs.E) / (A_EARTH * bs.cosl[:, None])
    return f, gx, gy


def continuity_flux(D, U, V, lnsp, gx, gy, A, B) -> dict[int, np.ndarray]:
    """H(k+1/2) = eta_dot * dp/deta on the interface BELOW model level k (what calc_etadot writes), all levels,
    from mass continuity:
        S_k = div(V_k dp_k) = dp_k D_k + dB_k ps (V_k . grad ln ps),   dp_k = dA_k + dB_k ps
        H(k+1/2) = B(k+1/2) sum_all S - sum_{j<=k} S_j        (H = 0 at the top and exactly 0 at the ground)
    D: {level: field} for ALL levels 1..N; U, V: {level: field} for every level with dB != 0 (others unused);
    lnsp, gx, gy: ln(ps) and its gradient (per metre); A, B: the N+1 half-level coefficients."""
    nlev = len(A) - 1
    ps = np.exp(lnsp)
    S = {}
    for k in range(1, nlev + 1):
        dA, dB = A[k] - A[k - 1], B[k] - B[k - 1]
        S[k] = (dA + dB * ps) * D[k] + (dB * ps * (U[k] * gx + V[k] * gy) if dB != 0 else 0.0)
    tot = sum(S.values()); out = {}; cum = 0.0
    for k in range(1, nlev + 1):
        cum = cum + S[k]
        out[k] = B[k] * tot - cum
    return out


P00 = 101325.0


def etadot_from_flux(H: dict[int, np.ndarray], ps: np.ndarray, A, B, first: int, last: int) -> dict[int, np.ndarray]:
    """ERA5's eta_dot (1/s, as MARS parameter 77) on the full levels first..last from the half-level fluxes H.
    Inverse of the conversion in etadot.py:  H(k+1/2) + H(k-1/2) = 2 eta_dot dp_k/deta_k,
    dp_k = dA_k + dB_k ps, deta_k = dA_k/p00 + dB_k  (so etadot.half_level_flux gives H back from these values)."""
    out = {}
    for k in range(first, last + 1):
        dA, dB = A[k] - A[k - 1], B[k] - B[k - 1]
        above = H[k - 1] if k > 1 else 0.0
        out[k] = (H[k] + above) * (dA / P00 + dB) / (2.0 * (dA + dB * ps))
    return out


class ReducedGG:
    """Reduced Gaussian grid (ERA5 N320 values of q, clwc, ciwc): bilinear interpolation to lat-lon points.
    lat, lon: the coordinate arrays of the grid points (north to south, west to east within each row)."""

    def __init__(self, lat, lon):
        self.lat = np.asarray(lat, float); self.lon = np.asarray(lon, float)
        self.starts = np.flatnonzero(np.r_[True, np.diff(self.lat) != 0])
        self.rows = self.lat[self.starts]; self.cnt = np.diff(np.r_[self.starts, self.lat.size])

    def plan(self, tlat, tlon):
        """interpolation weights for the target points, reusable for every field of the same grid"""
        tlat = np.asarray(tlat, float); tlon = np.mod(np.asarray(tlon, float), 360.0)
        rows = self.rows
        j0 = np.clip(np.searchsorted(-rows, -tlat) - 1, 0, len(rows) - 2)
        wl = np.clip((rows[j0] - tlat) / (rows[j0] - rows[j0 + 1]), 0.0, 1.0)
        plan = []
        for jj in (j0, j0 + 1):                          # the two rows around each target latitude
            idx0 = np.empty((len(tlat), len(tlon)), int); idx1 = np.empty_like(idx0); wx = np.empty(idx0.shape)
            for a, j in enumerate(jj):
                s, c = self.starts[j], self.cnt[j]
                x = self.lon[s:s + c]; pos = (tlon - x[0]) / (360.0 / c)   # equally spaced from the first point
                i0 = np.floor(pos).astype(int); wx[a] = pos - i0
                idx0[a] = s + np.mod(i0, c); idx1[a] = s + np.mod(i0 + 1, c)
            plan.append((idx0, idx1, wx))
        return plan, wl[:, None]

    @staticmethod
    def apply(plan, vals):
        (p, wl) = plan
        rows = [(1 - wx) * vals[i0] + wx * vals[i1] for (i0, i1, wx) in p]
        return (1 - wl) * rows[0] + wl * rows[1]


def gaussian_latitudes(n: int) -> np.ndarray:
    """Latitudes (degrees, north to south) of the regular Gaussian grid with n latitudes between the pole and the equator."""
    x, _ = np.polynomial.legendre.leggauss(2 * n)
    return np.degrees(np.arcsin(x))[::-1]


def latitude_rows(target_lat, n: int):
    """The Gaussian rows (N=n) that bracket the target latitudes, and the weights W[target, row] of the linear
    interpolation in latitude between them (rows without a target between them are dropped). This is the last step of
    MARS's gridding of spectral fields: full-resolution synthesis on the Gaussian grid whose longitudes coincide with
    the target grid, then linear interpolation in latitude. Targets beyond the outermost row take that row's value."""
    g = gaussian_latitudes(n)
    t = np.asarray(target_lat, float)
    i = np.clip(np.searchsorted(-g, -t, side="right") - 1, 0, len(g) - 2)            # g[i] >= t > g[i+1]
    w = np.clip((t - g[i + 1]) / (g[i] - g[i + 1]), 0.0, 1.0)
    rows = np.unique(np.concatenate([i, i + 1]))
    pos = {r: j for j, r in enumerate(rows)}
    W = np.zeros((len(t), len(rows)))
    for k in range(len(t)):
        W[k, pos[i[k]]] += w[k]
        W[k, pos[i[k] + 1]] += 1.0 - w[k]
    return g[rows], W
