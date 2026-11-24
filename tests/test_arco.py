# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""arco.py with an in-memory fake of the three Zarr stores (T4 truncation, 10 model levels, a tiny reduced grid)."""
import json
import zlib
from datetime import date, datetime

import numpy as np
import pytest

from dcfe import arco, era5, etadot, gribio, requests as rq
from dcfe.config import Config
from dcfe.rawindex import RawIndex

numcodecs = pytest.importorskip("numcodecs")

NLEV, NCOEF = 10, 30                                       # T4: (4+1)(4+2) = 30 stored values (15 complex)
A = np.array([0.0, 200.0, 800.0, 1500.0, 2200.0, 2500.0, 2000.0, 1200.0, 500.0, 100.0, 0.0])
B = np.array([0.0, 0.0, 0.0, 0.02, 0.1, 0.25, 0.45, 0.68, 0.87, 0.97, 1.0])
GG_LAT = np.repeat([15.0, 5.0, -5.0, -15.0], 8); GG_LON = np.tile(np.arange(8) * 45.0, 4)
CODEC = numcodecs.Blosc()


class FakeBucket:
    """serves .zattrs, .zarray, .zmetadata and chunks of the three stores; chunks are reproducible noise"""

    def __init__(self, stop="2023-08-31", stop_t="2023-09-02", lnsp_stop=None):
        self.stop, self.stop_t, self.lnsp_stop = stop, stop_t, lnsp_stop or stop_t
        self.calls = 0

    def _arr(self, dtype, a):
        return CODEC.encode(np.ascontiguousarray(a, dtype=dtype))

    def __call__(self, url: str) -> bytes:
        self.calls += 1
        rel = url.split("/co/")[1]
        store, rest = rel.split("/", 1)
        if rest == ".zattrs":
            stop_t = self.lnsp_stop if store == arco.SURF else self.stop_t
            return json.dumps({"valid_time_start": "1940-01-01", "valid_time_stop": self.stop,
                               "valid_time_stop_era5t": stop_t}).encode()
        if rest == ".zmetadata":
            return json.dumps({"metadata": {"lnsp/.zattrs": {"GRIB_pv": list(A) + list(B)}}}).encode()
        var, tail = rest.split("/", 1)
        if tail == ".zarray":
            dt = "<i8" if var == "time" else "<f8" if var in ("latitude", "longitude") else "<f4"
            return json.dumps({"dtype": dt, "compressor": CODEC.get_config()}).encode()
        idx = [int(x) for x in tail.split(".")]
        if var == "time":
            return self._arr("<i8", np.arange(2_000_000))
        if var == "latitude":
            return self._arr("<f8", GG_LAT)
        if var == "longitude":
            return self._arr("<f8", GG_LON)
        rng = np.random.default_rng(zlib.crc32(f"{var}{idx}".encode()))
        if var in ("q", "clwc", "ciwc"):
            return self._arr("<f4", rng.uniform(0, 1e-3, GG_LAT.size))
        scale = {"t": 1.0, "d": 3e-6, "vo": 3e-5, "lnsp": 1e-2}[var]
        v = rng.standard_normal(NCOEF) * scale
        if var == "lnsp":
            v[0] = 11.5                                   # (n=0, m=0): mean ln(ps) -> ps ~ 1e5 Pa
        if var == "t":
            v[0] = 250.0
        return self._arr("<f4", v)


def cfg(tmp_path, **kw):
    a = dict(start="20230830", end="20230830", area=(12, 5, 8, 9), grid=0.5, levels=(4, 10), rawdir=tmp_path / "raw",
             source="mixed")
    a.update(kw)
    return Config.make(a.pop("start"), a.pop("end"), a.pop("area"), a.pop("grid"), a.pop("levels"), **a)


