# Claude Code Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the minimal `.claude/` infrastructure this repo currently lacks — a root `CLAUDE.md` and a team-shared `.claude/settings.json` — so Claude Code stops re-deriving the non-obvious build/test commands every session.

**Architecture:** Two config artifacts plus one `.gitignore` line. `CLAUDE.md` carries only facts Claude cannot infer from the code or tooling (test invocation, dev-env constraint, two domain gotchas). `settings.json` pre-approves a small set of clearly-safe commands. No rules/hooks/skills/agents are created — see "Deliberately out of scope".

**Tech Stack:** Markdown (CLAUDE.md), JSON (settings.json). No code, no test framework involvement.

## Global Constraints

- `CLAUDE.md` must stay **under 200 lines** (Anthropic best practice); target here is **< 50**.
- `CLAUDE.md` must contain **only what Claude can't infer** from code or tool configs. **No** code-style / linter-enforced rules (the repo has no formatter configured, and style is not Claude's job).
- Every file path referenced in `CLAUDE.md` **must exist** in the repo.
- `.claude/settings.json` must be **valid JSON**.
- Commit messages in **English**, imperative mood, **no** `Co-Authored-By` trailer (user global preference).
- This is config/docs, not code: there is no failing-test-first cycle. Each task's "verify" step is a **real guard** (line count, path existence, JSON validity), run before the commit.

## File Structure

- **Create `CLAUDE.md`** (repo root) — project orientation + non-inferrable commands and gotchas. One responsibility: onboard Claude to this repo in < 50 lines.
- **Create `.claude/settings.json`** — team-shared permission allowlist. One responsibility: cut routine confirmation prompts for safe commands.
- **Modify `.gitignore`** — add `.claude/settings.local.json` so per-user local settings are never committed even on machines without a global excludes file.

### Deliberately out of scope (do **not** create)

- `.claude/rules/` — premature for this repo size; the two domain gotchas fit in `CLAUDE.md`. (YAGNI.)
- `.claude/hooks` / PostToolUse formatter — no linter/formatter is configured (no `pyproject.toml`/`ruff.toml`), so there is nothing to run.
- `.claude/skills`, `.claude/agents`, `.claude/commands` — no recurring, specific use case.

---

### Task 1: Root `CLAUDE.md`

**Files:**
- Create: `CLAUDE.md`
- References (must exist; all verified present): `docs/superpowers/specs/2026-06-24-clarinet-pacs-proxy-design.md`, `plugin/proxy_core.py`, `plugin/clarinet_proxy.py`, `pytest.ini`, `staging/vm/README.md`, `etc/10-core.json`, `deploy/astra-notes.md`, `deploy/install.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `CLAUDE.md` at repo root — loaded into Claude's context every session. No programmatic interface.

- [ ] **Step 1: Write `CLAUDE.md`**

Create `CLAUDE.md` with exactly this content:

```markdown
# clarinet-pacs-proxy

Orthanc-based DICOM + DICOMweb pass-through proxy in front of a hospital PACS.
Full design: `docs/superpowers/specs/2026-06-24-clarinet-pacs-proxy-design.md`

## Layout

- `plugin/proxy_core.py` — pure proxy logic (no Orthanc imports, unit-testable)
- `plugin/clarinet_proxy.py` — Orthanc Python-plugin glue (callbacks, C-MOVE driver)
- `tests/` — unit suite over the core + glue with a fake Orthanc (no DICOM stack)
- `staging/` — end-to-end tests over a 3-node DICOM network (pacs/proxy/worker)
- `deploy/` — systemd units, `install.sh`, eviction; `etc/` — Orthanc JSON config

## Build / test

- `uv run pytest -q` — unit tests. Use `uv run`, not bare `pytest`; `pytest.ini`
  sets `pythonpath = plugin tests deploy`, so imports only resolve under it.
- `bash staging/vm/run.sh` — end-to-end suite. The host has no Docker; this brings
  the DICOM network up inside a throwaway QEMU/KVM VM (`staging/vm/README.md`).

## Gotchas

