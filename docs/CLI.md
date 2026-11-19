# CLI reference

Run `python chopify.py SOURCE [options]` from the checkout with its Python
environment activated. `SOURCE` is a local video path or a yt-dlp-supported URL.
It is optional only when resuming a project or reading a batch file. Paths and
text containing spaces should be quoted.

The tables list each current option, including the shared caption controls.
Examples in the last column are argument values, not complete commands. Defaults
describe a new project: an existing project retains saved settings unless an
option is explicitly supplied. See the [workflow guide](WORKFLOWS.md) for complete
commands and project layout.

## Input

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--live` | none | Watch a live HLS/RTMP URL (or a live page URL resolved with yt-dlp) and auto-clip hype moments. Mutually exclusive with a source, `--batch`, and `--project`. | `"https://twitch.tv/channel"` |
| `--workdir` | `work` | Intermediate files and single-video project directory. | `"work/podcast"` |
| `--whisper-model` | `medium` | Model name passed to faster-whisper; common names are `tiny`, `base`, `small`, `medium`, and `large-v3`. | `small` |
| `--device` | `cpu` | `cpu`, `cuda`, or `auto` for transcription. | `cuda` |

## Video types and formats

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--video-type` | `auto` | `auto` or one of `podcast`, `gaming`, `movie`, `livestream`, `interview`, `tutorial`, `music`, `unknown`. An explicit type overrides local detection; `auto` detects from cached audio, visual, transcript and source metadata signals. | `gaming` |
| `--format` | none | A built-in or custom format name, or a path to a format JSON file. Overrides the detected type's default format. | `my_cast` |
| `--video-type-model` | none | Optional local Ollama chat model used only to resolve uncertain detections. No cloud fallback. | `qwen2.5:7b` |
| `--list-formats` | off | Print built-in and custom format names and exit. | (flag) |

A format is a saved recipe that supplies existing options: clip length band,
scoring profile, caption style, layout, fit, tighten, loudnorm, hook wording and
(for streams) live thresholds. Explicit flags always win over format values.
When detection is uncertain, an interactive run offers **use suggested / pick a
format / build a new format**; `--no-input`, batch and agent runs use the best
guess and record the choice without prompting.

Built-in formats:

| Type | Band (s) | Profile | Caption style | Layout / fit | Tighten | Loudnorm |
| --- | --- | --- | --- | --- | --- | --- |
| podcast / interview | 45–180 | talk | podcast | single / cover | yes | yes |
| gaming | 15–60 | gameplay/action | bold | stacked / cover | no | no |
| livestream | 15–90 | gameplay/action | bold | single / cover | no | no |
| movie | 60–300 | mixed | minimal | single / contain | no | no |
| tutorial | 30–180 | mixed | clean | single / cover | no | yes |
| music | 15–90 | mixed | neon | single / cover | no | yes |
| unknown | 30–120 | mixed | default | single / cover | no | no |

A stacked or split layout is used only with `--aspect 9:16`; otherwise `single`
is kept. A custom format is a JSON object validated on save:

```json
{
  "name": "my_cast",
  "video_type": "podcast",
  "min_len": 45,
  "max_len": 180,
  "profile": "talk",
  "caption_style": "podcast",
  "layout": "single",
  "fit": "cover",
  "tighten": true,
  "loudnorm": true,
  "hook_style": "insight",
  "live": {}
}
```

Custom formats are stored in `formats/<name>.json` beside the app and appear in
`--list-formats` and Studio.

## Live settings

