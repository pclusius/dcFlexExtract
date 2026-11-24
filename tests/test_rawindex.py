# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

from datetime import datetime, timedelta

import numpy as np
import pytest

eccodes = pytest.importorskip("eccodes")

from dcfe import era5, gribio
from dcfe.rawindex import RawIndex


def make_msg(param_id, valid: datetime, value, accum=False, level=0, tol="surface"):
    g = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib1")
    eccodes.codes_set(g, "paramId", param_id)
    eccodes.codes_set(g, "dataDate", int(valid.strftime("%Y%m%d")))
    eccodes.codes_set(g, "dataTime", valid.hour * 100)
    if accum:
        eccodes.codes_set(g, "stepType", "accum")
        eccodes.codes_set(g, "stepRange", "0-1")       # valid time = base + 1 h
        base = valid - timedelta(hours=1)
        eccodes.codes_set(g, "dataDate", int(base.strftime("%Y%m%d")))
        eccodes.codes_set(g, "dataTime", base.hour * 100)
    n = eccodes.codes_get(g, "numberOfDataPoints")
    eccodes.codes_set_values(g, np.full(n, float(value)))
    m = eccodes.codes_get_message(g)
    eccodes.codes_release(g)
    return m


@pytest.fixture
def raw(tmp_path):
    t0 = datetime(2023, 8, 30, 0)
    d = tmp_path / "raw"
    d.mkdir()
    with open(d / "flux.grb", "wb") as f:
        for h in range(8):
            for n in era5.FLUX_ORDER:
                # lsp: 0.002 m per hour -> 2 mm/h ; others 7200 J/m2 -> 2 W/m2
                f.write(make_msg(era5.PARAM_ID[n], t0 + timedelta(hours=h), 0.002 if n in ("LSP", "CP") else 7200., accum=True))
    with open(d / "sfc.grb", "wb") as f:
        for n in era5.SFC_ORDER:
            f.write(make_msg(era5.PARAM_ID[n], t0 + timedelta(hours=3), 1.5))
    with open(d / "static.grb", "wb") as f:
        for n in era5.STATIC_ORDER:
            f.write(make_msg(era5.PARAM_ID[n], t0, 0.5))
    return d, t0


def test_index_is_by_content_not_by_filename(raw, tmp_path):
    d, t0 = raw
    (d / "flux.grb").rename(d / "whatever_name.grb")
    ix = RawIndex.from_dir(d)
    assert len(ix.flux) == 8 * 6 and len(ix.sfc) == 9 and len(ix.static) == 4
    assert ix.missing_for(t0 + timedelta(hours=3)) == []


def test_constant_flux_gives_constant_rate_in_original_order(raw):
    d, t0 = raw
    ix = RawIndex.from_dir(d)
    msgs = ix.flux_messages(t0 + timedelta(hours=3))          # interior hour: disaggregated
    assert [ix_ for ix_ in range(6)] == list(range(len(msgs)))
    for name, m in zip(era5.FLUX_ORDER, msgs):
        v = gribio.values(m)
        assert np.allclose(v, 2.0, atol=1e-3), name
        g = eccodes.codes_new_from_message(m)
        assert eccodes.codes_get(g, "paramId") == era5.PARAM_ID[name]
        assert eccodes.codes_get(g, "dataTime") == 300 and eccodes.codes_get(g, "step") == 0


def test_missing_data_is_reported(raw):
    d, t0 = raw
    ix = RawIndex.from_dir(d)
    assert "LSP" in ix.missing_for(t0 + timedelta(hours=20))
    assert set(ix.missing_for(t0 + timedelta(hours=5))) >= set(era5.SFC_ORDER)


def test_missing_for_with_no_flux_data_at_all_reports_instead_of_crashing(tmp_path):
    from datetime import datetime
    from dcfe.rawindex import RawIndex
    ix = RawIndex.from_dir(tmp_path)                       # empty directory
    miss = ix.missing_for(datetime(2023, 8, 30, 14))
    assert "LSP" in miss and "SSR" in miss and "SD" in miss


def test_remove_raw_deletes_only_files_inside_the_period(raw):
    from datetime import date
    from dcfe.config import Config
    from dcfe.process import remove_raw
    d, t0 = raw
    (d / "sub").mkdir()
    (d / "sub" / "far.grb").write_bytes(make_msg(era5.PARAM_ID[era5.SFC_ORDER[0]], t0 + timedelta(days=9), 1.0))
    ix = RawIndex.from_dir(d)
    cfg = Config.make(t0.strftime("%Y%m%d"), t0.strftime("%Y%m%d"), (61, 21, 59, 25), 0.25, rawdir=d)
    gone = {p.name for p in remove_raw(ix, cfg)}
    assert gone == {"flux.grb", "sfc.grb", "static.grb"}      # the file 9 days later holds data of another period
    assert (d / "sub" / "far.grb").exists() and not (d / "flux.grb").exists()
