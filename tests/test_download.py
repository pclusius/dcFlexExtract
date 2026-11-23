# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

import itertools
import json
from pathlib import Path

import pytest

from dcfe import client as C
from dcfe.config import Config
from dcfe.download import DONE, FAILED, PENDING, SPLIT, SUBMITTED, Downloader, Manifest
from dcfe.gribcount import count_messages
from dcfe.requests import ONE_DAY, Request
from dcfe.requests import plan as _plan


def plan(c, spans=ONE_DAY):
    return _plan(c, spans)


def grib1_messages(n: int) -> bytes:
    body = b"GRIB" + (20).to_bytes(3, "big") + b"\x01" + b"\x00" * 8 + b"7777"
    assert len(body) == 20
    return body * n


class FakeClient:
    """Fake CDS. `behaviour(dataset, body)` may return 'too_large' / 'transient' / 'fatal' / None."""

    def __init__(self, behaviour=None, polls_until_done=1):
        self.behaviour = behaviour or (lambda ds, body: None)
        self.polls_until_done = polls_until_done
        self.jobs: dict[str, dict] = {}
        self.submitted: list[dict] = []
        self.counter = itertools.count(1)
        self.max_open = {}
        self.deleted = []
        self.fail_cleanup = False

    def _expected(self, ds, body):
        if ds == "reanalysis-era5-complete":
            params = body["param"].split("/")
            lnsp = params.count("152.128")                  # exists on level 1 only
            tok = body["levelist"].split("/")
            nlev = (int(tok[2]) - int(tok[0]) + 1) if len(tok) == 3 and tok[1] == "to" else len(tok)
            nvars = len(params) - lnsp
            d0, _, d1 = body["date"].partition("/to/")
            ndays = 1 if not d1 else (int(d1[6:]) - int(d0[6:]) + 1)     # tests stay within one month
            return (nvars * nlev + lnsp) * len(body["time"].split("/")) * ndays
        return len(body["variable"]) * len(body["time"]) * len(body["day"])

    def submit(self, dataset, body):
        kind = self.behaviour(dataset, body)
        if kind == "too_large":
            raise C.SubmitError("403 Client Error: cost limits exceeded. Your request is too large")
        if kind == "fatal":
            raise C.SubmitError("401 invalid key")
        if kind == "busy":
            raise C.SubmitError("403 Client Error: too many queued requests, limit of 20 reached")
        jid = f"job{next(self.counter)}"
        self.jobs[jid] = dict(ds=dataset, body=body, polls=0, kind=kind)
        self.submitted.append(body)
        open_now = sum(1 for j in self.jobs.values() if j["ds"] == dataset and not j.get("fetched"))
        self.max_open[dataset] = max(self.max_open.get(dataset, 0), open_now)
        return jid

    def poll(self, jid):
        j = self.jobs[jid]
        j["polls"] += 1
        if j["polls"] < self.polls_until_done:
            return C.QUEUED, ""
        if j["kind"] == "transient":
            return C.FAILED, "502 bad gateway, try later"
        return C.DONE, ""

    def download(self, jid, target):
        j = self.jobs[jid]
        j["fetched"] = True
        Path(target).write_bytes(grib1_messages(self._expected(j["ds"], j["body"])))


def _cleanup(self, jid):
    if self.fail_cleanup:
        raise RuntimeError("boom")
    self.deleted.append(jid)
    self.jobs[jid]["fetched"] = True


FakeClient.cleanup = _cleanup


def mkcfg(tmp_path, **kw):
    base = dict(start="20260105", end="20260105", area=(61, 21, 59, 25), levels=(60, 137),
                rawdir=tmp_path / "raw")
    base.update(kw)
    return Config.make(**base)


def run(dl, maxsteps=200):
    for _ in range(maxsteps):
        if dl.step():
            return
    raise AssertionError(f"not finished: {dl.summary()}")


def quiet(**kw):
    clock = itertools.count(step=10_000)            # every call jumps past any backoff delay
    return dict(log=lambda m: None, now=lambda: float(next(clock)), **{"spans": ONE_DAY, **kw})


