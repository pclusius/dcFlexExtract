# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.
#
# The flux stencil and edge handling follow flex_extract (EcFlexpart.py deacc_fluxes and
# Mods/disaggregation.py).

"""Index of everything in a raw directory, and construction of the non-model-level part of an
hourly output file (fluxes, surface analysis, static fields)."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import era5, gribio
from .fluxes import FLUX_PARAMS, darain, dapoly, to_rate

_ID = era5.PARAM_ID
_FLUX_IDS = {_ID[n] for n in era5.FLUX_ORDER}
_STATIC_IDS = {_ID[n] for n in era5.STATIC_ORDER}
_SFC_IDS = {_ID[n] for n in era5.SFC_ORDER}
_ML_IDS = {_ID[n] for n in era5.ML_VARS}


class RawIndex:
    def __init__(self, paths, cfg=None):
        """With a Config, gridded messages of any other area or grid are left out (and listed in `foreign`);
        spectral messages are global and always kept."""
        self.foreign: dict[tuple | None, int] = {}         # area (None: not regular lat-lon) -> messages left out
        self.foreign_paths: set[str] = set()
        self.flux: dict[tuple[int, datetime], gribio.MsgRef] = {}
        self.sfc: dict[tuple[int, datetime], gribio.MsgRef] = {}
        self.static: dict[int, gribio.MsgRef] = {}
        self.lnsp: dict[datetime, gribio.MsgRef] = {}       # spectral LNSP
        self.glnsp: dict[datetime, gribio.MsgRef] = {}      # LNSP on the regular grid
        self.sp: dict[datetime, gribio.MsgRef] = {}         # ECMWF surface pressure
        self.ml: dict[tuple[int, datetime, int], gribio.MsgRef] = {}
        self.files = 0
        self.span: dict[str, tuple[datetime, datetime]] = {}   # file -> first and last valid time of its time-dependent messages
        for p in sorted(paths):
            self.files += 1
            for r in gribio.scan(p):
                if cfg is not None and r.grid_type != "sh" and not cfg.covers(r.area):
                    self.foreign[r.area] = self.foreign.get(r.area, 0) + 1
                    self.foreign_paths.add(r.path)
                    continue
                self._add(r)
                if r.param_id not in _STATIC_IDS:
                    lo, hi = self.span.get(r.path, (r.valid, r.valid))
                    self.span[r.path] = (min(lo, r.valid), max(hi, r.valid))

    @classmethod
    def from_dir(cls, rawdir, cfg=None) -> "RawIndex":
        return cls(Path(rawdir).rglob("*.grb"), cfg)

    def used_paths(self) -> set[str]:
        """files that hold at least one message the index kept"""
        refs = [*self.flux.values(), *self.sfc.values(), *self.static.values(), *self.lnsp.values(),
                *self.glnsp.values(), *self.sp.values(), *self.ml.values()]
        return {r.path for r in refs}

    def grid_sizes(self) -> dict[int, int]:
        """number of values -> how many gridded (non-spectral) messages have that size; a single entry unless
        the raw data holds tiles of the domain or data of another grid"""
        out: dict[int, int] = {}
        for d in (self.flux, self.sfc, self.ml, self.glnsp, self.sp):
            for r in d.values():
                out[r.nvalues] = out.get(r.nvalues, 0) + 1
        return out

    def _add(self, r):
        pid = r.param_id
        if pid in _FLUX_IDS and r.step_type == "accum":
            self.flux.setdefault((pid, r.valid), r)          # first occurrence wins (duplicates ignored)
        elif pid in _STATIC_IDS:
            self.static.setdefault(pid, r)
        elif pid in _SFC_IDS:
            self.sfc.setdefault((pid, r.valid), r)
        elif pid == _ID["LNSP"]:
            (self.lnsp if r.grid_type == "sh" else self.glnsp).setdefault(r.valid, r)
        elif pid == _ID["SP"] and r.type_of_level == "surface":
            self.sp.setdefault(r.valid, r)
        elif pid in _ML_IDS and r.type_of_level == "hybrid":
            self.ml.setdefault((pid, r.valid, r.level), r)

    # ---- fluxes -------------------------------------------------------------------------
    def flux_range(self, pid):
        ts = sorted(t for (p, t) in self.flux if p == pid)
        if not ts:
            raise ValueError(f"no flux messages for paramId {pid}")
        return ts[0], ts[-1]

    def _rate(self, pid, t, cache: OrderedDict):
        key = (pid, t)
        if key not in cache:
            cache[key] = to_rate(gribio.values(gribio.read(self.flux[key])), pid)
            while len(cache) > 24:
                cache.popitem(last=False)
        return cache[key]

    def flux_messages(self, T: datetime) -> list[bytes]:
        """The six flux messages valid at T: instantaneous rates, as in the original output."""
        cache: OrderedDict = OrderedDict()
        h = timedelta(hours=1)
        out = []
        for name in era5.FLUX_ORDER:
            pid = _ID[name]
            t0, te = self.flux_range(pid)
            f = darain if FLUX_PARAMS[pid] else dapoly
            if not t0 <= T <= te:
                raise KeyError(f"no flux data for {name} at {T}")
            if T == t0 or T == te:
                v = self._rate(pid, T, cache)
            elif T == te - h:
                v = f([self._rate(pid, te - k * h, cache) for k in range(4)])
            else:
                v = f([self._rate(pid, T + k * h, cache) for k in (-1, 0, 1, 2)])
            tmpl = gribio.read(self.flux[(pid, T)])
            out.append(gribio.modified(tmpl, v, dataDate=int(T.strftime("%Y%m%d")), dataTime=T.hour * 100,
                                       stepRange=0))
        return out

    # ---- surface analysis and static ------------------------------------------------------
    def surface_messages(self, T: datetime) -> list[bytes]:
        return [gribio.read(self.sfc[(_ID[n], T)]) for n in era5.SFC_ORDER]

    def flux_refs(self, T: datetime) -> list:
        """References to the accumulated messages the flux values at T are derived from (T-1h .. T+2h)."""
        h = timedelta(hours=1)
        return [self.flux[(_ID[n], T + k * h)] for n in era5.FLUX_ORDER for k in (-1, 0, 1, 2)
                if (_ID[n], T + k * h) in self.flux]

    def surface_refs(self, T: datetime) -> list:
        return [self.sfc[(_ID[n], T)] for n in era5.SFC_ORDER]

    def static_refs(self) -> list:
        return [self.static[_ID[n]] for n in era5.STATIC_ORDER]

    def static_messages(self) -> list[bytes]:
        return [gribio.read(self.static[_ID[n]]) for n in era5.STATIC_ORDER]

    def missing_for(self, T: datetime) -> list[str]:
        """What is absent for hour T (surface part only for now)."""
        miss = [n for n in era5.SFC_ORDER if (_ID[n], T) not in self.sfc]
        miss += [n for n in era5.STATIC_ORDER if _ID[n] not in self.static]
        for n in era5.FLUX_ORDER:
            pid = _ID[n]
            ok = [(pid, T + timedelta(hours=k)) in self.flux for k in (-1, 0, 1, 2)]
            if not all(ok) and not self._flux_edge_ok(pid, T):
                miss.append(n)
        return miss

    def _flux_edge_ok(self, pid, T):
        try:
            t0, te = self.flux_range(pid)
        except ValueError:
            return False
        return T in (t0, te, te - timedelta(hours=1)) and (pid, T) in self.flux
