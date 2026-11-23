# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""ARCO-ERA5 (Google, gs://gcp-public-data-arco-era5) as the source of the model-level fields: `--source mixed`.

The model-level data of one hour is read from the public Zarr stores over plain HTTPS (no MARS queue, no account),
evaluated on the lat-lon box of the run and written as a GRIB file that looks like the MARS data (U V T Q ETADOT
CLWC CIWC on the retrieved levels, gridded LNSP on level 1), so that `process` needs no change:

    spectral T639  t, d, vo, lnsp  -> T, U, V, LNSP, and ETADOT from mass continuity (spectral.py)
    N320 reduced Gaussian  q, clwc, ciwc  -> bilinear interpolation to the box

ARCO has no ETADOT (MARS parameter 77): it is computed from the divergence of all 137 levels and the surface
pressure gradient, which needs `d` on every level. Surface, flux and static fields are NOT taken from here (CDS).
Zarr v2 chunks are blosc-compressed; decoding needs `numcodecs` (part of any zarr installation).
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from . import era5, spectral
from .config import Config

LOG = logging.getLogger("dcfe")
ROOT = "https://storage.googleapis.com/gcp-public-data-arco-era5/co"
WIND, MOIST, SURF = "model-level-wind.zarr-v2", "model-level-moisture.zarr-v2", "single-level-surface.zarr-v2"
STORES = (WIND, MOIST, SURF)
EPOCH = datetime(1900, 1, 1)          # the time axis of all stores: hours since this instant
BLOCK = 12                            # model levels synthesised at once (memory ~ 60 MB per level at T639, 129 x 161 points)
_ID = era5.PARAM_ID


class ArcoError(RuntimeError):
    pass


class ArcoMissing(ArcoError):
    """the object does not exist in the bucket (HTTP 404)"""


class ArcoUnavailable(ArcoError):
    """the requested dates are not (all) in ARCO-ERA5; the message says what to do"""


def http_get(url: str, tries: int = 5, timeout: float = 60) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ArcoMissing(url) from e
            err = e
        except Exception as e:                       # noqa: BLE001 - network hiccup: retry, then give up
            err = e
        if i < tries - 1:
            time.sleep(1 + 2 * i)
    raise ArcoError(f"{url}: {err}")


