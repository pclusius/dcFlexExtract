# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.
#
# The processing sequence (fort.NN files, calc_etadot call, order of the output messages) follows
# flex_extract (Mods/prepare_flexpart.py and Classes/EcFlexpart.py).

"""Raw ERA5 GRIB -> hourly FLEXPART input files.

For each output hour T the model-level fields are handed to the Fortran program calc_etadot
(through the fort.NN files it expects, in a private scratch directory), and the result is
concatenated with the cloud water fields, the fluxes, the surface fields and the static fields:

    [calc_etadot: U V ETADOT T SP Q] [CLWC] [CIWC] [6 fluxes] [9 surface] [4 static]

Existing complete output files are skipped, raw data is never touched.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import era5, etadot, gribio
from .config import Config
from .rawindex import RawIndex

_ID = era5.PARAM_ID
LOG = logging.getLogger("dcfe")
SP_ID = 134          # surface pressure: not downloaded, computed by calc_etadot from LNSP


def prefix_for(grid: float) -> str:
    """Output name prefix, e.g. EA025 for 0.25 degrees (as in the original CONTROL files)."""
    return f"EA{round(grid * 100):03d}"


def hours(cfg: Config) -> list[datetime]:
    t = datetime.combine(cfg.start, datetime.min.time())
    end = datetime.combine(cfg.end, datetime.min.time()) + timedelta(hours=23)
    out = []
    while t <= end:
        out.append(t)
        t += timedelta(hours=1)
    return out


def out_name(cfg: Config, T: datetime) -> str:
    return f"{prefix_for(cfg.grid)}{T:%y%m%d%H}"


def expected_grid_size(cfg: Config) -> int:
    """number of grid points of the domain"""
    ni, nj = cfg.shape
    return ni * nj


def describe_area(area: tuple | None) -> str:
    if area is None:
        return "not a regular lat-lon grid"
    n, w, s, e, ni, nj = area
    w, e = (((x + 180) % 360) - 180 for x in (w, e))
    return f"N {n:g} W {w:g} S {s:g} E {e:g} ({ni} x {nj} points)"


def outputs_of_another_area(cfg: Config, outdir: Path) -> list[tuple[str, str]]:
    """(name, what it holds) of every existing output file of this run that is not on the grid of the domain"""
    bad = []
    for T in hours(cfg):
        f = outdir / out_name(cfg, T)
        if f.exists():
            found, area = gribio.first_gridded(f)
            if not (found and cfg.covers(area)):
                bad.append((f.name, describe_area(area) if found else "no gridded GRIB message"))
    return bad


def namelist(cfg: Config, mnauf: int) -> str:
    """fort.4 for calc_etadot, ERA5 case: ETADOT from MARS (meta=1), regular lat-lon, no omega."""
    west, east = cfg.west, cfg.east
    if east < west:
        east += 360
    maxl = int(round((east - west) / cfg.grid)) + 1
    maxb = int(round((cfg.north - cfg.south) / cfg.grid)) + 1
    return (
        "&NAMGEN\n"
        f"  maxl = {maxl},\n  maxb = {maxb},\n  mlevel = {era5.MAX_LEVEL},\n"
        f'  mlevelist = "{cfg.level_first}/to/{cfg.level_last}",\n'
        f"  mnauf = {mnauf},\n  metapar = 77,\n"
        f"  rlo0 = {west},\n  rlo1 = {east},\n  rla0 = {cfg.south},\n  rla1 = {cfg.north},\n"
        "  momega = 0,\n  momegadiff = 0,\n  mgauss = 0,\n  msmooth = 0,\n"
        "  meta = 1,\n  metadiff = 0,\n  mdpdeta = 1\n/\n"
    )


def missing(ix: RawIndex, cfg: Config, T: datetime) -> list[str]:
    miss = ix.missing_for(T)
    src = {"spectral": ("LNSP (spectral)", ix.lnsp), "lnsp": ("LNSP (gridded)", ix.glnsp),
           "sp": ("SP (surface pressure)", ix.sp)}[cfg.pressure]
    if T not in src[1]:
        miss.append(src[0])
    for name in era5.ML_VARS:
        absent = [lv for lv in range(cfg.level_first, cfg.level_last + 1) if (_ID[name], T, lv) not in ix.ml]
        if absent:
            miss.append(f"{name}(levels {absent[0]}..{absent[-1]}, {len(absent)} missing)")
    return miss


def _ml_msgs(ix: RawIndex, cfg: Config, name: str, T: datetime) -> list[bytes]:
    return [gribio.read(ix.ml[(_ID[name], T, lv)]) for lv in range(cfg.level_first, cfg.level_last + 1)]


def _etadot_from_ground(msgs: list[bytes], ix: RawIndex, cfg: Config, T: datetime, lnsp: bytes) -> list[bytes]:
    """Replace the ETADOT messages written by calc_etadot (running sum started at the top of the retrieved
    levels) by the ground-start result, see etadot.py. Before replacing anything, our own implementation of
    the legacy calculation must reproduce the Fortran values; otherwise we stop instead of writing numbers
    we cannot account for."""
    name = out_name(cfg, T)
    pv = gribio.get_array(lnsp, "pv")
    A, B = pv[: len(pv) // 2], pv[len(pv) // 2:]
    if cfg.level_last != len(A) - 1:
        LOG.warning("%s: levels end at %d, not at the lowest model level %d: the ground boundary cannot be used, "
                    "ETADOT keeps the flex_extract (top) boundary", name, cfg.level_last, len(A) - 1)
        return msgs
    pid = {m: gribio.get_int(m, "paramId") for m in msgs}
    ps = gribio.values(next(m for m in msgs if pid[m] == SP_ID))
    raw = {lv: gribio.values(gribio.read(ix.ml[(_ID["ETADOT"], T, lv)])) for lv in range(cfg.level_first, cfg.level_last + 1)}
    fortran = {gribio.get_int(m, "level"): gribio.values(m) for m in msgs if pid[m] == _ID["ETADOT"]}
    legacy = etadot.half_level_flux(raw, ps, A, B, "top")
    err = max(float(np.abs(legacy[k] - fortran[k]).max()) for k in legacy)
    scale = max(float(np.abs(v).max()) for v in fortran.values())
    if err > 1e-4 * scale + 1e-9:
        raise RuntimeError(f"{name}: our reproduction of calc_etadot's ETADOT differs from it by {err:.3g} "
                           f"(field maximum {scale:.3g}); not replacing it. Use --etadot-boundary top.")
    ground = etadot.half_level_flux(raw, ps, A, B, "ground")
    LOG.debug("%s: ETADOT from the ground boundary; our top-start reproduction matches calc_etadot to %.2g "
              "(field max %.3g); mean change at the lowest level %.4g Pa/s", name, err, scale,
              float(np.abs(ground[cfg.level_last] - legacy[cfg.level_last]).mean()))
    out = []
    for m in msgs:
        if pid[m] == _ID["ETADOT"]:
            m = gribio.modified(m, ground[gribio.get_int(m, "level")])
        out.append(m)
    return out


def _surface_pressure_msg(ix: RawIndex, cfg: Config, T: datetime) -> tuple[bytes, np.ndarray]:
    """(SP message, values in Pa) for the lnsp and sp routes. FLEXPART recognises surface pressure by
    paramId 134 on the surface; the message is cut from ECMWF's own SP, or, for gridded LNSP, from the
    surface geopotential message of the same hour (same grid, surface level) with exp(LNSP) as values."""
    if cfg.pressure == "sp":
        m = gribio.read(ix.sp[T])
        return m, gribio.values(m)
    ps = np.exp(gribio.values(gribio.read(ix.glnsp[T])))
    z = gribio.read(ix.sfc[(_ID["Z"], T)])
    return gribio.modified(z, ps, paramId=SP_ID, bitsPerValue=24), ps


def _build_without_fortran(ix: RawIndex, cfg: Config, T: datetime, final: Path, etadot_boundary: str) -> Path:
    """The lnsp and sp routes: surface pressure is given on the grid, so no spectral transform is needed.
    U, V, T, Q pass through untouched; ETADOT is converted by etadot.py. Same message order as calc_etadot."""
    name = final.name
    u, v, t, q = (_ml_msgs(ix, cfg, n, T) for n in ("U", "V", "T", "Q"))
    pv = gribio.get_array(u[0], "pv")
    A, B = pv[: len(pv) // 2], pv[len(pv) // 2:]
    sp_msg, ps = _surface_pressure_msg(ix, cfg, T)
    raw_msgs = _ml_msgs(ix, cfg, "ETADOT", T)
    raw = {cfg.level_first + i: gribio.values(m) for i, m in enumerate(raw_msgs)}
    boundary = etadot_boundary
    if boundary == "ground" and cfg.level_last != len(A) - 1:
        LOG.warning("%s: levels end at %d, not at the lowest model level %d: ETADOT uses the top boundary",
                    name, cfg.level_last, len(A) - 1)
        boundary = "top"
    H = etadot.half_level_flux(raw, ps, A, B, boundary)
    eta = [gribio.modified(m, H[cfg.level_first + i]) for i, m in enumerate(raw_msgs)]
    LOG.debug("%s: pressure source %s (SP %.1f..%.1f Pa), ETADOT boundary %s, no Fortran step",
              name, cfg.pressure, float(ps.min()), float(ps.max()), boundary)
    return _write_final(u + v + eta + t + [sp_msg] + q, ix, cfg, T, final)


def _write_final(model_msgs: list[bytes], ix: RawIndex, cfg: Config, T: datetime, final: Path) -> Path:
    part = final.with_name(final.name + ".part")
    with open(part, "wb") as f:
        f.write(b"".join(model_msgs))
        for name in ("CLWC", "CIWC"):
            f.write(b"".join(_ml_msgs(ix, cfg, name, T)))
        for m in ix.flux_messages(T) + ix.surface_messages(T) + ix.static_messages():
            f.write(m)
    os.replace(part, final)
    return final


def build_hour(ix: RawIndex, cfg: Config, T: datetime, exe: str | Path, outdir: Path, scratch: Path,
               keep_scratch: bool = False, etadot_boundary: str = "ground") -> Path:
    final = outdir / out_name(cfg, T)
    if final.exists():
        return final
    miss = missing(ix, cfg, T)
    if miss:
        raise RuntimeError(f"cannot build {final.name}: missing {', '.join(miss)}")
    if cfg.pressure != "spectral":
        return _build_without_fortran(ix, cfg, T, final, etadot_boundary)
    work = scratch / f"{T:%Y%m%d%H}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    wr = lambda name, msgs: (work / name).write_bytes(b"".join(msgs))

    lnsp = gribio.read(ix.lnsp[T])
    nl = namelist(cfg, gribio.get_int(lnsp, "pentagonalResolutionParameterJ"))
    wr("fort.4", [nl.encode()])
    LOG.debug("%s: scratch dir %s; fort.4 namelist:\n%s", out_name(cfg, T), work, nl.rstrip())
    wr("fort.10", _ml_msgs(ix, cfg, "U", T) + _ml_msgs(ix, cfg, "V", T))
    wr("fort.11", _ml_msgs(ix, cfg, "T", T))
    wr("fort.12", [lnsp])      # LNSP, spectral (fort.13 would be the divergence, unused for ETA=1)
    wr("fort.17", _ml_msgs(ix, cfg, "Q", T))
    wr("fort.21", _ml_msgs(ix, cfg, "ETADOT", T))
    refs = [ix.lnsp[T]] + [ix.ml[(_ID[n], T, lv)] for n in era5.ML_VARS
                           for lv in range(cfg.level_first, cfg.level_last + 1)]
    refs += ix.flux_refs(T) + ix.surface_refs(T) + ix.static_refs()
    LOG.debug("%s: input messages come from: %s", out_name(cfg, T), ", ".join(sorted({r.path for r in refs})))
    LOG.debug("%s: running (cd %s && %s) with inputs fort.10 (U,V) fort.11 (T) fort.12 (LNSP) fort.17 (Q) "
              "fort.21 (ETADOT)", out_name(cfg, T), work, exe)
    r = subprocess.run([str(exe)], cwd=work, capture_output=True, text=True)
    text = r.stdout + r.stderr
    LOG.debug("%s: calc_etadot exit status %d; last output lines:\n%s", out_name(cfg, T), r.returncode,
              "\n".join(text.strip().splitlines()[-6:]))
    if r.returncode != 0 or "CONGRATULATIONS" not in text:
        (outdir / f"{final.name}.calc_etadot.log").write_text(text)
        LOG.error("%s: calc_etadot failed (exit status %d); full output:\n%s", out_name(cfg, T), r.returncode, text)
        raise RuntimeError(f"calc_etadot failed for {T} (log next to the output)")

    msgs = gribio.read_all(work / "fort.15")
    if etadot_boundary == "ground":
        msgs = _etadot_from_ground(msgs, ix, cfg, T, lnsp)
    elif etadot_boundary != "top":
        raise ValueError(f"etadot_boundary must be 'ground' or 'top', not {etadot_boundary!r}")
    _write_final(msgs, ix, cfg, T, final)
    if not keep_scratch:
        shutil.rmtree(work, ignore_errors=True)
        LOG.debug("%s: scratch dir %s removed", out_name(cfg, T), work)
    return final


def remove_raw(ix: RawIndex, cfg: Config) -> list[Path]:
    """Delete the raw files this run used, once every hourly output exists (the caller checks that). A file is only
    removed if all its time-dependent messages lie inside the period plus the flux padding, so data for days that
    were not processed is never lost. Empty directories and a finished download manifest go with them. Returns
    the deleted files."""
    pad = timedelta(days=era5.FLUX_PAD_DAYS)
    lo = datetime.combine(cfg.start, datetime.min.time()) - pad
    hi = datetime.combine(cfg.end, datetime.min.time()) + pad + timedelta(days=1, hours=12)
    gone, freed = [], 0
    for path in sorted(ix.used_paths()):
        if path in ix.foreign_paths:
            LOG.info("kept %s: it also holds data of another area or grid", path)
            continue
        a, b = ix.span.get(path, (lo, lo))
        if lo <= a and b <= hi:
            size = Path(path).stat().st_size
            Path(path).unlink()
            LOG.debug("removed raw file %s (%d bytes)", path, size)
            gone.append(Path(path))
            freed += size
        else:
            LOG.info("kept %s: it holds data outside %s - %s", path, lo.date(), hi.date())
    root = Path(cfg.rawdir)
    dom = root / cfg.domain_tag
    if dom.is_dir() and not any(dom.rglob("*.grb")) and (dom / "manifest.json").exists():
        (dom / "manifest.json").unlink()
        LOG.info("removed %s (no raw files left; a new download starts afresh)", dom / "manifest.json")
    for d in sorted((q for q in root.rglob("*") if q.is_dir()), key=lambda q: len(q.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    LOG.info("removed %d raw file(s), %.1f MB", len(gone), freed / 1e6)
    return gone
