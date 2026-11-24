# dcFlexExtract - resumable ERA5 download and post-processing for FLEXPART input
# Copyright (C) 2026 the dcFlexExtract contributors
# SPDX-License-Identifier: CC-BY-4.0
#
# Builds on flex_extract, (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert
# (CC BY 4.0; https://www.flexpart.eu/wiki/FpInputMetEcmwf). See NOTICE.md.

"""dcFlexExtract command line:  plan | download | process | cleanup   (see README.md).

download, process and cleanup write a complete log of the run into <outdir>/logs/ (see runlog.py)."""
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from . import era5, runlog
from .config import Config
from .requests import plan
from .runlog import LOG


def _domain_args(p):
    p.add_argument("--start", required=True, help="first day, YYYYMMDD")
    p.add_argument("--end", required=True, help="last day, YYYYMMDD, inclusive")
    p.add_argument("--area", required=True, nargs=4, type=float, metavar=("N", "W", "S", "E"),
                   help="latitude/longitude rectangle in degrees, e.g. 61 21 59 25")
    p.add_argument("--grid", type=float, default=0.25, help="grid spacing in degrees, 0.25 or 0.5 (default 0.25)")
    p.add_argument("--levels", nargs=2, type=int, default=[60, 137], metavar=("FIRST", "LAST"),
                   help="model levels, 137 is the lowest (default 60 137)")
    p.add_argument("--source", choices=era5.SOURCES, default=era5.DEFAULT_SOURCE,
                   help="where the data comes from: mars = CDS/MARS (default); mixed = ARCO-ERA5 (Google Cloud public "
                        "data, no queue) for the model levels, CDS for surface, flux and static fields; arco = everything "
                        "from ARCO-ERA5, no CDS account (needs netCDF4)")
    p.add_argument("--gridding", choices=era5.GRIDDINGS, default=era5.DEFAULT_GRIDDING,
                   help="--source mixed/arco only: exact = evaluate the spectral fields exactly at the grid points "
                        "(default); mars = reproduce MARS's own gridding step (Gaussian grid, then linear in latitude), "
                        "for consistency with older MARS-route data. Not better data, only comparable data")
    p.add_argument("--pressure", choices=era5.PRESSURE_SOURCES, default=era5.DEFAULT_PRESSURE,
                   help="source of the surface pressure: lnsp = exp(gridded LNSP from MARS) (default), "
                        "sp = ECMWF single-levels surface pressure, spectral = spectral LNSP through calc_etadot "
                        "(flex_extract's way; needs the Fortran build)")


def _outdir_arg(p, what):
    p.add_argument("--outdir", default="out",
                   help=f"output directory: {what}; logs go to <outdir>/logs (default: out)")


def _split_order(text):
    from .requests import SPLIT_KINDS
    order = tuple(x.strip().lower() for x in text.split(",") if x.strip())
    bad = [x for x in order if x not in SPLIT_KINDS]
    if bad or not order or len(set(order)) != len(order):
        raise argparse.ArgumentTypeError(f"give each of {','.join(SPLIT_KINDS)} at most once, comma-separated "
                                         f"(got {text!r})")
    return order


def _spans(text):
    return {k.strip().upper(): int(v) for k, v in (kv.split("=") for kv in text.split(",") if kv)}


