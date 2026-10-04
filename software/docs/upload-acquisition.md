# upload_acquisition.py — moving a large acquisition to a NAS

`tools/upload_acquisition.py` streams a Squid acquisition to a mounted destination (a NAS
share, an external drive, anything the OS has mounted) **while the acquisition is still
running**, so a run can be larger than the acquisition disk.

It decides what is safe to take using, in order:

1. `transfer_manifest.jsonl` in the experiment folder — see
   [transfer-manifest.md](transfer-manifest.md). This is what large acquisition mode
   writes, and it is the only source of truth that works mid-run for every format.
2. Otherwise, a `.done` file in the experiment folder itself: the whole run is finished and
   everything is movable once the folder has gone quiet.

Without a manifest **nothing moves mid-run**. A timepoint folder's own `.done` marker only
says the worker finished *imaging* that timepoint; its save jobs run asynchronously and can
still be writing (or stalled) long after, and no quiet period can prove they finished. Runs
you want to offload while they are still going must use large acquisition mode, which writes
the manifest.

The destination must be a folder disjoint from the experiment folder: the tool appends the experiment name to it (`<nas>/<experiment>/…`) and refuses a destination that is, contains, or lies inside the source (for example `upload_acquisition.py /data/exp /data`), because that would make every file match itself and, in move mode, delete the run.

Standard library only, Python 3.10+. Copy it onto a transfer box if you like; it does not
need a Squid checkout.

## Usage

```
upload_acquisition.py <experiment_dir> <destination_dir> [--mode copy|move] [--follow]
                      [--checksum] [--quiesce-s 30] [--poll-s 2] [--dry-run]
                      [--log-level LEVEL]
upload_acquisition.py verify <experiment_dir> <destination_dir>
```

The experiment folder **name** is appended to the destination, so

```
upload_acquisition.py /data/2026-09-15_plate3 /Volumes/microscopy
```

writes `/Volumes/microscopy/2026-09-15_plate3/...`, preserving the relative tree.

### During a large acquisition

Mount the NAS first (Finder, `mount_smbfs`, `/etc/fstab`, ...), confirm the mount point
exists, then:

```bash
cd software
python tools/upload_acquisition.py "/data/2026-09-15_plate3" /Volumes/microscopy \
    --follow --mode move
```

It polls the manifest every `--poll-s` seconds, moves each newly finished file or chunk
directory, and when the run ends — and the folder has been quiet for `--quiesce-s`
seconds — moves the remainder (logs, parameters, Zarr plate metadata, `.done` markers) and
finally the manifest itself. Then it exits 0.

Leave it running in its own terminal (or `tmux`/`screen`) for the length of the run.

### After the acquisition

```bash
python tools/upload_acquisition.py verify "/data/2026-09-15_plate3" /Volumes/microscopy
```

`verify` checks, against whatever is actually at the destination:

- every `complete` path in the manifest exists, with the size the manifest recorded
  (directories must exist as directories); it reports missing and short files;
- the manifest reached its `end` event;
- **TIFF-based runs** — every timepoint folder has `.done` and `coordinates.csv`;
- **OME-TIFF runs** — `ome_tiff/` (at the root or inside each timepoint) is non-empty, and
  no stray `squid_ome_*_metadata.json` sidecar was dragged along;
- **Zarr runs** — every `zarr.json` under a `*.zarr` store that carries
  `attributes._squid.acquisition_complete` has it set to `true`, and a `plate.ome.zarr`
  has plate, row and well `zarr.json` metadata.

It exits 0 when clean, 1 with a readable list of problems otherwise.

### Resuming after an interruption

Just run the same command again. Every file is copied to `<final>.partial`, fsynced and
renamed into place, so a destination file is either complete or absent; a file already at
the destination with a matching size is skipped (with `--checksum`, the sha256 must match
too, so a corrupted copy is re-sent). A failed copy never removes the source — with
`--follow` the entry is retried on the next poll, otherwise it is listed in the summary
and the exit code is 1.

The manifest is the resume token: it is moved last, and only once everything else has
landed, so an interrupted move always leaves enough information at the source to finish.

## Options

| option | meaning |
|---|---|
| `--mode copy` (default) | leave the source intact. Safe, but does not free any disk. |
| `--mode move` | unlink each source file *after* its copy is verified at the destination. Frees the acquisition disk as the run goes. Empty source directories are removed only after `end` (the experiment folder itself is left behind). |
| `--follow` | keep polling until the run ends and the final sweep completes. Without it, the tool transfers what is movable now and exits. |
| `--checksum` | verify sha256 as well as size — on copy, and when deciding whether an existing destination file can be skipped. Slower (the destination is read back). |
| `--quiesce-s` | how long the experiment folder must be untouched before unlisted files are considered safe after the run has ended. Default 30 s. |
| `--poll-s` | poll interval for `--follow`. Default 2 s. |
| `--dry-run` | report what would move; change nothing, poll nothing. |
| `--log-level` | `DEBUG` lists every skipped and moved file. |

## Exit codes

| code | meaning |
|---|---|
| 0 | everything movable was transferred (and, if the run had ended, the final sweep completed) |
| 1 | transfer failures, a bad argument, or `verify` found problems |
| 2 | nothing was movable yet and the run is still in progress — no manifest and no root `.done`. Normal early in a run; try again later, or use `--follow`. |

## Notes

- `copy` then `verify`, then delete the source by hand, is the conservative workflow.
  `move` is the one that actually lets a run outgrow the acquisition disk.
- The destination must already be mounted; the tool refuses to run if
  `<destination_dir>` is not a directory (so a dead mount cannot silently fill the local
  disk).
- Timepoint folder names follow `FILE_ID_PADDING`, so they may be `00000` or plain `0`;
  the tool accepts either and orders them numerically.
- A `WARNING` about a manifest schema newer than the tool understands means the entry
  semantics may have changed: re-copy the current `tools/upload_acquisition.py` from the
  Squid checkout that wrote the run.
- A `WARNING` about falling back to the inline manifest reader means a manifest line is
  corrupt. The transfer continues with every line that still parses; run `verify`
  afterwards and check the acquisition log.
- Running two instances against the same experiment folder is not supported.