def test_count_messages(tmp_path):
    p = tmp_path / "a.grb"
    p.write_bytes(grib1_messages(3))
    assert count_messages(str(p)) == 3
    p.write_bytes(grib1_messages(3)[:-5])
    assert count_messages(str(p)) == -1
    p.write_bytes(b"junk" * 10)
    assert count_messages(str(p)) == -1


def test_full_run_and_files_have_expected_content(tmp_path):
    cfg, fc = mkcfg(tmp_path), FakeClient()
    dl = Downloader(cfg, fc, **quiet())
    run(dl)
    assert dl.summary() == {DONE: len(plan(cfg))}
    for r in plan(cfg):
        assert count_messages(str(tmp_path / "raw" / r.relpath)) == r.expected_messages
    assert not list((tmp_path / "raw").rglob("*.part"))


def test_one_inflight_per_queue_but_two_queues_in_parallel(tmp_path):
    fc = FakeClient(polls_until_done=3)
    dl = Downloader(mkcfg(tmp_path), fc, max_inflight=1, **quiet())
    dl.step()
    states = [u["state"] for u in dl.manifest.units.values()]
    assert states.count(SUBMITTED) == 2          # one MARS + one single-levels
    run(dl)
    assert max(fc.max_open.values()) == 1


def test_resume_after_interruption_only_requests_missing_units(tmp_path):
    cfg = mkcfg(tmp_path)
    fc1 = FakeClient(polls_until_done=1)
    dl1 = Downloader(cfg, fc1, **quiet())
    for _ in range(2):                       # interrupted partway
        dl1.step()
    done1 = dl1.summary().get(DONE, 0)
    assert 0 < done1 < len(plan(cfg))
    dl1.manifest._lock.close()               # process "dies": lock released
    fc2 = fc1                                 # same (fake) server: it remembers queued jobs
    dl2 = Downloader(cfg, fc2, **quiet())
    run(dl2)
    assert dl2.summary() == {DONE: len(plan(cfg))}
    assert len(fc2.submitted) == len(plan(cfg))          # nothing requested twice


def test_reattaches_to_job_submitted_by_earlier_process(tmp_path):
    cfg = mkcfg(tmp_path)
    fc = FakeClient(polls_until_done=2)
    dl1 = Downloader(cfg, fc, **quiet())
    dl1.step()
    n_sub = len(fc.submitted)
    dl1.manifest._lock.close()
    dl2 = Downloader(cfg, fc, **quiet())     # same (fake) server, new process
    assert sum(u["state"] == SUBMITTED for u in dl2.manifest.units.values()) == n_sub
    run(dl2)
    assert len(fc.submitted) == len(plan(cfg))           # nothing was requested twice


def test_second_downloader_on_same_domain_is_refused(tmp_path):
    cfg = mkcfg(tmp_path)
    first = Downloader(cfg, FakeClient(), **quiet())
    with pytest.raises(RuntimeError, match="another dcfe download"):
        Downloader(cfg, FakeClient(), **quiet())


