# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""arconc.py with small fake NetCDF files (lat 12..8, lon 4..10 at 0.25 degrees) instead of the global ones."""
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from dcfe import arconc, era5, gribio
from dcfe.config import Config

nc4 = pytest.importorskip("netCDF4")

LAT = np.arange(13, 7, -0.25); LON = np.arange(3, 11, 0.25)


def fake_fetch(url, dest):
    """a one-day file; the value encodes the variable, the hour and the point, so the cut can be checked"""
    var, day = url.split("/")[-2], url.split("/")[-5:-2]
    d0 = datetime(int(day[0]), int(day[1]), int(day[2]))
    with nc4.Dataset(dest, "w") as ds:
        ds.createDimension("valid_time", 24); ds.createDimension("latitude", len(LAT)); ds.createDimension("longitude", len(LON))
        t = ds.createVariable("valid_time", "i8", ("valid_time",)); t.units = "seconds since 1970-01-01"
        t[:] = [int((d0 + timedelta(hours=h) - datetime(1970, 1, 1)).total_seconds()) for h in range(24)]
        ds.createVariable("latitude", "f8", ("latitude",))[:] = LAT
        ds.createVariable("longitude", "f8", ("longitude",))[:] = LON
        v = ds.createVariable("xx", "f4", ("valid_time", "latitude", "longitude"))
        k = sorted(set(era5.SFC_FLUX_VARS.values()) | set(era5.SFC_AN_VARS.values()) | set(era5.SFC_STATIC_VARS.values())).index(var)
        v[:] = (1000 * k + np.arange(24)[:, None, None] + 0.001 * np.arange(len(LAT))[None, :, None] + 1e-6 * np.arange(len(LON))[None, None, :])


def cfg(tmp_path, **kw):
    a = dict(start="20230830", end="20230831", area=(11, 5, 9.5, 8), grid=0.25, levels=(60, 137), rawdir=tmp_path / "raw", source="arco")
    a.update(kw)
    return Config.make(a.pop("start"), a.pop("end"), a.pop("area"), a.pop("grid"), a.pop("levels"), **a)


def test_arco_source_needs_no_cds_requests(tmp_path):
    from dcfe import requests as rq
    assert rq.plan(cfg(tmp_path)) == []
    with pytest.raises(ValueError, match="pressure"):
        cfg(tmp_path, pressure="sp")


def test_tasks_cover_the_run_and_the_flux_padding_days(tmp_path):
    t = arconc.NcDownloader(cfg(tmp_path), fetch_fn=fake_fetch).tasks
    assert [x for x in t if x[0] == "sl"] == [("sl", date(2023, 8, 30)), ("sl", date(2023, 8, 31))]
    assert [d for k, d in t if k == "flux"] == [date(2023, 8, 29), date(2023, 8, 30), date(2023, 8, 31), date(2023, 9, 1)]
    assert [x for x in t if x[0] == "static"] == [("static", date(2023, 8, 30))]


def test_files_have_the_form_of_the_mars_raw_data_and_are_cut_to_the_box(tmp_path):
    c = cfg(tmp_path)
    dl = arconc.NcDownloader(c, fetch_fn=fake_fetch, log=lambda s: None)
    while dl.step():
        pass
    assert dl.summary() == {"nc_tasks_done": 7, "nc_tasks_todo": 0}
    assert not (tmp_path / "raw" / c.domain_tag / ".nc").exists()                    # scratch NetCDF files are removed
    from dcfe.rawindex import RawIndex
    ix = RawIndex.from_dir(c.rawdir)
    assert len(ix.flux) == 6 * 24 * 4 and len(ix.sfc) == 9 * 24 * 2 and len(ix.static) == 4
    pid, t = era5.PARAM_ID["SSHF"], datetime(2023, 8, 30, 14)
    ref = ix.flux[(pid, t)]
    assert ref.step_type == "accum" and ref.edition == 1 and ref.valid == t and ref.nvalues == 7 * 13
    v = gribio.values(gribio.read(ref)).reshape(7, 13)
    names = sorted(set(era5.SFC_FLUX_VARS.values()) | set(era5.SFC_AN_VARS.values()) | set(era5.SFC_STATIC_VARS.values()))
    k = names.index(era5.SFC_FLUX_VARS["SSHF"])
    # row 0 of the box is 11 N = row 8 of the file, column 0 is 5 E = column 8
    assert v[0, 0] == pytest.approx(1000 * k + 14 + 0.001 * 8 + 1e-6 * 8, abs=1e-3)
    assert gribio.get_int(gribio.read(ref), "dataTime") == 1300                     # accumulation of 13-14 UTC
    sd = ix.sfc[(era5.PARAM_ID["2T"], t)]
    assert sd.step_type == "instant" and sd.valid == t


def test_a_finished_task_is_not_repeated(tmp_path):
    calls = []
    dl = arconc.NcDownloader(cfg(tmp_path, end="20230830"), fetch_fn=lambda u, d: (calls.append(u), fake_fetch(u, d)),
                             log=lambda s: None)
    assert dl.step()
    n = len(calls)
    dl2 = arconc.NcDownloader(cfg(tmp_path, end="20230830"), fetch_fn=fake_fetch, log=lambda s: None)
    assert dl2.summary()["nc_tasks_done"] == 1 and n == 6


def test_points_off_the_file_grid_are_refused(tmp_path):
    fake_fetch("x/2023/08/30/10m_u_component_of_wind/surface.nc", tmp_path / "a.nc")
    with pytest.raises(ValueError, match="0.25"):
        arconc.read_box(tmp_path / "a.nc", np.array([11.1]), np.array([5.0]))


def test_missing_day_stops_the_run(tmp_path):
    with pytest.raises(arconc.ArcoRawUnavailable, match="01-09-2023"):
        arconc.check_range(cfg(tmp_path), exists_fn=lambda u: "/09/01/" not in u)
