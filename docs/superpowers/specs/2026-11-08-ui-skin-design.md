# Squid UI skin: "Soft native" theme, light by default, with Dark and Classic

Date: 2026-11-08. Branch: `worktree-ui-modern-skin`. Status: design approved in conversation; plan to follow.

## Intent and why

The Squid GUI (`software/main_hcs.py`, PyQt5 5.15 through qtpy, Qt "Fusion" style) looks dated. The
widgets are default Fusion grey with bevels, the Windows font is Qt's fallback "MS Shell Dlg 2" at 8 pt
(Tahoma), action buttons are an ad-hoc lavender (`#C2C2FF`), the scan-settings rows sit on a peach band, and
the pyqtgraph dock title bars are purple. The person who runs the instrument said plainly: the grey default
widgets look dated.

We want Squid to look like a current application without changing how it works. Same panels in the same
places, same tabs, same controls, same workflow. Only the clothes change.

Three directions were mocked up on the real control column (a crisp light web-app look, a dark "lab" look,
and a Windows 11 Fluent "soft native" look). The soft native direction was chosen, with the requirement that
it also supports a dark mode. The reference mockup is
[`assets/2026-11-08-ui-skin-mockup.html`](assets/2026-11-08-ui-skin-mockup.html) (open in a browser; the
Light/Dark and Segoe/Ubuntu buttons at the top are mockup controls, not product). The current look is in
[`assets/2026-11-08-ui-skin-before.png`](assets/2026-11-08-ui-skin-before.png), captured from
`main_hcs.py --simulation` with the Aries machine config.

## Goals

1. Squid's chrome (window, docks, tabs, inputs, buttons, menus, status bar, dialogs) renders in the soft
   native look described below, in a Light theme and a Dark theme.
2. Light is the default for everyone after the update.
3. Light and Dark switch at runtime from the Settings menu with no restart. Entering or leaving Classic
   changes the application font and takes effect at the next start (see Switching semantics). The choice
   persists either way.
4. A Classic theme reproduces today's rendering exactly, as an escape hatch.
5. No hard-coded colour anywhere in the GUI escapes the theme: Dark has no unreadable spots.
6. The same skin renders on Windows 11 (including 150 % display scaling) and Ubuntu 22.04.
7. One module owns every colour, radius and font decision, so a brand recolour later is a token change.

## Non-goals

- Rearranging panels, changing the widget hierarchy, or altering any behaviour.
- A new icon set. The handful of existing icons stay.
- Re-drawing the navigation viewer (`NavigationViewer` in `control/core/core.py`). It renders a white
  background PNG with OpenCV drawing inside a white pyqtgraph `GraphicsLayoutWidget`. It will look like a
  white tile in Dark. That is accepted for this change; a follow-up PR redraws it theme-aware.
- Re-theming the image canvases: napari viewers, the live-view image area, the LUT/histogram strip and the
  pyqtgraph plots keep their dark canvases in all three themes, as today.
- Following the operating system's light/dark setting automatically. Possible later on both platforms;
  not in this cut.
- The fluidics GUI (`gui.py` in the separate Squid-Fluidics checkout) and the `ndviewer_light` and
  `fluidics_v2` submodules. They are separate codebases.
- Bundling a typeface. Each platform uses its system UI font.

## Anti-goals (what would make this a failure even if it "works")

- The Controls dock gets wider or text clips because padding grew. The dock width comes from
  `minimumSizeHint`; the skin must keep control heights and paddings within a few pixels of today.
- Classic stops being today's look. Anyone who picks Classic must see exactly what they see now.
- A new runtime dependency (no `qdarkstyle`, `qt-material`, `PyQt-Fluent-Widgets` or similar). Hand-written
  stylesheet and palette only.
- Startup gets noticeably slower, or theme switching flickers through per-widget restyling. One
  application-level stylesheet plus palette; widgets carry roles, not colours.
- Dark mode by inversion. Dark is its own palette, with a lighter accent so filled buttons stay legible.

## Decisions already made (by the person who asked for this)

