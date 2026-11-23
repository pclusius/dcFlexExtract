# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""PROTOTYPE: are the ARCO raw single-level NetCDF files (hourly ERA5 accumulations, global 0.25 deg, one day per
file) the same numbers as the MARS flux data we use? Downloads 6 files of 50 MB, reads the test box and compares with
the MARS GRIB of livetest/ (steps of the 18 UTC forecast, accumulated since the base time).
Needs numpy, eccodes, and netCDF4 or h5netcdf.
usage (repository root):  python livetest/arco_flux_test.py [--grib livetest/raw/.../20230830/FLUX_all.grb] [--out DIR]"""
import argparse, os, sys, urllib.request
from datetime import datetime, timedelta
import numpy as np
import eccodes as ec

URL = "https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level/{y}/{m}/{d}/{v}/surface.nc"
VARS = {142: "large_scale_precipitation", 143: "convective_precipitation", 146: "surface_sensible_heat_flux",
        176: "surface_net_solar_radiation", 180: "eastward_turbulent_surface_stress", 181: "northward_turbulent_surface_stress"}
ap = argparse.ArgumentParser()
ap.add_argument("--date", default="2023-08-30")
ap.add_argument("--grib", default="livetest/raw/N61_W21_S59_E25_g0p25/20230830/FLUX_all.grb")
ap.add_argument("--out", default=".")
ap.add_argument("--box", nargs=4, type=float, default=[61, 59, 21, 25])
a = ap.parse_args()
day = datetime.fromisoformat(a.date)
N, S, W, E = a.box
os.makedirs(a.out, exist_ok=True)

def open_nc(path):
    try:
        import netCDF4
        return netCDF4.Dataset(path)
    except ImportError:
        import h5netcdf
        return h5netcdf.File(path, "r")

def nc_hours(ds):
    t = ds.variables["valid_time"] if "valid_time" in ds.variables else ds.variables["time"]
    units = t.attrs["units"] if hasattr(t, "attrs") else t.units
    vals = np.array(t[:]); unit, since = units.split(" since ")
    base = datetime.fromisoformat(since.strip().replace("Z", ""))
    step = {"hours": 3600, "seconds": 1, "minutes": 60, "days": 86400}[unit.strip()]
    return [base + timedelta(seconds=int(v) * step) for v in vals]

nc = {}
for pid, name in VARS.items():
    f = os.path.join(a.out, f"{name}_{day:%Y%m%d}.nc")
    if not os.path.exists(f):
        print("downloading", name, flush=True)
        urllib.request.urlretrieve(URL.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}", v=name), f)
    ds = open_nc(f)
    var = [k for k in ds.variables if k not in ("time", "valid_time", "latitude", "longitude", "number", "expver")][0]
    lat = np.array(ds.variables["latitude"][:]); lon = np.array(ds.variables["longitude"][:]) % 360
    j = np.where((lat <= N + 1e-6) & (lat >= S - 1e-6))[0]; i = np.where((lon >= W - 1e-6) & (lon <= E + 1e-6))[0]
    j = j[np.argsort(-lat[j])]; i = i[np.argsort(lon[i])]
    data = np.array(ds.variables[var][:]) if ds.variables[var].ndim == 3 else None
    nc[pid] = (nc_hours(ds), data[:, j][:, :, i], var)
    print(f"  {name}: variable {var}, {len(nc[pid][0])} hours, first {nc[pid][0][0]}, box shape {nc[pid][1].shape[1:]}")

ref = {}
with open(a.grib, "rb") as fh:
    while (g := ec.codes_grib_new_from_file(fh)):
        pid = ec.codes_get(g, "paramId")
        base = datetime.strptime(f"{ec.codes_get(g, 'dataDate')} {ec.codes_get(g, 'dataTime'):04d}", "%Y%m%d %H%M")
        valid = base + timedelta(hours=ec.codes_get(g, "step"))
        ni, nj = ec.codes_get(g, "Ni"), ec.codes_get(g, "Nj")
        ref[(pid, valid)] = (base, ec.codes_get(g, "step"), ec.codes_get_values(g).reshape(nj, ni))
        ec.codes_release(g)
print("MARS GRIB:", len(ref), "messages")
print("pid  valid(UTC)       MARS(base,step)   max|MARS|   max|nc|   max|nc-MARS|   max|nc-(MARS_t-MARS_t-1)|")
for pid in VARS:
    hours, arr, _ = nc[pid]
    for h in (3, 7, 14):
        v = datetime(day.year, day.month, day.day, h)
        if v not in hours or (pid, v) not in ref: continue
        base, step, m = ref[(pid, v)]
        x = arr[hours.index(v)]
        prev = ref.get((pid, v - timedelta(hours=1)))
        d2 = np.abs(x - (m - prev[2])).max() if prev and prev[0] == base else float("nan")
        print(f"{pid} {v:%d %H} {base:%d %H}+{step:<2d}  {np.abs(m).max():11.4g} {np.abs(x).max():11.4g} {np.abs(x - m).max():11.4g} {d2:14.4g}")
