# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
"""Build ONE hour of the raw ARCO model-level GRIB of a run (what `download --source mixed` does for every hour).
usage (repository root):  python livetest/arco_build_hour.py 2023-08-30T14 livetest/raw_mixed"""
import sys, time
from datetime import datetime
from dcfe import arco
from dcfe.config import Config
T = datetime.fromisoformat(sys.argv[1])
cfg = Config.make(T.strftime("%Y%m%d"), T.strftime("%Y%m%d"), (61, 21, 59, 25), 0.25, (60, 137), rawdir=sys.argv[2], source="mixed")
st = arco.Store()
print("valid range:", st.valid_range(), "check:", arco.check_range(cfg, st))
t0 = time.time(); b = arco.HourBuilder(cfg, st); h = b.build(T); print(f"built in {time.time() - t0:.1f} s")
p = arco.hour_path(cfg, T); p.parent.mkdir(parents=True, exist_ok=True)
msgs = arco.messages(cfg, h, b.A, b.B); p.write_bytes(b"".join(msgs)); print(len(msgs), "messages ->", p)
