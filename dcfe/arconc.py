# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""Surface, flux and static fields from ARCO-ERA5 (--source arco): Google's copy of the hourly ERA5 single-level
data, one global NetCDF file per variable and day (about 50 MB each). Each file is downloaded, cut to the box,
written as GRIB1 messages in the form the CDS/MARS route delivers (so `process` is unchanged) and deleted.

  fluxes   hourly accumulations labelled by the end of the hour, in the units of the MARS data (J m-2, m, N m-2 s)
  surface  the analysis fields of the SL group, all 24 hours of a day
  static   the time-invariant fields, from 00 UTC of the first day
"""
from __future__ import annotations

import logging
import os
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from . import era5
from .arco import box_axes
from .config import Config

LOG = logging.getLogger("dcfe")
URL = ("https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level/"
       "{y}/{m:02d}/{d:02d}/{v}/surface.nc")
_ID = era5.PARAM_ID
_COORDS = {"time", "valid_time", "latitude", "longitude", "number", "expver"}


def nc_url(var: str, day: date) -> str:
    return URL.format(y=day.year, m=day.month, d=day.day, v=var)


def fetch(url: str, dest: Path, tries: int = 4) -> None:
    """Download to dest (via dest.part); a few retries on network errors."""
    part = dest.with_name(dest.name + ".part")
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(part, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            os.replace(part, dest)
            return
        except OSError as e:
            if k == tries - 1:
                raise
            LOG.warning("%s: %s; retrying", url.rsplit("/", 3)[-2], e)
            time.sleep(5 * (k + 1))


def exists(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60):
            return True
    except OSError:
        return False


class ArcoRawUnavailable(RuntimeError):
    pass


def check_range(cfg: Config, exists_fn: Callable[[str], bool] = exists) -> None:
    """Stops before anything is downloaded if the first or last file of the run is not there."""
    pad = timedelta(days=era5.FLUX_PAD_DAYS)
    for var, day in (("large_scale_precipitation", cfg.end + pad), ("2m_temperature", cfg.end),
                     ("large_scale_precipitation", cfg.start - pad)):
        if not exists_fn(nc_url(var, day)):
            raise ArcoRawUnavailable(f"ARCO-ERA5 has no single-level file for {day:%d-%m-%Y} ({var}). The run needs "
                                     f"{(cfg.start - pad):%d-%m-%Y} to {(cfg.end + pad):%d-%m-%Y} (fluxes need one day "
                                     "before and after). Use an earlier end date or --source mars.")


# ---------------------------------------------------------------------------------------------------- reading
def read_box(path: Path, lat: np.ndarray, lon: np.ndarray) -> tuple[list[datetime], np.ndarray]:
    """(valid times, array[time, lat, lon]) of the first data variable of a NetCDF file at the box points. The
    file's own latitude/longitude axes are searched, the points must lie on them (0.25 degree files)."""
    import netCDF4
    with netCDF4.Dataset(path) as ds:
        var = next(k for k in ds.variables if k not in _COORDS)
        v = ds.variables[var]
        flat, flon = np.array(ds.variables["latitude"][:], float), np.array(ds.variables["longitude"][:], float) % 360
        rows = np.abs(flat[None, :] - lat[:, None]).argmin(axis=1)
        d = np.abs(((flon[None, :] - (lon[:, None] % 360) + 180) % 360) - 180)
        cols = d.argmin(axis=1)
        if np.abs(flat[rows] - lat).max() > 1e-3 or d[np.arange(len(lon)), cols].max() > 1e-3:
            raise ValueError(f"{path.name}: the grid points of the run are not on the 0.25 degree grid of the file")
        tv = ds.variables["valid_time"] if "valid_time" in ds.variables else ds.variables["time"]
        unit, since = tv.units.split(" since ")
        base = datetime.fromisoformat(since.strip().replace("Z", ""))
        sec = {"hours": 3600, "seconds": 1, "minutes": 60, "days": 86400}[unit.strip()]
        times = [base + timedelta(seconds=int(x) * sec) for x in np.array(tv[:])]
        r0, r1 = rows.min(), rows.max() + 1
        raw = v[:, r0:r1, :] if v.ndim == 3 else v[:, :, r0:r1, :].max(axis=1)      # ERA5T files carry an expver axis
        data = np.ma.filled(raw, np.nan)
    return times, np.asarray(data[:, rows - r0][:, :, cols], float)


