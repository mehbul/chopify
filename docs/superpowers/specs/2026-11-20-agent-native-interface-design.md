# Agent-native interface design

## Goal

Make Chopify operable by AI agents without a hosted service or a new protocol:
give agents a stable, self-describing, machine-readable interface and a shipped
skill that teaches the exact workflow. An agent should be able to discover what
Chopify can do at runtime, drive a full source-to-exports job, detect and recover
from failures, and verify outputs — all from a shell, with no TTY, no MCP server,
and no changes to the human-facing app.

## Decisions made with the user

| Decision | Choice |
| --- | --- |
| Protocol | No MCP. A dedicated CLI interface plus a distributable agent skill. |
| Primary surface | `agent.py` with NDJSON stdout; human `chopify.py` unchanged. |
| Discovery | `agent.py describe` returns operations with JSON Schemas. |
| Scope | Interface layer + skill + docs. No changes to the editing pipeline. |

## Non-goals

- An MCP server, A2A, AG-UI, or any new network protocol.
- A daemon, long-lived process, or hosted API.
- New third-party dependencies; stdlib only (`argparse`, `json`, `contextlib`,
  `datetime`, etc.).
- Changing Studio, the loopback HTTP API, clip scoring, or rendering behavior.
- Automatic publishing or removing the human review step.
- Guaranteeing that an agent makes good editorial choices; scores remain
  suggestions.

## Architecture

The interface is a thin layer over existing repository functions. There are no
new core capabilities and no duplicated business logic.

```
AI agent --shell--> agent.py --imports--> projects / live / campaign / render_clips
                         |                         |
                         |                    project files, live sessions
                         v
                    NDJSON on stdout (result or typed error)
```

### 1. `agent.py` — machine-facing entry point

A new top-level module whose stdout is **always** newline-delimited JSON (NDJSON),
one object per line. It never prompts, never reads a TTY, never colors output,
and never prints a human log to stdout.

Operations are exposed as argparse subcommands:

| Command | Purpose |
| --- | --- |
| `describe` | Return the operation catalog with JSON Schemas (inputs and outputs). |
| `preflight` | Return the environment check result. |
| `styles` | Return the caption preset names. |
| `project create --source PATH [--options-json '{...}'] --workdir DIR` | Initialize a project; return id and path. |
| `project get --project DIR` | Return project state, progress, candidates, selection, files. |
| `project prepare --project DIR` | Run the prepare stage. |
| `project render --project DIR [--pick 1,3] [--preview] [--out DIR]` | Render selected clips. |
| `project wait --project DIR --timeout SECONDS` | Poll until a terminal status or timeout. |
| `batch create --file PATH [--projects-dir DIR] [--action prepare\|render]` | Create and run each source synchronously; return per-source results. |
| `batch wait --root DIR --timeout SECONDS` | Poll a batch root until all projects are settled. |
| `live start --source URL [--root DIR] [--options-json '{...}']` | Start a detached live worker; return id and directory. |
| `live status --id ID [--root DIR]` | Return session status, health, scores, triggers. |
| `live stop --id ID [--root DIR]` | Stop a live session and wait for `stopped`. |

`--projects-dir` and `--root` default to `projects`. A live session is read from
`<root>/live/<id>/live_session.json`. Because each `agent.py` invocation is a
short-lived process, `live start` cannot keep a thread alive: it spawns a
detached `agent.py _live-worker` process (new process group on Windows,
`start_new_session` on POSIX), redirects its output to
`<root>/live/<id>/agent.log`, and waits for `live_session.json` to appear. To
make this possible, `LiveSession.__init__` gains an optional `session_id`
argument (default `None`, preserving existing behavior). Stopping is
cross-process: `live stop` writes a `stop` file in the session directory, which
`LiveSession._loop()` checks at the top of each iteration and answers by calling
`self.stop()`. Neither change affects dashboard or CLI live sessions.

`--options-json` is a JSON object validated with `projects.validate_options`
(and `render_clips.validate_caption` for the `caption` field). Unknown keys,
wrong types, and out-of-range values are rejected as `INVALID_ARGUMENT` before
any work starts.

