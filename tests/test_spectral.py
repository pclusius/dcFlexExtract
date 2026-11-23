# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""spectral.py against cases with a known answer (no data, no network)."""
import numpy as np
import pytest

from dcfe import etadot, spectral
from dcfe.spectral import A_EARTH

N = 4
LAT = np.array([60.0, 30.0, 0.0, -45.0]); LON = np.array([0.0, 40.0, 100.0, 250.0])


def coef(n, m, re=0.0, im=0.0):
    """flat coefficient vector with one (n, m) coefficient set"""
    mm, nn = spectral.sh_layout(N)
    v = np.zeros(2 * mm.size)
    i = int(np.flatnonzero((mm == m) & (nn == n))[0])
    v[2 * i], v[2 * i + 1] = re, im
    return v


@pytest.fixture(scope="module")
def bs():
    return spectral.Basis(LAT, LON, N)


def test_truncation_from_length():
    assert spectral.truncation(30) == 4 and spectral.truncation(410240) == 639
    with pytest.raises(ValueError):
        spectral.truncation(31)


def test_scalar_harmonics(bs):
    lat, lon = np.radians(LAT)[:, None], np.radians(LON)[None, :]
    assert np.allclose(spectral.scalar([coef(0, 0, 7.0)], bs)[0], 7.0)
    assert np.allclose(spectral.scalar([coef(1, 0, 2.0)], bs)[0], 2.0 * np.sqrt(3) * np.sin(lat) + 0 * lon)
    # (n=1, m=1): 2 * Re[c exp(i lon)] * Pbar_11,  Pbar_11 = sqrt(3/2) cos(lat)  (IFS normalisation, no phase)
    want = 2 * np.sqrt(1.5) * np.cos(lat) * (3.0 * np.cos(lon) - 1.0 * np.sin(lon))
    assert np.allclose(spectral.scalar([coef(1, 1, 3.0, 1.0)], bs)[0], want)


def test_solid_body_rotation_gives_eastward_wind(bs):
    """vorticity zeta = 2 Omega sin(lat) is the solid-body rotation u = Omega a cos(lat), v = 0"""
    z10 = 1e-5
    u, v = spectral.winds([coef(1, 0, z10)], [np.zeros(2 * len(spectral.sh_layout(N)[0]))], bs)
    omega = z10 * np.sqrt(3) / 2
    assert np.allclose(u[0], (omega * A_EARTH * np.cos(np.radians(LAT)))[:, None] + 0 * u[0], rtol=1e-9)
    assert np.allclose(v[0], 0, atol=1e-9)


def test_divergence_gives_meridional_wind(bs):
    """divergence D = d10 sqrt(3) sin(lat)  <->  v = -a d10 sqrt(3)/2 cos(lat), u = 0, and div(V) gives D back"""
    d10 = 1e-5
    zero = np.zeros(2 * len(spectral.sh_layout(N)[0]))
    u, v = spectral.winds([zero], [coef(1, 0, d10)], bs)
    assert np.allclose(u[0], 0, atol=1e-9)
    assert np.allclose(v[0], (-A_EARTH * d10 * np.sqrt(3) / 2 * np.cos(np.radians(LAT)))[:, None] + 0 * v[0], rtol=1e-9)


def test_gradient_of_a_scalar(bs):
    f, gx, gy = spectral.gradient_scalar(coef(1, 0, 2.0), bs)
    assert np.allclose(gy, (2.0 * np.sqrt(3) * np.cos(np.radians(LAT)) / A_EARTH)[:, None] + 0 * gy)
    assert np.allclose(gx, 0, atol=1e-18)
    f, gx, gy = spectral.gradient_scalar(coef(1, 1, 1.0, 0.0), bs)     # 2 sqrt(3/2) cos(lat) cos(lon)
    want = -2 * np.sqrt(1.5) * np.sin(np.radians(LON))[None, :] / A_EARTH     # d/dx = (1/(a cos lat)) d/dlon
    assert np.allclose(gx, want + 0 * gx)


# ---- continuity ------------------------------------------------------------------------------
A = np.array([0.0, 1000.0, 3000.0, 2000.0, 600.0, 0.0])        # half levels 0..5, A(0) = B(0) = 0, A(5) = 0, B(5) = 1
B = np.array([0.0, 0.0, 0.1, 0.4, 0.8, 1.0])


def test_continuity_uniform_divergence_has_a_closed_form():
    """D = D0 everywhere, no wind, flat surface pressure:  H(k+1/2) = -D0 A(k+1/2)  (so H = 0 at the ground)"""
    shape = (3, 4); D0 = 2e-6
    D = {k: np.full(shape, D0) for k in range(1, 6)}
    z = np.zeros(shape)
    H = spectral.continuity_flux(D, {k: z for k in D}, {k: z for k in D}, np.full(shape, np.log(1e5)), z, z, A, B)
    for k in range(1, 6):
        assert np.allclose(H[k], -D0 * A[k], rtol=1e-12, atol=1e-18)


def test_continuity_ground_flux_is_zero_for_any_field():
    rng = np.random.default_rng(3); shape = (3, 4)
    f = lambda s=1.0: rng.standard_normal(shape) * s
    D = {k: f(1e-5) for k in range(1, 6)}; U = {k: f(10) for k in D}; V = {k: f(10) for k in D}
    H = spectral.continuity_flux(D, U, V, np.log(1e5 + f(500)), f(1e-6), f(1e-6), A, B)
    assert np.abs(H[5]).max() < 1e-12 * max(np.abs(H[k]).max() for k in range(1, 5))


def test_etadot_round_trip_through_the_pipeline_conversion():
    """eta_dot written for the raw data must give back H when process converts it (ground boundary)"""
    rng = np.random.default_rng(4); shape = (3, 4)
    ps = 1e5 + rng.standard_normal(shape) * 800
    H = {k: rng.standard_normal(shape) * 0.1 for k in range(1, 5)}; H[5] = np.zeros(shape)
    eta = spectral.etadot_from_flux(H, ps, A, B, 2, 5)
    back = etadot.half_level_flux(eta, ps, A, B, "ground")
    for k in range(2, 6):
        assert np.allclose(back[k], H[k], atol=1e-12)


def test_reduced_gaussian_interpolation():
    lat = np.repeat([15.0, 5.0, -5.0, -15.0], [6, 8, 8, 6])
    lon = np.concatenate([np.arange(c) * 360.0 / c for c in (6, 8, 8, 6)])
    gg = spectral.ReducedGG(lat, lon)
    plan = gg.plan(np.array([12.0, 5.0, -3.0]), np.array([10.0, 100.0, 359.0, 370.0]))
    assert np.allclose(spectral.ReducedGG.apply(plan, np.full(lat.size, 3.5)), 3.5)
    got = spectral.ReducedGG.apply(plan, lat)                                   # linear in latitude: exact
    assert np.allclose(got, np.array([12.0, 5.0, -3.0])[:, None] + 0 * got)
    got = spectral.ReducedGG.apply(plan, lon * 0 + np.where(lon < 180, lon, lon - 360))   # wraps through 360 = 0
    assert np.isfinite(got).all()