class Store:
    """Read access to the three Zarr v2 stores. `get(url) -> bytes` is replaceable (tests)."""

    def __init__(self, get: Callable[[str], bytes] | None = None, root: str = ROOT, workers: int = 16):
        self._get = get or http_get
        self.root = root
        self.pool = ThreadPoolExecutor(workers)
        self._json: dict[str, dict] = {}
        self._checked: set[str] = set()

    def get(self, rel: str) -> bytes:
        return self._get(f"{self.root}/{rel}")

    def json(self, rel: str) -> dict:
        if rel not in self._json:
            self._json[rel] = json.loads(self.get(rel))
        return self._json[rel]

    def chunk(self, store: str, var: str, *idx: int) -> np.ndarray:
        meta = self.json(f"{store}/{var}/.zarray")
        raw = self.get(f"{store}/{var}/" + ".".join(str(i) for i in idx))
        if meta.get("compressor"):
            import numcodecs
            raw = numcodecs.get_codec(dict(meta["compressor"])).decode(raw)
        return np.frombuffer(raw, dtype=meta["dtype"])

    def submit(self, store: str, var: str, *idx: int) -> Future:
        return self.pool.submit(self.chunk, store, var, *idx)

    def hybrid_coefficients(self) -> tuple[np.ndarray, np.ndarray]:
        """A, B of the half levels 0..137 (only the consolidated metadata of the stores holds them)"""
        pv = self.json(f"{SURF}/.zmetadata")["metadata"]["lnsp/.zattrs"]["GRIB_pv"]
        n = len(pv) // 2
        return np.array(pv[:n], float), np.array(pv[n:], float)

    def index(self, store: str, T: datetime) -> int:
        """position of hour T on the time axis; the axis is hourly from 1900-01-01 (checked once per store)"""
        if store not in self._checked:
            t = self.chunk(store, "time", 0)
            if not (t[0] == 0 and t[1] == 1 and t[-1] == t.size - 1):
                raise ArcoError(f"{store}: unexpected time axis (not hourly since 1900-01-01)")
            self._checked.add(store)
        return int((T - EPOCH).total_seconds() // 3600)

    def valid_range(self) -> tuple[date, date, date]:
        """(first day, last day, last day of final ERA5) that ALL three stores have; later days up to `last` are
        ERA5T, the preliminary version"""
        at = [self.json(f"{s}/.zattrs") for s in STORES]
        d = lambda a, k: date.fromisoformat(a[k][:10])
        first = max(d(a, "valid_time_start") for a in at)
        last = min(d(a, "valid_time_stop_era5t") if "valid_time_stop_era5t" in a else d(a, "valid_time_stop") for a in at)
        stable = min(d(a, "valid_time_stop") for a in at)
        return first, last, stable


def check_range(cfg: Config, store: Store) -> date:
    """Raise ArcoUnavailable unless ARCO has every day of the run; return the last day of final (non-ERA5T) data."""
    first, last, stable = store.valid_range()
    if cfg.start < first or cfg.end > last:
        if cfg.end > last:
            hint = f"Use --source mars from {last + timedelta(days=1):%d-%m-%Y} onwards."
        else:
            hint = f"Use --source mars before {first:%d-%m-%Y}."
        raise ArcoUnavailable(f"ARCO-ERA5 has model-level data for {first:%d-%m-%Y} to {last:%d-%m-%Y} "
                              f"(the earliest end among its wind, moisture and surface-pressure stores), but the run "
                              f"covers {cfg.start:%d-%m-%Y} to {cfg.end:%d-%m-%Y}. {hint}")
    return stable


# ------------------------------------------------------------------------------------------------ one hour
class Hour:
    """Model-level fields of one hour on the box: fields[name][level] -> (nlat, nlon); lnsp (nlat, nlon)."""

    def __init__(self, T, lnsp, fields, lat, lon):
        self.T, self.lnsp, self.fields, self.lat, self.lon = T, lnsp, fields, lat, lon


def box_axes(cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    east = cfg.east + 360 if cfg.east < cfg.west else cfg.east
    nlat = int(round((cfg.north - cfg.south) / cfg.grid)) + 1
    nlon = int(round((east - cfg.west) / cfg.grid)) + 1
    return cfg.north - cfg.grid * np.arange(nlat), cfg.west + cfg.grid * np.arange(nlon)


class HourBuilder:
    """Reads and evaluates the fields of one hour. Keeps what does not change between hours (hybrid
    coefficients, Legendre tables, interpolation weights)."""

    def __init__(self, cfg: Config, store: Store):
        self.cfg, self.store = cfg, store
        self.lat, self.lon = box_axes(cfg)
        self.A, self.B = store.hybrid_coefficients()
        self.nlev = len(self.A) - 1
        dB = np.diff(self.B)
        self.need_uv = [k for k in range(1, self.nlev + 1) if dB[k - 1] != 0]      # levels that carry dB * ps * V.grad(ln ps)
        self.wanted = list(range(cfg.level_first, cfg.level_last + 1))
        self.vo_levels = set(self.need_uv) | set(self.wanted)
        self._basis = None
        self._gg = None

    def _spectral(self, ncoef: int) -> spectral.Basis:
        if self._basis is None:
            LOG.debug("ARCO: Legendre tables for T%d at %d x %d points", spectral.truncation(ncoef), len(self.lat), len(self.lon))
            self._basis = spectral.Basis(self.lat, self.lon, spectral.truncation(ncoef))
        return self._basis

    def _reduced(self):
        if self._gg is None:
            st = self.store
            gg = spectral.ReducedGG(st.chunk(MOIST, "latitude", 0), st.chunk(MOIST, "longitude", 0))
            self._gg = (gg, *gg.plan(self.lat, self.lon))
        return self._gg

    def _request(self, block, iw, im):
        st, f = self.store, {}
        for k in block:
            f[("d", k)] = st.submit(WIND, "d", iw, k - 1, 0)
            if k in self.vo_levels:
                f[("vo", k)] = st.submit(WIND, "vo", iw, k - 1, 0)
            if k in self.wanted:
                f[("t", k)] = st.submit(WIND, "t", iw, k - 1, 0)
                for v in ("q", "clwc", "ciwc"):
                    f[(v, k)] = st.submit(MOIST, v, im, k - 1, 0)
        return f

    def build(self, T: datetime) -> Hour:
        st, cfg = self.store, self.cfg
        iw, im, isf = st.index(WIND, T), st.index(MOIST, T), st.index(SURF, T)
        lnsp_flat = st.chunk(SURF, "lnsp", isf, 0)
        bs = self._spectral(lnsp_flat.size)
        gg, plan, wl = self._reduced()
        blocks = [list(range(a, min(a + BLOCK, self.nlev + 1))) for a in range(1, self.nlev + 1, BLOCK)]
        out = {n: {} for n in ("U", "V", "T", "Q", "CLWC", "CIWC")}
        D, UV = {}, {}
        pending = self._request(blocks[0], iw, im)
        for i, block in enumerate(blocks):
            nxt = self._request(blocks[i + 1], iw, im) if i + 1 < len(blocks) else None     # prefetch the next block
            got = {k: f.result() for k, f in pending.items()}
            for k, v in zip(block, spectral.scalar([got[("d", k)] for k in block], bs)):
                D[k] = v
            uv = [k for k in block if k in self.vo_levels]
            if uv:
                u, v = spectral.winds([got[("vo", k)] for k in uv], [got[("d", k)] for k in uv], bs)
                for j, k in enumerate(uv):
                    UV[k] = (u[j], v[j])
            tl = [k for k in block if k in self.wanted]
            if tl:
                for k, v in zip(tl, spectral.scalar([got[("t", k)] for k in tl], bs)):
                    out["T"][k] = v
                for k in tl:
                    out["U"][k], out["V"][k] = UV[k]
                    for name, var in (("Q", "q"), ("CLWC", "clwc"), ("CIWC", "ciwc")):
                        out[name][k] = spectral.ReducedGG.apply((plan, wl), got[(var, k)].astype(float))
            pending = nxt
        lnsp, gx, gy = spectral.gradient_scalar(lnsp_flat, bs)
        H = spectral.continuity_flux(D, {k: v[0] for k, v in UV.items()}, {k: v[1] for k, v in UV.items()},
                                     lnsp, gx, gy, self.A, self.B)
        out["ETADOT"] = spectral.etadot_from_flux(H, np.exp(lnsp), self.A, self.B, cfg.level_first, cfg.level_last)
        LOG.debug("ARCO %s: ground flux |H| max %.2e Pa/s (must be 0)", T, float(np.abs(H[self.nlev]).max()))
        return Hour(T, lnsp, out, self.lat, self.lon)


# ------------------------------------------------------------------------------------------------ GRIB output
def hour_path(cfg: Config, T: datetime) -> Path:
    return (Path(cfg.rawdir) / cfg.domain_tag / f"{T:%Y%m%d}" /
            f"ARCO_ML_L{cfg.level_first:03d}-{cfg.level_last:03d}_h{T.hour:02d}.grb")


def messages(cfg: Config, h: Hour, A: np.ndarray, B: np.ndarray) -> list[bytes]:
    """GRIB2 messages as MARS delivers ERA5 model-level analysis: hybrid levels, regular_ll, 24-bit simple packing,
    hybrid coefficients (pv) in every message. Header keys checked against real ERA5 messages with grib_compare."""
    import eccodes as ec
    g = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        def s(k, v):
            ec.codes_set(g, k, v)
        s("centre", 98); s("subCentre", 0); s("tablesVersion", 5); s("significanceOfReferenceTime", 1)
        s("dataDate", int(h.T.strftime("%Y%m%d"))); s("dataTime", h.T.hour * 100)
        s("productionStatusOfProcessedData", 0); s("typeOfProcessedData", 0)
        s("setLocalDefinition", 1); s("grib2LocalSectionNumber", 1)
        s("class", "ea"); s("type", "an"); s("stream", "oper"); s("expver", "0001")
        s("typeOfFirstFixedSurface", 105); s("typeOfSecondFixedSurface", 255)
        s("numberOfVerticalCoordinateValues", len(A) + len(B))
        ec.codes_set_array(g, "pv", np.concatenate([A, B]))
        s("typeOfGeneratingProcess", 0); s("generatingProcessIdentifier", 145); s("backgroundProcess", 255)
        ec.codes_set_missing(g, "hoursAfterDataCutoff"); ec.codes_set_missing(g, "minutesAfterDataCutoff")
        s("Ni", len(h.lon)); s("Nj", len(h.lat)); s("iScansNegatively", 0); s("jScansPositively", 0)
        s("latitudeOfFirstGridPointInDegrees", float(h.lat[0])); s("latitudeOfLastGridPointInDegrees", float(h.lat[-1]))
        s("longitudeOfFirstGridPointInDegrees", float(h.lon[0] % 360)); s("longitudeOfLastGridPointInDegrees", float(h.lon[-1] % 360))
        s("iDirectionIncrementInDegrees", cfg.grid); s("jDirectionIncrementInDegrees", cfg.grid)
        s("packingType", "grid_simple"); s("bitsPerValue", 24)
        out = []

        def emit(pid, level, values):
            s("paramId", pid); s("level", level)
            ec.codes_set_values(g, np.asarray(values, float).ravel())
            out.append(ec.codes_get_message(g))
        emit(_ID["LNSP"], 1, h.lnsp)
        for name in era5.ML_VARS:
            for lv in range(cfg.level_first, cfg.level_last + 1):
                emit(_ID[name], lv, h.fields[name][lv])
        return out
    finally:
        ec.codes_release(g)


class ArcoDownloader:
    """Builds the hourly raw GRIB files of the run, one file per hour, resumable: a finished file is never redone,
    unfinished work is written as .part and renamed."""

    def __init__(self, cfg: Config, store: Store | None = None, log: Callable[[str], None] = LOG.info):
        self.cfg, self.log = cfg, log
        self.store = store or Store()
        self._builder: HourBuilder | None = None
        from .process import hours
        self.hours = hours(cfg)

    def pending(self) -> list[datetime]:
        return [T for T in self.hours if not hour_path(self.cfg, T).exists()]

    def summary(self) -> dict[str, int]:
        n = len(self.pending())
        return {"arco_hours_done": len(self.hours) - n, "arco_hours_todo": n}

    def step(self) -> bool:
        """Build the next missing hour; False when nothing is left."""
        todo = self.pending()
        if not todo:
            return False
        T = todo[0]
        t0 = time.time()
        if self._builder is None:
            self._builder = HourBuilder(self.cfg, self.store)
        b = self._builder
        h = b.build(T)
        path = hour_path(self.cfg, T)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        with open(part, "wb") as f:
            for m in messages(self.cfg, h, b.A, b.B):
                f.write(m)
        os.replace(part, path)
        self.log(f"ARCO {T:%Y-%m-%d %H} UTC: {path.name} ({len(todo) - 1} hours left, {time.time() - t0:.0f} s)")
        return True