- Direction: soft native (Windows 11 Fluent feel), not the crisp web-app or dark-lab directions.
- Light is the default. Dark is a manual choice.
- Classic (today's look) stays available as a selectable fallback.
- The navigation viewer is out of scope; separate PR.
- The Well Selector keeps square cells at today's size, centred in its dock.
- Ubuntu gets the identical skin; only the typeface differs (system font).
- Windows typeface: "Segoe UI" at 8 pt, for now. Qt's default there is "MS Shell Dlg 2" 8 pt (Tahoma), a
  large part of the dated feel. 9 pt was shown on the real window and declined for now because it widens
  the fixed-width Controls column by about 11 % (828 vs 748 device px at 150 %); 8 pt is width-neutral
  (765 px, +2 %).

## Decisions made by me (flagged for review; change freely)

- **Accent colour**: Windows blue, `#0067c0` in Light and `#4cc2ff` in Dark. A Cephla brand colour can
  replace it later by editing one token.
- **Typeface on Linux**: `QFontDatabase.systemFont(QFontDatabase.GeneralFont)`, which is the Ubuntu font
  on Ubuntu and is usually what Qt already picks there. Classic keeps Qt's default font on every platform.
  (The Windows size was the person's decision; see above. Moving to 9 pt later is a one-token change plus
  a re-check of the Controls column width, which grows about 11 % at 9 pt.)
- **Where the switch lives**: Settings menu, an "Appearance" submenu with three exclusive radio actions:
  Light, Dark, Classic. Applies immediately.
- **Where it persists**: in the machine `.ini` under `[GENERAL]` as `ui_theme = light | dark | classic`,
  the same store the Preferences dialog's Views tab already uses for UI preferences. Read at startup through
  `control._def` (default `light` when absent). Written by the menu action using the same ConfigParser
  write path `PreferencesDialog` uses.
- **Mechanism**: a new theme module (suggested `software/control/theme.py`) owns the tokens (colours, radii,
  font) for Light and Dark, generates one application stylesheet (QSS) plus a matching `QPalette` from them,
  and applies both to the `QApplication`. Classic applies no stylesheet and the default palette, after
  `app.setStyle("Fusion")`, exactly as `main_hcs.py` does today.
- **Roles, not colours, on widgets**: the ~130 `setStyleSheet(...)` calls with literal colours across 17
  files (`control/widgets.py`, `control/gui_hcs.py`, `control/laser_engine_widget.py`, `control/laser_engine_v2_widget.py`, `control/laser_engine_v2_bench.py`,
  `control/versalase_widget.py`, `control/widgets_claude.py`, `control/widgets_slack.py`,
  `control/widgets_workflow.py`, `control/widgets_z_lift.py`, `control/widgets_fluidics/*.py`,
  `tools/view_laser_af_reference_image.py`) become dynamic properties the central stylesheet styles, for
  example `setProperty("role", "primary")`, `setProperty("tone", "warning")`, `setProperty("active", True)`.
  Dynamic state changes call `style().unpolish(w); style().polish(w)`. In Classic the same roles resolve to
  today's literal colours (lavender `#C2C2FF` primary, `#C2FFC2` toggled load-slide, peach band, `#666`
  italic notes, red warnings), so Classic stays pixel-faithful. Colour swatches that show data (channel
  colours, the colour picker buttons) keep their literal colours in every theme; they are content, not chrome.
- **Colours set outside stylesheets count too.** Row brushes and palette colours are set directly in
  code: the controller-port mapping rows (`control/widgets.py` ~15690, a 240,240,240 background), a
  palette background (`control/widgets.py` ~3559), workflow rows (`control/widgets_workflow.py` ~485–487
  and ~734, light-blue background and grey text), fluidics protocol rows
  (`control/widgets_fluidics/protocol_tab.py` ~773–776, `_INVALID` and `_HIGHLIGHT` brushes) and the
  Objectives editor's conflict rows (`control/widgets_objectives.py` ~302, `_CONFLICT`). In Dark, a
  240,240,240 row under `#f0f0f0` text is invisible. These become theme tokens (`row-highlight`,
  `row-invalid`, `row-conflict`, `row-disabled`, `text-muted`) read from the theme module at the moment the
  rows are painted or refreshed, and the owning widgets re-apply them on the theme module's
  `theme_changed` signal so a Light ↔ Dark switch repaints them. Classic returns today's literals for the
  same tokens. A test checks every row-brush token in Light and Dark against that theme's text colour for
  a contrast ratio of at least 4.5:1.
- **pyqtgraph docks**: the purple `DockLabel` is drawn by pyqtgraph's `DockLabel.updateStyle` with built-in
  colours. Override it (subclass used through a small factory, or a targeted monkeypatch applied by the theme
  module) so the dock bars are quiet card headers in Light/Dark and untouched in Classic. The `DockArea`
  background follows the theme ground colour.
- **Image canvases stay dark** in all three themes. The napari viewers keep their `dark` theme.
- **Icons for stylesheet parts**: QSS needs images for combo-box arrows, spin-box arrows, check marks and
  radio dots. Ship a few tiny SVGs under `software/icon/theme/`, recoloured at load time from the theme's
  tokens: arrows in the muted colour, check marks and radio dots in the accent-text colour. Assets, not
  dependencies.
