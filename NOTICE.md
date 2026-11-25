# Notice: origin, authorship and licences

dcFlexExtract ("de-coupled flex_extract") is built on **flex_extract**, the software package that has
retrieved ECMWF data for FLEXPART and FLEXTRA for many years. The idea, the processing chain, the Fortran
program that computes the vertical velocity input and the flux disaggregation all come from that work.
Without it this project would not exist, and we¹²³ are grateful to its authors.

¹Petri Clusius<br>
²INAR (https://www.helsinki.fi/fi/inar)<br>
³AMC-Lahti (www.amc-lahti.fi/)


## flex_extract and its authors

flex_extract (https://www.flexpart.eu/wiki/FpInputMetEcmwf) was developed by

* **Anne Philipp**, Department of Meteorology and Geophysics, University of Vienna
* **Leopold Haimberger**, Department of Meteorology and Geophysics, University of Vienna
* **Petra Seibert**, Institute of Meteorology (BOKU-Met), University of Natural Resources and Life Sciences, Vienna

(as listed in the flex_extract documentation; the 2020 paper below lists the same three authors, the first as A. Tipka).
The Fortran program `calc_etadot` and its numerical routines are credited in the source to
**Leopold Haimberger** and **Gerhard Wotawa** (1994), **Alexander Beck** (2003), L. Haimberger again (2006, 2010
versions for arbitrary regular grids, T799/T1279 and GRIB edition 2) and **Petra Seibert** (2019, code
clean-up and documentation). The conversion of accumulated fluxes (`dapoly`, `darain`) was moved to Python by
L. Haimberger and further developed by A. Philipp; its new precipitation algorithm is described in
Hittmeir, Philipp and Seibert (2018).

Copyright of flex_extract: (C) 2014-2020 Anne Philipp, Leopold Haimberger and Petra Seibert (the individual
files state 2014-2019 or 2014-2020).

### Please cite

* Tipka, A., Haimberger, L., and Seibert, P.: Flex_extract v7.1.2 - a software package to retrieve and prepare
  ECMWF data for use in FLEXPART, Geosci. Model Dev., 13, 5277-5310, https://doi.org/10.5194/gmd-13-5277-2020, 2020.
* Hittmeir, S., Philipp, A., and Seibert, P.: A conservative reconstruction scheme for the interpolation of
  extensive quantities in the Lagrangian particle dispersion model FLEXPART, Geosci. Model Dev., 11, 2503-2523,
  https://doi.org/10.5194/gmd-11-2503-2018, 2018. (the flux disaggregation implemented in `dcfe/fluxes.py`)
* FLEXPART, for which this software prepares input: https://www.flexpart.eu

Also credit ECMWF and the Copernicus Climate Change Service (C3S) for the ERA5 data, following the licence and
citation terms of the Climate Data Store.

## What dcFlexExtract took from flex_extract, and what changed

This notice also serves as the statement of modifications required by the licence (CC BY 4.0, section 3(a)(1)(B)).

| dcFlexExtract | origin in flex_extract | changes |
|---|---|---|
| `fortran/calc_etadot.f90` | `Source/Fortran/calc_etadot.f90` | one line removed (call of `POSNAM`, see below); otherwise identical |
| `fortran/rwgrib2.f90`, `ftrafo.f90`, `grphreal.f90`, `phgrreal.f90` | same names in `Source/Fortran/` | identical (a header comment was added) |
| `fortran/Makefile` | `Source/Fortran/makefile_local_gfortran_new` (GPL-2.0, L. Haimberger, P. Seibert) | rewritten around it: ecCodes via pkg-config, no EMOS, optional `USE_EMOS=1`; licence stays GPL-2.0-only |
| `fortran/selftest/` | `Testing/Installation/Calc_etadot/` | input files (meteorological fields as distributed with flex_extract) and the reference `fort.15` (renamed `expected.fort.15`), unchanged |
| `dcfe/fluxes.py` | `Source/Python/Mods/disaggregation.py` (`dapoly`, `darain`) | ported, made non-mutating, rate conversion added |
| `dcfe/process.py`, `rawindex.py` | processing in `prepare_flexpart.py`, `Classes/EcFlexpart.py` | reimplemented: content-based file index, per-hour scratch directories, no dependence on file names; the vertical velocity (`dcfe/etadot.py`) uses `calc_etadot`'s conversion formula but, by default, integrates from the ground instead of from the top of the retrieved levels (`--etadot-boundary top` gives flex_extract's values); the `lnsp` and `sp` pressure sources do without `calc_etadot` altogether |
| `dcfe/era5.py`, `requests.py` | parameter lists and request contents of `Classes/EcFlexpart.py`, `Classes/MarsRetrieval.py` | reimplemented for ERA5 only, new download units |
| `dcfe.sh`, `sites/csc.sh` | `Run/general_callscript.sh` (module list, `calc_etadot` self-test) | rewritten |
| `docs/baseline.md` | description of the requests and output of flex_extract | notes made while analysing it |

Everything else (the resumable downloader and its manifest, the CDS client wrapper, the run logs, the
command line) was written for dcFlexExtract. The ERA5-only scope drops the ERA-Interim, CERA-20C, operational,
ENFO, ELDA, GFS and ecgate/cca code of flex_extract.

## Third-party material

* `POSNAM` (`posnam.f90` in flex_extract, "Author: Mats Hamrud, ECMWF") is **not** included. It was only used to
  position the namelist file, which is unnecessary here. `jparams.h` (an ECMWF EMOS include file) is not included
  either, because nothing uses it.
* The EMOS library is **not** included and not needed. `fortran/fft99_lite.f90` is an independent, slower
  implementation of the two EMOS routines `calc_etadot` calls; its behaviour was derived by comparing results
  with EMOS (test scripts are kept on the `dev` branch), not by copying code. `make USE_EMOS=1` can still link EMOS.
* `fortran/phgrreal.f90` implements the spectral filter of Sardeshmukh and Hoskins (Mon. Wea. Rev., 1984), as
  its comments say.
* Used as libraries: ecCodes (Apache-2.0), ecmwf-datastores-client (Apache-2.0), NumPy (BSD-3-Clause),
  numcodecs (MIT; only for `--source mixed`).
* `--source mixed` reads **ARCO-ERA5**, Google's public copy of ERA5 (`gs://gcp-public-data-arco-era5`; Google's
  recipes are Apache-2.0, "not an official Google product"). The data are ERA5 from the Copernicus Climate Change
  Service, redistributed with C3S's permission. No ARCO or Google code is used here; `dcfe/spectral.py` and
  `dcfe/arco.py` are written for dcFlexExtract (the spherical-harmonic conventions were checked against ECMWF's own
  gridded values and against analytic cases, test scripts on the `dev` branch). Please cite, in addition to the ERA5 reference
  above: Carver, R. W. and Merose, A. (2023): ARCO-ERA5: An Analysis-Ready Cloud-Optimized Reanalysis Dataset.
  22nd Conf. on AI for Env. Science, Denver, CO, Amer. Meteo. Soc, 4A.1,
  https://ams.confex.com/ams/103ANNUAL/meetingapp.cgi/Paper/415842 and Hersbach et al. (2017), Complete ERA5, C3S
  Climate Data Store (accessed DD-MM-YYYY). "The results contain modified Copernicus Climate Change Service
  information. Neither the European Commission nor ECMWF is responsible for any use that may be made of the
  Copernicus information or data it contains."

## Licence of dcFlexExtract

flex_extract states the licence **Creative Commons Attribution 4.0 International (CC BY 4.0)** for the package
as a whole (README, LICENSE.md and the SPDX tag of almost all files). dcFlexExtract follows it:

* All files are licensed under **CC BY 4.0** (`LICENSE`) unless a file says otherwise,
  `SPDX-License-Identifier: CC-BY-4.0`. The original is provided as is; see section 5 of the licence for the
  disclaimer of warranties.
* `fortran/Makefile` is adapted from a flex_extract makefile that carries `GPL-2.0`, and is therefore marked
  `SPDX-License-Identifier: GPL-2.0-only` (text in `LICENSES/GPL-2.0-only.txt`). The Makefile is a separate
  file (a build recipe); the other files are not derived from it.

Note that the flex_extract tree also contains GPL-2.0 (other makefiles) and MIT-0 (regression test scripts) files.
None of the MIT-0 files is used here.
