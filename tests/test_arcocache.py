# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""--arco-cache, with the fake bucket of test_arco.py and the fake NetCDF files of test_arconc.py."""
import io
import logging
import os
from datetime import date, datetime

import pytest

from dcfe import arco, arconc
from dcfe.__main__ import build_parser
from dcfe.arcocache import ObjectCache
from test_arco import FakeBucket, cfg

pytest.importorskip("numcodecs")
T = datetime(2023, 8, 30, 14)


class Recording(FakeBucket):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url.split("/co/")[1])
        return super().__call__(url)

    def data(self):
        """the data chunks that were downloaded (not metadata, axes)"""
        return [u for u in self.urls if u.split("/")[1] in arco.CACHED_VARS and u.split("/")[-1][0] != "."]


def build(c, bucket, cache):
    b = arco.HourBuilder(c, arco.Store(get=bucket, cache=cache))
    h = b.build(T)
    return b"".join(arco.messages(c, h, b.A, b.B))


def test_another_area_is_built_from_the_cache_alone_and_is_identical(tmp_path):
    other = dict(area=(11, 20, 9, 24))
    first, second, plain = Recording(), Recording(), Recording()
    cache = ObjectCache(tmp_path / "cache")
    build(cfg(tmp_path), first, cache)
    assert cache.added == len(first.data()) > 0 and cache.hits == 0
    again = ObjectCache(tmp_path / "cache")
    got = build(cfg(tmp_path, **other), second, again)
    assert second.data() == [] and again.hits == cache.added and again.added == 0
    assert got == build(cfg(tmp_path, **other), plain, None)
    files = [p for p in (tmp_path / "cache").rglob("*") if p.is_file()]
    assert len(files) == cache.added and not [p for p in files if p.name.startswith(".")]     # no metadata, no temporaries
    assert (tmp_path / "cache" / "co" / arco.WIND / "t").is_dir()


def test_more_levels_download_only_what_is_missing(tmp_path):
    cache = ObjectCache(tmp_path / "cache")
    build(cfg(tmp_path, levels=(8, 10)), Recording(), cache)
    more = Recording()
    build(cfg(tmp_path, levels=(6, 10)), more, cache)
    assert more.data() and {int(u.split("/")[-1].split(".")[1]) for u in more.data()} <= {5, 6}   # level index 5, 6


def test_preliminary_data_is_not_kept(tmp_path):
    cache = ObjectCache(tmp_path / "cache")
    build(cfg(tmp_path), Recording(stop="2023-08-29", stop_t="2023-09-02"), cache)
    assert cache.added == 0 and not (tmp_path / "cache").exists()


def test_a_damaged_file_is_downloaded_again(tmp_path, caplog):
    cache = ObjectCache(tmp_path / "cache")
    good = build(cfg(tmp_path), Recording(), cache)
    victim = next(p for p in (tmp_path / "cache" / "co" / arco.WIND / "t").iterdir())
    original = victim.read_bytes()
    victim.write_bytes(original[: len(original) // 2])
    repair = Recording()
    with caplog.at_level(logging.WARNING, "dcfe"):
        assert build(cfg(tmp_path), repair, ObjectCache(tmp_path / "cache")) == good
    assert len(repair.data()) == 1 and victim.read_bytes() == original and "damaged" in caplog.text


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_a_cache_that_cannot_be_written_does_not_stop_the_run(tmp_path, caplog):
    d = tmp_path / "cache"
    d.mkdir(); d.chmod(0o555)
    try:
        cache = ObjectCache(d)
        with caplog.at_level(logging.WARNING, "dcfe"):
            assert build(cfg(tmp_path), Recording(), cache)
        assert cache.added == 0 and caplog.text.count("cannot be written") == 1
    finally:
        d.chmod(0o755)


def test_files_follow_the_umask(tmp_path):
    old = os.umask(0o002)
    try:
        c = ObjectCache(tmp_path / "cache")
        c.write("co/a/0.0", b"x")
    finally:
        os.umask(old)
    assert c.path("co/a/0.0").stat().st_mode & 0o777 == 0o664


def test_netcdf_files_are_reused_for_another_area(tmp_path):
    pytest.importorskip("netCDF4")
    from test_arconc import cfg as nccfg, fake_fetch
    calls = []

    def fetch(url, dest):
        calls.append(url)
        fake_fetch(url, dest)

    def run(sub, final, **kw):
        cache = ObjectCache(tmp_path / "cache")
        dl = arconc.NcDownloader(nccfg(tmp_path / sub, end="20230830", **kw), fetch_fn=fetch, log=lambda s: None,
                                 cache=cache, final_until=final)
        while dl.step():
            pass
        return cache

    c = run("a", date(2023, 8, 30))                  # the flux padding day 31 is "preliminary" here
    assert len(calls) == 4 + 9 + 3 * 6 and c.added == 4 + 9 + 2 * 6
    calls.clear()
    c = run("b", date(2023, 8, 30), area=(10, 6, 9, 7))
    assert len(calls) == 6 and all("/08/31/" in u for u in calls) and c.hits == 4 + 9 + 2 * 6
    victim = next((tmp_path / "cache").rglob("surface.nc"))
    victim.write_bytes(b"not a netcdf file")
    calls.clear()
    c = run("c", date(2023, 8, 30))
    assert len(calls) == 6 + 1 and c.added == 1 and victim.stat().st_size > 100


def test_a_download_that_ends_early_is_refused(tmp_path, monkeypatch):
    class Short(io.BytesIO):
        headers = {"Content-Length": "10"}

    monkeypatch.setattr(arconc.urllib.request, "urlopen", lambda *a, **k: Short(b"abc"))
    monkeypatch.setattr(arconc.time, "sleep", lambda s: None)
    with pytest.raises(OSError, match="incomplete"):
        arconc.fetch("http://x/a/b/c/d.nc", tmp_path / "d.nc")
    assert not (tmp_path / "d.nc").exists()


def test_the_option_needs_an_arco_source(tmp_path):
    assert cfg(tmp_path, arco_cache=tmp_path).arco_cache == tmp_path
    with pytest.raises(ValueError, match="--arco-cache"):
        cfg(tmp_path, source="mars", arco_cache=tmp_path)
    a = build_parser().parse_args("download --start 20230830 --end 20230830 --area 61 21 59 25 --arco-cache X".split())
    assert a.arco_cache == "X"
