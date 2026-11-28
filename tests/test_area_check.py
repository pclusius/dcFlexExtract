# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""process uses only data on the grid of its domain and refuses an outdir that holds another area. The two areas
have the same number of points, so counting values cannot tell them apart."""
from datetime import datetime

import numpy as np
import pytest

from dcfe import gribio
from dcfe.__main__ import main
from dcfe.config import Config
from dcfe.process import out_name, outputs_of_another_area, remove_raw
from dcfe.rawindex import RawIndex

NORTH, SOUTH = (60, 20, 50, 30), (-50, 20, -60, 30)


def cfg(area, **kw):
    return Config.make("20220101", "20220101", area, 0.25, **kw)


def message(area, value=1.0, param=167):
    import eccodes as ec
    c = cfg(area)
    g = ec.codes_grib_new_from_samples("regular_ll_sfc_grib1")
    try:
        ni, nj = c.shape
        for k, v in (("paramId", param), ("dataDate", 20220101), ("dataTime", 0), ("Ni", ni), ("Nj", nj),
                     ("latitudeOfFirstGridPointInDegrees", float(c.north)), ("latitudeOfLastGridPointInDegrees", float(c.south)),
                     ("longitudeOfFirstGridPointInDegrees", float(c.west % 360)), ("longitudeOfLastGridPointInDegrees", float(c.east % 360)),
                     ("iDirectionIncrementInDegrees", 0.25), ("jDirectionIncrementInDegrees", 0.25)):
            ec.codes_set(g, k, v)
        ec.codes_set_values(g, np.full(ni * nj, value))
        return ec.codes_get_message(g)
    finally:
        ec.codes_release(g)


@pytest.mark.parametrize("area", [NORTH, SOUTH, (10, 170, 0, -170), (10, -20, 0, -10)])
def test_a_domain_covers_its_own_grid(tmp_path, area):
    (tmp_path / "a.grb").write_bytes(message(area))
    assert cfg(area).covers(gribio.scan(tmp_path / "a.grb")[0].area)


def test_covers_rejects_a_shift_of_one_step_but_allows_less(tmp_path):
    c = cfg(NORTH)
    n, w, s, e, ni, nj = 60.0, 20.0, 50.0, 30.0, *c.shape
    assert c.shape == cfg(SOUTH).shape and not c.covers((-50.0, w, -60.0, e, ni, nj))
    assert not c.covers((n + 0.25, w, s + 0.25, e, ni, nj)) and not c.covers((n, w + 0.25, s, e + 0.25, ni, nj))
    assert c.covers((n - 0.1, w + 0.1, s - 0.1, e + 0.1, ni, nj))
    assert not c.covers((n, w, s, e, ni + 1, nj)) and not c.covers(None)


def two_areas(tmp_path):
    (tmp_path / "north.grb").write_bytes(message(NORTH, 1.0))
    (tmp_path / "south.grb").write_bytes(message(SOUTH, 2.0))


@pytest.mark.parametrize("area,value", [(NORTH, 1.0), (SOUTH, 2.0)])
def test_index_takes_each_field_from_its_own_area(tmp_path, area, value):
    two_areas(tmp_path)
    ix = RawIndex.from_dir(tmp_path, cfg(area))
    ref = ix.sfc[(167, datetime(2022, 1, 1))]
    assert gribio.values(gribio.read(ref))[0] == value and sum(ix.foreign.values()) == 1


def test_remove_raw_keeps_files_with_data_of_another_area(tmp_path):
    two_areas(tmp_path)
    (tmp_path / "both.grb").write_bytes(message(NORTH, param=165) + message(SOUTH, param=166))
    c = cfg(NORTH, rawdir=tmp_path)
    gone = remove_raw(RawIndex.from_dir(tmp_path, c), c)
    assert [p.name for p in gone] == ["north.grb"]
    assert (tmp_path / "south.grb").exists() and (tmp_path / "both.grb").exists()


def args(tmp_path, area, *more):
    return ["process", "--start", "20220101", "--end", "20220101", "--area", *map(str, area),
            "--outdir", str(tmp_path / "out"), *more]


@pytest.mark.parametrize("more", [(), ("--check",), ("--remove-raw",)])
def test_process_stops_at_output_of_another_area(tmp_path, more, capsys):
    out = tmp_path / "out"
    (out / "raw").mkdir(parents=True)
    (out / "raw" / "south.grb").write_bytes(message(SOUTH))
    name = out_name(cfg(NORTH), datetime(2022, 1, 1, 5))
    (out / name).write_bytes(message(NORTH))
    assert outputs_of_another_area(cfg(NORTH), out) == []
    assert [n for n, _ in outputs_of_another_area(cfg(SOUTH), out)] == [name]
    with pytest.raises(SystemExit) as e:
        main(args(tmp_path, SOUTH, *more))
    assert e.value.code == 2 and "separate --outdir" in "".join(capsys.readouterr())
    assert sorted(p.name for p in out.iterdir()) == sorted(["raw", "logs", name]) and (out / "raw" / "south.grb").exists()


def test_process_stops_when_all_raw_data_is_for_another_area(tmp_path, capsys):
    (tmp_path / "out" / "raw").mkdir(parents=True)
    (tmp_path / "out" / "raw" / "south.grb").write_bytes(message(SOUTH))
    with pytest.raises(SystemExit) as e:
        main(args(tmp_path, NORTH))
    assert e.value.code == 2 and "no raw data is on the grid" in "".join(capsys.readouterr())