- **Density**: control height about 26 px, 4 px radius on controls, 6 px on the tab tray, 8 px on cards
  (all logical px, see Constraints). The card "padding" is the sections' existing layout margins; the
  stylesheet adds only the 1 px border, so cards add about 2 px per section to the Controls column, not
  20. No layout edits.
- **Switching semantics**: Light ↔ Dark apply instantly (same font, same metrics; one stylesheet and
  palette re-apply plus a polish pass). Switching to or from Classic changes the application font, and
  several sizes are frozen from `minimumSizeHint()` at construction (`centralWidget`, the Controls dock,
  the Well Selector dock, the laser-focus docks in `gui_hcs.py`), so that direction takes effect at the
  next start: the menu action saves the choice and offers the existing `restart_application` path, the
  way other restart-requiring settings do. If the person picks "Later", the menu shows the saved choice
  checked (it is what the next start will use), and while the saved choice differs from the active theme
  its label carries the suffix "(after restart)". Acceptance 3 (Classic matches today) is checked from a
  cold start.
- **Shadows**: QSS has no box-shadow. The faint card and pill shadows in the mockup are approximated with
  borders (a slightly darker 1 px bottom border on cards and the selected tab pill).
- **Jupyter console** (`USE_JUPYTER_CONSOLE`): qtconsole sets its own light stylesheet; in Dark, apply its
  built-in dark style (`qtconsole.styles`), otherwise it stays a light island.

## The look, as tokens

Geometry and typography (both themes):

- Ground (window background) carries cards: each dock and each section of the Controls column is a card
  with a 1 px border, 8 px radius and a faint shadow in Light (no shadow in Dark).
- Dock title bars become card headers: 11 px semibold uppercase muted text with a small drag grip at the
  left. Docks still drag and resize exactly as today.
- Tabs are segmented pills: the tab bar sits in a 6 px-radius tray; the selected tab is a raised white (or
  charcoal) pill with a soft shadow. No underline, no bevel.
- Inputs (line edits, spin boxes, combo boxes) are flat with a 1 px border and a darker 1 px bottom edge,
  the Fluent text-box convention. Focus: accent border.
- Buttons are flat with a 1 px border and a hairline bottom shadow. Only two buttons in the main window are
  filled with the accent: Live and Start Acquisition. Snap, Autolevel, Forward/Backward, Browse, Save
  Coordinates, Snap Images and the rest are secondary.
- The scan-settings band (today peach) and the XY/Z/Time group frames (today orange when enabled) become
  one tinted band in the accent family: accent-soft fill with a 1 px border; an enabled group gets an accent
  border. State stays visible without a second colour language.
- Checkboxes: 14 px, 4 px radius, accent fill when on, with the check mark in the accent-text colour
  (white in Light, `#0b1a24` in Dark: white on Dark's `#4cc2ff` accent would be 2:1 contrast, the dark
  mark is 8.8:1). Radio dots the same.
- Slider: thin track, accent fill to the handle, round handle with an accent ring.
- Status bar: muted text; warnings in a rounded pill (amber tones) instead of coloured text.
- Menu bar and menus follow the ground and surface colours.
- Typeface: Segoe UI 8 pt on Windows, system UI font on Linux. Card headers and the dock headers use the
  same size in semibold uppercase; nothing in the chrome is set larger than the body size.

Light tokens:

| token | value | used for |
|---|---|---|
| ground | `#f3f3f3` | window, dock area, menu bar, status bar |
| surface | `#fbfbfb` | cards, docks |
| header | `#f6f6f6` | dock headers |
| border | `#e5e5e5` | card and dock borders, separators |
| text | `#1a1a1a` | primary text |
| muted | `#5f5f5f` | labels, headers, placeholder |
| input bg | `#ffffff` | line edits, spin boxes, combos, lists, table cells |
| input border | `#e0e0e0`, bottom `#8a8a8a` | inputs |
| button bg / border | `#fbfbfb` / `#d9d9d9` | secondary buttons |
| accent | `#0067c0` | primary buttons, checked states, focus, selection |
| accent text | `#ffffff` | text on accent |
| accent soft | `#e8f1fb` | enabled groups, selected list rows, selected wells |
| band | `#f7f9fc`, border `#e5e5e5` | scan-settings band |
| tab tray / selected tab | `#ececec` / `#ffffff` | segmented tabs |
| warning | bg `#fff4ce`, text `#8a5a00`, border `#f5dd9a` | status pill, notes |

Dark tokens:

| token | value |
|---|---|
| ground | `#202020` |
| surface | `#2b2b2b` |
| header | `#272727` |
| border | `#3a3a3a` |
| text | `#f0f0f0` |
| muted | `#a6a6a6` |
| input bg | `#333333` |
| input border | `#454545`, bottom `#9a9a9a` |
| button bg / border | `#353535` / `#474747` |
| accent | `#4cc2ff` |
| accent text | `#0b1a24` |
| accent soft | `rgba(76,194,255,.16)` |
| band | `#30343a`, border `#3d4148` |
| tab tray / selected tab | `#262626` / `#3c3c3c` |
| warning | bg `#3d3200`, text `#ffd45c`, border `#5a4a00` |

Classic: no stylesheet, default Fusion palette, today's literal colours. Nothing in this table applies.

## Constraints

- Qt binding is selected at runtime (`squid/qt_binding.py`: `--qt-api`, `SQUID_QT_API`, the ini, then
  PyQt5). Everything goes through `qtpy`; no PyQt5-only API.
- Windows 11 at 150 % scaling and Ubuntu 22.04 at 100 % must both look right. Under PyQt5 the app does
  not enable `AA_EnableHighDpiScaling` (verified: `devicePixelRatio` is 1.0 and `logicalDpi` is 144 on the
  reference machine), so a `px` in QSS is a device pixel while `pt` fonts scale. Under PyQt6 scaling is
  always on and `px` is logical. The theme module therefore generates lengths itself: every length token in
  this document is in logical pixels at 96 dpi, and the module multiplies by `logicalDpiX() / 96` when
  `devicePixelRatio == 1`, by 1 otherwise. Enabling Qt high-DPI scaling is out of scope: it would rescale
  every fixed size in the app, which is not a clothes-only change. Avoid bitmap images in the stylesheet;
  use SVG.
- `control/widgets.py` is 17,500 lines. The role conversion is mechanical and must not touch behaviour.
- Startup already does `app.setStyle("Fusion")` in `main_hcs.py` before anything else; the theme applies
  right after that and before any widget is created, then again on switch.
- Tests live in `software/tests` (pytest, `QT_QPA_PLATFORM=offscreen` works for logic tests; note that the
  offscreen platform on this Windows machine has no font database, so it renders no text, which is fine for
  logic tests and useless for screenshots).
- The simulation screenshot approach used for the mockups (run `main_hcs.py --simulation` on the real
  display with `QApplication.exec_` patched to grab the main window and quit) is worth keeping as a small
  tool, e.g. `software/tools/ui_screenshot.py`, to produce before/after images for review in each theme.
  It needs the machine's `user_profiles/`, `machine_configs/` and `.ini` present, and on a piezo-only
  config it must auto-confirm the nosepiece prompt.

## Acceptance

1. `main_hcs.py --simulation` runs in each of Light, Dark, Classic on Windows and on Ubuntu with no clipped
   text, and the Controls dock in Light/Dark is within 3 % of its Classic width (on the reference machine
   Segoe UI 8 pt alone measured +17 device px on 748; the cards' borders may add a few more).
2. Settings > Appearance switches Light ↔ Dark at runtime; everything restyles at once, including open
   dialogs, the dock bars, the well selector table, row brushes in tables and lists, and the status bar.
   Choosing Classic from Light or Dark, or Light or Dark from Classic, saves the choice, offers the restart
   prompt, and applies at the next start. The choice survives a restart in every case.
3. Classic screenshots match today's screenshots.
4. In Dark, every dialog (Preferences, Stage Utils, channel editors, Slack settings, message boxes) is
   readable; no white-on-white or black-on-black.
5. Existing tests pass. New tests cover: token → QSS generation for both themes, Classic applying no
   stylesheet, the ini round trip for `ui_theme`, the role → colour mapping in Classic equalling today's
   literals, the row-brush tokens' contrast against each theme's text colour, and that a Light ↔ Dark
   switch repaints a table that uses row brushes.

## Left to the builder

- Exact QSS: selectors for `QTabBar::tab` as pills in a tray, `QGroupBox`, `QHeaderView`, `QTableWidget`
  (the well selector), `QScrollBar`, `QMenu`, `QToolTip`, `QProgressBar`, `QSlider`, `QSplitter::handle`,
  `QDockWidget` (Jupyter console) and the pyqtgraph dock pieces.
- The role vocabulary (a short list such as `primary`, `secondary`, `danger`, `note`, `band`, `group`,
  `active`) and the helper API widgets call.
- Whether the Dark/Light stylesheet is one template with token substitution or two generated strings.
- How to keep Classic faithful: a `classic` mapping in the theme module that returns today's literals for
  each role, and a test that pins them.
- Whether the Appearance choice is mirrored in the Preferences dialog's Views tab as well as the menu.
- Order of work (suggested): theme module and tokens; apply at startup with the menu and persistence;
  convert `gui_hcs.py` and `widgets.py` roles; pyqtgraph dock labels; remaining files; screenshot tool and
  review pass in all three themes on both platforms.
