# Quickstart: Control the Microscope with Claude

Drive the microscope by **talking to Claude Code in plain English** — no code, no
tool syntax. This is the 5-minute version; for setup options, the full tool list,
and troubleshooting see [MCP Integration](mcp_integration.md).

## 1. Launch

1. Start the Squid GUI.
2. **Settings → Launch Claude Code.**

That's it. The GUI starts the control server, wires up the connection, and pre-approves
all microscope commands. A terminal opens with Claude Code ready. (First time only: if
prompted, let it install Claude Code, and set your key via **Settings → Set Anthropic
API Key…** unless you're already logged in with `claude login`.)

Verify it's connected — just ask:

> **"Are you connected to the microscope? What's its status?"**

Claude will report the instrument state (e.g. `INITIALIZED`), current objective, and position.

## 2. Just ask for what you want

You don't call tools or remember parameter names. Say what you want; Claude picks the
right commands and fills in the details. Examples that work out of the box:

**Look around**

> "What channels and objectives are available right now?"
> "Move the stage to x=20, y=20 and show me the current position."
> "Take a brightfield image and show it to me."

**Set up imaging**

> "Switch to the 488 fluorescence channel and set exposure to 100 ms."
> "Turn the illumination on, grab an image, then turn it off."

**Run a plate scan** — the big one:

> "Scan wells A1 through B3 on a 96-well plate in brightfield and 488, 2×2 fields per
> well, and tell me when it's done."

Claude starts the acquisition, gets a job handle back, and can poll progress for you.
Follow up naturally:

> "How's the scan going?"  → Claude reports FOVs done, elapsed time, any AF/save failures.
> "Abort it — finish the current field first."  → graceful abort, reports whether it was clean.

**Use a saved method** (if any exist on the instrument):

> "What acquisition methods are saved? Run 'spheroid_4ch_20x' on wells A1–D6."

## 3. What Claude can do for you

Behind the scenes it has tools for: status/position/capabilities, stage moves and homing,
channel/exposure/illumination/objective control, single-image and laser-AF-image capture,
reflection autofocus, and the full acquisition lifecycle (grid scans, saved methods,
GUI-saved YAMLs, job tracking, abort). You rarely need the names — describe the goal.

The full catalogue is the tool table in [MCP Integration](mcp_integration.md#available-commands).

## 4. When you want raw Python (advanced, off by default)

For one-off exploration Claude can run Python directly on the live microscope objects —
but it's **disabled by default for safety**. Enable it per session in the GUI:
**Settings → Enable MCP Python Exec** (accept the warning; resets to off on restart). Then:

> "Using python_exec, list the methods available on the autofocus camera object."

Only turn this on when you need it — it is not sandboxed.

## 5. If Claude says it can't reach the microscope

- Make sure the GUI is running and the control server is on
  (**Settings → Enable MCP Control Server**, or use **Launch Claude Code** which starts it).
- Everything else — port details, auth, timeouts — is in
  [MCP Integration → Troubleshooting](mcp_integration.md#troubleshooting).

## Not using Claude?

The same capabilities are plain HTTP — see the [Plate Scan Quickstart](quickstart-plate-scan.md)
for `curl` examples and the [`run_acquisition.py`](automation.md) CLI.
