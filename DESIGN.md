# Design — Chopify Studio

## Direction

**Signal Desk** (factory-records catalog identity), seeded direction `f8fe4d38`.
The Studio is a catalog console: a matte black field, white hairline plots,
catalog numbers, and status strips that decode on hover/focus. It refuses the
generic card-grid admin layout.

Raises folded in from declined challengers:
- From Japanese high-density web: ruled modules carry more information per
  screen; whitespace does not inflate the tool.
- From airport wayfinding: every state names the next action and its wait time.

## Tokens

| Role | Value |
| --- | --- |
| Background | `#0b0e0f` (matte black) |
| Rail | `#080a0b` |
| Surface | `#111516` / `#171c1d` |
| Hairline | `#2b3234` / `#1f2527` |
| Ink | `#f2f4ee`, dim `#bcc5c1`, muted `#8c9692` |
| Decode accent | `#d4ef83` (carried from the incumbent mark) |
| Status codes | ready `#d4ef83`, running `#f2c14e`, failed `#ff8a6b`, queued `#8db8ff` |
| Type | system sans for UI; `Cascadia Code`/Consolas for codes and measurements |

## Components

- **Rail / index**: fac-numbered project entries in a ruled list; active entry
  uses a full 1px accent outline, never a thick side bar.
- **Job plate**: project header with `FAC XXXXX` code, status strip, and a
  hairline pulse canvas driven by recent scores.
- **Ruled lists**: candidates, transcript words, outputs, and live clips are
  hairline-separated rows, not equal-size cards.
- **Forms**: labels above fields, 44px minimum targets, hairline borders, accent
  focus ring.
- **Live view**: same grammar as the job plate, with clip feed as ruled rows.

## Motion and states

One authored motion: the pulse traverse on `#job-pulse`, stopped entirely under
`prefers-reduced-motion`. Buttons step 2px on hover and press on click. Focus,
hover, disabled, loading, error, empty, and success states are styled.

## Responsive

At 860px the rail becomes a horizontal index, the workspace becomes one column,
panel metadata hides so headings breathe, and all controls remain 44px tall.
At 420px grids collapse to one column and word rows keep numeric fields usable.

## Copy

Labels name actions and objects. Marketing language was removed: "Pick the
clips. Export them." replaces the earlier slogan; "Render settings" replaces
"Make it yours". No facts, capabilities, or claims were invented.

## Verification

- `impeccable detect.mjs`: no findings (regex fallback; parser modules
  unavailable, so computed contrast was not evaluated).
- Unslop phrase scan: 0 hard hits; 2 soft hits caused by concatenating separate
  UI labels into one test line, protected as label boundaries.
- `python -m pytest -q`: 373 passing.
- `node --check assets/studio/app.js`: clean.
- Desktop 1440 and phone 390 screenshots captured with the taste skill's
  headless shooter; no console errors in the live Studio tab.

## Open decisions

None for this surface. The palette may be revisited if the user wants the
incumbent lime accent replaced outright; it was kept as the decode color for
brand continuity.