def test_too_large_is_split_by_days_area_then_levels_and_covers_everything_once(tmp_path):
    def cells(body):
        n, w, s_, e = (float(x) for x in body["area"].split("/"))
        return int(round((n - s_) / 0.25) + 1) * int(round((e - w) / 0.25) + 1)

    def size(body):                                        # CDS-like cost: number of values requested
        d0, _, d1 = body["date"].partition("/to/")
        ndays = 1 if not d1 else int(d1[6:]) - int(d0[6:]) + 1
        a, _, b = body["levelist"].partition("/to/")
        nlev = int(b) - int(a) + 1 if b else len(body["levelist"].split("/"))
        return len(body["param"].split("/")) * nlev * len(body["time"].split("/")) * ndays * cells(body)

    beh = lambda ds, body: "too_large" if ds == "reanalysis-era5-complete" and size(body) > 150_000 else None
    cfg, fc = mkcfg(tmp_path, pressure="spectral"), FakeClient(beh)
    dl = Downloader(cfg, fc, split_order=("days", "area", "levels"), **quiet())
    run(dl)
    assert dl.summary().get(FAILED, 0) == 0
    orig = next(r for r in plan(cfg) if r.group == "ML")
    leaves = [Request.from_dict(u["request"]) for k, u in dl.manifest.units.items()
              if "/ML_" in k and u["state"] == DONE]
    assert all(l.tiled for l in leaves) and max(l.tile_depth for l in leaves) == 3      # 1/8 of the area ...
    assert any(l.nlevels < orig.nlevels for l in leaves)                                 # ... then the levels
    assert all(len(l.vars) == len(orig.vars) and len(l.hours) == 24 for l in leaves)    # variables/hours untouched
    # every (grid point, level) exactly once: the tiles partition the area, the level parts partition the levels
    pts = lambda r: int(round((r.area[0] - r.area[2]) / 0.25) + 1) * int(round((r.area[3] - r.area[1]) / 0.25) + 1)
    assert sum(pts(l) * l.nlevels for l in leaves) == pts(orig) * orig.nlevels
    assert not (tmp_path / "raw" / orig.relpath).exists()      # parent never downloaded as one file


def test_unsplittable_too_large_fails_cleanly(tmp_path):
    cfg = mkcfg(tmp_path, area=(60.25, 22, 60, 22.25))       # 2x2 grid points
    beh = lambda ds, b: "too_large" if "low_vegetation_cover" in b.get("variable", []) else None
    dl = Downloader(cfg, FakeClient(beh), **quiet())
    run(dl)
    assert dl.summary().get(FAILED, 0) == 4 and dl.finished()   # CVL at each of the 4 points
    assert "cannot be split" in next(u for u in dl.manifest.units.values() if u["state"] == FAILED)["error"]


def test_fatal_error_fails_unit_without_retries(tmp_path):
    fc = FakeClient(lambda ds, b: "fatal")
    dl = Downloader(mkcfg(tmp_path), fc, **quiet())
    run(dl)
    assert dl.summary() == {FAILED: len(plan(mkcfg(tmp_path)))}
    assert fc.submitted == []


def test_transient_errors_are_retried_then_give_up(tmp_path):
    calls = {"n": 0}

    def beh(ds, body):
        calls["n"] += 1
        return "transient" if calls["n"] <= 2 else None          # first two jobs fail once
    dl = Downloader(mkcfg(tmp_path), FakeClient(beh), max_attempts=3, **quiet())
    run(dl)
    assert dl.summary() == {DONE: len(plan(mkcfg(tmp_path)))}
    dl2 = Downloader(mkcfg(tmp_path / "x"), FakeClient(lambda ds, b: "transient"), max_attempts=2, **quiet())
    run(dl2)
    assert dl2.summary() == {FAILED: len(plan(mkcfg(tmp_path)))}


def test_truncated_download_is_rejected_and_retried(tmp_path):
    class Trunc(FakeClient):
        first = True

        def download(self, jid, target):
            super().download(jid, target)
            if Trunc.first:
                Trunc.first = False
                p = Path(target)
                p.write_bytes(p.read_bytes()[:-7])
    dl = Downloader(mkcfg(tmp_path), Trunc(), **quiet())
    run(dl)
    assert dl.summary() == {DONE: len(plan(mkcfg(tmp_path)))}


def test_manually_placed_file_is_adopted(tmp_path):
    cfg = mkcfg(tmp_path)
    r = next(r for r in plan(cfg) if r.group == "SL")
    p = tmp_path / "raw" / r.relpath
    p.parent.mkdir(parents=True)
    p.write_bytes(grib1_messages(r.expected_messages))
    fc = FakeClient()
    dl = Downloader(cfg, fc, **quiet())
    run(dl)
    assert len(fc.submitted) == len(plan(cfg)) - 1