The human CLI (`chopify.py`) and the dashboard keep their current behavior. The
agent interface calls the same functions the CLI calls; it does not parse CLI
stdout.

### 2. Event and error contract

Every command emits a sequence of objects with a stable shape:

```json
{"event":"start","op":"project.prepare","ts":"2026-11-20T10:00:00Z","request_id":"..."}
{"event":"progress","op":"project.prepare","ts":"...","stage":"transcribe","done":12,"total":40}
{"event":"result","op":"project.prepare","ts":"...","data":{"status":"review","candidates":[...]}}
```

Failures emit a single error event instead of a result:

```json
{"event":"error","op":"project.render","ts":"...","request_id":"...",
 "code":"RENDER_FAILED","message":"2 clip(s) failed to render",
 "retryable":false,"details":{"render_errors":[...]}}
```

`progress` events are emitted when the called function reports progress; functions
without a progress callback emit only `start` and `result`. Every line has
`event`, `op`, and `ts`; `start`, `result`, and `error` also carry `request_id`.
The final line of a successful run is always `result`; the final line of a failed
run is always `error`.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Operation completed and a `result` was emitted. |
| 2 | Invalid input, unknown operation, or schema violation. |
| 3 | Operation failed (`error` emitted, `retryable` may be true or false). |
| 4 | Timeout or conflict (e.g. `project wait`, project busy). |

Stable error codes: `INVALID_ARGUMENT`, `INVALID_OPTIONS`, `NOT_FOUND`,
`PRECONDITION_FAILED`, `BUSY`, `TIMEOUT`, `PREFLIGHT_FAILED`, `PROCESS_FAILED`,
`RENDER_FAILED`, `LIVE_FAILED`. The `retryable` flag and `details` object give an
agent enough information to decide whether to fix input, wait, or escalate.

Human logs from underlying functions are redirected to stderr for the duration
of each operation, so stdout stays parseable. `--verbose` forwards those logs to
stderr instead of discarding them. Nothing is ever written to stdout except
NDJSON.

### 3. Self-description

`agent.py describe` returns a catalog object:

```json
{"version":1,"operations":[
  {"name":"project.create","summary":"Initialize a project from a source",
   "input_schema":{...},"output_schema":{...},"errors":["INVALID_ARGUMENT",...]}
]}
```

Schemas are generated from a single operation registry in `agent_contract.py`
(import-light, no core modules) so docs and runtime cannot drift. The registry
is the source of truth for subcommand parsing, validation, event metadata, and
the `describe` output. Handlers live in `agent_ops.py`; `agent.py` only builds
the parser, dispatches, and owns exit codes. The skill references `describe`
rather than hard-coding operation details.

### 4. `wait` semantics

`project wait` and `batch wait` poll project files on disk (no daemon). They
accept `--timeout` and return the settled state as a `result`; on deadline they
emit `TIMEOUT` with the last observed state in `details`. A project is settled
when its status is `complete`, `failed`, `interrupted`, or `review` — an agent
must stop waiting when a job needs editorial review and then call
`project get`. `interrupted` is reported as a state requiring an explicit retry.
Poll interval is 0.5 seconds. Agents that prefer to poll themselves can call
`project get` instead.

### 5. Distributable agent skill

A `SKILL.md` shipped at the repository root and mirrored under
`skills/chopify/SKILL.md` (identical content, two install paths). It teaches:

- When to use Chopify (local recording-to-shorts work) and when not to.
- Install/location instructions for `~/.claude/skills/chopify/SKILL.md` and
  `.agents/skills/chopify/SKILL.md`.
- The workflow: `describe → preflight → project create → project prepare →
  project get (review candidates) → project render → project wait → verify
  exports`.
- How to parse NDJSON, handle the error codes, and use `--verbose` for stderr.
- Constraints: loopback-only privacy, no publishing, keep local files private,
  review scores and captions before publishing, rights checks for streams.
