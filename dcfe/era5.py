# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.
#
# The ERA5 parameter sets, times and forecast-step conventions follow flex_extract (Classes/EcFlexpart.py
# and the CONTROL_EA5 examples).

"""Fixed ERA5 choices. The user never sets these (see options.txt); they were CONTROL-file keys
in flex_extract (ETA=1, CWC=1, DTIME=1, ACCTYPE FC, ACCTIME 06/18, ACCMAXSTEP 12, ...)."""

DATASET_MARS = "reanalysis-era5-complete"        # model levels, tape-backed MARS archive
DATASET_SFC = "reanalysis-era5-single-levels"     # surface fields, separate (disk) service

MAX_LEVEL = 137
# Level 137 is the lowest model level, at the surface. The requested levels go into ONE request (MARS waits
# are per request, so fewer, larger requests are faster). Levels are never split, not even after a "too
# large" error (days / variables / hours / tiles are split instead).
DEFAULT_LEVELS = (60, 137)

HOURS = tuple(range(24))

# model-level variables: name -> (MARS param id, GRIB shortName)
ML_VARS = {
    "T": "130.128", "U": "131.128", "V": "132.128", "Q": "133.128",
    "ETADOT": "77.128", "CLWC": "246.128", "CIWC": "247.128",
}
LNSP_PARAM = "152.128"   # level 1; spherical harmonics (grid OFF) or, as GLNSP, on the regular grid
SP_VAR = "surface_pressure"   # single-levels variable, the alternative pressure source

# where the surface pressure of the hourly files comes from
PRESSURE_SOURCES = ("lnsp", "sp", "spectral")
DEFAULT_PRESSURE = "lnsp"

# where the model-level data comes from: mars = CDS/MARS (everything from CDS); mixed = ARCO-ERA5 (Google) for the
# model levels and their derivations (see arco.py), CDS for surface, flux and static fields
SOURCES = ("mars", "mixed")
DEFAULT_SOURCE = "mars"
GRIDDINGS = ("exact", "mars")
DEFAULT_GRIDDING = "exact"

# single-level analysis fields: name -> CDS variable name
SFC_AN_VARS = {
    "SD": "snow_depth", "MSL": "mean_sea_level_pressure", "TCC": "total_cloud_cover",
    "10U": "10m_u_component_of_wind", "10V": "10m_v_component_of_wind",
    "2T": "2m_temperature", "2D": "2m_dewpoint_temperature", "Z": "geopotential",
    "LSM": "land_sea_mask",
}
# time-invariant-ish fields, requested once per run at 00 UTC of the first date
SFC_STATIC_VARS = {
    "SDOR": "standard_deviation_of_orography", "CVL": "low_vegetation_cover",
    "CVH": "high_vegetation_cover", "FSR": "forecast_surface_roughness",
}
# accumulated fluxes (forecast fields, deaccumulated in post-processing)
SFC_FLUX_VARS = {
    "LSP": "large_scale_precipitation", "CP": "convective_precipitation",
    "SSHF": "surface_sensible_heat_flux", "EWSS": "eastward_turbulent_surface_stress",
    "NSSS": "northward_turbulent_surface_stress", "SSR": "surface_net_solar_radiation",
}

# Flux data are needed one day before and after the requested period (disaggregation stencil).
FLUX_PAD_DAYS = 1

VALID_GRIDS = (0.25, 0.5)

# paramIds (GRIB paramId == MARS id without the table suffix). Verified against ERA5 reference
# files (ecCodes names/units) except LNSP, which is only verified by the live download.
PARAM_ID = {
    "T": 130, "U": 131, "V": 132, "Q": 133, "ETADOT": 77, "CLWC": 246, "CIWC": 247, "LNSP": 152,
    "SD": 141, "MSL": 151, "TCC": 164, "10U": 165, "10V": 166, "2T": 167, "2D": 168, "Z": 129, "LSM": 172,
    "SDOR": 160, "CVL": 27, "CVH": 28, "FSR": 244, "SP": 134,
    "LSP": 142, "CP": 143, "SSHF": 146, "SSR": 176, "EWSS": 180, "NSSS": 181,
}
# order of the messages in a finished hourly file (as in the original flex_extract output):
# model-level block (from calc_etadot), fluxes, surface analysis, static fields
FLUX_ORDER = ("LSP", "CP", "SSHF", "EWSS", "NSSS", "SSR")
SFC_ORDER = ("SD", "MSL", "TCC", "10U", "10V", "2T", "2D", "Z", "LSM")
STATIC_ORDER = ("SDOR", "CVL", "CVH", "FSR")