def test_finished_jobs_are_deleted_at_cds_only_after_verified_download(tmp_path):
    fc = FakeClient()
    dl = Downloader(mkcfg(tmp_path), fc, **quiet())
    run(dl)
    assert sorted(fc.deleted) == sorted(fc.jobs)           # every job cleaned up


def test_corrupt_download_does_not_delete_job_and_cleanup_errors_are_harmless(tmp_path):
    class Trunc(FakeClient):
        first = True

        def download(self, jid, target):
            super().download(jid, target)
            if Trunc.first:
                Trunc.first = False
                Path(target).write_bytes(b"GRIB")
    fc = Trunc()
    dl = Downloader(mkcfg(tmp_path), fc, **quiet())
    dl.step(); dl.step()
    bad = [k for k, u in dl.manifest.units.items() if u.get("error")]
    assert bad                                              # the corrupt one was rejected, its job not deleted
    assert len(fc.deleted) < len(fc.jobs)
    fc2 = FakeClient()
    fc2.fail_cleanup = True
    dl2 = Downloader(mkcfg(tmp_path / "y"), fc2, **quiet())
    run(dl2)
    assert dl2.summary() == {DONE: len(plan(mkcfg(tmp_path)))}


def test_busy_error_waits_and_does_not_burn_attempts(tmp_path):
    state = {"n": 0}

    def beh(ds, body):
        state["n"] += 1
        return "busy" if state["n"] <= 3 else None
    cfg = mkcfg(tmp_path)
    dl = Downloader(cfg, FakeClient(beh), max_attempts=1, **quiet())      # one attempt only
    run(dl)
    assert dl.summary() == {DONE: len(plan(cfg))}
    assert C.classify_error("403 Client Error: cost limits exceeded, too large") == C.TOO_LARGE
    assert C.classify_error("401 invalid key") == C.FATAL


def test_span_limit_is_learned_and_applied_to_pending_units(tmp_path):
    """CDS refuses ML requests longer than 2 days: the downloader halves, remembers, and does not
    send the other long requests at all."""
    c = mkcfg(tmp_path, start="20230801", end="20230816")
    def beh(ds, body):
        if ds == "reanalysis-era5-complete" and "/to/" in body["date"]:
            d0, d1 = body["date"].split("/to/")
            if int(d1[6:]) - int(d0[6:]) + 1 > 2:
                return "too_large"
    fc = FakeClient(beh)
    dl = Downloader(c, fc, max_inflight=50, **quiet(spans={"ML": 8, "LNSP": 16, "SL": 16, "FLUX": 18}))
    run(dl)
    assert dl.summary().get(FAILED, 0) == 0
    # 8-day refused -> limit 7; its 4-day halves refused -> limit 3; then 2-day units are accepted
    assert dl.manifest.meta["max_days"]["ML"] == 3
    ml_spans = {Request.from_dict(u["request"]).ndays for u in dl.leaves().values() if u["request"]["group"] == "ML"}
    assert max(ml_spans) == 2
    sent_ml = [b for b in fc.submitted if "levelist" in b]
    too_long = [b for b in sent_ml if "/to/" in b["date"] and int(b["date"].split("/to/")[1][6:]) - int(b["date"].split("/to/")[0][6:]) + 1 > 2]
    assert len(too_long) <= 4          # a few refused attempts, never repeated for every pending unit
    total_ml_msgs = sum(Request.from_dict(u["request"]).expected_messages for u in dl.leaves().values() if u["request"]["group"] == "ML")
    assert total_ml_msgs == 16 * 24 * (7 * 79 + 1)      # default pressure=lnsp: level 1 + gridded LNSP ride along


def test_manifest_remembers_learned_limit_across_restarts(tmp_path):
    c = mkcfg(tmp_path, start="20230801", end="20230804")
    beh = lambda ds, body: "too_large" if ds == "reanalysis-era5-complete" and "/to/" in body["date"] else None
    dl = Downloader(c, FakeClient(beh), **quiet(spans={"ML": 4}))
    run(dl)
    assert dl.manifest.meta["max_days"]["ML"] == 1            # every multi-day request is refused
    dl.manifest._lock.close()
    dl2 = Downloader(c, FakeClient(), **quiet(spans={"ML": 4}))
    assert dl2.manifest.meta["max_days"]["ML"] == 1