- Pointer to `docs/AGENT-API.md` for project file formats.

The skill is instructions only; it must not claim capabilities the registry does
not expose.

### 6. Documentation changes

- `docs/AGENT-API.md`: add an "Agent interface" section with the command table,
  event/error contract, exit codes, and skill installation.
- `llms.txt`: add a bullet linking the agent interface and the skill; keep the
  existing factual tone.
- `docs/product.json`: add `agent_cli` to `interfaces` and a short
  `agent_interface` object (entry point, NDJSON, `describe`, skill path).
- `CHANGELOG.md`: add an `Added` entry under `[Unreleased]`.
- `tools/build_site.py`: add `"SKILL.md": "skill.md"` to the `FILES` allowlist so
  the installable skill ships with the public docs. The `skills/chopify/` copy is
  an install path, not a separate site asset; do not add it. Never add local
  projects, transcripts, or logs to the site.

## Data flow

1. Agent runs `python agent.py describe` and reads operation schemas.
2. Agent runs `preflight`; on `PREFLIGHT_FAILED` it fixes the environment or
   reports the problem rather than starting a job.
3. Agent creates a project with a source and validated options.
4. Agent runs `prepare` (optionally streaming progress), then `get` to review
   candidates and selection statistics.
5. Agent renders chosen clips, optionally waits for terminal status, and reads
   `last_outputs` / `files` to verify exports exist.
6. Any failure produces a typed error event with a stable code and retry flag;
   the agent recovers or escalates.

## Error handling

- All exceptions from core functions are caught and mapped to a stable code;
  unexpected exceptions map to `PROCESS_FAILED` with the message preserved.
- Validation happens before side effects. An `INVALID_OPTIONS` error must leave
  no new project or session behind.
- A partial render failure reports `RENDER_FAILED` with `render_errors` in
  `details`; successful outputs are still listed in `data` and are not deleted.
- `TIMEOUT` does not cancel work; it reports the last known state so the agent
  can poll again.
- Live commands return session handles and do not block for the life of a stream.

## Testing

- Contract unit tests: every operation emits `start` then exactly one terminal
  `result`/`error`; all lines are valid JSON with required fields; `request_id`
  is consistent within a run.
- `describe` test: catalog covers every registered operation; each schema is a
  valid JSON Schema object; registry and subcommands cannot drift.
- Validation tests: unknown option keys, out-of-range caption values, and
  malformed `--options-json` produce `INVALID_OPTIONS`/`INVALID_ARGUMENT` with
  exit code 2 and no project created.
- Stream purity test: capture stdout while a core function prints to stdout;
  assert stdout contains only NDJSON and the human text went to stderr.
- Exit-code tests for success (0), validation (2), failure (3), timeout (4).
- Offline integration test: create/prepare/get/render against a project with
  `projects.run_project` stubbed, asserting a full NDJSON conversation and that
  exports are reported. Real rendering stays in the existing suite.
- `wait` test: a state sequence that reaches `complete` and one that hits the
  deadline, asserting `TIMEOUT` includes last state.
- Skill test: verify both `SKILL.md` files exist, are identical, and reference
  only operation names present in `describe`.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| NDJSON polluted by library prints | Redirect stdout to stderr inside each operation; enforce with a test. |
| Registry and CLI drift | Generate parsing, validation, and `describe` from one registry. |
| Agent misreads scores as predictions | Skill and docs repeat that scores are suggestions; review stays mandatory. |
| Changes break the human CLI | `agent.py` is additive; no existing function signatures change. |
| Skill thinks a capability exists | Skill test cross-checks names against `describe`. |

## Acceptance criteria

- `python agent.py describe` lists every operation with valid input/output
  schemas and error codes.
- A full create → prepare → get → render → wait → verify conversation succeeds
  on a local source with no TTY and no network.
- stdout is valid NDJSON for success, progress, and every error path.
- Exit codes and stable error codes match this spec.
- Both `SKILL.md` files exist and are identical.
- Full existing test suite still passes; human CLI output is unchanged.