_HELP = (
    ("WORKFLOW", None, [
        ("dcfe.sh setup [--no-fortran]", "check what the loaded modules provide and prepare the environment; the Fortran "
         "program is only needed for --pressure spectral (see README)"),
        ("dcfe.sh plan <domain>", "show what would be requested; no network, no files"),
        ("dcfe.sh download <domain>", "fetch from CDS; resumable, safe to Ctrl-C and run again"),
        ("dcfe.sh process <domain>", "raw GRIB -> hourly FLEXPART files <outdir>/EA025YYMMDDHH"),
        ("dcfe.sh cleanup [--yes]", "list / delete finished jobs stored in your CDS account"),
    ], "Use the same domain options and the same --outdir for download and process."),
    ("DOMAIN OPTIONS (plan, download, process)", None, [
        ("--start YYYYMMDD, --end YYYYMMDD", "first and last day, inclusive"),
        ("--area N W S E", "latitude/longitude rectangle in degrees"),
        ("--grid 0.25|0.5", "grid spacing (default 0.25)"),
        ("--levels FIRST LAST", "model levels, 137 is the lowest (default 60 137). All levels go in one request. "
         "To add levels later, download just those levels into the same raw directory; process finds data by content."),
        ("--source mars|mixed|arco", "where the model-level data comes from (default mars). mars: everything from "
         "CDS/MARS. mixed: model levels from ARCO-ERA5 (Google's public ERA5 copy, read over HTTPS, no queue): "
         "T, U, V from spectral data, ETADOT from mass continuity, Q/CLWC/CIWC interpolated from the N320 grid, "
         "surface pressure from spectral LNSP; surface, flux and static fields still come from CDS. Needs "
         "--pressure lnsp. ARCO ends a few weeks before today and the run stops at once if a day is missing "
         "('use --source mars from ...'); after the final-ERA5 date the data is preliminary ERA5T. Use a new "
         "--outdir when you change the source. arco: as mixed, and the surface, flux and static fields too come from "
         "ARCO-ERA5 (global hourly NetCDF files cut to the box, needs netCDF4): no CDS account, no requests."),
        ("--gridding exact|mars", "with --source mixed or arco: how the spectral model-level fields are put on the grid. "
         "exact (default): the spectral sums are evaluated at the grid points. mars: the same steps as MARS (full "
         "T639 synthesis on a Gaussian grid whose longitudes coincide with the grid, then linear interpolation in "
         "latitude), which smooths short waves, mostly in ETADOT, and reproduces MARS-route fields to numerical "
         "precision. Use it only for consistency with MARS-route data; it is not better data."),
        ("--pressure lnsp|sp|spectral", "where the surface pressure comes from (default lnsp). lnsp: exp(gridded "
         "LNSP), carried in the model-level request, no Fortran. sp: ECMWF single-levels surface_pressure, no "
         "Fortran. spectral: spectral LNSP through the Fortran calc_etadot, flex_extract's way. Use the same "
         "value for download and process."),
        ("--outdir DIR", "everything of a run: the hourly files, DIR/raw and DIR/logs (default out)"),
        ("--rawdir DIR", "raw GRIB files (default <outdir>/raw); process searches it recursively"),
    ], None),
    ("DOWNLOAD OPTIONS", None, [
        ("--once", "do one step (collect finished jobs, submit new ones) and exit, for cron or short jobs. "
         "Without it: loop until everything is done."),
        ("--poll SECONDS", "wait when nothing changed, and the shortest time between two status checks of the same "
         "queued CDS job (default 60). A refused request is split and its parts submitted at once, without waiting, "
         "so a short --poll is not needed to speed that up."),
        ("--inflight-mars N", "open MARS jobs, i.e. model levels (default 2). MARS runs one request per user at a "
         "time, the others wait in the queue."),
        ("--inflight-sfc N", "open single-levels jobs (default 3)"),
        ("--split-order LIST", "how a request that CDS refuses is made smaller, first to last. Kinds: days, "
         "variables, area, levels, hours (default in that order). Kinds not listed are never used. Leave out "
         "'area' to keep every file whole-area (tiles cannot be assembled yet). "
         "Example: --split-order days,variables,levels,hours"),
        ("--spans ML=30,SL=31", "days per request to try first, by group (ML LNSP SL SP FLUX). Default: the whole "
         "period for MARS groups, one calendar month for single-level groups. Normally not needed."),
    ], None),
    ("PROCESS OPTIONS", None, [
        ("--check", "only report what is missing for each hour; write nothing"),
        ("--remove-raw", "when all hours are written, delete the raw files the run used (only files inside the period "
         "plus one day either side) and the download manifest; never partial output. Not undoable"),
        ("--etadot-boundary ground|top", "vertical velocity: integrate from the ground, where it is zero (default, "
         "correct for any level range), or from the top of the retrieved levels as flex_extract did (reproduces "
         "old output; wrong unless level 1 is included)"),
        ("--exe PATH", "calc_etadot executable (--pressure spectral only)"),
        ("--scratch DIR, --keep-scratch", "per-hour working files (default <outdir>/.scratch); keep them"),
    ], None),
    ("FILES", None, [
        ("<outdir>/logs/<time>_<command>.log", "full log of each run; starts with the date and the command"),
        ("<outdir>/logs/commands.log", "one line per run"),
        ("<rawdir>/<domain>/manifest.json", "download state (job ids); keep it while downloading"),
    ], "Existing output files are skipped, never overwritten: use a new --outdir when you change --pressure, "
       "--levels or --etadot-boundary. README.md has the details."),
)


def _render_help(width: int = 78) -> str:
    import textwrap
    out = []
    for title, _, items, note in _HELP:
        out.append(title)
        for flag, text in items:
            out.append("  " + flag)
            out.extend(textwrap.wrap(text, width, initial_indent="      ", subsequent_indent="      "))
        if note:
            out.append("")
            out.extend(textwrap.wrap(note, width, initial_indent="  ", subsequent_indent="  "))
        out.append("")
    return "\n".join(out)


