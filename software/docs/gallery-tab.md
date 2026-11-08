# Gallery Tab

The Gallery tab lists the acquisitions saved under the saving directory, newest first, and opens the one you pick.

## What it shows

Every folder directly under the saving directory that holds an acquisition's metadata (`acquisition.yaml`,
`acquisition parameters.json`, `acquisition_channels.yaml`, or the older `configurations.xml`) is listed as a tile:

- **Thumbnail**: the saved mosaic overview (`<timepoint>/mosaic_view/mosaic_*.ome.tiff`) when the Views settings
  saved one, else the first image of the first timepoint. Zarr acquisitions without an overview show a text tile.
- **Name and start time**: the experiment name typed in the multipoint tab and the timestamp the controller appended
  to the folder name (or `start_time` from `acquisition.yaml`).
- **Details pane**: objective, sample format, channels, number of regions and FOVs, Nz, Nt and the storage format
  (TIFF images, Multi-page TIFF, OME-TIFF or OME-Zarr).

The filter box narrows the list by name, channel, objective or format.

## Opening an acquisition

- **Open in NDViewer** (or a double-click) shows the acquisition in the NDViewer tab and switches to it. This needs the
  NDViewer tab (Settings > Preferences > Views > Enable NDViewer, and the `ndviewer_light` submodule). Without it,
  opening shows the folder in the file browser instead and the status line says why.
- **Show in file browser** opens the folder in Explorer / Finder / the desktop's file manager.
- **Load settings into acquisition tab** fills the multipoint tab the acquisition was run from (wellplate or flexible,
  read from `acquisition.yaml`) with its saved settings, the same way dropping the file on that tab does. It is refused
  while an acquisition runs, and for folders without an `acquisition.yaml`.

The same actions are on the tile's right-click menu.

## Which folder is listed

The gallery follows the saving directory chosen in the multipoint tabs. **Browse…** lists another folder instead;
**Saving folder** returns to the current saving directory. **Refresh** re-reads the folder; a finished acquisition is
added on its own two seconds after it ends, once its last mosaic overview has been written.

## Implementation notes

- `control/widgets_gallery.py`: `AcquisitionGalleryWidget` plus the pure functions it is built on
  (`scan_acquisitions`, `read_acquisition_entry`, `inspect_images`, `load_thumbnail_array`).
- Directory scans and thumbnail decoding run on worker threads and reach the widget through Qt signals tagged with a
  generation counter, so a later refresh always wins over a slower earlier one.
- `gui_hcs.py` adds the tab to the image display tabs and routes the widget's signals:
  `signal_open_acquisition` to `NDViewerTab.set_dataset_path`, `signal_load_settings` to the matching multipoint
  widget's `_load_acquisition_yaml`.
- Tests: `tests/control/test_widgets_gallery.py`.