def test_per_queue_limits_single_levels_parallel_mars_limited(tmp_path):
    cfg = mkcfg(tmp_path, start="20230801", end="20230806")
    fc = FakeClient(polls_until_done=4)
    dl = Downloader(cfg, fc, max_inflight={"reanalysis-era5-complete": 2, "reanalysis-era5-single-levels": 3},
                    **quiet(spans=ONE_DAY))
    dl.step()
    open_ = {}
    for rp, u in dl.manifest.units.items():
        if u["state"] == SUBMITTED:
            q = dl._req(rp).queue
            open_[q] = open_.get(q, 0) + 1
    assert open_ == {"reanalysis-era5-complete": 2, "reanalysis-era5-single-levels": 3}
    run(dl)
    assert fc.max_open["reanalysis-era5-complete"] == 2 and fc.max_open["reanalysis-era5-single-levels"] == 3
    assert dl.summary() == {DONE: len(plan(cfg))}


def test_default_limits():
    from dcfe.download import DEFAULT_INFLIGHT
    assert DEFAULT_INFLIGHT == {"reanalysis-era5-complete": 2, "reanalysis-era5-single-levels": 3}


def test_split_order_without_area_never_makes_tiles_and_without_days_never_learns_a_span(tmp_path):
    cfg = mkcfg(tmp_path, start="20260105", end="20260106", pressure="spectral")
    def beh(ds, body):                                       # a "size" rule on levels x days only
        if ds != "reanalysis-era5-complete" or body["param"] == "152.128":
            return None
        a, _, b = body["levelist"].partition("/to/")
        nlev = int(b) - int(a) + 1 if b else 1
        d0, _, d1 = body["date"].partition("/to/")
        return "too_large" if nlev * (1 if not d1 else 2) > 40 else None
    dl = Downloader(cfg, FakeClient(beh), split_order=("levels",), **quiet(spans={"ML": 2}))
    run(dl)
    assert dl.summary().get(FAILED, 0) == 0
    ml = [Request.from_dict(u["request"]) for k, u in dl.manifest.units.items() if "/ML_" in k and u["state"] == DONE]
    assert ml and not any(r.tiled for r in ml) and all(r.ndays == 2 for r in ml)       # days stayed together
    assert all(r.nlevels * 2 <= 40 for r in ml) and sum(r.nlevels for r in ml) == 78
    assert "ML" not in dl.manifest.meta["max_days"]                                     # no day-span was learned


def test_variables_are_dropped_one_at_a_time_and_leftovers_go_out_together(tmp_path):
    """CDS accepts at most 4 variables: 7 refused -> 6 refused -> 5 refused -> 4 accepted, and the three dropped
    variables are requested together (one request), not one by one."""
    asked = []

    def beh(ds, b):
        if ds == "reanalysis-era5-complete" and b["param"] != "152.128":      # (not the spectral LNSP request)
            asked.append(len(b["param"].split("/")))
            return "too_large" if asked[-1] > 4 else None
    cfg, fc = mkcfg(tmp_path, pressure="spectral"), FakeClient(beh)
    dl = Downloader(cfg, fc, split_order=("variables",), **quiet())
    run(dl)
    assert dl.summary().get(FAILED, 0) == 0
    ml = [Request.from_dict(u["request"]) for k, u in dl.manifest.units.items() if "/ML_" in k and u["state"] == DONE]
    assert sorted(len(r.vars) for r in ml) == [3, 4]                       # two requests, not 4 + 1 + 1 + 1
    assert sorted(v for r in ml for v in r.vars) == sorted(next(r for r in plan(cfg) if r.group == "ML").vars)
    assert asked[:3] == [7, 6, 5] and sorted(asked[3:]) == [3, 4]          # three free refusals, two real requests


