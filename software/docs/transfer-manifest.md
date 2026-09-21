# Transfer manifest

When **large acquisition mode** is on, Squid appends JSON lines to

```
{experiment_dir}/transfer_manifest.jsonl
```

as it writes. The manifest tells an external mover which files the acquisition is
*finished with*, so data can be streamed off the acquisition disk while the run is still
going. `tools/upload_acquisition.py` is the reference consumer; see
[upload-acquisition.md](upload-acquisition.md).

## Format

One JSON object per line, appended and flushed as the acquisition progresses.

```jsonl
{"event":"start","schema":1,"experiment_id":"exp","format":"INDIVIDUAL_IMAGES","nt":3,"ts":1.0}
{"event":"complete","path":"00000/A1_0000_0000_BF.tiff","kind":"file","bytes":8192,"t":0,"region":"A1","fov":0,"ts":2.0}
{"event":"complete","path":"plate.ome.zarr/A/1/0/0/c/0","kind":"dir","bytes":null,"t":0,"region":"A1","fov":0,"ts":3.0}
{"event":"timepoint_done","t":0,"ts":4.0}
{"event":"end","reason":"completed","ts":9.0}
```

### Events

| event | fields | meaning |
|---|---|---|
| `start` | `schema`, `experiment_id`, `format`, `nt`, `ts` | the run began. `format` is the `FileSavingOption` (`INDIVIDUAL_IMAGES`, `MULTI_PAGE_TIFF`, `OME_TIFF`, `ZARR_V3`), `nt` the number of timepoints. |
| `complete` | `path`, `kind`, `bytes`, `t`, `region`, `fov`, `ts` | **the only movable unit.** Squid will not write to this path again. |
| `timepoint_done` | `t`, `ts` | timepoint `t` is fully listed: its images, its `coordinates.csv`, and the mosaic view the GUI saves under `<t>/mosaic_view/`. No later `complete` record names a file of that timepoint. Omitted (never wrong) when a save result or the mosaic view was still outstanding at the timepoint boundary; those files are then listed as soon as they finish (also while the run is paused), at the latest before `end`. A failed mosaic save is never listed. |
| `end` | `reason` (`completed`, `completed_with_errors`, `user_abort`, `error`), `ts` | the acquisition is over **and nothing is being written any more**. Anything still unlisted becomes movable after quiescence. If a mosaic view save is still running when the run ends, `end` is written late, after that save has finished and its files are listed; if that can never be established (the GUI died), `end` never appears and unlisted files stay where they are. |

### `complete` fields

- **`path`** — relative to the experiment directory, always with **POSIX separators**, even
  on Windows. Never absolute, never containing `..`. Join it as
  `Path(dest, *rel.split("/"))` rather than `Path(dest) / rel`, and reject anything that
  does not look relative — the manifest is data, not a trusted command.
- **`kind`** — `"file"`, or `"dir"` meaning *the whole subtree at this path*. Walk a `dir`
  unit file by file. Squid itself lists every file individually, including each chunk of a
  finished Zarr chunk directory, so the manifest doubles as the inventory `verify` checks; the
  `dir` kind stays in the contract for other writers.
- **`bytes`** — the file size, or `null` (always `null` for `kind: "dir"`).
- **`t`**, **`region`**, **`fov`** — provenance, useful for progress reporting.

## Rules for a mover

1. **Only `complete` entries are movable** while the run is in progress.
2. **Everything not listed moves only after `end` *and* after the experiment folder has
   gone quiet** (no file mtime newer than the quiescence window, 30 s by default). That
   includes:
   - `acquisition.log`, `acquisition parameters.json`, `configurations.xml`
   - the root `coordinates.csv`, `acquisition.yaml`
   - a manually saved mosaic view (`mosaic_view_<timestamp>/`); the per-timepoint `<t>/mosaic_view/` is listed
   - Zarr `zarr.json` / plate / well metadata (finalized at the end of the run)
   - `.done` markers
   - the manifest itself
3. **The manifest moves last**, after everything it describes has landed. It is the resume
   token: as long as it is still at the source, a re-run can pick up where it left off.
   If any file fails in the final sweep, leave the manifest in place.
4. **Never delete a directory before `end`** — not even one whose files have all been
   moved; the writer may still create files in it. After `end`, empty source directories
   may be removed (move mode only).
5. **The last line may be truncated** — the writer can be killed mid-line. Skip an
   unparsable final line; do not abort the transfer.
6. **Lines are appended while you run.** Re-read the manifest each poll; entries you have
   already handled are simply no longer present at the source (move mode) or already match
   at the destination (copy mode).
7. **`timepoint_done` is an ordering guarantee, not a gate.** It is emitted after every
   `complete` record of that timepoint, so on seeing it you know timepoint `t` is fully
   listed. Its *presence* is best-effort and bounded — a crashed writer may skip one — so
   never require it before moving anything.

### Windows note

Paths inside the manifest are POSIX (`a/b/c`) regardless of the writing platform. Split on
`/` and re-join with the platform separator; do not pass the raw string to `open()` on a
platform where `\` is the separator and `/` is not guaranteed to work in every API.

## Reading the manifest

Squid ships `control.core.transfer_manifest.read_manifest(path) -> list[dict]`, which is
tolerant of a truncated last line (it raises `ValueError` for a malformed line anywhere
else, which is real corruption). Use it when you are inside a Squid checkout.

`tools/upload_acquisition.py` imports it lazily and falls back to an inline JSONL reader,
because the tool is meant to be copied onto a transfer box that has no Squid installed —
the format above is the contract, so a self-contained reader is safe. The fallback also
catches the corruption case: one bad line must not strand a whole acquisition on a full
disk, so the tool logs a warning, skips the line, and moves everything else.

`schema` in the `start` event is the format version (`SCHEMA_VERSION`, currently 1). A
mover that sees a higher number should say so loudly — `complete` may no longer mean what
it assumed.

## Writing your own mover — checklist

- [ ] Re-read the manifest every poll; ignore a truncated last line.
- [ ] Validate every `path`: relative, no `..`, no leading `/`, no drive letter.
- [ ] Treat `kind: "dir"` as a subtree; walk it file by file.
- [ ] Copy to `<final>.partial` at the destination, `fsync`, verify size (and sha256 if you
      care), then `os.replace` into place. Only then unlink the source.
- [ ] Preserve mtimes.
- [ ] Never delete a source on a failed copy — retry it next pass.
- [ ] Be idempotent: a file already at the destination with a matching size (and checksum)
      is a skip, not an error.
- [ ] Wait for `end` + quiescence before touching anything unlisted.
- [ ] Move the manifest last, and only if everything else succeeded.
- [ ] Do not remove source directories before `end`.
- [ ] Timepoint folder names use `FILE_ID_PADDING`, which is configurable: they are
      `00000`, `00001`, ... with the usual padding of 5, but `0`, `1`, `2`, ... when it is
      0. Match any all-digit directory name and sort numerically (`2` before `10`).
