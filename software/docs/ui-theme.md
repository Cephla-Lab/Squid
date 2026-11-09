# UI Theme (Light / Dark / Classic)

This document describes how the Squid GUI is skinned and, more importantly, the rules new GUI code must follow so it renders correctly in every theme. The design rationale and implementation plan are kept with the team's internal planning documents; this is the working reference.

## Overview

Squid ships three looks, chosen from **Settings > Appearance** and stored in the machine `.ini` as `[GENERAL] ui_theme`:

| Theme | What it is | Switch |
|---|---|---|
| `light` (default) | "Soft native" skin: Windows 11 Fluent feel, cards on a soft grey ground, one accent colour | live |
| `dark` | Same skin on charcoal surfaces, sky-blue accent | live |
| `classic` | The pre-skin look: Fusion style, default palette, no application stylesheet | after restart |

Light and Dark are one stylesheet rendered from two token sets. Classic is **not** a third stylesheet: it is the absence of one, plus the exact colour literals the code used before the skin existed. Every theme-aware call site therefore has two behaviours: "apply today's literal" (Classic) or "tag the widget and let the stylesheet decide" (Light/Dark).

Everything lives in `control/theme.py`. That module imports only the standard library, qtpy and pyqtgraph, never `control._def` or `squid.*` at module scope, so it can be imported before the configuration system and under either Qt binding.

## Architecture

```
Tokens (LIGHT / DARK / CLASSIC)          frozen dataclass: colours + geometry in logical px
   │
   ├─ build_stylesheet(tokens, scale, icon_dir)   one QSS string, lengths scaled by scale_factor(app)
   ├─ build_palette(tokens)                        QPalette for everything QSS does not reach
   └─ write_theme_icons(tokens, cache_dir)         icon/theme/*.svg recoloured into cache/theme_icons/<theme>/
                        │
install(app, theme)          module function: creates the one ThemeManager and applies the startup theme
ThemeManager (one per QApplication, theme.manager())
   .apply(theme)              palette + font first, then stylesheet, then the pyqtgraph dock patch
   .current / .is_classic()
   .needs_restart(theme)      True when crossing the Classic <-> skinned boundary
   .tokens() / .color(name) / .row_brush(name)     for colours painted by code
   .theme_changed  Signal(str)
```

Startup (`main_hcs.py`) calls `control.theme.install(app, control.theme.load_theme_choice())` right after `app.setStyle("Fusion")`. Any exception there falls back to Classic so a theme bug can never block the microscope.

Two details that are easy to break:

- **Order matters in `apply`.** The palette and font are set before the stylesheet. A stylesheet change repolishes every widget against the palette in force at that moment; a palette set afterwards does not reach already-polished widgets (symptom: white text edits and scroll areas after a live Light to Dark switch).
- **Classic on a pristine app is a no-op.** `apply("classic")` only undoes what the manager itself did. Calling `setFont`/`setPalette` on an untouched application would override the platform theme's per-class fonts and change Classic's rendering. Classic is verified pixel-identical to the pre-skin baseline; keep it that way.

## Rules for new GUI code

### 1. Never hard-code a chrome colour

Do not write `widget.setStyleSheet("color: red")`, `"background-color: #C2C2FF"` or similar for buttons, labels, frames, group boxes or any other chrome. Use a role:

```python
from control import theme

theme.set_role(self.btn_start, "primary")            # the one accent button of a panel
theme.set_role(self.btn_live, "action", on=is_live)  # a toggle; the on state is a boolean property
theme.set_role(self.lbl_hint, "note")                # muted secondary text
theme.set_role(self.lbl_error, "danger")             # bold error text
theme.set_role(panel, "card")                        # a floating card (sets WA_StyledBackground)
theme.set_role(group_box, "group-plain")             # an untitled QGroupBox without the title band
```

`set_role` applies the Classic literal in Classic and, in Light/Dark, clears the widget stylesheet, sets `role` (and any state) as dynamic properties and repolishes. States are independent properties, so a state omitted on a later call is switched off rather than left stale.

The full role list is `CLASSIC_STYLES` in `control/theme.py`. The common ones:

| Role | Meaning |
|---|---|
| `primary` | The accent button (Start Acquisition, Live) |
| `action`, `action:on` | A toggle button; `on=True` renders the engaged state |
| `action-bold` | Bold-text action button |
| `note`, `note-gray`, `note-engine`, `hint`, `hint-gray`, `mono-note` | Secondary text in its several historical greys |
| `danger`, `danger-soft`, `danger-text`, `alert`, `alert-bold`, `alert-group` | Error text (bold or not) and an error-coloured group box title |
| `success`, `status-ok`, `status-bad`, `status-idle`, `status-info`, `status-warn`, `status-neutral` | Status label colours |
| `alert-badge`, `banner-danger`, `banner-engine`, `callout-warning` | Filled badges and banners |
| `card`, `group-plain` | Layout containers |
| `drop-target` | Drag-and-drop highlight; use `set_drop_target_feedback(widget, active)` |

### 2. Multipoint group frames use `style_group`

