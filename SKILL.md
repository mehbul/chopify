---
name: chopify
description: Use when the user wants to turn a local recording or video URL into scored, captioned shorts with Chopify, or wants to operate an existing Chopify project, batch or live session. This skill covers the agent CLI only; use the Chopify Studio docs for browser work.
---

# Chopify agent interface

Chopify turns a long video into reviewed, captioned 9:16 clips on the user's
computer. Use it for local files or yt-dlp URLs. It makes no uploads and needs
no API key. Scores are editing suggestions, not predictions of virality.

## Install

The skill is shipped in the Chopify repository as `SKILL.md`. Copy that single
file to either location:

- `~/.claude/skills/chopify/SKILL.md`
- `.agents/skills/chopify/SKILL.md`

Then run commands from the Chopify checkout with the project's Python:
`python agent.py <command> ...`.

## Output contract

`agent.py` prints newline-delimited JSON to stdout. Every line has `event`,
`op`, `ts`; `start`, `result` and `error` also have `request_id`. Parse the last
line: it is always `result` (with `data`) or `error` (with `code`, `message`,
`retryable`, `details`). Human logs go to stderr with `--verbose`; otherwise
they are discarded. Never parse stdout as plain text.

Exit codes: `0` success, `2` bad input, `3` operation failed, `4` timeout.

## Commands

`describe`, `preflight`, `styles`, `project.create`, `project.get`,
`project.prepare`, `project.render`, `project.wait`, `batch.create`,
`batch.wait`, `live.start`, `live.status`, `live.stop`. Each maps to a CLI
subcommand such as `python agent.py project create --help`.

## Workflow

1. `python agent.py describe` — read the operation catalog and JSON Schemas.
2. `python agent.py preflight` — if `PREFLIGHT_FAILED`, fix the environment or
   report `details.problems`; do not start a job.
3. `python agent.py project create --source SOURCE [--options-json '{...}']`
   — save the returned `id` and `path`.
4. `python agent.py project prepare --project PATH` — may stream `progress`
   events. When it returns, status is usually `review`.
5. `python agent.py project get --project PATH` — review `candidates`,
   `selection` and `files`. Do not treat `hook` or `overall` as guaranteed.
6. `python agent.py project render --project PATH --pick 1,3 [--out DIR]`
   — render the chosen one-based clip numbers.
7. `python agent.py project wait --project PATH --timeout 600` — returns when
   the project is settled. `review` is settled; `interrupted` needs an explicit
   retry.
8. Verify the exports listed in `last_outputs` / `files` before telling the user
   the job is done.

For many sources, write a JSON array to a file and use
`python agent.py batch create --file FILE [--action prepare|render]`, then
`batch wait --root projects`. For live streams use `live start`, `live status`
and `live stop` with the returned `id`.

## Error recovery

- `INVALID_ARGUMENT` / `INVALID_OPTIONS`: fix the command or options object and
  retry. No project is created on validation failure.
- `NOT_FOUND`: check the project path or live id.
- `BUSY`: wait and call `get` / `status` again.
- `TIMEOUT`: the work may still be running; poll `get` rather than restarting.
- `PREFLIGHT_FAILED`: install the missing tool or package listed in `details`.
- `RENDER_FAILED`: successful clips are kept; read `details.render_errors` and
  retry only the failed ones.
- `PROCESS_FAILED` / `LIVE_FAILED`: inspect the message and `details`; use
  `--verbose` on a retry to capture human logs.

## Constraints

- Everything runs locally. Keep project files, transcripts and recordings
  private; never publish a running Studio token.
- Do not publish, schedule or upload clips. The user reviews and publishes.
- Live streams carry rights obligations; read `docs/LIVE.md` before clipping.
- Prefer `describe` over memorizing flags, and re-read it after upgrading.
- Full file formats are in `docs/AGENT-API.md`.
