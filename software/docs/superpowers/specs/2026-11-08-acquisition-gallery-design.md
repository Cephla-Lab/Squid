# Acquisition Gallery: design

Date: 2026-11-08. Status: reviewed by a builder check; awaiting the user's approval.

## Intent

Get back to a past acquisition from inside Squid in one click, with enough context on screen to recognise the
right run among a hundred. Today the user leaves Squid, hunts through folders in the file browser, and launches
ndviewer_light by hand from a desktop shortcut. Where the data ends up varies by machine and project, so a listing
of one folder is not enough: Squid keeps its own record of every acquisition it runs, the **acquisition history**,
and the Gallery tab is the view onto that record.

## Goals

- Every acquisition Squid starts is recorded, with its metadata and a visual preview, wherever it was saved.
- The Gallery tab shows that history newest first, as a grid of picture tiles or as a table, the user's choice.
- Each entry carries all the information Squid saved about the run (objective, sample, channels, regions and
  FOVs, Nz, Nt, autofocus, file format, location, how it ended) and a preview: the mosaic overview, built as a
  max intensity projection when the run had z-stacks.
- One click opens a run inside Squid (the NDViewer tab) or in a standalone ndviewer_light window. Both are
  offered; opening inside Squid can be switched off in Preferences.
- A run's saved settings can be loaded back into the multipoint tab it was run from. Its folder can be shown in
  the file browser.
- Older data, and data from other machines, can be added to the history by pointing at a folder.
- Entries whose drive is unplugged stay in the history, visibly offline, and come back when the drive does.

## Non-goals (for now)

- Browsing the history from another machine or in a web browser. Nobody needs it today. The store is kept plain
  (text records and PNG previews on disk) so a web page could read it later without a migration.
- Sharing one history between microscopes, or syncing it anywhere.
- Notes, tags or ratings on runs. Possible later; the record format should not make it hard.
- Editing or re-processing data from the gallery.

## Anti-goals

- Slowing down or destabilising an acquisition. History writes are tiny appends; preview work happens off the GUI
  thread and after the run, never in the acquisition loop.
- Blocking the GUI while folders or previews are read.
- Requiring ndviewer_light, or the NDViewer tab, for the gallery itself to work. When a viewer is missing the
  gallery says so and still offers the folder.
- Silent failure: a viewer that cannot start, a folder that is gone, a file that cannot be read all show a reason
  in the gallery.

## Constraints

- Squid's GUI is Qt through qtpy, with napari and vispy already in the process. Installations run PyQt5 or PyQt6,
  so everything here is written against qtpy only: no binding-specific imports, no enum spellings that exist in
  one binding only, and the gallery, the history recorder and the preview code are checked under both bindings
  before they ship (the current build has only been run under PyQt5). The standalone ndviewer_light process
  brings its own binding and is unaffected. The gallery is a Qt widget in the image display tabs, placed after
  the NDViewer tab. No web stack.
- Windows and Linux; Python 3.10+.
- An acquisition folder, `<base_path>/<experiment_ID>`, already holds `acquisition.yaml`,
  `acquisition parameters.json`, `acquisition_channels.yaml`, `coordinates.csv`, one numbered folder per timepoint
  (or a zarr store), and, when the mosaic view is on, `<timepoint>/mosaic_view/mosaic_<mode>_<res>um.ome.tiff`
  with a YAML sidecar. The gallery reads these; it does not change their layout.
- The NDViewer tab exists only when the Views preference "Enable NDViewer" is on and the `ndviewer_light`
  submodule is checked out; `NDViewerTab.set_dataset_path(folder)` loads a saved folder.
- Standalone ndviewer_light runs as the `ndviewer-light <folder>` command (a pip install) or
  `python -m ndviewer_light <folder>`.
- Performance mode disables the non-essential image display tabs; the gallery follows that rule.
- The current Gallery tab (`control/widgets_gallery.py`, uncommitted) lists one folder's acquisitions with
  thumbnails and three actions. It is the starting point: its folder reading and thumbnail code become the import
  path and the fallback preview; its widget becomes the grid view.

## Decisions

Marked **(user)** when the user decided it, **(call)** when it is the author's choice the user has not reviewed.

### The history store

1. **(call)** One history per microscope PC, shared by every Squid checkout on it: in the per-user state
   directory Squid already uses for its run-state breadcrumb (`platformdirs.user_state_path("squid", "cephla")`,
   where `watchdog/run.json` lives), as `acquisition_history/history.jsonl` and `acquisition_history/previews/`.
   Not `software/cache/`: that is per checkout, and one PC often runs two checkouts. The recorder hooks in beside
   the existing `RunStateWriter` in the controller and reuses its progress and end-reason for the finished fraction.