The XY / Z / Time frames in the acquisition widgets switch between active and inactive looks through `theme.style_group(frame, axis, part, active)` with `axis` in `xy|z|time` and `part` in `frame|body`. Their Classic literals are in `CLASSIC_GROUP_STYLES`.

### 3. Colours painted by code come from tokens

Table and list rows coloured in code (disabled, running, highlighted, invalid, conflicting rows), custom-painted status widgets and the Jupyter console take their colours from the manager, never from literals:

```python
m = theme.manager()
item.setBackground(m.row_brush("row_highlight"))
item.setForeground(m.row_brush("row_text_muted"))
pen = QPen(m.color("danger_text"))
```

In Classic the token values are the literals the code used before, so behaviour is unchanged there. If a widget stays open across a live Light/Dark switch, connect to `theme.manager().theme_changed` and recolour (see `ControllerPortMappingDialog._recolour_rows` or `WarningErrorWidget._restyle` for the pattern).

### 4. What may stay literal

Content colours are not chrome and stay as they are: channel swatches and chips coloured from channel data (`setStyleSheet(f"...")` built from a data colour), status dots, laser line colours, painted icons (the homing stop sign), image and thumbnail pixels, plot curves. The rule of thumb: if the colour carries information rather than decorating the frame around it, it is content.

### 5. Adding a role

A new meaning needs three edits, and a test enforces the third:

1. `CLASSIC_STYLES["<role>"]` in `control/theme.py`: the literal the widget would have used before (so Classic is unchanged).
2. A `[role="<role>"]` rule in `build_stylesheet`, written against tokens (`{accent}`, `{muted}`, ...), never a hex literal.
3. If the rule needs a colour that no token provides, add the field to `Tokens` and give it a value in all three of `LIGHT`, `DARK` and `CLASSIC`.

`tests/control/test_theme_roles.py::test_every_classic_role_has_a_light_rule` fails when step 2 is missing. `tests/control/test_theme_conversion.py` scans the converted widget files for the old literals and fails if one creeps back; add new widget files to its `SCANNED` tuple when you convert them.

### 6. Widths and clipping

The skin uses Segoe UI 8 pt on Windows (the system UI font elsewhere) and keeps Fusion's natural control heights, so the Controls column is within a few percent of its Classic width. Padding is tuned so no label clips at Classic's column width; if you add a wide control, check it in Light with the screenshot tool below rather than widening the column.

## Persistence and the Appearance menu

- `load_theme_choice()` reads `control._def.UI_THEME` (parsed from `[GENERAL] ui_theme`; unknown values fall back to `light`).
- `save_theme_choice(ini_path, choice)` writes the key through `control.utils.write_ini_atomically`, the same writer the Preferences dialog uses, and returns `False` instead of raising when the ini is missing or read-only.
- The menu (`HighContentScreeningGui._build_appearance_menu`) tracks the **saved** theme separately from the active one. "(after restart)" is shown on whichever entry is saved but not active. Light and Dark apply live; entering or leaving Classic saves first, then offers "Restart Now / Later". A failed save never changes the saved marker, and the warning says which theme the next start will use.

## Checking how it looks

`tools/ui_screenshot.py` runs the simulated GUI on the real display and captures eight panels per theme:

```bash
cd software
python tools/ui_screenshot.py --theme light --out cache/ui_check/light
python tools/ui_screenshot.py --theme classic --out cache/ui_check/classic
python tools/ui_screenshot.py --compare cache/ui_baseline/classic/01-main-default.png cache/ui_check/classic/01-main-default.png --out cache/ui_check/diff
```

It prints the font in use and the measured Controls column width, installs the operator's machine ini only for the run (from `cache/ui_screenshot/*.ini`), refuses a configuration that would drive real hardware under `--simulation` (a Prior stage), never reads or writes the operator's persisted GUI state, uses a private single-instance lock and log directory so it can run beside a real Squid, and exits non-zero if a capture is missing. The offscreen Qt platform renders no text on Windows, so screenshots need a real display; the logic tests do not.

## Tests

| File | Covers |
|---|---|
| `tests/control/test_theme.py` | tokens, contrast ratios, stylesheet and palette generation, icon recolouring |
| `tests/control/test_theme_manager.py` | apply order, Classic no-op, restart rule, dock patch guard |
| `tests/control/test_theme_persistence.py` | ini read/write, atomic writer, failure modes |
| `tests/control/test_theme_roles.py` | `set_role`, states, `style_group`, every role has a Light rule |
| `tests/control/test_theme_conversion.py` | no old literals in converted widget files |
| `tests/control/test_theme_brushes.py` | row brushes and code-painted colours (GUI, own process) |
| `tests/control/test_theme_menu.py` | the Appearance menu end to end (GUI, own process) |

The two GUI modules build the main window; run them in their own pytest process with `QT_QPA_PLATFORM=offscreen`, as CI does for `test_HighContentScreeningGui.py`.

## Known gaps

- The navigation viewer keeps its white canvas in Dark; its redraw is a separate piece of work.
- The skin has been verified on Windows 11 at 150 % DPI (PyQt5 5.15) and in an isolated PyQt6 environment; the Ubuntu 22.04 visual check is still to be done.
