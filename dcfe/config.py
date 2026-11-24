# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""User configuration: exactly the four things the user chooses (options.txt) plus directories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import era5


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y%m%d").date()


@dataclass(frozen=True)
class Config:
    start: date
    end: date                      # inclusive
    north: float
    west: float
    south: float
    east: float
    grid: float = 0.25
    level_first: int = era5.DEFAULT_LEVELS[0]
    level_last: int = era5.DEFAULT_LEVELS[1]
    rawdir: Path = Path("raw")
    outdir: Path = Path("out")
    pressure: str = era5.DEFAULT_PRESSURE      # lnsp | sp | spectral
    source: str = era5.DEFAULT_SOURCE          # mars | mixed
    gridding: str = era5.DEFAULT_GRIDDING      # exact | mars (spectral data from ARCO only)

    def __post_init__(self):
        if self.end < self.start:
            raise ValueError("end date is before start date")
        if self.north <= self.south:
            raise ValueError("north must be larger than south")
        if not (-90 <= self.south and self.north <= 90):
            raise ValueError("latitudes must be within -90..90")
        if self.east == self.west:
            raise ValueError("west and east are equal")
        if self.pressure not in era5.PRESSURE_SOURCES:
            raise ValueError(f"pressure must be one of {era5.PRESSURE_SOURCES}")
        if self.source not in era5.SOURCES:
            raise ValueError(f"source must be one of {era5.SOURCES}")
        if self.gridding not in era5.GRIDDINGS:
            raise ValueError(f"gridding must be one of {era5.GRIDDINGS}")
        if self.gridding != era5.DEFAULT_GRIDDING and self.source == "mars":
            raise ValueError("--gridding mars only applies to model levels read from ARCO-ERA5 "
                             "(--source mixed or arco); the MARS route is gridded by MARS itself")
        if self.source in ("mixed", "arco") and self.pressure != "lnsp":
            raise ValueError("--source mixed and arco take the surface pressure from the spectral LNSP of ARCO-ERA5: "
                             "use --pressure lnsp (the default)")
        if self.grid not in era5.VALID_GRIDS:
            raise ValueError(f"grid must be one of {era5.VALID_GRIDS}")
        if not (1 <= self.level_first <= self.level_last <= era5.MAX_LEVEL):
            raise ValueError(f"levels must satisfy 1 <= first <= last <= {era5.MAX_LEVEL}")

    @classmethod
    def make(cls, start, end, area, grid=0.25, levels=era5.DEFAULT_LEVELS,
             rawdir="raw", outdir="out", pressure=era5.DEFAULT_PRESSURE,
             source=era5.DEFAULT_SOURCE, gridding=era5.DEFAULT_GRIDDING) -> "Config":
        """area = (north, west, south, east); levels = (first, last)."""
        n, w, s, e = (float(x) for x in area)
        return cls(parse_date(start), parse_date(end), n, w, s, e, float(grid),
                   int(levels[0]), int(levels[1]), Path(rawdir), Path(outdir), pressure, source, gridding)

    @property
    def domain_tag(self) -> str:
        """Directory name that identifies the domain, so data of different domains never mix."""
        f = lambda x: f"{x:g}".replace("-", "m").replace(".", "p")
        return f"N{f(self.north)}_W{f(self.west)}_S{f(self.south)}_E{f(self.east)}_g{f(self.grid)}"