def test_a_request_is_never_rebuilt_from_leftovers_when_it_was_already_refused(tmp_path):
    """CVL alone is refused: merging leftovers must not recreate a refused combination, and nothing may be lost"""
    cfg = mkcfg(tmp_path, area=(60.25, 22, 60, 22.25))
    beh = lambda ds, b: "too_large" if "low_vegetation_cover" in b.get("variable", []) else None
    dl = Downloader(cfg, FakeClient(beh), split_order=("variables",), **quiet())
    run(dl)
    st = [Request.from_dict(u["request"]) for k, u in dl.manifest.units.items() if "/STATIC_" in k and u["state"] == DONE]
    got = sorted(v for r in st for v in r.vars)
    assert got == ["CVH", "FSR", "SDOR"]                                   # everything but the refused CVL, each once
    failed = [k for k, u in dl.manifest.units.items() if u["state"] == FAILED]
    assert len(failed) == 1 and "STATIC_CVL" in failed[0]


def test_a_refusal_chain_is_resolved_in_one_settle_without_waiting(tmp_path):
    """the parts of a split are submitted at once; nothing sleeps (the test would hang with a 1 h poll if it did)"""
    asked = []

    def beh(ds, b):
        if ds == "reanalysis-era5-complete" and b["param"] != "152.128":
            asked.append(len(b["param"].split("/")))
            return "too_large" if asked[-1] > 4 else None
    fc = FakeClient(beh, polls_until_done=1)
    dl = Downloader(mkcfg(tmp_path, pressure="spectral"), fc, split_order=("variables",), poll_seconds=3600, **quiet())
    dl.settle()
    assert asked[:3] == [7, 6, 5] and sorted(asked[3:]) == [3, 4]      # the whole ladder in one call
    # every submit happened in that one call; only the (instant) fake jobs remained to be polled and fetched
    assert len(fc.submitted) >= 2


def test_queued_jobs_are_polled_no_more_often_than_poll_seconds(tmp_path):
    t = [0.0]
    fc = FakeClient(polls_until_done=10 ** 9)                       # stays queued forever
    cfg = mkcfg(tmp_path)
    dl = Downloader(cfg, fc, poll_seconds=60, log=lambda m: None, now=lambda: t[0], spans=ONE_DAY)
    dl.settle()                                                      # submits the first jobs
    n_jobs = sum(1 for u in dl.manifest.units.values() if u["state"] == SUBMITTED)
    assert n_jobs > 0
    total = lambda: sum(j["polls"] for j in fc.jobs.values())
    for _ in range(50):                                              # many passes within the same minute
        t[0] += 0.5
        dl.settle()
    assert total() <= n_jobs                                         # each job asked about once, not 50 times
    t[0] += 61
    dl.settle()
    assert total() >= 2 * n_jobs - 1 or total() > n_jobs             # a minute later they are asked again


def test_run_does_other_work_instead_of_sleeping_while_cds_is_queued(tmp_path):
    """`idle` (the ARCO hours of --source mixed) runs while CDS jobs are queued, the loop only ends when both are done"""
    cfg, fc = mkcfg(tmp_path, source="mixed"), FakeClient(polls_until_done=3)
    todo = [1, 2, 3, 4, 5]
    log = []

    def idle():
        if not todo:
            return False
        log.append((todo.pop(0), dict(Downloader.summary(dl))))
        return True
    sleeps = []
    dl = Downloader(cfg, fc, poll_seconds=60, **quiet())
    import dcfe.download as D
    orig, D.time.sleep = D.time.sleep, sleeps.append
    try:
        assert dl.run(idle=idle) is True
    finally:
        D.time.sleep = orig
    assert not todo and len(log) == 5                       # all the other work was done
    assert dl.summary() == {DONE: len(plan(cfg))}           # and all CDS units
    assert not any(r.group == "ML" for r in plan(cfg))      # a mixed run asks MARS for nothing
    assert sleeps == [] or len(sleeps) < 3                  # busy with other work, not sleeping