def test_source_option_validation(tmp_path):
    assert cfg(tmp_path).source == "mixed" and Config.make("20230830", "20230830", (61, 21, 59, 25)).source == "mars"
    with pytest.raises(ValueError, match="--pressure lnsp"):
        cfg(tmp_path, pressure="spectral")
    with pytest.raises(ValueError, match="source"):
        cfg(tmp_path, source="era6")


def test_mixed_plan_has_no_model_level_requests(tmp_path):
    kinds = {u.group for u in rq.plan(cfg(tmp_path))}
    assert kinds == {rq.SL, rq.FLUX, rq.STATIC}
    assert {rq.ML, rq.SL, rq.FLUX, rq.STATIC} <= {u.group for u in rq.plan(cfg(tmp_path, source="mars"))}


def test_valid_range_is_the_earliest_end_of_the_three_stores(tmp_path):
    st = arco.Store(get=FakeBucket(stop="2023-08-31", stop_t="2023-09-02", lnsp_stop="2023-08-30"))
    assert st.valid_range() == (date(1940, 1, 1), date(2023, 8, 30), date(2023, 8, 31))


def test_missing_days_stop_the_run_and_say_where_mars_starts(tmp_path):
    st = arco.Store(get=FakeBucket(stop="2023-08-31", stop_t="2023-09-02"))
    assert arco.check_range(cfg(tmp_path, end="20230902"), st) == date(2023, 8, 31)     # ERA5T days are allowed
    with pytest.raises(arco.ArcoUnavailable, match=r"--source mars from 03-09-2023 onwards"):
        arco.check_range(cfg(tmp_path, end="20230903"), st)
    with pytest.raises(arco.ArcoUnavailable, match="--source mars"):
        arco.check_range(cfg(tmp_path, start="19391231", end="19400102"), st)


def test_hour_has_the_expected_fields(tmp_path):
    c = cfg(tmp_path); b = arco.HourBuilder(c, arco.Store(get=FakeBucket()))
    h = b.build(datetime(2023, 8, 30, 14))
    assert h.lnsp.shape == (9, 9) and 8.0 < h.lnsp.mean() < 12.0       # 12..8 N, 5..9 E at 0.5 degrees
    assert set(h.fields) == {"U", "V", "T", "Q", "CLWC", "CIWC", "ETADOT"}
    for name in h.fields:
        assert sorted(h.fields[name]) == list(range(4, 11)), name
        assert all(v.shape == (9, 9) and np.isfinite(v).all() for v in h.fields[name].values())
    assert all(0 < v.min() and v.max() < 1e-3 for v in h.fields["Q"].values())    # interpolation stays within the data


def test_etadot_of_the_hour_survives_the_conversion_in_process(tmp_path):
    """the eta_dot we write is turned into H by etadot.half_level_flux; H must vanish at the ground and
    satisfy the continuity H(k+1/2) - H(k-1/2) = -(dB_k * dps/dt + S_k) that it was built from"""
    c = cfg(tmp_path, levels=(1, 10)); b = arco.HourBuilder(c, arco.Store(get=FakeBucket()))
    h = b.build(datetime(2023, 8, 30, 14))
    ps = np.exp(h.lnsp)
    H = etadot.half_level_flux(h.fields["ETADOT"], ps, A, B, "ground")
    assert np.abs(H[10]).max() == 0.0
    # H(1/2) = 0 at the top: the sum of the first level's two interfaces is Et(1) = H(1+1/2)
    dA, dB = A[1] - A[0], B[1] - B[0]
    assert np.allclose(2 * h.fields["ETADOT"][1] * (dA + dB * ps) / (dA / etadot.P00 + dB), H[1], rtol=1e-9)


