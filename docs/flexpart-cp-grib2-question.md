# Draft: question to the FLEXPART developers

**Title:** GRIB2 reader divides convective precipitation (CP) by 1000 but not large-scale precipitation (LSP): intended?

**Version:** master, `src/windfields_mod.f90` (checked against origin/master of 2026-11-19; the older branches `optimise` and `wetdepo_bugfix` have the same code).

**What the code does.** In the GRIB2 branch of `readwind_ecmwf` (and its nested-domain counterpart) `conversion_factor=1000.` is set for snow depth (parameter category 1, number 11) and for convective precipitation (category 1, number 10), but not for large-scale precipitation (category 1, number 9 or paramId 142). The factor is applied when the field is read (`convprec(...) = zsec4(...)/conversion_factor`, `sd(...) = ...`); LSP is read without it. In the GRIB1 branch the factor is 1 for all of them. In `origin/wetdepo_bugfix` the two lines carry the comment `! Only used for operational data`.

**What we observed.** We have ERA5 data on 0.25 degrees (made with flex_extract and with a rewrite of it) in which LSP and CP are both in mm/h and both GRIB1. We rewrote the same hourly files so that only CP was a GRIB2 message (WMO parameters 0/1/10, values and dates unchanged) and printed the field maxima right after the read:

| input | max LSP (mm/h) | max CP (mm/h) | factor |
|---|---|---|---|
| both GRIB1 | 7.74e-02 | 3.80e-01 | 1.0 |
| CP GRIB2 | 7.74e-02 | 3.80e-04 | 1000.0 |
| LSP GRIB2 (control) | 7.74e-02 | 3.80e-01 | 1.0 |

So with the same units for both fields, convective precipitation is 1000 times smaller when it arrives as GRIB2. It is used together with LSP for the wet scavenging, so a GRIB2 CP field of this kind weakens wet deposition from convective rain with no warning.

**Question.** Which units does the GRIB2 reader expect for CP and snow depth, and for LSP? Our guess from the comment is that the operational ECMWF GRIB2 data has CP and snow depth in different units from LSP (so the factor is deliberate for that data set), but we could not find it documented. If that is the case, could the expected units be written into the manual or the code comment, and could the reader check the `units` key (m, mm, kg m-2) instead of using a fixed factor? If it is not intended, the factor for CP would need to go, or LSP would need the same one.

**Why it matters.** Software that prepares FLEXPART input (flex_extract and our rewrite) delivers the flux fields as GRIB1, where the factor is 1. If the data or the preparation software moves these fields to GRIB2, as ECMWF has been doing with its products, CP would silently become 1000 times too small.
