CSS = """
:root{--bg:#f6f7f9;--fg:#1c2330;--muted:#5b6678;--line:#8793a6;--card:#ffffff;--rule:#dde2ea;
--mars:#b4540a;--marsbg:#fdf0e3;--arco:#0b6e75;--arcobg:#e1f3f4;--share:#3b4a68;--sharebg:#e8ecf5;--warn:#9a2a2a;
--sans:'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif;--mono:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#10151d;--fg:#e4e9f1;--muted:#98a3b5;--line:#6f7b90;--card:#171e29;--rule:#2a3445;
--mars:#f0a05c;--marsbg:#2b2015;--arco:#5cc9d1;--arcobg:#112a2d;--share:#a9b8da;--sharebg:#1d2535;--warn:#f08a8a;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#10151d;--fg:#e4e9f1;--muted:#98a3b5;--line:#6f7b90;--card:#171e29;--rule:#2a3445;
--mars:#f0a05c;--marsbg:#2b2015;--arco:#5cc9d1;--arcobg:#112a2d;--share:#a9b8da;--sharebg:#1d2535;--warn:#f08a8a;color-scheme:dark}
body{background:var(--bg);color:var(--fg);font-family:var(--sans);font-size:15px;line-height:1.55;
padding-block:28px 56px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
main{max-width:1060px;margin:0 auto}
h1{font-size:1.6rem;line-height:1.2;margin:0 0 6px;text-wrap:balance}
h2{font-size:1.1rem;margin:34px 0 8px;text-wrap:balance}
p{margin:0 0 10px;max-width:70ch}.lead{color:var(--muted);max-width:70ch}
nav{display:flex;gap:18px;flex-wrap:wrap;margin:0 0 20px;font-size:.9rem}
nav a{color:var(--fg);text-decoration:none;border-bottom:2px solid transparent;padding-bottom:2px}
nav a[aria-current]{border-color:var(--share);font-weight:600}nav a:hover{border-color:var(--line)}
figure{margin:14px 0 0}.scroll{overflow-x:auto;border:1px solid var(--rule);background:var(--card);border-radius:6px;padding:8px}
svg{display:block;min-width:760px;width:100%;height:auto;color:var(--fg);font-family:var(--sans);font-size:12.5px}
figcaption{color:var(--muted);font-size:.88rem;margin-top:8px;max-width:80ch}
svg .box{fill:var(--card);stroke:var(--line);stroke-width:1.4}
svg .mars{fill:var(--marsbg);stroke:var(--mars);stroke-width:1.6}svg .arco{fill:var(--arcobg);stroke:var(--arco);stroke-width:1.6}
svg .share{fill:var(--sharebg);stroke:var(--share);stroke-width:1.6}
svg .ln{stroke:var(--line);stroke-width:1.5;fill:none}svg .lnm{stroke:var(--mars);stroke-width:1.8;fill:none}
svg .lna{stroke:var(--arco);stroke-width:1.8;fill:none}svg .lns{stroke:var(--share);stroke-width:1.8;fill:none}
svg .ah{fill:var(--line)}svg .ahm{fill:var(--mars)}svg .aha{fill:var(--arco)}svg .ahs{fill:var(--share)}
svg text{fill:var(--fg)}svg .t{font-weight:600}svg .s{fill:var(--muted);font-size:11.5px}svg .m{font-family:var(--mono);font-size:11.5px}
svg .lbl{fill:var(--muted);font-size:11.5px}svg .big{font-weight:700;font-size:15px}svg .warn{fill:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--rule)}
th{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
td.n{font-family:var(--mono);white-space:nowrap}.tw{overflow-x:auto}
code{font-family:var(--mono);font-size:.88em}
.k{display:inline-block;width:.8em;height:.8em;border-radius:2px;margin-right:6px;vertical-align:-1px}
ul{padding-left:1.2em;max-width:75ch}li{margin:0 0 6px}
"""
HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="claude:viewport-fit-sides" content="cover">
<title>%(title)s</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>%(css)s</style></head><body><main>
<nav><a href="index.html"%(a1)s>How ETADOT is obtained</a><a href="checks.html"%(a2)s>Sanity checks</a></nav>
"""
TAIL = "</main></body></html>"

DEFS = """<defs>
<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ah"/></marker>
<marker id="ahm" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ahm"/></marker>
<marker id="aha" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="aha"/></marker>
<marker id="ahs" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" class="ahs"/></marker>
</defs>"""

def box(x, y, w, h, cls, lines, anchor="start", pad=12):
    out = f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="5"/>'
    ty = y + 22
    for i, (txt, st) in enumerate(lines):
        out += f'<text x="{x+pad}" y="{ty}" class="{st}">{txt}</text>'
        ty += 17 if st != "big" else 20
    return out

# ---------------- page 1 ----------------
d1 = DEFS
# route A (MARS)
d1 += box(16, 28, 200, 92, "mars", [("MARS archive (ERA5)", "t"), ("ETADOT, param 77", "s"), ("stored as spectral T639", "s"), ("--source mars", "m")])
d1 += box(262, 28, 200, 92, "mars", [("CDS / MARS delivers", "t"), ("0.25° grid, box only", "s"), ("η̇ on full levels (1/s)", "s"), ("raw GRIB", "m")])
d1 += '<line class="lnm" x1="216" y1="74" x2="260" y2="74" marker-end="url(#ahm)"/><text class="lbl" x="218" y="64">request</text>'
d1 += '<path class="lnm" d="M462 74 H748" marker-end="url(#ahm)"/><text class="lbl" x="476" y="66">raw GRIB: η̇, nothing computed here</text>'
# route B (mixed)
d1 += box(16, 168, 200, 150, "arco", [("ARCO-ERA5 (Google)", "t"), ("spectral, T639:", "s"), ("divergence, all 137 levels", "s"), ("vorticity, levels with ΔB≠0", "s"), ("LNSP (surface pressure)", "s"), ("--source mixed", "m")])
d1 += box(262, 168, 200, 150, "arco", [("Synthesis at box points", "t"), ("Legendre + Fourier sums,", "s"), ("exact, no grid in between", "s"), ("D, ln ps, ∇ln ps;", "s"), ("u, v from ζ and D", "s")])
d1 += box(508, 168, 214, 150, "arco", [("Mass continuity", "t"), ("S_k = Δp_k·D_k", "m"), ("    + ΔB_k·ps·(V_k·∇ln ps)", "m"), ("H(k+½) = B·ΣS − Σ_{j≤k} S_j", "m"), ("η̇_k = (H_k+H_k−1)", "m"), ("   ·(ΔA/p0+ΔB)/(2Δp_k)", "m")])
d1 += '<line class="lna" x1="216" y1="243" x2="260" y2="243" marker-end="url(#aha)"/>'
d1 += '<line class="lna" x1="462" y1="243" x2="506" y2="243" marker-end="url(#aha)"/><text class="lbl" x="464" y="233">D, U, V</text>'
d1 += '<path class="lna" d="M722 243 H748" marker-end="url(#aha)"/>'
d1 += '<text class="lbl" x="508" y="338">written as ARCO_ML GRIB with the same keys as MARS data</text>'
# shared stage
d1 += box(750, 28, 274, 290, "share", [("process (both routes)", "t"), ("η̇ on full levels  →  H on half levels", "s"), ("", "s"), ("Et_K = 2·η̇_K·Δp_K/Δη_K", "m"), ("H(K+½) = Et_K − H(K−½)", "m"), ("", "s"), ("start of the sum:", "s"), ("--etadot-boundary ground (default)", "m"), ("  H = 0 at the ground", "s"), ("--etadot-boundary top", "m"), ("  as flex_extract; wrong unless", "s"), ("  level 1 is included", "s"), ("", "s"), ("H = η̇·∂p/∂η, Pa/s: the field", "t"), ("in the hourly FLEXPART file", "t")])
# surface pressure inputs
d1 += box(750, 348, 274, 108, "box", [("Surface pressure for Δp", "t"), ("exp(gridded LNSP)   --pressure lnsp", "m"), ("ECMWF surface_pressure  --pressure sp", "m"), ("spectral LNSP, Fortran  --pressure spectral", "m"), ("(mixed needs lnsp, from ARCO)", "s")], pad=10)
d1 += '<line class="ln" x1="887" y1="348" x2="887" y2="320" marker-end="url(#ah)"/>'
# notes
d1 += '<text class="s" x="16" y="352">Left column = where the numbers come from; the two coloured routes differ only up to the raw GRIB.</text>'
d1 += '<text class="s" x="16" y="372">Route A takes the model\'s own diagnosed η̇; route B rebuilds it from the model\'s divergence and surface pressure.</text>'
d1 += '<text class="s" x="16" y="392">process cannot tell the two apart: it converts η̇ to H the same way, then writes the hourly files.</text>'
d1 += '<text class="s" x="16" y="426">On route B H is computed first and turned into η̇ for the GRIB; process then recovers the same H</text>'
d1 += '<text class="s" x="16" y="446">(round trip tested to numerical precision).</text>'
svg1 = f'<svg viewBox="0 0 1040 476" role="img" aria-label="Two ways of getting vertical velocity into the hourly files: MARS delivers the model eta-dot on a 0.25 degree grid, or mixed mode computes it from ARCO divergence and surface pressure by mass continuity; process then converts either to the half-level flux H starting from the ground.">{d1}</svg>'

body1 = HEAD % dict(title="ETADOT Routes", css=CSS, a1=' aria-current="page"', a2="") + f"""
<h1>How ETADOT gets into the hourly files</h1>
<p class="lead">FLEXPART needs the vertical velocity as η̇·∂p/∂η on half levels. dcFlexExtract has two ways to obtain the η̇ it starts from, and one shared step that turns it into that field.</p>
<figure><div class="scroll">{svg1}</div>
<figcaption><span class="k" style="background:var(--mars)"></span>MARS route (default): the archived model η̇.
<span class="k" style="background:var(--arco)"></span>Mixed route: η̇ rebuilt from ARCO divergence and surface pressure.
<span class="k" style="background:var(--share)"></span>Shared processing.</figcaption></figure>
<h2>What differs between the routes</h2>
<ul>
<li><b>Gridding.</b> The model η̇ is a T639 spectral field. MARS puts it on the 0.25° grid by synthesising it on a Gaussian grid and interpolating linearly in latitude, which smooths the shortest waves (about 8 % in η̇). The mixed route evaluates the sums exactly at your grid points by default. <code>--gridding mars</code> repeats MARS's steps, only for consistency with older MARS-route data; it is not better data.</li>
<li><b>Levels used.</b> Continuity needs the divergence on all 137 levels, also when only levels 60–137 are written. The winds are needed where ΔB ≠ 0 (levels 54–137 for the L137 coefficients).</li>
<li><b>Queue.</b> The MARS route waits in the MARS queue. The mixed route reads ARCO over HTTPS and does not queue.</li>
</ul>
<p>How close the two come is on the <a href="checks.html">sanity checks</a> page.</p>
""" + TAIL
open("index.html", "w").write(body1)

# ---------------- page 2 ----------------
d2 = DEFS
d2 += box(380, 24, 280, 70, "arco", [("Ours (mixed route)", "t"), ("continuity from ARCO, exact at the points", "s")])
d2 += box(20, 190, 280, 70, "mars", [("MARS 0.25° file (CDS)", "t"), ("what the MARS route delivers", "s")])
d2 += box(740, 190, 280, 70, "mars", [("MARS's own T639 coefficients", "t"), ("exact synthesis at the points", "s")])
d2 += '<line class="ln" x1="440" y1="94" x2="200" y2="188" marker-end="url(#ah)" marker-start="url(#ah)"/>'
d2 += '<text class="big warn" x="236" y="128">8.1 %</text>'
d2 += '<line class="ln" x1="600" y1="94" x2="840" y2="188" marker-end="url(#ah)" marker-start="url(#ah)"/>'
d2 += '<text class="big" x="778" y="128" style="fill:var(--arco)">0.9 %</text>'
d2 += '<line class="ln" x1="302" y1="225" x2="738" y2="225" marker-end="url(#ah)" marker-start="url(#ah)"/>'
d2 += '<text class="big warn" x="480" y="216">7.9 %</text><text class="lbl" x="395" y="246">MARS gridding: Gaussian grid N360, then linear in latitude</text>'
svg2 = f'<svg viewBox="0 0 1040 280" role="img" aria-label="Our continuity eta-dot agrees with the exact synthesis of the MARS spectral coefficients to 0.9 percent and differs from the MARS 0.25 degree file by 8.1 percent; the file differs from the exact synthesis by 7.9 percent because of MARS gridding.">{d2}</svg>'

rows = [
 ("Analytic cases", "spectral synthesis, gradients, continuity closed form, zero flux at the ground, η̇↔H round trip", "pass (9 tests)"),
 ("Other fields, same synthesis", "T, U, V, Q against the MARS 0.25° file", "0.01 %, 0.8 %, 0.4 %, 0.2 %"),
 ("Ours vs MARS's spectral η̇", "small box (9×17 points) and 20°×20° area (81×81 points), levels 60–137", "0.9 % and 0.9 %"),
 ("Ours vs the MARS 0.25° file", "small box, levels 60–137", "8.1 %"),
 ("MARS's gridding step, reproduced", "T639 → Gaussian N360 → linear in latitude; η̇, divergence, LNSP, U, V", "0.00002 %"),
 ("<code>--gridding mars</code> vs the MARS file", "mixed route with MARS's step, small box", "U, V, T 0.000 %; η̇ 0.8 %"),
 ("No effect", "finite-element-like vertical integral, evaluation on N320 points, lower truncations, CDO remapping, continuity from gridded fields", "does not explain it"),
]
tr = "".join(f"<tr><td><b>{a}</b></td><td>{b}</td><td class='n'>{c}</td></tr>" for a, b, c in rows)
body2 = HEAD % dict(title="ETADOT Checks", css=CSS, a1="", a2=' aria-current="page"') + f"""
<h1>Sanity checks on the mixed-route ETADOT</h1>
<p class="lead">One hour (2023-08-30 14 UTC), levels 60–137. Numbers are the rms difference as a share of the rms of the reference field.</p>
<figure><div class="scroll">{svg2}</div>
<figcaption>Our η̇ is 0.9 % from the exact synthesis of the model's own spectral η̇ and 8.1 % from the 0.25° file that MARS delivers. The file differs from the same coefficients by 7.9 %, so the difference arises in MARS's gridding.</figcaption></figure>
<h2>Why the MARS file differs</h2>
<p>MARS archives ETADOT as spectral T639 coefficients. For a 0.25° grid it synthesises the full field on the regular Gaussian grid N360 (1440×720, longitudes coinciding with the 0.25° points) and then interpolates <b>linearly in latitude</b> to your latitudes. These are the documented MARS defaults (<code>truncation=AUTO</code>, <code>intgrid=AUTO</code>, <code>interpolation=linear</code>). Repeating exactly this reproduces the MARS 0.25° η̇, divergence, LNSP, U and V to 0.00002 %.</p>
<p>Linear interpolation between rows damps the shortest waves. η̇ and divergence are affected because 3–38 % of their variance sits above wavenumber 320, T, U and V are not. Nothing is wrong with the gridded file, and MARS does not recompute η̇. Our exact evaluation is closer to the model's own field. <code>--gridding mars</code> repeats MARS's step for consistency with MARS-route data; it is not better data.</p>
<h2>Checks</h2>
<div class="tw"><table><thead><tr><th>Check</th><th>What</th><th>Result</th></tr></thead><tbody>{tr}</tbody></table></div>
<h2>Still open</h2>
<ul><li>FLEXPART has not been run on either route.</li>
<li>Only one hour has been checked, on a small box and on one 20°×20° area.</li></ul>
""" + TAIL
open("index.html", "w").write(HEAD % dict(title="ETADOT Routes", css=CSS, a1=' aria-current="page"', a2="") + body1_text) if False else None
open("checks.html", "w").write(body2)
