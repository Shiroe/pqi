# Phase 3: Deployment Portability

**Status:** blocked on Phase 0 positive verdict
**Estimated effort:** 3–5 days of focused work
**Owner:** TBD
**Last updated:** 2026-05-25 (session 47 — packaging references updated for uv + `src/` layout; phase still blocked on Phase 0)

---

## Why this phase exists

Goal: **the system runs on its own without requiring the user at
the terminal.** Currently every `analyze` is manually invoked.

The right first step is **not** a full continuous-run TUI or a
microservices architecture. It's a **single Docker container
wrapping the existing monolith**, deployable to a small VPS
(Hetzner / Linode / similar), with cron-scheduled analyze runs
(and optionally the Phase 2 listener).

**This phase is deployment, not architecture.** The code doesn't
split into services. The container just lets the same monolith run
on a machine that isn't the user's laptop.

See `docs/scaling_assessment.md` §8 for the "portability not
scale-out" framing.

---

## Entry criteria

- [ ] Phase 0 complete with "edge validated" OR "inconclusive"
      verdict (not "no edge" — don't deploy a known-losing system)
- [ ] Decision: this deploys as **advisor** (cron-triggered
      analyze, outputs to log + notification) or as **listener**
      (continuously-running process, Phase 2's MFE listener) or
      both. Same container, different entrypoints.

---

## Goals (measurable)

1. **Single Docker image** builds cleanly from the current repo.
2. **docker-compose.yml** orchestrates the container + persistent
   volumes (`~/.pqi/` equivalent).
3. **Runs on a VPS** with <30 min setup from a fresh Debian/Ubuntu
   image, following a documented runbook.
4. **Secrets managed** via `.env` file (not baked into the image).
5. **Crash-recoverable** — restart brings state back cleanly from
   the persistent volume.
6. **Observable from logs** — tail `docker logs` and see what's
   happening without attaching a debugger.

---

## Tasks

### Task 3.1 — Dockerfile

**What:** Write `Dockerfile` for the monolith.

**Implementation:**

- Base: `python:3.11-slim` (matches `CLAUDE.md` tech stack), or the
  official `ghcr.io/astral-sh/uv` image for a uv-native build
- Use multi-stage build to keep the final image lean:
  - Stage 1 (`builder`): copy `pyproject.toml` + `uv.lock`, run
    `uv sync --frozen --no-dev` (add `build-essential` for C-extensions
    like `rapidfuzz` if no wheel is available)
  - Stage 2 (`runtime`): copy the resulting `.venv/` + `src/` from the
    builder, slim base
- Copy `src/`, `scripts/`, `pyproject.toml`, `uv.lock`; don't copy
  `.env`, `.pqi/`, `.venv/`, or `__pycache__`
- Default command: `pqi --help` (the installed console script — require
  an explicit entrypoint override for production so an unconfigured
  container can't run an arbitrary command)

**Acceptance criteria:**

- [ ] `docker build -t pqi .` succeeds
- [ ] `docker run pqi --help` produces help text
- [ ] Image size reasonable (<500 MB)

**Files touched:**

- New: `Dockerfile`
- New: `.dockerignore` (exclude `.env`, `~/.pqi/`, `__pycache__/`,
  `.git/`, `docs/`, `scripts/dev_*`)

**Risks:**

- Python dependencies with C extensions (`rapidfuzz`, `duckdb`,
  possibly `py-clob-client`) need either wheels or
  `build-essential` in the builder stage. The `uv sync` output during
  build shows what compiled from source.
- `py-clob-client` may have heavy transitive deps (web3, eth-utils).
  If the Phase 2 listener isn't in the deploy, move it to an optional
  dependency-group in `pyproject.toml`
  (`[project.optional-dependencies]`) and `uv sync` only the advisor
  group for advisor-only builds.

---

### Task 3.2 — docker-compose.yml

**What:** Compose file orchestrating pqi + persistent state.

**Implementation:**

- Service: `pqi`
- Volumes:
  - `./pqi-state:/app/.pqi` — persistent state (history, cache,
    stats, agent_weights, clv, mfe, alerts); set `PQI_DATA_DIR=/app/.pqi`
    so the app writes here regardless of where the source sits in the image
  - `./pqi-logs:/var/log/pqi` — log files (mountable to host for
    easy tailing and logrotate)
- `env_file: .env`
- Restart policy: `unless-stopped` (for listener mode); `no` for
  one-off advisor runs via cron
- Healthcheck: simple `pqi --version` or a custom heartbeat file
  mtime check

**Acceptance criteria:**

- [ ] `docker compose up -d` starts the service
- [ ] State persists across `docker compose restart` (verify by
      running an analyze, restarting, checking `history.jsonl`)
- [ ] `.env` values load into the container

**Files touched:**

- New: `docker-compose.yml`
- `.env.example` — add container-specific guidance
  (`PQI_MODE`, volume paths)

---

### Task 3.3 — Entrypoints for advisor vs listener

**What:** Support running the container in different modes via a
single entrypoint script.

**Modes:**

- `analyze-cron`: schedules periodic `pqi analyze` runs
- `listener`: runs the Phase 2 MFE listener (if Phase 2 promoted)
- `cli`: interactive CLI for manual invocation via
  `docker exec -it`

**Implementation:**

- `scripts/docker_entrypoint.sh` dispatches on `PQI_MODE` env var
- For `analyze-cron` mode: simple bash loop with `sleep $INTERVAL`,
  or use `busybox crond` — pick whichever is lighter. **Do not
  bring in a full scheduler** (Airflow/Prefect rejected in the
  scaling assessment).
- `listener` mode: exec the Phase 2 listener script with proper
  signal handling so `SIGTERM` stops it cleanly.

**Acceptance criteria:**

- [ ] Each mode starts cleanly (tested locally)
- [ ] `analyze-cron` mode triggers analyze at the configured
      interval
- [ ] `docker stop pqi` stops the listener gracefully (not with
      `SIGKILL`)
- [ ] `cli` mode runs interactively when attached via
      `docker exec -it`

---

### Task 3.4 — VPS deployment documentation

**What:** Write a concise deployment runbook.

**Implementation:** `docs/deployment.md` with the following
sections:

- **Recommended VPS sizing:** ≥1 GB RAM, ≥10 GB disk, Debian 12 or
  Ubuntu 22.04+
- **Setup steps** (aim for 10 commands total):
  1. Install Docker + compose plugin
  2. `git clone` the repo
  3. Copy `.env.example` to `.env` and populate keys
  4. `docker compose build`
  5. `docker compose up -d`
- **Secret hygiene:**
  - `.env` must be `chmod 600` on the VPS
  - Document the key list (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`,
    `PANDASCORE_API_KEY`, and Phase 2/Theme B keys if applicable)
  - VPS firewall: block all inbound except SSH
- **Log rotation:** mount `/var/log/pqi` to host, install
  `logrotate` config (daily, keep 14 days, compress)
- **Backup:** the state volume is the only thing worth backing up
  — `rsync` the `pqi-state/` dir to an external target weekly
- **Upgrade:** `git pull && docker compose build && docker compose
  up -d`

**Acceptance criteria:**

- [ ] Documentation complete and follows the "new machine in
      <30 min" target
- [ ] End-to-end walkthrough tested by actually deploying once on
      a fresh VPS (or a local Docker-in-Docker VM)

**Files touched:**

- New: `docs/deployment.md`

---

### Task 3.5 — Observability minimum

**What:** Ensure the container emits enough signal that you can
tell from logs whether it's healthy.

**Implementation:**

- Structured logs (already using Python `logging`; ensure log level
  configurable via `LOG_LEVEL` env var — defaults to INFO in
  container)
- **Heartbeat log line** every N minutes (listener mode) or per
  analyze run (advisor mode), containing:
  - Last analyze timestamp
  - Open positions count (listener mode)
  - Error count since start
  - Current banded-WR snapshot digest (so log tail shows whether
    stats are updating)
- Optional: Slack notification on crash / error spike (reuse the
  Phase 2 `SLACK_WEBHOOK_URL` plumbing)

**Acceptance criteria:**

- [ ] Heartbeat line visible in `docker logs pqi`
- [ ] Errors log with enough context to diagnose (full stack
      traces, API error response bodies)

---

## Exit criteria

All of:

- [ ] Image builds (`docker build` succeeds)
- [ ] `docker compose up` works locally
- [ ] Deployed to a VPS at least once end-to-end following the
      runbook
- [ ] Deployment doc written
- [ ] Running for 1 week without requiring manual intervention

---

## Validation: "are we done?"

1. Can a new machine be brought up from scratch in <30 min using
   the runbook? (Yes → 3.4 passed)
2. If the VPS reboots, does pqi come back up and resume normal
   operation? (Yes → 3.2 + 3.5 passed, and restart policy works)
3. Can I tail logs and see what's happening? (Yes → 3.5 passed)

---

## Risks / decision points

- **Secrets on VPS.** `.env` on a VPS with SSH access is the main
  risk surface. If Phase 2 has promoted to auto-execution with real
  wallet keys (Theme B), harden this: consider moving wallet keys
  to a hardware token or a secrets manager rather than a plain
  `.env` file. For advisor-only deploys, `.env` with chmod 600 is
  acceptable.
- **VPS outages.** A $5/mo VPS is not HA. If the listener is
  business-critical, dual-region or auto-restart becomes a Phase 4
  item.
- **Log volume.** DEBUG × continuous operation = gigabytes per
  week. Default to INFO; make DEBUG opt-in via
  `LOG_LEVEL=DEBUG` env override.
- **Container upgrade path.** `git pull && docker compose build`
  is simple but means downtime during rebuild. For an advisor
  cron, that's fine. For a listener, either:
  - Accept ~30s downtime per upgrade (acceptable for most use
    cases)
  - Blue-green: run `pqi-new` alongside `pqi`, cut over after
    healthcheck green (Phase 4 if needed)

---

## Related work

### Roadmap themes

- **Theme B.1** (continuous TUI) — Phase 3 is the deployment
  foundation; Theme B.1 is the interactive UX on top. Don't do
  them together; ship Phase 3 first.

### Files referenced

- `src/pqi/main.py` — CLI entry point (`pqi` console script)
- `pyproject.toml` + `uv.lock` — dependency manifest (replaced `requirements.txt`)
- `.env.example` — secret scaffolding
- `docs/known-issues.md` — environment gotchas (persisted state
  file locations)

### Session logs

- Session 27 (prompt caching, cost-relevant for long-running
  container)
- Session 30 (Anthropic retry, resilience-relevant for
  unattended operation)

---

## Session state

**Last work session:** *not yet started (blocked on Phase 0)*

**Done:** *nothing yet*

**In-progress:** *nothing*

**Next action:** Wait for Phase 0 to complete with at least
"inconclusive" verdict. Then begin Task 3.1 — Dockerfile sketch.

**Notes for next session:**

- Don't start this before Phase 0 has a positive-enough signal to
  justify the deployment work.
- Check whether `py-clob-client` is needed in the container
  (depends on whether Phase 2 listener is part of the deploy). If
  not, split it into an optional `[project.optional-dependencies]`
  group and `uv sync` only the advisor deps.
- VPS choice: user has stated preference for small/local. Hetzner
  CX11 (~€4/mo) or a Linode Nanode ($5/mo) is likely sufficient
  for advisor + listener.
- Verify that `DuckDB` installed in the container has read access
  to `PQI_DATA_DIR`'s `history.jsonl` via the mounted volume — path
  handling inside the container matters.
