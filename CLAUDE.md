# CLAUDE.md — geozarr-pyramid-maker

Converts regular-grid xarray data into GeoZarr multiscale pyramids (Zarr v3, sharded) via a `.geozarr` accessor.

## Read first
- `docs/PLAN.md`: design, API, milestones (M0–M6)
- `docs/DECISIONS.md`: accepted decisions D-01…D-15. Follow them; propose a new entry rather than silently deviating
- `docs/PROGRESS.md`: current status

## Agent delegation (mandatory)
The main session (Opus) plans, breaks work into tasks, reviews diffs and makes design calls. It does **not** write code itself.

| Task | Model : effort |
|---|---|
| Coding (implementation, tests, refactors): almost everything | **Sonnet : medium** subagent |
| Tests keep failing after a Sonnet:medium attempt | Sonnet : high (pass the failing output along) |
| Extreme cases only (Sonnet:high stuck, subtle design/algorithm problem) | Opus : low → medium (log the reason in PROGRESS.md) |
| Reading tasks (code/docs digests, API lookups) | Sonnet : medium |
| Web searches | Haiku : high (verify before relying on the results) |
| Progress log `docs/PROGRESS.md` | Haiku : high, after every step or milestone |

Escalation ladder: Sonnet:medium → Sonnet:high → Opus:low → Opus:medium. The Agent tool has no effort parameter, so
state the effort level in the prompt. Each coding handoff includes the relevant PLAN/DECISIONS sections, the files to touch
and the target tests, plus the rule: TDD (red → green), and run `uv run pytest` and `uv run ruff check` before reporting.

## Git workflow
- Every piece of work gets its own **feature-named branch** (e.g. `feat/m1-detect-plan`, `fix/shard-alignment`, `docs/readme`).
- If that clashes with other work in progress (another agent or session on a branch, or uncommitted changes in the working tree),
  use a **git worktree** for the branch instead of switching the main checkout.
- Once the work is finished, merge it back into **`main`**. `main` must always run: `uv sync`, `uv run pytest` and `uv run ruff check`
  must pass on the merged result before the merge is kept. Never leave `main` broken.
- Commit messages: short imperative subject (`feat:`, `fix:`, `docs:`, `test:`, `chore:` prefixes).

## Conventions
- Package manager: **uv** only (`uv add`, `uv run`, `uv sync`). No pip or conda.
- src layout: `src/geozarr_pyramid_maker/`. Python ≥3.12.
- Logging: **loguru**; the library never adds or removes handlers (D-08). INFO for milestones, DEBUG for the plan, TRACE for internals, WARNING for auto-corrections.
- All processing is lazy with dask
- Metadata is built with `geozarr-toolkit` helpers, not handwritten dicts.
- ISO-8601 dates; metric units.
