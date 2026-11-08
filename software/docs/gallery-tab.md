# Gallery Tab

The Gallery tab is the view onto Squid's acquisition history: a per-machine record of every acquisition Squid runs, wherever it was saved. It lets you recognise a past run by its picture and metadata and open it in one click. It sits after the NDViewer tab among the image display tabs.

## What it shows

Entries are listed newest first, as a grid of picture tiles or as a table (list view). The choice is remembered across sessions in `cache/last_gallery_view.yaml`.

- **Toolbar**: a search box (matches name, sample, channel, objective and format; for a protocol run, those of its rounds too), status chips (All, Running, Complete, Aborted, Failed, Offline, Snaps, each with a count), the grid/list switch, "Add folder to history" and Refresh.
- **Grid tiles**: the preview with a status badge in its top-left corner (Complete, Aborted at x%, Failed, Running with progress, Drive offline, Folder missing, Unknown; the badge colour tells the status apart at a glance) and, for snaps, a `Snap` tag in the top-right corner. Under it three lines: `name · date and time` (a protocol run adds its number of rounds), `objective · sample`, and the scan summary `channels · FOVs · Nz · Nt` (Nz and Nt only when more than one). Anything not known is left out. The tooltip repeats the status with the folder and the full summary.
- **List rows**: the same badged thumbnail, then name, started, sample, objective, channels, FOVs, Nz / Nt, format and status columns. The list view can be sorted by any column.
- **Details pane** for the selected entry: a large preview with one chip per channel (click to toggle that channel in the composite), the metadata, a "Details" expander with the raw text of the run's `acquisition.yaml`, and the action buttons.
- **Fluidics protocol runs** are one entry that groups its imaging rounds. The details pane lists the rounds (round, step, attempt, time, outcome), and each round has its own Open buttons.
- The acquisition in progress appears at the top as Running with its progress. Its preview arrives when it finishes. Only Show folder and Copy path are available on it.

### Previews

The preview is a colour composite of all channels (channel colours, percentile stretch per channel, longest side 512 px) saved as a PNG in the history folder, so a run keeps its picture when its drive is offline. It is built from the mosaic overview saved at the end of the run. Runs without an overview (mosaic view off, performance mode, imported older data) use the first images of the first timepoint instead. Zarr runs with neither get a text tile naming the format. Previews are made after the run finishes, never during acquisition: a preview asked for while a run is under way (for example an earlier run's late overview) waits until that run is over.

The mosaic overview of a z-stack is a max intensity projection (MIP) across z. MIP is also the default for the live Mosaic View. Preferences > Views > "Z-stack projection" chooses between "Max intensity projection" and "Last plane acquired" (the earlier behaviour). Runs with one z-plane are unaffected.

## Opening an acquisition

- **Open in Squid** loads the folder into the NDViewer tab and switches to it. It is hidden when "Enable NDViewer" is off, and disabled while an acquisition is running (the NDViewer tab is feeding the live run). See [NDViewer Tab](ndviewer-tab.md).
- **Open in window** starts a standalone ndviewer_light window as a separate process that outlives Squid. The program is found in this order: the location set in Preferences > Views > "ndviewer_light location"; the `ndviewer-light` command on the PATH; the `ndviewer_light` package in Squid's Python; the `ndviewer_light` submodule. If none is found the button is disabled and gives the reason. The viewer's output goes to `ndviewer_light.log` in the history folder (rewritten at each launch); if the viewer exits within a few seconds, Squid shows the last lines of that log.
- Double-click or Enter does Open in Squid when available, otherwise Open in window, otherwise Show folder.

## Other actions

Also available from the right-click menu:

- **Show folder** opens the run's folder in the system file browser.
- **Load settings** loads the run's `acquisition.yaml` into the multipoint tab it was run from and switches to that tab. Refused while an acquisition is running.
- **Copy path** copies the folder path.
- **Locate...** points an entry at the folder's new location (after the data was moved). Not while the entry is running.
- **Remove from history** drops the entry. It never deletes data. Not while the entry is running.

## Importing older data

"Add folder to history" scans the chosen folder one level deep for acquisition folders (any of `acquisition.yaml`, `acquisition parameters.json`, `acquisition_channels.yaml`, `configurations.xml`), records new ones, skips those already in the history, and builds previews in the background (the size on disk of imported runs is not computed). The button is disabled while an acquisition runs. A folder whose acquisition subfolders carry a `protocol` section is imported as one protocol run with its rounds grouped. `snapped images...` folders are taken only when "Record snaps in the acquisition history" is on.

On the first launch with an empty history, Squid offers once to import the current saving folder (the folder is checked in the background; an import accepted during an acquisition starts when it ends).

## Offline and interrupted runs

An entry whose drive is unplugged stays in the history, marked "Drive offline"; one whose drive is present but whose folder is gone is marked "Folder missing". Actions that need the data are disabled, Show folder included; Copy path, Locate... and Remove stay. Checks happen in the background when the tab is shown or refreshed, not continuously and not when an entry is selected. On Linux, a missing folder whose nearest existing parent is `/`, `/mnt`, `/media`, `/media/<user>`, `/run/media` or `/run/media/<user>` is taken to be on an unplugged drive. Entries come back to normal when the drive does.

A run whose Squid process died before it finished is closed the next time Squid starts on that machine, as Failed with the reason "interrupted"; so is an import of a protocol run that never finished. A run still alive in another Squid process is left Running. Runs that fail to start or fail partway are recorded as Failed. Runs started with saving skipped are not recorded.

## Preferences

Settings > Preferences > Views:

- **Enable NDViewer**: shows the NDViewer tab; governs Open in Squid.
- **Z-stack projection**: Max intensity projection (default) or Last plane acquired.
- **Record snaps in the acquisition history**: off by default. When on, Snap acquisitions are recorded (Snap badge and filter) and imported.
- **ndviewer_light location**: a command or folder used first by Open in window.

## Where the history lives

One history per machine, shared by every Squid checkout on it, in `platformdirs.user_state_path("squid", "cephla")/acquisition_history`. Set `SQUID_ACQUISITION_HISTORY_DIR` to use another folder.

- `history.jsonl`: append-only, one JSON event per line: `started`, `finished`, `preview`, `imported`, `protocol_started`, `protocol_finished`, `relocated`, `removed`. The gallery folds the events into entries when it loads. A truncated last line (crash mid-write) is ignored, and so is any line that is not valid JSON or has no id; a wrongly typed field is read as unknown. Appends from Squid's own threads are serialised; two Squid processes writing at the same moment are not locked against each other.
- `previews/<id>.png`: the preview images.

An entry id is the experiment folder name plus a short hash of its absolute path. Entries never expire. Deleting the history folder only forgets the runs; the data is untouched.

## Implementation notes

- `squid/acquisition_history.py`: the store (event folding, relocation, orphan closing); `squid/version.py`: software version recorded with each run.
- `control/acquisition_history_recorder.py`: hooks into `MultiPointController`.
- `control/gallery_previews.py`: composite previews; `control/gallery_import.py`: folder import.
- `control/ndviewer_launcher.py`: Open in window resolution.
- `control/widgets_gallery.py`, `control/widgets_gallery_details.py`: the tab and its details pane; `control/widgets_mosaic.py`: MIP projection.
- Written against qtpy only (no binding-specific imports).
- Tests: `tests/squid/test_acquisition_history.py`, `tests/control/test_acquisition_history_recorder.py`, `test_gallery_previews.py`, `test_gallery_import.py`, `test_ndviewer_launcher.py`, `test_widgets_gallery.py`.