2. **(call)** `history.jsonl` is an append-only file of JSON events, one per line: `started`, `finished`,
   `imported`, `relocated`, `removed`. The gallery folds the events into entries at load. A truncated last line
   (a crash mid-write) is ignored, not fatal. Appends happen synchronously; they are a few hundred bytes.
3. **(call)** An entry's id is stable and unique per machine: the experiment folder name plus a short hash of the
   absolute path, so the same folder imported twice is one entry and two runs with the same name are two.
4. **(user)** The record carries all the metadata Squid saves about the run. At `started`: id, folder path,
   experiment name, start time, microscope name (hostname or the configured instrument name), software version
   (`git describe`), widget type (wellplate or flexible), objective and pixel size, sample format, channel names,
   regions and planned FOV count, Nz and dz, Nt and dt, autofocus settings, file format. At `finished`: end time,
   outcome (`complete`, `aborted`, `failed`), the fraction done when it stopped (FOVs or timepoints completed over
   planned), the preview file name, and, when cheap to know, the size on disk.
5. **(call)** Entries never expire. "Remove from history" drops one entry; it never deletes data.

### What counts as an acquisition

6. **(user)** Whether single snaps (the Snap button in the multipoint tabs, which writes a full
   `snapped images…` acquisition folder each time) go into the history is the user's choice. **(call)** A
   Preferences > Views switch, "Record snaps in the acquisition history", off by default. When on, snaps are
   recorded with a `snap` kind, shown with a Snap badge and a Snap filter, and `snapped images…` folders are taken
   on import; when off, neither happens.
7. **(call)** Runs started with saving skipped write no data and are not recorded: there is nothing to open. Runs
   that fail to start, or fail during the run, are recorded with outcome `failed` and the reason the controller
   gives, so the attempt is visible and can be removed. A run whose Squid process died before it finished (a crash
   or power loss) is closed the next time Squid starts on that machine, as `failed` with the reason `interrupted`;
   a run still alive in another Squid process on the same PC is left running.
8. **(user)** A fluidics protocol run is one entry that groups its imaging rounds, not one entry per round.
   **(call)** Shape: the protocol run (its run folder) is the parent entry, created when the protocol starts;
   each imaging round is a child record carrying the protocol fields `acquisition.yaml` already stores (protocol,
   run name, round, step, attempt). The gallery shows the parent as one tile or row, named after the run, with the
   round count and the latest round's preview; while it runs, the parent shows Running with the current round.
   The details pane lists the rounds (round, step, attempt, when, outcome) and each round has its own Open in
   Squid and Open in window, since each round is a dataset of its own. Import recognises a folder whose acquisition
   subfolders carry a `protocol` section in their `acquisition.yaml` as one protocol run and groups them the same
   way, so "Add folder to history" finds protocol runs sitting one level below the operator's saving folder.

### Previews

