# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""MARS data for the finite-difference ETADOT test (see arco_proto.py): gridded divergence, U, V on ALL 137 levels
and gridded LNSP, 2023-08-30 14 UTC, box 58-62N 19-27E (wider than the 59-61N 21-25E test box so that horizontal
differences exist inside it). One request; re-attaches to the saved job when run again.
usage (repository root):  PYTHONPATH=. python -u livetest/arco_mars_fd.py"""
import json
import os
import time

from dcfe.client import DatastoresClient

JOB = "livetest/fdtest/job.json"
BODY = {"class": "ea", "expver": "1", "stream": "oper", "type": "an", "levtype": "ml", "date": "20230830",
        "time": "14", "levelist": "/".join(str(i) for i in range(1, 138)), "param": "155.128/131.128/132.128/152.128",
        "grid": "0.25/0.25", "area": "62/19/58/27", "accuracy": "24"}

c = DatastoresClient()
if os.path.exists(JOB):
    job = json.load(open(JOB))["job"]
    print("re-attached to", job, flush=True)
else:
    job = c.submit("reanalysis-era5-complete", BODY)
    json.dump({"job": job, "body": BODY}, open(JOB, "w"))
    print("submitted", job, flush=True)
while True:
    st, msg = c.poll(job)
    if st in ("done", "failed"):
        break
    time.sleep(60)
print(st, msg, flush=True)
if st == "done":
    c.download(job, "livetest/fdtest/fd.grb")
    c.cleanup(job)
    print("downloaded", flush=True)
