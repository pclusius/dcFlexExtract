# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""Optional local copy of the global ARCO-ERA5 objects (--arco-cache DIR).

ARCO-ERA5 is read as global objects: one Zarr chunk per variable, model level and hour, and one NetCDF file per
single-level variable and day. They do not depend on the area, grid or --gridding of a run, so a copy kept under the
bucket's own path serves any later run, also of other users who share the directory.

  * Files are written under a unique temporary name and renamed: several runs can fill one cache at the same time.
  * They are created with the permissions of the user's umask (use umask 002 and a setgid directory for a group).
  * Only final ERA5 is kept. Preliminary ERA5T may be revised, and a copy would go on being used.
  * The cache is never required: if it cannot be written (read-only, disk full) the run warns once and goes on.
  * Nothing is ever removed because of age or size.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import threading
from pathlib import Path
from typing import Callable

LOG = logging.getLogger("dcfe")


class ObjectCache:
    """Files under root/<path in the bucket>. Thread-safe (the Zarr chunks are read by a thread pool)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.hits = 0            # objects taken from the cache
        self.added = 0           # objects written to it
        self._lock = threading.Lock()
        self._writable = True

    def path(self, rel: str) -> Path:
        return self.root / rel

    def used(self) -> None:
        with self._lock:
            self.hits += 1

    def read(self, rel: str) -> bytes | None:
        try:
            data = self.path(rel).read_bytes()
        except OSError:                                      # not there (or not readable): download
            return None
        return data or None

    def write(self, rel: str, data: bytes) -> None:
        self._put(rel, lambda tmp: tmp.write_bytes(data))

    def add_file(self, rel: str, source: Path) -> None:
        self._put(rel, lambda tmp: shutil.copyfile(source, tmp))

    def discard(self, rel: str) -> None:
        try:
            self.path(rel).unlink(missing_ok=True)
        except OSError:
            pass

    def _put(self, rel: str, make: Callable[[Path], object]) -> None:
        if not self._writable:
            return
        dest = self.path(rel)
        tmp = dest.with_name(f".{dest.name}.{socket.gethostname()}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                make(tmp)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)                  # only still there if something failed
        except OSError as e:
            with self._lock:
                if self._writable:
                    self._writable = False
                    LOG.warning("ARCO cache %s cannot be written (%s): the run goes on without adding to it", self.root, e)
            return
        with self._lock:
            self.added += 1