def test_grib_messages_are_what_process_expects(tmp_path):
    c = cfg(tmp_path); b = arco.HourBuilder(c, arco.Store(get=FakeBucket()))
    T = datetime(2023, 8, 30, 14); h = b.build(T)
    msgs = arco.messages(c, h, b.A, b.B)
    assert len(msgs) == 7 * 7 + 1
    p = arco.hour_path(c, T); p.parent.mkdir(parents=True); p.write_bytes(b"".join(msgs))
    ix = RawIndex.from_dir(c.rawdir)
    ids = era5.PARAM_ID
    for name in era5.ML_VARS:
        for lv in range(4, 11):
            r = ix.ml[(ids[name], T, lv)]
            assert (r.type_of_level, r.grid_type, r.edition, r.nvalues) == ("hybrid", "regular_ll", 2, 81)
    assert T in ix.glnsp and set(ix.grid_sizes()) == {81}
    assert np.allclose(gribio.values(gribio.read(ix.glnsp[T])).reshape(9, 9), h.lnsp, atol=1e-5)
    u = gribio.read(ix.ml[(ids["U"], T, 7)])
    assert len(gribio.get_array(u, "pv")) == 22
    assert np.allclose(gribio.values(u).reshape(9, 9), h.fields["U"][7], rtol=1e-5, atol=1e-5)
    assert (gribio.get_int(u, "latitudeOfFirstGridPoint"), gribio.get_int(u, "longitudeOfFirstGridPoint")) == (12000000, 5000000)


def test_downloader_is_resumable_and_does_one_hour_per_step(tmp_path):
    c = cfg(tmp_path)
    bucket = FakeBucket()
    d = arco.ArcoDownloader(c, store=arco.Store(get=bucket), log=lambda s: None)
    assert d.summary() == {"arco_hours_done": 0, "arco_hours_todo": 24}
    assert d.step() and d.summary()["arco_hours_done"] == 1
    assert [p.name for p in arco.hour_path(c, datetime(2023, 8, 30, 0)).parent.iterdir()] == ["ARCO_ML_L004-010_h00.grb"]
    d2 = arco.ArcoDownloader(c, store=arco.Store(get=bucket), log=lambda s: None)       # a new run continues
    assert d2.summary()["arco_hours_done"] == 1 and d2.pending()[0].hour == 1
    while d2.step():
        pass
    assert d2.summary() == {"arco_hours_done": 24, "arco_hours_todo": 0}
    assert not list(arco.hour_path(c, datetime(2023, 8, 30, 0)).parent.glob("*.part"))


def test_latitude_rows_interpolate_linearly_between_gaussian_rows():
    from dcfe import spectral
    lat = 60.0 - 0.25 * np.arange(9)
    rows, W = spectral.latitude_rows(lat, 360)
    assert np.allclose(W.sum(axis=1), 1.0) and (W >= 0).all()
    assert np.allclose(W @ rows, lat)                                # linear in latitude reproduces latitude exactly
    assert len(rows) >= 9 and rows[0] > lat[0] and rows[-1] < lat[-1]  # the rows bracket the box
    assert np.all(np.diff(rows) < 0)


def test_gridding_mars_is_close_to_exact_for_smooth_fields(tmp_path):
    bucket = FakeBucket()
    exact = arco.HourBuilder(cfg(tmp_path), arco.Store(get=bucket)).build(datetime(2023, 8, 30, 14))
    mars = arco.HourBuilder(cfg(tmp_path, gridding="mars"), arco.Store(get=bucket)).build(datetime(2023, 8, 30, 14))
    assert not np.array_equal(exact.fields["T"][5], mars.fields["T"][5])          # a different evaluation ...
    for name in ("T", "U", "V", "ETADOT"):
        for k in (4, 7, 10):
            a, b = exact.fields[name][k], mars.fields[name][k]
            assert np.abs(a - b).max() <= 5e-3 * np.abs(a).max(), (name, k)       # ... but only slightly, T4 data is smooth
    assert np.abs(exact.lnsp - mars.lnsp).max() < 1e-3


def test_gridding_mars_is_only_for_model_levels_from_arco(tmp_path):
    with pytest.raises(ValueError, match="gridding"):
        cfg(tmp_path, source="mars", gridding="mars")
