# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""PROTOTYPE: surface analysis and static fields from the ARCO raw single-level NetCDF files (CDS hourly data, global
0.25 deg, one day per file, ~50 MB per variable and day) against the MARS GRIB of livetest/. 13 files are downloaded.
Needs numpy, eccodes, netCDF4 or h5netcdf.   usage (repository root): python livetest/arco_sfc_test.py [--out DIR]"""
import argparse, os, urllib.request
from datetime import datetime
import numpy as np
import eccodes as ec

URL = "https://storage.googleapis.com/gcp-public-data-arco-era5/raw/date-variable-single_level/{y}/{m}/{d}/{v}/surface.nc"
# paramId -> (ARCO directory, GRIB shortName used for the reference)
VARS = {141: "snow_depth", 151: "mean_sea_level_pressure", 164: "total_cloud_cover", 165: "10m_u_component_of_wind",
        166: "10m_v_component_of_wind", 167: "2m_temperature", 168: "2m_dewpoint_temperature", 129: "geopotential",
        172: "land_sea_mask", 160: "standard_deviation_of_orography", 27: "low_vegetation_cover",
        28: "high_vegetation_cover", 244: "forecast_surface_roughness"}
ap = argparse.ArgumentParser()
ap.add_argument("--date", default="2023-08-30"); ap.add_argument("--hour", type=int, default=14)
ap.add_argument("--sl", default="livetest/raw/N61_W21_S59_E25_g0p25/20230830/SL_all.grb")
ap.add_argument("--static", default="livetest/raw/N61_W21_S59_E25_g0p25/20230830/STATIC_all.grb")
ap.add_argument("--out", default="."); ap.add_argument("--box", nargs=4, type=float, default=[61, 59, 21, 25])
a = ap.parse_args(); day = datetime.fromisoformat(a.date); N, S, W, E = a.box
os.makedirs(a.out, exist_ok=True)

def open_nc(path):
    try:
        import netCDF4; return netCDF4.Dataset(path)
    except ImportError:
        import h5netcdf; return h5netcdf.File(path, "r")

ref = {}
for f in (a.sl, a.static):
    with open(f, "rb") as fh:
        while (g := ec.codes_grib_new_from_file(fh)):
            pid = ec.codes_get(g, "paramId"); hh = ec.codes_get(g, "dataTime") // 100
            ref[(pid, hh)] = ec.codes_get_values(g).reshape(ec.codes_get(g, "Nj"), ec.codes_get(g, "Ni")); ec.codes_release(g)
print(f"{a.date} {a.hour:02d} UTC, box {N}/{S}/{W}/{E}\npid  variable                              max|MARS|  max|nc-MARS|  rel")
for pid, name in VARS.items():
    f = os.path.join(a.out, f"{name}_{day:%Y%m%d}.nc")
    if not os.path.exists(f):
        urllib.request.urlretrieve(URL.format(y=day.year, m=f"{day.month:02d}", d=f"{day.day:02d}", v=name), f)
    ds = open_nc(f)
    var = [k for k in ds.variables if k not in ("time", "valid_time", "latitude", "longitude", "number", "expver")][0]
    lat = np.array(ds.variables["latitude"][:]); lon = np.array(ds.variables["longitude"][:]) % 360
    j = np.where((lat <= N + 1e-6) & (lat >= S - 1e-6))[0]; i = np.where((lon >= W - 1e-6) & (lon <= E + 1e-6))[0]
    j = j[np.argsort(-lat[j])]; i = i[np.argsort(lon[i])]
    hr = a.hour if (pid, a.hour) in ref else 0          # the static fields of the MARS data are those of 00 UTC
    x = np.array(ds.variables[var][hr])[np.ix_(j, i)]
    m = ref.get((pid, hr))
    if m is None: print(f"{pid:4d} {name:36s} no MARS reference"); continue
    d = np.abs(x - m).max(); sc = np.abs(m).max() or 1
    print(f"{pid:4d} {name:36s} {np.abs(m).max():10.4g} {d:12.4g} {d / sc:8.1e}")
