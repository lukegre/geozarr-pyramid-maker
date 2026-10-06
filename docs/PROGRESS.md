# Progress log — geozarr-pyramid-maker

## Status
- Phase: v0.1 feature-complete (M0–M6 done)
- Last updated: 2026-10-02

## Log
| Date | Step | Status | Notes |
|---|---|---|---|
| 2026-09-29 | Kick-off: requirements captured (xarray accessor, regional data, multi-var, dask, GeoZarr best practice, zarr v3 auto chunks/shards, loguru, uv) | done | |
| 2026-09-29 | Research: summarise polar_hackathon geozarr-conversion page (sonnet) | done | docs/research/01-polar-hackathon-notebook.md |
| 2026-09-29 | Research: GeoZarr conventions & tooling (haiku) | done | docs/research/02-geozarr-conventions.md |
| 2026-09-29 | Clarifying questions to user | done | native CRS; levels at root with all vars; python>=3.12; extras = validation, CLI, dry-run plan, preview HTML |
| 2026-09-29 | Write PLAN.md + ADRs | done | docs/PLAN.md, docs/DECISIONS.md D-01..D-10 |
| 2026-09-29 | User review of PLAN.md + open questions Q1–Q4 | done | D-05–D-10 accepted; Q1 roll to −180…180 w/ warning (D-11); Q2 curvilinear out of scope (D-12); Q3 obstore + fsspec fallback (D-13); Q4 mode default (D-14); housekeeping D-15: MIT, personal GitHub + Actions, README+examples, basal-melt ref test |
| 2026-09-29 | Planning complete — ready for M0 scaffold | done | |
| 2026-09-29 | M0 verification of APIs (sonnet) | done | research/02 ⚠ items resolved; accessor `geozarr` free; findings led to D-16..D-19 |
| 2026-09-29 | M0 blocker: uv panics inside the macOS sandbox | worked around | `excludedCommands` "uv *" not effective; main session runs uv unsandboxed, subagents use `.venv/bin/{pytest,ruff}` |
| 2026-09-29 | D-16..D-19 accepted by user | done | |
| 2026-09-29 | M0 scaffold (sonnet) | done | pyproject, CLI stub, logging, smoke tests, pre-commit, CI, LICENSE |
| 2026-09-29 | CLAUDE.md: uv sandbox workaround documented | done | main session runs uv unsandboxed; subagents use .venv/bin/{python -m pytest,ruff} |
| 2026-09-29 | M1 chunking.py (sonnet) | done | PLAN §4.4 rules; float64 at 512 px → k=8 (128 MiB, rule is ≤) |
| 2026-09-29 | M1 detect.py + conftest fixtures (sonnet) | done | all §6 fixtures; lazy; lon roll/antimeridian gap, y flip, fill masking; grid_mapping stripped (re-added in M3) |
| 2026-09-29 | M1 fix: float32 coord regularity/transform (sonnet) | done | least-squares step; tolerance max(1e-6·step, 8·eps·max\|v\|) |
| 2026-09-29 | M1 plan.py + .geozarr.plan() accessor (sonnet) | done | 241 tests green on py3.12 + 3.14; merged to main (041278f) |
| 2026-09-29 | M2 resample.py + write.py (sonnet ×2, parallel) | done | merged to main (a9cf61e); 452 tests; notebook-sized 650 MiB test pyramid: 5 levels in 2.7 s, peak RSS 1.6 GiB (to revisit) |
| 2026-09-29 | M3 metadata.py + write wiring (sonnet) | done | geozarr-toolkit helpers; D-16 schema URLs; CF spatial_ref/grid_mapping/GeoTransform; root history; consolidate after attrs; overwrite refuses non-Zarr dirs |
| 2026-09-29 | M3 validate.py (sonnet) | done | toolkit validate_group per group + required-conventions check (toolkit silently skips absent ones) + structure/plan/datatree checks |
| 2026-09-29 | M3 merged | done | 503 passed, 1 skipped (GDAL 3.12.4 < 3.13, Zarr-driver test skipped); main 3cdde34 |
| 2026-09-29 | M4 CLI + logging polish/fixes (sonnet ×2, parallel) | done | CLI convert/plan/validate; log review; main 947039f |
| 2026-09-29 | M5 preview + server (sonnet) | done | OL 10.10.0 via jsDelivr +esm; Range/CORS server; facts verified against OL source (docs/research/03) |
| 2026-09-29 | M5 bug: preview blank (`await getView`) | fixed | found via headless Chrome console; Map needs the Promise |
| 2026-09-29 | D-20 int sentinel fill (user decision) + preview var order | done | tests/test_fill.py was missing at first and then written: 14 tests |
| 2026-09-29 | M6 reference dataset changed by user | done | OceanSODA dfco2 (Zarr v3) instead of basal melt |
| 2026-09-29 | M5 merged | done | headless-Chrome checks (demo_polar: melt/mask render, no JS errors); main 8d975f8 |
| 2026-09-29 | M6 README, examples/oceansoda_dfco2.py, examples/demo.ipynb (sonnet) | done | notebook: 11 cells, runs start to finish on 12 time steps (user request) |
| 2026-09-29 | M6 reference test (dfco2, 12 time steps) + notebook test (sonnet) | done | slow/network, excluded by default; 5/5 pass (run by main session outside sandbox); level1/2 = NumPy 2×2 nanmean of the level above |
| 2026-09-29 | M6 dfco2 preview in headless Chrome | done | aligns with the OSM basemap; time slider OK |
| 2026-09-29 | Open follow-ups | open | peak RSS about 1.6 GiB on the 650 MiB test (threads × shard size); OL per-level resolution vs padding (research/03); GDAL 3.13 Zarr-driver test untested (bundled GDAL 3.12.4); CI not yet run on GitHub; pre-commit-hooks rev v5.0.0; no push/PyPI yet |
| 2026-09-29 | Preview layout redesign | done | collapsible sidebar with per-variable cards and legends (nice ticks, display names, µatm-style units), bottom time scrubber (play/step/keys, date-positioned ticks), in-page colourmap/min/max/symmetric/reset/reversed controls persisted in localStorage, keyless OSM basemaps (Light/Dark via CSS filters, OSM, None), value readout, light/dark theme; centre-longitude control was added then reverted (8b33750) – see D-24; user ran uv sync --all-extras, pytest, ruff on main: all pass; merged to main at a68aedb (fast-forward of feat/preview-layout); note subagent escalation to Sonnet:high for wrapX diagnosis |
| 2026-09-29 | Viewer: settings cog + S3 endpoint field | done | (i) button is now settings cog; panel: S3 endpoint URL field (s3:// paths only); endpoint via ?endpoint= → /api/open → check_store(endpoint=); write._s3_endpoint_options maps endpoint to obstore (endpoint + skip_signature) / fsspec (endpoint_url/anon); browser_url uses path-style URLs for custom endpoints; verified vs s3://spi-pamir-public/test/oceansoda_dfco2.zarr on https://os.zhdk.cloud.switch.ch; OPEN: bucket lacks CORS rules (blocks browser tile fetch); OPEN: tests/test_write.py imports obstore unconditionally (breaks plain uv sync); merged 28a9dc4 |
| 2026-10-01 | Viewer UI update merged to main (merge 476fa13; commits 66b1680, fa991cd) | done | (1) store path box now full-width top bar fixed at top, hidden when sidebar collapses; (2) S3 endpoint field moved into sidebar, shown automatically for s3:// inputs; (3) sun/settings button replaced by info button whose panel shows Metadata and Global attributes tables (new config keys `attrs`, `levels`); (4) multiple variables selectable at once (toggle cards, ≥1 selected, stacking = selection order, per-variable opacity/style, scrubber = union of dims, per-variable readout, URL `var=a,b` and `param.<var>`). Decisions D-32, D-33. Checks: 661 passed, ruff clean; browser-checked with 3-variable test pyramid. |
| 2026-10-01 | Fix: obstore panicked ('Expected config prefix to start with aws_') for http:// S3 endpoints because `_s3_endpoint_options` passed `allow_http` as a top-level kwarg; it now goes in `client_options={'allow_http': True}`. Regression tests added in tests/test_preview.py. | fixed | SWITCH (os.zhdk.cloud.switch.ch) 301-redirects http to https, so use the https:// endpoint. |

## Next step (for the next agent)
Push to GitHub and check CI (py3.12–3.14); user browser check of the preview; decide on the follow-ups above; then tag v0.1.0.

## Milestones

- [x] M0 Scaffold
- [x] M1 Detect+plan+chunking
- [x] M2 Resample+write
- [x] M3 Metadata+validation
- [x] M4 Logging+CLI
- [x] M5 Preview
- [x] M6 Docs+reference test

- 2026-10-02: Standalone preview HTML (D-35): built-in blank configuration and basemaps, browser GeoZarr loading, optional Python API preserved. Verified direct store loading, raster rendering and time controls in Brave against a Range-capable local host.
- 2026-10-05: Preview sidebar Selected/Other sections (D-36). Selected variable cards sit in a "Selected (n)" section at the top, top-most layer first. Unselected cards sit below in an "Other (n)" section, in dataset order. Other collapses, and its state is remembered in localStorage key `gpm-preview:other-collapsed` (kept in memory if storage fails). Checked in the browser with a 4-variable test pyramid. Merged to main at 227ffdc. On main: 679 passed, ruff clean. Note: a plain `uv sync` breaks collection of tests/test_write.py (unconditional obstore import, a known open issue); use `uv sync --all-extras`.
- 2026-10-06: Updated Selected/Other interactions (D-37): clicking retains selected cards, drag handles move cards between sections and reorder the map stack without recreating layers, and array names label the viewer. Empty/collapsed Other accepts drops; the final selected variable remains protected. Added executable JavaScript interaction coverage via Node.js, called from pytest. Full suite: 673 passed, 9 skipped, 8 deselected; ruff lint and format checks passed.
- 2026-10-06: Added explicit eye visibility and × removal controls (D-38), including empty selection and URL restoration. Expanded settings show the long name when available and remain accessible for hidden layers. Extended executable JavaScript tests to use the real card/control handlers and verify visibility, removal, labels, retained layers, drag ordering and URL restoration. Full suite: 673 passed, 9 skipped, 8 deselected; ruff lint and format checks passed.
