# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""Resumable ERA5 downloader: a persistent state machine over the units from requests.plan().

Unit states (stored in <rawdir>/<domain>/manifest.json, written atomically):
    pending -> submitted(job_id) -> done            file exists, message count verified
                          \\-> split (children)      CDS said "too large"; children replace it
                          \\-> failed                fatal error or too many attempts
Nothing is ever deleted. A restart re-attaches to jobs that are still queued at CDS (by job id)
and skips every unit whose verified file is already on disk. At most `max_inflight` jobs per
CDS queue are open at a time (MARS and single-levels are separate queues).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import client as C
from . import era5
from .config import Config
from .gribcount import count_messages
from .requests import _GROUP_VARS, SPLIT_ORDER, Request, plan

# Open CDS jobs per queue. Single-level data does not use the MARS archive, so a few can run in
# parallel; for MARS the user may run only one request at a time (the others wait in line).
DEFAULT_INFLIGHT = {era5.DATASET_MARS: 2, era5.DATASET_SFC: 3}

_detail = logging.getLogger("dcfe").debug       # file-only detail (see runlog.py)

PENDING, SUBMITTED, DONE, SPLIT, FAILED = "pending", "submitted", "done", "split", "failed"


class Manifest:
    """JSON state file with an exclusive lock (only one downloader per domain at a time)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = open(path.with_suffix(".lock"), "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"another dcfe download is running for {path.parent}") from None
        self.units: dict[str, dict] = {}
        self.meta: dict = {"max_days": {}}          # learned: largest day-span CDS accepted per group
        if path.exists():
            d = json.loads(path.read_text())
            self.units = d["units"]
            self.meta = d.get("meta", self.meta)

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"units": self.units, "meta": self.meta}, indent=1, sort_keys=True))
        os.replace(tmp, self.path)

    def add(self, req: Request):
        u = self.units.setdefault(req.relpath, {"state": PENDING, "attempts": 0, "request": req.to_dict()})
        if u["state"] == SPLIT and not u.get("refused"):
            # it was only merged into a larger request earlier and is needed as such again: revive it
            u.update(state=PENDING, children=[], job_id=None, attempts=0)


# canonical order of the variables of a group (LNSP last), used when leftover variables are merged
_VAR_ORDER = {g: v + (("LNSP",) if g == "ML" else ()) for g, v in _GROUP_VARS.items()}


@dataclass
class Downloader:
    cfg: Config
    client: C.Client
    max_inflight: object = None              # int (all queues) or {dataset: int}; None = DEFAULT_INFLIGHT
    max_attempts: int = 5
    poll_seconds: float = 60.0
    spans: Optional[dict] = None             # days per request to try first, by group (see requests.DEFAULT_SPANS)
    split_order: tuple = SPLIT_ORDER         # kinds of split to try after a refusal, first to last
    log: Callable[[str], None] = print
    now: Callable[[], float] = time.time
    manifest: Optional[Manifest] = field(default=None, init=False)
    events: int = field(default=0, init=False)        # submits, splits, fetches, failures so far
    _polled: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        self.root = Path(self.cfg.rawdir)
        self.manifest = Manifest(self.root / self.cfg.domain_tag / "manifest.json")
        for r in plan(self.cfg, self.spans):
            self.manifest.add(r)
        self._adopt_existing_files()
        self.manifest.save()

    def _limit(self, queue: str) -> int:
        if isinstance(self.max_inflight, int):
            return self.max_inflight
        return {**DEFAULT_INFLIGHT, **(self.max_inflight or {})}[queue]

    # ---- helpers ----------------------------------------------------------------------
    def _path(self, relpath: str) -> Path:
        return self.root / relpath

    def _req(self, relpath: str) -> Request:
        return Request.from_dict(self.manifest.units[relpath]["request"])

    def _file_ok(self, req: Request) -> bool:
        p = self._path(req.relpath)
        return p.exists() and count_messages(str(p)) == req.expected_messages

    def _adopt_existing_files(self):
        """Files already on disk (earlier run, or put there by hand) count as done."""
        for rp, u in self.manifest.units.items():
            if u["state"] in (PENDING, FAILED, SUBMITTED) and self._file_ok(self._req(rp)):
                u["state"] = DONE
                _detail("ADOPT   %s already on disk and verified (%d messages)", rp, self._req(rp).expected_messages)

    def leaves(self) -> dict[str, dict]:
        """Units that actually have to be downloaded (split parents excluded)."""
        return {k: v for k, v in self.manifest.units.items() if v["state"] != SPLIT}

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for u in self.leaves().values():
            out[u["state"]] = out.get(u["state"], 0) + 1
        return out

    def finished(self) -> bool:
        return all(u["state"] in (DONE, FAILED) for u in self.leaves().values())

    # ---- state transitions --------------------------------------------------------------
    def _learn_span(self, req: Request):
        """CDS refused `req.ndays` days: remember that this group takes at most one day less."""
        lim = self.manifest.meta["max_days"]
        lim[req.group] = min(lim.get(req.group, req.ndays), req.ndays - 1)
        self.log(f"LEARNED {req.group}: at most {lim[req.group]} day(s) per request")

    def _presplit(self, rp: str):
        """Split a pending unit locally (no request sent) while it is longer than the learned limit."""
        req = self._req(rp)
        lim = self.manifest.meta["max_days"].get(req.group)
        if not lim or req.ndays <= lim or "days" not in self.split_order:
            return
        self._split(rp, f"above learned limit of {lim} days", learn=False)
        for kid in self.manifest.units[rp].get("children", []):
            if self.manifest.units[kid]["state"] == PENDING:
                self._presplit(kid)

    def _split(self, rp: str, reason: str, learn: bool = True):
        req = self._req(rp)
        if learn and req.ndays > 1 and "days" in self.split_order:
            self._learn_span(req)
        kids = req.split(self.split_order)
        u = self.manifest.units[rp]
        if not kids:
            u.update(state=FAILED, error=f"too large and cannot be split further: {reason}")
            self.log(f"FAILED  {rp}: {u['error']}")
            return
        self.events += 1
        u.update(state=SPLIT, children=[k.relpath for k in kids], job_id=None)
        if learn:
            u["refused"] = True                    # CDS itself refused this one (not just split locally)
        for k in kids:
            self.manifest.add(k)
            if self._file_ok(k):
                self.manifest.units[k.relpath]["state"] = DONE
        self.log(f"SPLIT   {rp} -> {len(kids)} parts ({reason[:80]})")
        if len(kids) == 2 and kids[0].vars == req.vars[:-1] and kids[1].vars == req.vars[-1:]:
            self._merge_leftovers(kids[1])

    @staticmethod
    def _family(r: Request):
        """what must be equal for two units to be requested together: everything but the variables"""
        return (r.group, r.day, r.ndays, r.levels, r.hours, r.area, r.grid, r.tiled, r.tile_depth)

    def _merge_leftovers(self, left: Request):
        """`left` is the variable dropped from a refused request. Dropped variables of the same family that
        are still waiting go out together as ONE request (each request costs a queue wait); if that is refused
        too, it is peeled again by the same rule."""
        m = self.manifest
        m.units[left.relpath]["leftover"] = True
        others = []
        for k, u in list(m.units.items()):
            if k != left.relpath and u["state"] == PENDING and u.get("leftover"):
                r = self._req(k)
                if self._family(r) == self._family(left):
                    others.append(r)
        if not others:
            return
        order = {v: i for i, v in enumerate(_VAR_ORDER[left.group])}
        names = sorted({v for r in others + [left] for v in r.vars}, key=lambda v: order.get(v, 99))
        merged = Request(**{**left.__dict__, "vars": tuple(names)})
        # never rebuild what CDS has already refused (or anything containing it), and never an existing unit
        for k, u in m.units.items():
            if u.get("refused") and self._family(self._req(k)) == self._family(left) \
                    and set(self._req(k).vars) <= set(names):
                return
        if merged.relpath in m.units and m.units[merged.relpath]["state"] != SPLIT:
            return
        for r in others + [left]:
            m.units[r.relpath].update(state=SPLIT, children=[merged.relpath], job_id=None)
        m.add(merged)
        m.units[merged.relpath]["leftover"] = True
        if self._file_ok(merged):
            m.units[merged.relpath]["state"] = DONE
        self.log(f"MERGE   leftover variables {','.join(names)} -> {merged.relpath}")

    def _retry_or_fail(self, rp: str, msg: str):
        u = self.manifest.units[rp]
        u["attempts"] += 1
        u["job_id"] = None
        if u["attempts"] >= self.max_attempts:
            u.update(state=FAILED, error=msg)
            self.log(f"FAILED  {rp}: {msg[:200]}")
        else:
            u.update(state=PENDING, error=msg, not_before=self.now() + min(60 * 2 ** u["attempts"], 1800))
            self.log(f"RETRY   {rp} (attempt {u['attempts']}): {msg[:120]}")

    def _handle_failure(self, rp: str, msg: str):
        kind = C.classify_error(msg)
        if kind == C.BUSY:
            # CDS wants us to slow down: back off, do not count it as a failed attempt
            u = self.manifest.units[rp]
            u.update(state=PENDING, job_id=None, error=msg, not_before=self.now() + 600)
            self.log(f"BUSY    {rp}: {msg[:100]} (retry in 10 min)")
        elif kind == C.TOO_LARGE:
            self._split(rp, msg)
        elif kind == C.FATAL:
            self.manifest.units[rp].update(state=FAILED, error=msg)
            self.log(f"FAILED  {rp}: {msg[:200]}")
        else:
            self._retry_or_fail(rp, msg)

    def _fetch(self, rp: str):
        u, req = self.manifest.units[rp], self._req(rp)
        final = self._path(rp)
        part = final.with_name(final.name + ".part")
        final.parent.mkdir(parents=True, exist_ok=True)
        self.client.download(u["job_id"], str(part))
        n = count_messages(str(part))
        if n != req.expected_messages:
            self.log(f"BADFILE {rp}: {n} messages, expected {req.expected_messages}")
            self._retry_or_fail(rp, f"downloaded file has {n} messages, expected {req.expected_messages}")
            return
        os.replace(part, final)
        job_id = u["job_id"]
        u.update(state=DONE, job_id=None)
        self.events += 1
        self.log(f"DONE    {rp} ({req.expected_messages} messages)")
        _detail("SAVED   %s: %d bytes, %d messages, from CDS job %s", final, final.stat().st_size, n, job_id)
        # the data is safely on disk and verified: remove the finished job from the CDS account so
        # that completed requests do not pile up. Failure to delete is harmless.
        try:
            self.client.cleanup(job_id)
            _detail("CLEANUP deleted CDS job %s", job_id)
        except Exception as e:                       # noqa: BLE001
            self.log(f"WARN    could not delete CDS job {job_id}: {type(e).__name__}: {str(e)[:100]}")

    # ---- one pass ---------------------------------------------------------------------
    def step(self) -> bool:
        """Advance everything once. Returns True when all units are done or failed."""
        m = self.manifest
        # 1. poll jobs that are in flight (also those submitted by an earlier process)
        for rp, u in list(m.units.items()):
            if u["state"] != SUBMITTED:
                continue
            if self.now() - self._polled.get(rp, float("-inf")) < self.poll_seconds:
                continue                              # asked about this job a moment ago: do not hammer CDS
            self._polled[rp] = self.now()
            try:
                state, msg = self.client.poll(u["job_id"])
                if state == C.DONE:
                    self._fetch(rp)
                elif state == C.FAILED:
                    self._handle_failure(rp, msg)
                else:
                    if u.get("cds_state") != state:
                        _detail("POLL    %s job %s: %s", rp, u["job_id"], state)
                    u["cds_state"] = state
            except Exception as e:                   # noqa: BLE001 - network hiccups: try again later
                self.log(f"WARN    {rp}: {type(e).__name__}: {str(e)[:120]}")
            m.save()
        # 2. submit pending units, respecting the per-queue limit
        inflight: dict[str, int] = {}
        for rp, u in m.units.items():
            if u["state"] == SUBMITTED:
                q = self._req(rp).queue
                inflight[q] = inflight.get(q, 0) + 1
        for rp, u in list(m.units.items()):
            if u["state"] != PENDING or u.get("not_before", 0) > self.now():
                continue
            req = self._req(rp)
            lim = m.meta["max_days"].get(req.group)
            if lim and req.ndays > lim and "days" in self.split_order:
                self._presplit(rp)
                continue
            if inflight.get(req.queue, 0) >= self._limit(req.queue):
                continue
            try:
                u["job_id"] = self.client.submit(req.dataset, req.cds_request())
                u.update(state=SUBMITTED, submitted_at=self.now())
                self.events += 1
                inflight[req.queue] = inflight.get(req.queue, 0) + 1
                self.log(f"SUBMIT  {rp} job {u['job_id']}")
                _detail("REQUEST %s -> %s: %s", rp, req.dataset, json.dumps(req.cds_request(), sort_keys=True))
            except C.SubmitError as e:
                self._handle_failure(rp, str(e))
            except Exception as e:                   # noqa: BLE001
                self.log(f"WARN    submit {rp}: {type(e).__name__}: {str(e)[:120]}")
            m.save()
        m.save()
        return self.finished()

    def settle(self, max_passes: int = 1000) -> bool:
        """Repeat step() without waiting for as long as something happens (a refusal is split and the parts
        are submitted at once, finished jobs are fetched and the next ones submitted). Waiting is only
        needed when nothing changed, and then it is up to the caller. Returns True when all is done/failed."""
        for _ in range(max_passes):
            before = self.events
            if self.step():
                return True
            if self.events == before:
                return False
        return False

    def run(self, idle=None):
        """Loop until everything is done or failed. Safe to interrupt (Ctrl-C) and restart.
        Sleeps `poll_seconds` between passes only when nothing changed; queued jobs are polled at most that often.
        `idle`: a callable that does one piece of other work (the ARCO hours) and returns True if it did some; it
        is called instead of sleeping, so that the CDS queue and that work proceed side by side."""
        try:
            last = None
            while True:
                done = self.settle()
                worked = bool(idle and idle())
                if done and not worked:
                    break
                if self.summary() != last:               # progress line only when something changed
                    last = self.summary()
                    self.log(f"... {last}")
                if not worked:
                    time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            self.log("interrupted; state is saved, rerun to continue")
            return False
        self.log(f"finished: {self.summary()}")
        return self.summary().get(FAILED, 0) == 0