HELP_EPILOG = _render_help()


def _wrap(text, width=78):
    import textwrap
    return "\n".join(textwrap.wrap(text, width))


def build_parser():
    from .requests import SPLIT_ORDER
    default_order = ",".join(SPLIT_ORDER)
    ap = argparse.ArgumentParser(prog="dcfe", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description="dcFlexExtract: ERA5 from CDS to hourly FLEXPART input files.",
                                 epilog=HELP_EPILOG)
    sub = ap.add_subparsers(dest="cmd", required=True)

    fmt = dict(formatter_class=argparse.RawDescriptionHelpFormatter)
    ex = ("  dcfe.sh {} --start 20230830 --end 20230902 --area 61 21 59 25 \\\n"
          "      --levels 60 137 --outdir /scratch/me/run1")
    p = sub.add_parser("plan", help="list download units for a configuration (no network, no files)", **fmt,
                       description=_wrap("Show the CDS requests a run would make, with expected message counts. "
                                   "Nothing is sent or written."),
                       epilog="Example:\n" + ex.format("plan") + " --show-request")
    _domain_args(p)
    _outdir_arg(p, "only used to show where the raw data would go; plan writes nothing")
    p.add_argument("--rawdir", default=None, help="raw data directory (default: <outdir>/raw)")
    p.add_argument("--show-request", action="store_true", help="also print the CDS request body")
    p.add_argument("--spans", default="", metavar="ML=30,SL=31",
                   help="days per request by group (ML LNSP SL SP FLUX); default: whole period / one month")

    d = sub.add_parser("download", help="download (resumable); re-run to continue", **fmt,
                       description=_wrap("Fetch the raw ERA5 data from CDS. Safe to stop (Ctrl-C) and run again: finished "
                                   "files are kept and queued CDS jobs are re-attached. Run it in tmux/screen, "
                                   "or use --once from cron. See 'dcfe.sh --help' for the split order."),
                       epilog="Example:\n" + ex.format("download") + "\n  (cron)  ... download --once ...")
    _domain_args(d)
    _outdir_arg(d, "raw data goes to <outdir>/raw unless --rawdir is given")
    d.add_argument("--rawdir", default=None, help="where raw GRIB files are stored (default: <outdir>/raw)")
    d.add_argument("--once", action="store_true", help="advance state once and exit (cron / short jobs)")
    d.add_argument("--poll", type=float, default=60,
                   help="seconds to wait when nothing changed, and the shortest time between two status checks of a "
                        "queued CDS job (default 60). Refusals are split and retried at once, not after this wait")
    d.add_argument("--inflight-mars", type=int, default=2, help="open jobs in the MARS queue (model levels, LNSP)")
    d.add_argument("--inflight-sfc", type=int, default=3, help="open jobs for single-level data (not MARS)")
    d.add_argument("--split-order", type=_split_order, default=None, metavar=default_order,
                   help="how to split a request that CDS refuses, first to last (default: " + default_order +
                        "). Kinds you leave out are never used. Area (tiles) needs mosaicking "
                        "before process, which is not implemented yet: leave it out to keep every file whole-area")
    d.add_argument("--spans", default="", metavar="ML=30,SL=31",
                   help="days per request to try first, by group (ML LNSP SL SP FLUX); default: the whole period "
                        "(single-level groups: one calendar month); refused sizes are learned and halved")

    pr = sub.add_parser("process", help="raw GRIB -> hourly FLEXPART files", **fmt,
                        description=_wrap("Build the hourly FLEXPART files <outdir>/EA025YYMMDDHH from the raw GRIB files "
                                    "(any source: downloaded by 'download' or by hand). Existing outputs are "
                                    "skipped. Uses the Fortran calc_etadot only with --pressure spectral."),
                       epilog="Examples:\n" + ex.format("process") + " --check      (what is missing?)\n" + ex.format("process"))
    _domain_args(pr)
    _outdir_arg(pr, "the hourly FLEXPART files are written here")
    pr.add_argument("--rawdir", default=None,
                    help="directory searched recursively for *.grb, any source (default: <outdir>/raw)")
    pr.add_argument("--exe", default=str(Path(__file__).resolve().parent.parent / "fortran" / "calc_etadot"),
                    help="calc_etadot executable (only used with --pressure spectral)")
    pr.add_argument("--scratch", default=None, help="scratch dir (default: <outdir>/.scratch)")
    pr.add_argument("--keep-scratch", action="store_true", help="do not delete the per-hour working files")
    pr.add_argument("--check", action="store_true", help="only report what is missing for each hour")
    pr.add_argument("--remove-raw", action="store_true",
                    help="after all hours are written, delete the raw files this run used (only files whose data lies "
                         "inside the period plus one day either side) and the download manifest")
    pr.add_argument("--etadot-boundary", choices=("ground", "top"), default="ground",
                    help="ETADOT (vertical velocity) is integrated upward from the ground, where it must be zero "
                         "(default, correct for any level range), or - as flex_extract does - downward from the "
                         "top of the retrieved levels, which gives an alternating error unless level 1 is included")

    c = sub.add_parser("cleanup", help="delete finished (successful) jobs stored in your CDS account", **fmt,
                       description=_wrap("CDS keeps finished jobs in your account; 'download' deletes the ones it has "
                                   "fetched. This lists the rest (deleting them makes their data unavailable)."),
                       epilog="Example:\n  dcfe.sh cleanup --outdir /scratch/me/run1 --yes")
    _outdir_arg(c, "only the log is written there")
    c.add_argument("--yes", action="store_true", help="really delete (default: only list how many)")
    return ap