9. **(user)** The preview is the mosaic overview, and for z-stacks it is a max intensity projection across z.
10. **(call)** Mechanism: the mosaic view gains the MIP z-projection the code already defines but never used
   (`ZProjectionMode.MIP`): while an acquisition runs, a tile for a position that already holds a tile from a
   lower z-level of the same timepoint is combined by running maximum instead of overwriting it. Today every
   z-plane overwrites the one before, so the live Mosaic View and the saved overview show whichever plane arrived
   last. MIP becomes the default for z-stacks, which changes what the live Mosaic View shows during a z-stack run
   as well as the saved overview; today's last-plane behaviour stays as the other option in Preferences > Views
   (there is no middle-plane mode to keep, despite the enum's docstring, and none is built). Runs with one z-plane
   are unaffected. A single-FOV z-stack is the same thing with one tile.
11. **(call)** When a run finishes, Squid writes a small composite PNG (longest side 512 px, all channels coloured
   with the channel colour map, percentile-stretched per channel) into `previews/`, made from the last timepoint's
   saved overview. This copy is what the gallery shows, so a run still has its picture when its drive is offline.
   The full-resolution overview in the data folder is used for the larger preview in the details pane when the
   data is online.
12. **(call)** Runs without a saved overview (mosaic view off, performance mode, imported old data, zarr runs saved
   without it) fall back to the first image of the first timepoint, composited per channel when the files are
   there. Zarr runs with no overview and no plain images get a text tile naming the format.
13. **(call)** Preview generation runs on a worker thread after `acquisition_finished` and during import; never
    while an acquisition is running, apart from the mosaic widget's existing per-timepoint overview save.

### The Gallery tab

14. **(user)** Two views the user switches between, grid and list, with the choice remembered across sessions
    (as the mosaic widget remembers its view mode, in the cache folder).
15. **(call)** Toolbar: a search box matching name, sample, channel, objective and format; status filters All,
    Complete, Aborted, Offline, Running, each with its count; the grid/list switch; "Add folder to history";
    Refresh. Sort is newest first; the list view also sorts by any column.
16. **(call)** Grid tiles: preview, name, date, objective and sample on one line, a one-line scan summary
    (channels, FOVs, Nz or Nt), and a status badge: Complete, Aborted with the fraction, Drive offline, Folder
    missing, Running with progress.
17. **(call)** List rows: thumbnail, name, started, sample, objective, channels, FOVs, Nz / Nt, format, status.
18. **(user)** Details pane for the selected entry: the preview, large, with one chip per channel (click toggles
    that channel in the composite), then all the metadata, then the actions. **(call)** A "Details" expander shows
    the raw text of the run's `acquisition.yaml` for anything the summary leaves out.
19. **(call)** The running acquisition appears at the top as Running with its progress fraction, from the
    controller's existing progress signals. Its preview arrives when it finishes. Only Show folder is enabled on it.
20. **(call)** Offline handling: an entry whose drive root does not exist shows Drive offline; one whose drive
    exists but whose folder does not shows Folder missing. Actions that need the data are disabled; "Locate…" lets
    the user point the entry at the folder's new place and records a `relocated` event. Online checks are cheap
    path tests done when the tab is shown or refreshed, not continuous polling.
21. **(call)** "Add folder to history" scans the chosen folder one level deep for acquisition folders (the
    current build's rules: any of `acquisition.yaml`, `acquisition parameters.json`,
    `acquisition_channels.yaml`, `configurations.xml`), reads their metadata, records an `imported` event per new
    folder, skips folders already in the history, and generates previews in the background. On the first launch
    with an empty history the gallery offers, once, to import the current saving folder.
22. **(call)** Performance mode disables the gallery tab like the other image display tabs.

### Opening a run

23. **(user)** Two open actions on every entry: **Open in Squid** (the NDViewer tab loads the folder and the view
    switches to it) and **Open in window** (a standalone ndviewer_light window on the folder).
24. **(user)** Open in Squid can be switched off in Preferences. **(call)** It is governed by the existing
    "Enable NDViewer" switch under Preferences > Views: when the tab is off, the in-Squid action is not shown.
25. **(call)** Open in window starts ndviewer_light as a separate process, resolved in this order: the
    `ndviewer-light` command on the PATH; `python -m ndviewer_light` with Squid's own interpreter when the package
    imports there; the Squid submodule run as a module when it is checked out; a command or folder set in
    Preferences > Views ("ndviewer_light location"). When none is found the action is disabled with the reason and
    the setup step. The child process is not killed when Squid exits.
26. **(call)** Double-click and Enter on an entry: Open in Squid when it is available, else Open in window when it
    is available, else Show folder. The buttons always show every available action, so the default never hides one.
    **(call)** While an acquisition is running, Open in Squid is unavailable: the NDViewer tab has one viewer and
    the live run is feeding it, so loading a past run there would replace the live view and misdirect its updates.
    The action is disabled with that reason; double-click falls through to Open in window, which is unaffected.
27. **(user)** Show folder opens the run's folder in the system file browser. **(user)** Load settings loads the
    run's `acquisition.yaml` into the multipoint tab it was run from (chosen from the file's widget type) and
    switches to that tab; **(call)** it is refused while an acquisition is running.
28. **(call)** Right-click menu on an entry carries the same actions plus Remove from history, Locate… and Copy path.

## Left to the builder

- Exact field names of the JSONL events and the entry id hash.
- How the running maximum is wired into the mosaic tile pipeline (per-layer canvas, plate and mosaic modes), and
  the Preferences control for the projection mode.
- The composite colouring details (reuse the channel colour map the mosaic view uses) and the percentile stretch.
- How the finished fraction is computed from the controller's progress signals for aborted runs.
- Table column widths, sort behaviour, keyboard navigation, and the empty-state wording.
- Where in Preferences > Views the ndviewer_light location, the projection mode and the snap switch sit.
- The exact record shape for a protocol run and its rounds, and how the running round reports progress to the
  parent entry.
- Tests: the store (event folding, truncated last line, relocation, duplicate import, protocol grouping), preview
  generation, import, the widget with pytest-qt, and the GUI routing test that already exists for the current build.
