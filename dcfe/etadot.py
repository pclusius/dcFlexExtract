# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.
#
# The conversion formula is that of flex_extract's calc_etadot.f90 (ETA=1, MDPDETA=1; L. Haimberger);
# the choice of the integration boundary is new here.

"""Vertical velocity for FLEXPART: eta_dot * dp/deta on model HALF levels, from ERA5 parameter 77.

ERA5 stores eta_dot (1/s) on full model levels. FLEXPART wants H = eta_dot * dp/deta (Pa/s) on half levels.
calc_etadot writes, for full level K, the value at the interface BELOW it, H(K+1/2), from

    Et(K)  = 2 * eta_dot(K) * dp_K / d_eta_K,     dp_K = dA_K + dB_K * ps,   d_eta_K = dA_K/p00 + dB_K
    H(K+1/2) = Et(K) - H(K-1/2)                    (full level = mean of its two interfaces)

The recursion needs one boundary value. flex_extract starts at the top of the RETRIEVED levels with
H = 0, which is only right if that top is the model top (level 1): for levels first..137 the true value
at the top of level `first` is not zero (here about 0.04 Pa/s) and the error reappears with alternating
sign in every level, leaving a non-zero H at the ground. Starting at the ground instead, where
H = eta_dot * dp/deta is exactly 0, gives the correct profile for any level range ending at the lowest
level.

boundary="ground"  integrate upward from the lowest model level, H = 0 at the surface (default)
boundary="top"     flex_extract's behaviour: H = 0 above the first retrieved level"""
from __future__ import annotations

import numpy as np

P00 = 101325.0
BOUNDARIES = ("ground", "top")


def half_level_flux(etadot: dict[int, np.ndarray], ps: np.ndarray, A: np.ndarray, B: np.ndarray,
                    boundary: str = "ground") -> dict[int, np.ndarray]:
    """etadot: {level: values} for the consecutive levels first..last; ps in Pa (same shape);
    A, B: hybrid coefficients of the half levels 0..N (N = lowest model level, e.g. 137).
    Returns {level: H at the interface below that level}, as calc_etadot writes it."""
    if boundary not in BOUNDARIES:
        raise ValueError(f"boundary must be one of {BOUNDARIES}, not {boundary!r}")
    levels = sorted(etadot)
    first, last = levels[0], levels[-1]
    if levels != list(range(first, last + 1)):
        raise ValueError("etadot must hold consecutive levels")
    if boundary == "ground" and last != len(A) - 1:
        raise ValueError(f"the ground boundary needs the lowest model level ({len(A) - 1}) among the levels, "
                         f"but the last level is {last}")
    et = {}
    for k in levels:
        dak, dbk = A[k] - A[k - 1], B[k] - B[k - 1]
        et[k] = 2.0 * etadot[k] * ps * (dak / ps + dbk) / (dak / P00 + dbk)
    out: dict[int, np.ndarray] = {}
    if boundary == "top":
        prev = np.zeros_like(ps, dtype=float)
        for k in levels:
            prev = et[k] - prev
            out[k] = prev
    else:
        below = np.zeros_like(ps, dtype=float)             # H = 0 at the ground
        for k in reversed(levels):
            out[k] = below
            below = et[k] - below                          # interface above level k
    return out