These options apply only with `--live`. See the [live clipping guide](LIVE.md)
for signals, session files, and rights notes.

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--live-buffer` | `300` | Rolling segment retention in seconds (60–3600). | `600` |
| `--live-buffer-max-mb` | `512` | Buffer disk cap in megabytes (32–8192). | `1024` |
| `--live-pre-roll` | `30` | Seconds saved before a trigger (5–300). | `45` |
| `--live-tail` | `5` | Seconds saved after a trigger (0–60). | `10` |
| `--live-window` | `10` | Scoring window in seconds (2–60). | `15` |
| `--live-threshold` | `8.0` | Hype score needed to save a clip (0–10). | `7.5` |
| `--live-cooldown` | `60` | Minimum seconds between clips (0–3600). | `120` |
| `--live-max-clips` | `10` | Session clip cap, `0` for unlimited. | `20` |
| `--live-no-chat` | off | Disable the chat signal. | (flag) |
| `--live-no-transcript` | off | Disable the Whisper signal. | (flag) |
| `--live-skip-ads` | off | Suppress clip triggers while a flat, saturated ad slate is detected. Best-effort; footage is never discarded. | (flag) |
| `--live-header` | none | Repeatable `Name: Value` ffmpeg header, e.g. a needed `Referer`. | `"Referer: https://example.com/"` |
| `--brief` | none | Campaign requirements text; a local LLM extracts rules and only matching clips are kept. | `"Must mention: price. Avoid: spoilers. 15-30s, 9:16"` |
| `--brief-file` | none | Path to a `.txt`/`.md` file containing the brief. Conflicts with `--brief`. | `"briefs/campaign.md"` |
| `--brief-provider` | auto | `ollama`, `claude`, `codex`, `hermes`, or `none` to force keyword rules. Auto-detected from installed tools by default. | `claude` |
| `--brief-command` | none | Custom local command template with `{prompt}` for other agents. | `"my-llm --ask {prompt}"` |

## Scoring and discovery

Without a discovery query, Chopify scores candidates heuristically, or with a
local Ollama model when selected. Keyword and semantic search cannot be combined.
In an interactive single-video run, omitted score and preferred-length settings
are prompted for; non-interactive runs use the defaults below.

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--llm` | Disabled | Optional Ollama model name; supplying the flag without a value selects `qwen2.5:7b`. | `qwen2.5:7b` |
| `--search` | Disabled | Text whose keywords must all occur in matching transcript sentences; skips virality scoring. | `"pricing plans"` |
| `--semantic` | Disabled | Meaning-based query using local embeddings. | `"advice for new creators"` |
| `--embed-model` | `nomic-embed-text` | Local embedding model name for semantic search. | `nomic-embed-text` |
| `--min-score` | `8.0` | Finite score from `0` to `10`. | `7.5` |
| `--preferred-len` | AI chooses | Soft duration target, clamped to the minimum and maximum clip length. Accepts seconds or duration text. | `45s`, `2m`, `1m30s` |
| `--max-clips` | All passing clips | Optional integer safety cap from `1` to `3600`. | `5` |
| `--min-len` | `15` | Minimum clip duration in seconds, an integer from `1` to `3600`; cannot exceed the maximum. | `30` |
| `--max-len` | `300` | Maximum clip duration in seconds, an integer from `1` to `3600`. | `120` |
| `--explain` | Off | Print score components and selection reasons. | No value |

## Captions

Available named presets are `default`, `clean`, `minimal`, `bold`, `boxed`,
`karaoke`, `pop`, `neon`, `typewriter`, `retro`, `hormozi`, `mrbeast`, and `podcast`.
Caption settings merge in this order: named preset, brand file, then explicit
caption overrides. Unset caption controls inherit the selected style; the table
also shows the value in the `default` preset where applicable.

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--list-styles` | Not requested | Print every caption preset name, one per line, then exit before any source check or preflight; the output matches the values `--style` accepts. | No value |
| `--style` | `default` | One of the named presets listed above. | `hormozi` |
| `--caption-json` | No override | Path to a JSON caption-style object; explicit caption flags override its fields. | `"styles/captions.json"` |
| `--words-per-cue` | Inherit (`3`) | Integer from `1` to `8`. | `2` |
| `--uppercase` | Inherit (`false`) | Force uppercase captions. | No value |
| `--no-uppercase` | Inherit | Explicitly disable uppercase captions. | No value |
| `--caption-position` | Inherit (`bottom`) | `bottom`, `center`, or `top`. | `center` |
| `--caption-animation` | Inherit (`none`) | `none`, `pop`, `bounce`, `fade`, or `typewriter`. | `pop` |
| `--caption-background` | Inherit (`none`) | `none` or `box`. | `box` |
| `--caption-outline` | Inherit (`5`) | Integer width from `0` to `12`, measured at 1080px output height. | `4` |
| `--caption-shadow` | Inherit (`2`) | Integer depth from `0` to `12`, measured at 1080px output height. | `0` |
| `--emoji` | Inherit (`false`) | Add one relevant emoji per cue. | No value |
| `--no-emoji` | Inherit | Explicitly disable emoji. | No value |
| `--emphasis` | Inherit (`false`) | Emphasize numbers, money, and power words. | No value |
| `--no-emphasis` | Inherit | Explicitly disable emphasis. | No value |
| `--caption-font` | Inherit (`Arial Black`) | Plain font-family name, 1–80 letters, digits, spaces, underscores, dots, or hyphens. | `"Arial Black"` |
| `--caption-color` | Inherit (`#FFFFFF`) | Caption color in `#RRGGBB` form. | `"#FFFFFF"` |
| `--caption-highlight` | Inherit (`#FFFF00`) | Active-word color in `#RRGGBB` form. | `"#FF7A00"` |
| `--caption-emphasis-color` | Inherit (`#FFD400`) | Emphasized-word color in `#RRGGBB` form. | `"#FFD400"` |

