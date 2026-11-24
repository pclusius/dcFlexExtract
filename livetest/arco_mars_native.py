# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""MARS data for the ETADOT comparison without any interpolation: ETADOT (77) and Q (133) on the NATIVE N320 reduced
Gaussian grid (no `grid`, no `area`), levels 60-137, 2023-08-30 14 UTC. ARCO's q is the same N320 data, so q checks that
both sides read identical numbers; ETADOT is then compared with ours evaluated at exactly the same grid points, which
removes MARS's interpolation to 0.25 degrees from the comparison. Re-attaches to the saved job when run again.
usage (repository root):  PYTHONPATH=. python -u livetest/arco_mars_native.py"""
import json
import os
import time

from dcfe.client import DatastoresClient

JOB = "livetest/fdtest/job_native.json"
BODY = {"class": "ea", "expver": "1", "stream": "oper", "type": "an", "levtype": "ml", "date": "20230830",
        "time": "14", "levelist": "/".join(str(i) for i in range(60, 138)), "param": "77.128/133.128",
        "accuracy": "24"}

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
    c.download(job, "livetest/fdtest/native.grb")
    c.cleanup(job)
    print("downloaded", flush=True)
