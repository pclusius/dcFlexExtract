# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.
#
# The request contents (parameters, times, forecast steps for the fluxes) follow what flex_extract
# sends to CDS (Classes/MarsRetrieval.py, Classes/EcFlexpart.py).

"""Pure request builder: Config -> list of download units. No I/O, no network.

A *unit* (Request) is the smallest thing that is downloaded, stored and skipped on restart:
a span of days x group x variable subset x level range x set of hours x (tile of the) area; by default the largest unit CDS accepts. Its file name is a deterministic
function of its content, so a restarted run finds what was already downloaded. If CDS refuses a
unit (cost limit), `Request.split()` gives smaller units; because GRIB files can simply be
concatenated, the split is invisible to post-processing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from . import era5
from .config import Config

# the area is halved at most this often (1/8 of the domain) before the model levels are split
MAX_AREA_SPLITS = 3
# kinds of split, and the default order in which they are tried after CDS refused a request
SPLIT_KINDS = ("days", "area", "levels", "variables", "hours")
SPLIT_ORDER = ("days", "variables", "area", "levels", "hours")

# groups
ML, LNSP, SP, SL, FLUX, STATIC = "ML", "LNSP", "SP", "SL", "FLUX", "STATIC"

_GROUP_VARS = {
    ML: tuple(era5.ML_VARS),
    LNSP: ("LNSP",),         # spectral (the flex_extract route)
    SP: ("SP",),             # ECMWF surface pressure, single levels
    SL: tuple(era5.SFC_AN_VARS),
    FLUX: tuple(era5.SFC_FLUX_VARS),
    STATIC: tuple(era5.SFC_STATIC_VARS),
}
_GROUP_DATASET = {
    ML: era5.DATASET_MARS, LNSP: era5.DATASET_MARS, SP: era5.DATASET_SFC,
    SL: era5.DATASET_SFC, FLUX: era5.DATASET_SFC, STATIC: era5.DATASET_SFC,
}


def _fmt(x: float) -> str:
    return f"{x:g}"


def _hours_tag(hours: tuple[int, ...]) -> str:
    runs, start, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h != prev + 1:
            runs.append((start, prev))
            start = h
        prev = h
    runs.append((start, prev))
    return "h" + "_".join(f"{a:02d}" if a == b else f"{a:02d}-{b:02d}" for a, b in runs)


@dataclass(frozen=True)
class Request:
    group: str
    day: date
    vars: tuple[str, ...]
    hours: tuple[int, ...]
    levels: Optional[tuple[int, int]]      # model levels (first, last); None for surface groups
    domain: str                            # Config.domain_tag
    area: tuple[float, float, float, float]  # N, W, S, E (a sub-rectangle if tiled)
    grid: float
    tiled: bool = False                    # True when `area` is a tile of the domain
    ndays: int = 1                         # consecutive days covered, starting at `day`
    tile_depth: int = 0                    # how many times the domain was halved to make this tile

    # ---- (de)serialisation, used by the download manifest ------------------------------------
    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["day"] = self.day.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Request":
        return cls(group=d["group"], day=date.fromisoformat(d["day"]), vars=tuple(d["vars"]),
                   hours=tuple(d["hours"]), levels=tuple(d["levels"]) if d["levels"] else None,
                   domain=d["domain"], area=tuple(d["area"]), grid=d["grid"], tiled=d["tiled"],
                   ndays=d.get("ndays", 1), tile_depth=d.get("tile_depth", 0))

    # ---- identity -------------------------------------------------------------------------
    @property
    def dataset(self) -> str:
        return _GROUP_DATASET[self.group]

    @property
    def queue(self) -> str:
        """Server-side queue this request waits in. MARS (tape) and single-levels are separate
        services, so one request may be in flight in each at the same time."""
        return self.dataset

    @property
    def relpath(self) -> str:
        full = _GROUP_VARS[self.group]
        vtag = ("all" if self.vars == full else "all-LNSP" if self.vars == full + ("LNSP",)
                else "-".join(self.vars))
        parts = [self.group, vtag]
        if self.levels:
            parts.append(f"L{self.levels[0]:03d}-{self.levels[1]:03d}")
        if self.hours != era5.HOURS and self.group != STATIC:
            parts.append(_hours_tag(self.hours))
        if self.tiled:
            n, w, s, e = self.area
            parts.append("T" + "_".join(_fmt(x).replace("-", "m").replace(".", "p") for x in (n, w, s, e)))
        span = f"{self.day:%Y%m%d}" if self.ndays == 1 else f"{self.day:%Y%m%d}-{self.last_day:%Y%m%d}"
        return f"{self.domain}/{span}/{'_'.join(parts)}.grb"

    unit_id = relpath

    @property
    def last_day(self) -> date:
        return self.day + timedelta(days=self.ndays - 1)

    @property
    def days(self) -> list[date]:
        return [self.day + timedelta(days=i) for i in range(self.ndays)]

    @property
    def nlevels(self) -> int:
        return 1 if self.levels is None else self.levels[1] - self.levels[0] + 1

    @property
    def expected_messages(self) -> int:
        """Number of GRIB messages the finished file must contain (used for sanity checks)."""
        if self.group == STATIC:
            return len(self.vars)
        per_hour = len(self.ml_vars) * (self.nlevels + (1 if self.has_lnsp and self.levels[0] > 1 else 0))
        if self.group == ML and self.has_lnsp:
            per_hour += 1                    # LNSP exists on level 1 only
        elif self.group != ML:
            per_hour = len(self.vars) * self.nlevels
        return per_hour * len(self.hours) * self.ndays

    @property
    def has_lnsp(self) -> bool:
        """gridded LNSP rides along in this model-level request (--pressure lnsp)"""
        return self.group == ML and "LNSP" in self.vars

    @property
    def ml_vars(self) -> tuple[str, ...]:
        return tuple(v for v in self.vars if v != "LNSP") if self.group == ML else self.vars

    # ---- CDS request body -----------------------------------------------------------------
    def cds_request(self) -> dict:
        n, w, s, e = self.area
        times = [f"{h:02d}" for h in self.hours]
        if self.dataset == era5.DATASET_MARS:
            if self.group == LNSP:
                params, levelist, grid = era5.LNSP_PARAM, "1", "OFF"   # spectral, as before
            else:
                grid = f"{_fmt(self.grid)}/{_fmt(self.grid)}"
                params = "/".join((era5.LNSP_PARAM if v == "LNSP" else era5.ML_VARS[v]) for v in self.vars)
                a, b = self.levels
                if not self.ml_vars:                      # only the gridded LNSP is left (after splits)
                    levelist = "1"
                elif self.has_lnsp and a > 1:
                    # LNSP lives on level 1 only, so level 1 must be in the list. CDS answers HTTP 500 to
                    # "1/60/to/137" (a lone level mixed with a range), so the levels are written out.
                    levelist = "/".join(str(x) for x in [1] + list(range(a, b + 1)))
                else:
                    levelist = f"{a}/to/{b}" if b > a else str(a)
            return {
                "class": "ea", "expver": "1", "stream": "oper", "type": "an", "levtype": "ml",
                "accuracy": "24",           # 24-bit packing, as in the original (ETADOT needs it)
                "date": (f"{self.day:%Y%m%d}" if self.ndays == 1
                         else f"{self.day:%Y%m%d}/to/{self.last_day:%Y%m%d}"),
                "time": "/".join(times),
                "levelist": levelist, "param": params, "grid": grid,
                "area": "/".join(_fmt(x) for x in (n, w, s, e)),
            }
        table = {SP: {"SP": era5.SP_VAR}, SL: era5.SFC_AN_VARS, FLUX: era5.SFC_FLUX_VARS, STATIC: era5.SFC_STATIC_VARS}[self.group]
        return {
            "product_type": ["reanalysis"],
            "variable": [table[v] for v in self.vars],
            "year": f"{self.day:%Y}", "month": f"{self.day:%m}",
            "day": [f"{d:%d}" for d in self.days],          # plan() keeps single-level spans inside one month
            "time": [f"{t}:00" for t in times],
            "data_format": "grib", "download_format": "unarchived",
            "area": [n, w, s, e], "grid": [self.grid, self.grid],
        }

    # ---- splitting after a "request too large" error --------------------------------------
    def split(self, order: tuple[str, ...] = None) -> list["Request"]:
        """Smaller units covering exactly the same data, tried only after CDS refused the larger one.
        `order` lists the kinds of split to try, first to last (default SPLIT_ORDER): days (halving, down to
        one day), variables (one variable dropped at a time), area (halving, at most MAX_AREA_SPLITS times =
        1/8 of the domain), levels (halving), hours (halving). When "area" is in the order, still smaller tiles are the very last resort.
        A kind that is not listed is never used. Returns [] when nothing is left to divide.
        Parts are plain GRIB; day, level, variable and hour parts are simply concatenated, tiles must be
        mosaicked back to the domain."""
        order = SPLIT_ORDER if order is None else tuple(order)
        if bad := [o for o in order if o not in SPLIT_KINDS]:
            raise ValueError(f"unknown split kind {bad}; choose from {SPLIT_KINDS}")

        def halves(seq):
            m = (len(seq) + 1) // 2
            return seq[:m], seq[m:]

        rep = lambda **kw: Request(**{**self.__dict__, **kw})

        def tiles():
            return [rep(area=t, tiled=True, tile_depth=self.tile_depth + 1)
                    for t in split_area(self.area, self.grid)]

        def by(kind):
            if kind == "days" and self.ndays > 1:
                k = (self.ndays + 1) // 2
                return [rep(ndays=k), rep(day=self.day + timedelta(days=k), ndays=self.ndays - k)]
            if kind == "area" and self.tile_depth < MAX_AREA_SPLITS and self.group != STATIC:
                return tiles()
            if kind == "levels" and self.group == ML and self.nlevels > 1:
                a, b = self.levels
                m = (a + b) // 2
                # the lower part keeps LNSP, if any (LNSP is on level 1; the level list is written out)
                return [rep(levels=(a, m)), rep(levels=(m + 1, b), vars=self.ml_vars)]
            if kind == "variables" and len(self.vars) > 1:
                # drop one variable at a time: the request without the last variable, and that variable
                # on its own (the downloader merges leftovers of the same family into one request)
                return [rep(vars=self.vars[:-1]), rep(vars=self.vars[-1:])]
            if kind == "hours" and len(self.hours) > 1 and self.group != STATIC:
                a, b = halves(self.hours)
                return [rep(hours=a), rep(hours=b)]
            return []

        for kind in order:
            if parts := by(kind):
                return parts
        return tiles() if "area" in order else []


def split_area(area, grid):
    """Halve a rectangle along its longer side (in grid points) without overlap: the two parts
    share no grid point, so they can be mosaicked without duplicates. [] if too small."""
    n, w, s, e = area
    if e < w:
        e += 360
    nlat = int(round((n - s) / grid)) + 1
    nlon = int(round((e - w) / grid)) + 1
    wrap = lambda x: x - 360 if x > 180 else x
    if nlat >= nlon and nlat > 1:
        k = nlat // 2                       # points in the southern part
        mid = s + (k - 1) * grid
        return [(n, w, round(mid + grid, 6), wrap(e)), (round(mid, 6), w, s, wrap(e))]
    if nlon > 1:
        k = nlon // 2                       # points in the western part
        mid = w + (k - 1) * grid
        return [(n, w, s, wrap(round(mid, 6))), (n, wrap(round(mid + grid, 6)), s, wrap(e))]
    return []


def level_blocks(first: int, last: int) -> list[tuple[int, int]]:
    """The model levels of a run as one block: MARS requests are tried as large as possible. To add levels
    to an existing download later, download just those levels (e.g. --levels 1 59) into the same raw directory;
    `process` finds data by content, so the pieces fit together."""
    return [(first, last)]


def _daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# Days per request to *try* first (CDS size limits are not documented; the downloader learns the
# largest accepted size and shrinks from there). Single-level groups never cross a month boundary.
ALL_DAYS = 10 ** 6            # "the whole period in one request"
DEFAULT_SPANS = {ML: ALL_DAYS, LNSP: ALL_DAYS, SP: 31, SL: 31, FLUX: 31}
ONE_DAY = {ML: 1, LNSP: 1, SP: 1, SL: 1, FLUX: 1}


def _chunks(days: list[date], span: int, same_month: bool):
    """Consecutive runs of at most `span` days (and within one month if same_month)."""
    out, cur = [], []
    for d in days:
        if cur and (len(cur) >= span or (same_month and (d.year, d.month) != (cur[0].year, cur[0].month))):
            out.append(cur)
            cur = []
        cur.append(d)
    if cur:
        out.append(cur)
    return out


def plan(cfg: Config, spans: Optional[dict] = None) -> list[Request]:
    """All units needed for cfg, ordered by start day. `spans` = days per request by group."""
    if cfg.source == "arco":                     # everything from ARCO-ERA5: nothing is requested from CDS
        return []
    spans = {**DEFAULT_SPANS, **(spans or {})}
    common = dict(domain=cfg.domain_tag, area=(cfg.north, cfg.west, cfg.south, cfg.east), grid=cfg.grid)
    pad = timedelta(days=era5.FLUX_PAD_DAYS)
    main_days = list(_daterange(cfg.start, cfg.end))
    flux_days = list(_daterange(cfg.start - pad, cfg.end + pad))

    units = []
    def add(group, days, levels, extra=()):
        for ch in _chunks(days, spans[group], same_month=_GROUP_DATASET[group] == era5.DATASET_SFC):
            units.append(Request(group, ch[0], _GROUP_VARS[group] + extra, era5.HOURS, levels, ndays=len(ch), **common))
    add(SL, main_days, None)
    if cfg.source == "mixed":                    # model levels (and LNSP) come from ARCO-ERA5, see arco.py
        pass
    elif cfg.pressure == "spectral":
        add(LNSP, main_days, (1, 1))
    elif cfg.pressure == "sp":
        add(SP, main_days, None)
    for i, blk in enumerate(level_blocks(cfg.level_first, cfg.level_last) if cfg.source == "mars" else []):
        # --pressure lnsp: the gridded LNSP is requested together with the first level block (one request less)
        add(ML, main_days, blk, extra=("LNSP",) if cfg.pressure == "lnsp" and i == 0 else ())
    add(FLUX, flux_days, None)
    order = {STATIC: 0, SL: 1, SP: 1, FLUX: 2, LNSP: 3, ML: 4}
    units.sort(key=lambda u: (u.day, order[u.group]))
    return [Request(STATIC, cfg.start, _GROUP_VARS[STATIC], (0,), None, **common)] + units