## Output

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--aspect` | `16:9` | `16:9`, `9:16`, or `1:1`. | `9:16` |
| `--fit` | `cover` | `cover` crops to fill the frame (speaker-tracked when possible); `contain` keeps the entire source frame visible with black bars. Good for gameplay/screen captures. | `contain` |
| `--out` | `CHOPIFY_OUT`, otherwise `./clips` | Export directory for the single-project CLI run; an explicit value overrides the environment variable. | `"exports/vertical"` |
| `--tighten` | Off | Remove filler words and long silences, retiming captions. | No value |
| `--loudnorm` | Off | Normalize audio to -14 LUFS. | No value |
| `--clips` | All selected candidates | Comma-separated candidate numbers to render from the score table. | `1,3,5` |
| `--preview` | Off | Render fast 480p-class drafts without poster or metadata export. | No value |
| `--brand` | No brand file | Path to a JSON brand preset. | `"brands/podcast.json"` |
| `--layout` | `single` | `single`, `split`, or `stacked` (gaming facecam above gameplay). `split` and `stacked` require vertical `9:16` output. | `stacked` |
| `--facecam-box` | none | Normalized `x y w h` (0–1) rectangle of the facecam in the source frame. Used by `stacked`; auto-detected when omitted. | `0 0.62 0.26 0.30` |
| `--facecam-height` | `0.34` | Fraction of output height used by the facecam panel in `stacked` layout (0.20–0.60). | `0.4` |
| `--centers` | `0.25 0.75` | Two finite horizontal speaker centers, each from `0` to `1`, for split-screen. | `0.2 0.8` |

## Batch and project

A batch cannot be combined with a positional source or an existing project.
Batch jobs retain their outputs inside their own project directories, rather
than copying them into the single-project export directory.

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `--project` | No existing project | Existing project directory to resume or render again. | `"projects/abc123"` |
| `--projects-dir` | `projects` | Storage directory for batch projects. | `"batch-projects"` |
| `--batch` | No batch | Path to a JSON list of local video paths or URLs. | `"batch.json"` |
| `--resume` | On | Reuse successful stages whose inputs still match. | No value |
| `--no-resume` | Off | Reprocess instead of reusing successful matching stages. | No value |
| `--action` | `all` | `all` runs every stage; `prepare` discovers candidates for review; `render` exports saved edits. | `render` |

## Diagnostics

| Flag | Default | Accepted values and behavior | Example value |
| --- | --- | --- | --- |
| `-h`, `--help` | Not requested | Print usage and all current options, then exit. | No value |
| `--version` | Not requested | Print `Chopify <version>` and exit before any source check or preflight; works with no video, ffmpeg, or network. | No value |
| `--no-input` | Off | Never prompt; use supplied options and defaults. | No value |
| `--no-preflight` | Off | Skip environment checks for ffmpeg, encoders, and Python packages; does not install or replace missing dependencies. | No value |

The normal preflight runs before pipeline stages and reports missing local
dependencies. These options do not change the requirement for working local
tools when actually downloading, transcribing, or rendering a source.