def _make_config(*args, **kw):
    try:
        return Config.make(*args, **kw)
    except ValueError as e:                              # a wrong combination of options: a message, not a traceback
        raise SystemExit(f"dcfe: {e}")


def _cfg(a):
    rawdir = a.rawdir or str(Path(a.outdir) / "raw")
    return _make_config(a.start, a.end, a.area, a.grid, a.levels, rawdir=rawdir, outdir=a.outdir, pressure=a.pressure,
                       source=a.source, gridding=a.gridding)


def cmd_process(a):
    from .gribcount import count_messages
    from .process import build_hour, hours, missing, out_name
    from .rawindex import RawIndex
    cfg = _cfg(a)
    LOG.info("raw data: %s   output: %s   ETADOT boundary: %s   pressure: %s", cfg.rawdir, cfg.outdir, a.etadot_boundary, cfg.pressure)
    ix = RawIndex.from_dir(cfg.rawdir)
    LOG.info("indexed %d raw files", ix.files)
    for p in sorted(Path(cfg.rawdir).rglob("*.grb")):
        LOG.debug("raw file: %s (%d bytes)", p, p.stat().st_size)
    LOG.debug("index: %d flux, %d surface, %d static, %d LNSP, %d model-level messages",
              len(ix.flux), len(ix.sfc), len(ix.static), len(ix.lnsp), len(ix.ml))
    from .process import expected_grid_size
    sizes, want = ix.grid_sizes(), expected_grid_size(cfg)
    if sizes and set(sizes) != {want}:
        LOG.error("raw data does not match the domain: %d values per field expected, found %s. Tiles of the domain "
                  "(CDS refused the larger request and the download was split by area) cannot be assembled by "
                  "process yet, or the data is for another area or grid.", want, sizes)
        return 2
    out = Path(cfg.outdir)
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(a.scratch) if a.scratch else out / ".scratch"
    bad = 0
    for T in hours(cfg):
        miss = missing(ix, cfg, T)
        if a.check or miss:
            LOG.info("%s: %s", out_name(cfg, T), "ok" if not miss else "MISSING " + ", ".join(miss))
            bad += bool(miss)
            continue
        existed = (out / out_name(cfg, T)).exists()
        f = build_hour(ix, cfg, T, a.exe, out, scratch, a.keep_scratch, a.etadot_boundary)
        if existed:
            LOG.info("exists, skipped: %s", f.name)
        else:
            LOG.info("wrote %s", f.name)
            LOG.debug("output %s: %d bytes, %d messages", f, f.stat().st_size, count_messages(str(f)))
    LOG.info("%s", "missing data for %d hour(s)" % bad if bad else "all hours complete")
    if a.remove_raw and not a.check:
        if bad or not all((out / out_name(cfg, T)).exists() for T in hours(cfg)):
            LOG.warning("--remove-raw: the output is not complete, raw files are kept")
        else:
            from .process import remove_raw
            remove_raw(ix, cfg)
    return 1 if bad else 0


def cmd_cleanup(a):
    from .client import DatastoresClient
    cl = DatastoresClient()
    ids = cl.list_successful()
    LOG.info("%d successful jobs stored at CDS", len(ids))
    if a.yes:
        for i in ids:
            cl.cleanup(i)
            LOG.debug("deleted CDS job %s", i)
        LOG.info("deleted")
    elif ids:
        LOG.info("re-run with --yes to delete them (they cannot be downloaded afterwards)")
    return 0


