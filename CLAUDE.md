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
and the target tests, plus the rule: TDD (red → green), and run `.venv/bin/python -m pytest`, `.venv/bin/ruff check` and
`.venv/bin/ruff format --check` before reporting (not `uv run`; see *uv and the sandbox*).

## Git workflow
- Every piece of work gets its own **feature-named branch** (e.g. `feat/m1-detect-plan`, `fix/shard-alignment`, `docs/readme`).
- If that clashes with other work in progress (another agent or session on a branch, or uncommitted changes in the working tree),
  use a **git worktree** for the branch instead of switching the main checkout.
- Once the work is finished, merge it back into **`main`**. `main` must always run: `uv sync`, `uv run pytest` and `uv run ruff check`
  must pass on the merged result before the merge is kept. Never leave `main` broken.
- Commit messages: short imperative subject (`feat:`, `fix:`, `docs:`, `test:`, `chore:` prefixes).

## Conventions
- Package manager: **uv** only (`uv add`, `uv run`, `uv sync`). No pip or conda.

## uv and the sandbox
- uv 0.9.9 panics inside the macOS Bash sandbox ("Attempted to create a NULL object") and cannot write `~/.cache/uv`.
  The `sandbox.excludedCommands: ["uv", "uv *"]` setting did not take effect (2026-09-29).
- **Main session** runs uv commands (`uv add`, `uv sync`, `uv lock`, `uv build`, `uv run …`) outside the sandbox
  (`dangerouslyDisableSandbox: true`); the user has authorised this for uv only.
- **Subagents never run uv** (their unsandboxed requests are denied). They use the already-synced venv, which works
  sandboxed: `.venv/bin/python -m pytest`, `.venv/bin/ruff check`, `.venv/bin/ruff format`. If a subagent needs a new
  dependency, it reports back and the main session runs `uv add`.
- Python 3.12 check (main session): `UV_PROJECT_ENVIRONMENT=$TMPDIR/venv312 uv run --python 3.12 --all-extras python -m pytest`.
- src layout: `src/geozarr_pyramid_maker/`. Python ≥3.12.
- Logging: **loguru**; the library never adds or removes handlers (D-08). INFO for milestones, DEBUG for the plan, TRACE for internals, WARNING for auto-corrections.
- All processing is lazy with dask
- Metadata is built with `geozarr-toolkit` helpers, not handwritten dicts.
- ISO-8601 dates; metric units.
