# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""Key-based access to raw GRIB files. Files are identified by their *content* (paramId, valid
time, level), never by name, so data downloaded by hand is accepted as long as the fields match."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np

import eccodes as ec


@dataclass(frozen=True)
class MsgRef:
    path: str
    offset: int
    param_id: int
    valid: datetime
    level: int
    type_of_level: str
    step_type: str
    edition: int
    grid_type: str = ""
    nvalues: int = 0
    area: tuple | None = None          # regular lat-lon grids: (north, west, south, east, Ni, Nj)


def _area(g) -> tuple | None:
    if ec.codes_get(g, "gridType") != "regular_ll":
        return None
    lat = [ec.codes_get(g, f"latitudeOf{k}GridPointInDegrees") for k in ("First", "Last")]
    return (max(lat), ec.codes_get(g, "longitudeOfFirstGridPointInDegrees"), min(lat),
            ec.codes_get(g, "longitudeOfLastGridPointInDegrees"), ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj"))


def first_gridded(path: str | Path) -> tuple[bool, tuple | None]:
    """(a gridded message was found, its area as in MsgRef.area) for the first non-spectral message of a file"""
    with open(path, "rb") as fh:
        while (g := ec.codes_grib_new_from_file(fh)) is not None:
            try:
                if ec.codes_get(g, "gridType") != "sh":
                    return True, _area(g)
            finally:
                ec.codes_release(g)
    return False, None


def _valid(g) -> datetime:
    return datetime.strptime(f"{ec.codes_get(g, 'validityDate')}{ec.codes_get(g, 'validityTime'):04d}", "%Y%m%d%H%M")


def scan(path: str | Path) -> list[MsgRef]:
    refs = []
    with open(path, "rb") as fh:
        while True:
            off = fh.tell()
            g = ec.codes_grib_new_from_file(fh)
            if g is None:
                break
            try:
                refs.append(MsgRef(str(path), ec.codes_get_message_offset(g), ec.codes_get(g, "paramId"), _valid(g),
                                   ec.codes_get(g, "level"), ec.codes_get(g, "typeOfLevel"),
                                   ec.codes_get(g, "stepType"), ec.codes_get(g, "edition"),
                                   ec.codes_get(g, "gridType"), ec.codes_get(g, "numberOfValues"), _area(g)))
            finally:
                ec.codes_release(g)
    return refs


def read(ref: MsgRef) -> bytes:
    with open(ref.path, "rb") as fh:
        fh.seek(ref.offset)
        g = ec.codes_grib_new_from_file(fh)
        try:
            return ec.codes_get_message(g)
        finally:
            ec.codes_release(g)


def values(msg: bytes) -> np.ndarray:
    g = ec.codes_new_from_message(msg)
    try:
        return ec.codes_get_values(g)
    finally:
        ec.codes_release(g)


def modified(msg: bytes, new_values: np.ndarray | None = None, **keys) -> bytes:
    """Copy of a message with replaced values and/or header keys (integers)."""
    g = ec.codes_new_from_message(msg)
    try:
        for k, v in keys.items():
            ec.codes_set(g, k, v)
        if new_values is not None:
            ec.codes_set_values(g, new_values)
        return ec.codes_get_message(g)
    finally:
        ec.codes_release(g)


def get_int(msg: bytes, key: str) -> int:
    g = ec.codes_new_from_message(msg)
    try:
        return ec.codes_get(g, key)
    finally:
        ec.codes_release(g)


def get_array(msg: bytes, key: str) -> np.ndarray:
    g = ec.codes_new_from_message(msg)
    try:
        return ec.codes_get_array(g, key)
    finally:
        ec.codes_release(g)


def read_all(path: str | Path) -> list[bytes]:
    """All messages of a GRIB file, in file order."""
    out = []
    with open(path, "rb") as fh:
        while (g := ec.codes_grib_new_from_file(fh)):
            try:
                out.append(ec.codes_get_message(g))
            finally:
                ec.codes_release(g)
    return out