def _arco_sources(cfg):
    """(model-level downloader, NetCDF downloader or None, one-step function) for --source mixed/arco, else Nones."""
    if cfg.source == "mars":
        return None, None, None
    if cfg.source == "arco":
        try:
            import netCDF4                                  # noqa: F401  checked first, not after hours of work
        except ImportError:
            raise SystemExit("dcfe: --source arco needs the Python package netCDF4, which this Python does not have. "
                             "Run './dcfe.sh setup --no-fortran' (it installs only what is missing), or load a module that provides it.")
    from . import arco
    ml = arco.ArcoDownloader(cfg)
    stable = arco.check_range(cfg, ml.store)               # stops here, before anything is sent, if a day is missing
    if cfg.end > stable:
        LOG.warning("%s onwards the ARCO-ERA5 data is preliminary (ERA5T) and may still be revised by ECMWF",
                    (stable + timedelta(days=1)).strftime("%d-%m-%Y"))
    nc = None
    if cfg.source == "arco":
        from . import arconc
        try:
            arconc.check_range(cfg)
        except arconc.ArcoRawUnavailable as e:
            raise SystemExit(f"dcfe: {e}")
        nc = arconc.NcDownloader(cfg)
    LOG.info("model levels from ARCO-ERA5: %s%s", ml.summary(), f"; surface, flux, static: {nc.summary()}" if nc else "")
    return ml, nc, (lambda: bool((nc and nc.step()) or ml.step()))


def cmd_download(a):
    from .client import DatastoresClient
    from .download import Downloader
    cfg = _cfg(a)
    LOG.info("raw data: %s", cfg.rawdir)
    ml, nc, step = _arco_sources(cfg)
    if cfg.source == "arco":                                # no CDS account, no queue: all local work
        while step():
            pass
        LOG.info("%s", {**ml.summary(), **nc.summary()})
        return 0 if not (ml.pending() or nc.pending()) else 1
    dl = Downloader(cfg, DatastoresClient(),
                    max_inflight={era5.DATASET_MARS: a.inflight_mars, era5.DATASET_SFC: a.inflight_sfc},
                    poll_seconds=a.poll, spans=_spans(a.spans), log=LOG.info,
                    **({"split_order": a.split_order} if a.split_order else {}))
    if a.once:
        ok = dl.settle() and dl.summary().get("failed", 0) == 0
        while ml and ml.step():                             # local work, no waiting: do all of it
            pass
        ok = ok and not (ml and ml.pending())
    else:
        ok = dl.run(idle=ml.step if ml else None)
    LOG.info("%s", {**dl.summary(), **(ml.summary() if ml else {})})
    return 0 if ok or a.once else 1


def cmd_plan(a):
    cfg = _make_config(a.start, a.end, a.area, a.grid, a.levels, pressure=a.pressure, source=a.source, gridding=a.gridding)
    units = plan(cfg, _spans(a.spans))
    if cfg.source == "arco":
        print(f"{'arco-nc':14s} {'':6s}  surface, flux and static fields: one GRIB file per day and kind, cut from the global "
              f"NetCDF files of ARCO-ERA5 (flux days {cfg.start - timedelta(days=1):%Y%m%d}-{cfg.end + timedelta(days=1):%Y%m%d}); no CDS request")
    if cfg.source in ("mixed", "arco"):
        from .process import hours
        print(f"{'arco-zarr':14s} {len(hours(cfg)):6d}  one GRIB file per hour: model levels {cfg.level_first}-{cfg.level_last} "
              "(U V T Q ETADOT CLWC CIWC) and gridded LNSP, read from ARCO-ERA5, no queue")
    for u in units:
        print(f"{u.queue.split('-era5-')[1]:14s} {u.expected_messages:6d}  {u.relpath}")
        if a.show_request:
            print("   ", json.dumps(u.cds_request()))
    print(f"{len(units)} units; raw data would go to {a.rawdir or Path(a.outdir) / 'raw'}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    a = build_parser().parse_args(argv)
    if a.cmd == "plan":
        raise SystemExit(cmd_plan(a))
    runlog.setup(a.outdir, a.cmd, argv, vars(a))
    run = {"download": cmd_download, "process": cmd_process, "cleanup": cmd_cleanup}[a.cmd]
    try:
        rc = run(a)
    except KeyboardInterrupt:
        LOG.warning("interrupted by user")
        rc = 130
    except Exception:                                    # noqa: BLE001 - leave the traceback in the log
        LOG.exception("%s failed", a.cmd)
        raise
    LOG.debug("exit status %s", rc)
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