# ---------------------------------------------------------------------------------------------------- writing
def grib1(cfg: Config, items) -> list[bytes]:
    """GRIB1 messages like the MARS ones: items = (shortName, valid time, accumulated?, 2-d values)."""
    import eccodes as ec
    lat, lon = box_axes(cfg)
    g = ec.codes_grib_new_from_samples("regular_ll_sfc_grib1")
    out = []
    try:
        def s(k, v):
            ec.codes_set(g, k, v)
        s("centre", 98); s("generatingProcessIdentifier", 145); s("table2Version", 128)
        s("setLocalDefinition", 1); s("class", "ea"); s("stream", "oper"); s("expver", "0001")
        s("Ni", len(lon)); s("Nj", len(lat)); s("iScansNegatively", 0); s("jScansPositively", 0)
        s("latitudeOfFirstGridPointInDegrees", float(lat[0])); s("latitudeOfLastGridPointInDegrees", float(lat[-1]))
        s("longitudeOfFirstGridPointInDegrees", float(lon[0] % 360)); s("longitudeOfLastGridPointInDegrees", float(lon[-1] % 360))
        s("iDirectionIncrementInDegrees", cfg.grid); s("jDirectionIncrementInDegrees", cfg.grid)
        s("packingType", "grid_simple"); s("bitsPerValue", 24)
        for name, valid, accum, values in items:
            base = valid - timedelta(hours=1) if accum else valid          # accumulation of the hour ending at `valid`
            s("indicatorOfParameter", _ID[name]); s("type", "fc" if accum else "an")
            s("dataDate", int(base.strftime("%Y%m%d"))); s("dataTime", base.hour * 100)
            if accum:
                s("timeRangeIndicator", 4); s("P1", 0); s("P2", 1)
            else:
                s("timeRangeIndicator", 0); s("P1", 0); s("P2", 0)
            ec.codes_set_values(g, np.asarray(values, float).ravel())
            out.append(ec.codes_get_message(g))
    finally:
        ec.codes_release(g)
    return out


# ---------------------------------------------------------------------------------------------------- downloader
def _kind_vars(kind: str) -> dict[str, str]:
    return {"flux": era5.SFC_FLUX_VARS, "sl": era5.SFC_AN_VARS, "static": era5.SFC_STATIC_VARS}[kind]


def raw_path(cfg: Config, kind: str, day: date) -> Path:
    name = {"flux": f"ARCO_FLUX_{day:%Y%m%d}.grb", "sl": f"ARCO_SL_{day:%Y%m%d}.grb", "static": "ARCO_STATIC.grb"}[kind]
    return Path(cfg.rawdir) / cfg.domain_tag / f"{day:%Y%m%d}" / name


class NcDownloader:
    """One task = one kind of data of one day (6 / 9 / 4 NetCDF files of 50 MB). Resumable: a written file is
    never redone. `fetch_fn(url, dest)` is injectable for tests."""

    def __init__(self, cfg: Config, log: Callable[[str], None] = LOG.info, fetch_fn: Callable[[str, Path], None] = fetch):
        self.cfg, self.log, self.fetch = cfg, log, fetch_fn
        pad = timedelta(days=era5.FLUX_PAD_DAYS)
        days = lambda a, b: [a + timedelta(days=i) for i in range((b - a).days + 1)]
        self.tasks = [("static", cfg.start)] + [("sl", d) for d in days(cfg.start, cfg.end)] + \
                     [("flux", d) for d in days(cfg.start - pad, cfg.end + pad)]
        self.tasks.sort(key=lambda t: (t[1], {"static": 0, "sl": 1, "flux": 2}[t[0]]))
        self.lat, self.lon = box_axes(cfg)

    def pending(self):
        return [t for t in self.tasks if not raw_path(self.cfg, *t).exists()]

    def summary(self) -> dict[str, int]:
        n = len(self.pending())
        return {"nc_tasks_done": len(self.tasks) - n, "nc_tasks_todo": n}

    def step(self) -> bool:
        todo = self.pending()
        if not todo:
            return False
        kind, day = todo[0]
        t0 = time.time()
        scratch = Path(self.cfg.rawdir) / self.cfg.domain_tag / ".nc"
        scratch.mkdir(parents=True, exist_ok=True)
        series: dict[str, tuple[list[datetime], np.ndarray]] = {}
        for name, var in _kind_vars(kind).items():
            f = scratch / f"{var}_{day:%Y%m%d}.nc"
            self.fetch(nc_url(var, day), f)
            try:
                series[name] = read_box(f, self.lat, self.lon)
            finally:
                f.unlink(missing_ok=True)
        items = []
        if kind == "static":                                    # as in the MARS data: the fields of 00 UTC
            for name, (times, a) in series.items():
                items.append((name, datetime.combine(day, datetime.min.time()), False, a[times.index(datetime.combine(day, datetime.min.time()))]))
        else:
            for t in range(24):
                valid = datetime.combine(day, datetime.min.time()) + timedelta(hours=t)
                for name in _kind_vars(kind):
                    times, a = series[name]
                    items.append((name, valid, kind == "flux", a[times.index(valid)]))
        path = raw_path(self.cfg, kind, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        with open(part, "wb") as fh:
            for m in grib1(self.cfg, items):
                fh.write(m)
        os.replace(part, path)
        try:
            scratch.rmdir()
        except OSError:
            pass
        self.log(f"ARCO NetCDF {kind} {day:%Y-%m-%d}: {path.name} ({len(todo) - 1} tasks left, {time.time() - t0:.0f} s)")
        return True