- **Charset:** C-FIND is answered in UTF-8 (`SpecificCharacterSet = ISO_IR 192`),
  driven by `DefaultEncoding: "Utf8"` in `etc/10-core.json`. Every Orthanc node on
  the path must use `Utf8` or non-ASCII (Cyrillic) names get down-converted and lost.
- **Astra Linux deploy:** read `deploy/astra-notes.md` before editing `deploy/install.sh`
  — plugin build differs per Astra version, plus libpython/ЗПС/МКЦ/GOST specifics.
```

- [ ] **Step 2: Verify size and that every referenced path exists**

Run:
```bash
wc -l CLAUDE.md
for f in docs/superpowers/specs/2026-06-24-clarinet-pacs-proxy-design.md \
         plugin/proxy_core.py plugin/clarinet_proxy.py pytest.ini \
         staging/vm/README.md etc/10-core.json deploy/astra-notes.md deploy/install.sh; do
  test -e "$f" && echo "OK   $f" || echo "MISS $f"
done
```
Expected: line count **< 50**; every path prints `OK` (no `MISS`).

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md with build/test commands and dev gotchas"
```

---

### Task 2: Team-shared `.claude/settings.json`

**Files:**
- Create: `.claude/settings.json`

**Interfaces:**
- Consumes: nothing.
- Produces: a `permissions.allow` list read by Claude Code at session start. Each entry is a `Bash(<prefix>:*)` or exact-command matcher.

- [ ] **Step 1: Write `.claude/settings.json`**

Create `.claude/settings.json` with exactly this content (minimal, clearly-safe commands only — read-only git plus the unit-test runner; the VM e2e script is intentionally **not** pre-approved because it is heavy/side-effecting):

```json
{
  "permissions": {
    "allow": [
      "Bash(uv run pytest:*)",
      "Bash(git status)",
      "Bash(git diff:*)",
      "Bash(git log:*)"
    ]
  }
}
```

- [ ] **Step 2: Verify it is valid JSON**

Run:
```bash
python3 -m json.tool .claude/settings.json
```
Expected: the file is pretty-printed back with no error (exit 0). A syntax error exits non-zero with a parse message.

- [ ] **Step 3: Commit**

```bash
git add .claude/settings.json
git commit -m "chore: add team-shared Claude Code permission allowlist"
```

---

### Task 3: Ignore local Claude settings

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: a `.gitignore` entry guaranteeing `.claude/settings.local.json` is never tracked, independent of any machine-global excludes file.

- [ ] **Step 1: Append the ignore entry**

Add this line to the end of `.gitignore`:

```
.claude/settings.local.json
```

After the edit, `.gitignore` reads:
```
__pycache__/
*.pyc
.pytest_cache/
.venv/
staging/.data/
*.log
.claude/settings.local.json
```

- [ ] **Step 2: Verify the path is ignored and the shared settings still tracked**

Run:
```bash
git check-ignore .claude/settings.local.json   # expect: prints the path (ignored)
git check-ignore .claude/settings.json || echo "settings.json NOT ignored (correct)"
```
Expected: first command prints `.claude/settings.local.json`; second prints `settings.json NOT ignored (correct)`.

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore local Claude Code settings"
```

---

## Self-Review

**1. Spec coverage** (spec = the audit findings):
- Audit rec #1 "create minimal CLAUDE.md" → Task 1. ✓
- Audit rec #2 "optional settings.json permission allowlist" → Task 2. ✓
- Audit note "`.gitignore` doesn't list settings.local.json (relies on global excludes)" → Task 3. ✓
- Audit recs "rules/hooks/skills not needed" → captured under "Deliberately out of scope". ✓

**2. Placeholder scan:** No `TBD`/`TODO`/"add appropriate…"/"similar to Task N". Every file's full content is inlined. ✓

**3. Type/name consistency:** Paths cited in `CLAUDE.md` (Task 1) all verified present in this repo. Permission matchers in Task 2 use Claude Code's `Bash(prefix:*)` syntax. `.claude/settings.local.json` (ignored, Task 3) is distinct from `.claude/settings.json` (tracked, Task 2). ✓
