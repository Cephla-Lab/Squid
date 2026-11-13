# Agent Session

An agent session makes it explicit who drives the microscope: a program (the "agent") that sends commands to the
TCP control server, or a human at the Squid window. Only one of them drives at a time.

The feature is opt-in. Without the command `begin_agent_session`, Squid and the control server behave as before.

For the control server itself, see [Automated Acquisition via Scripts](automation.md) and
[MCP Integration](mcp_integration.md).

## The four states

| State | Who drives | What Squid does |
|-------|-----------|-----------------|
| `none` | No session. | Nothing changes: the window and the server work as usual. |
| `agent` | The agent. | The window ignores mouse and keyboard input, the control panel is locked, the terminal console refuses lines. A banner says so and has a **Take over** button. |
| `stopping` | Nobody yet. | A human asked for the control (or the session ends). The server refuses new action commands. Squid waits until the work of the agent has stopped. |
| `human` | The human. | The window works as usual. The server refuses the action commands of the agent. The banner has **Hand back** and **End session**. |

```
none --begin--> agent --take over--> stopping --> human --hand back--> agent
                  |                                  |
                  +--------- end ---> stopping --> none <--- end ------+
```

## Commands

The commands go to the control server (port 5050) like every other command: one JSON line
`{"command": ..., "params": {...}}`, one JSON line back.

| Command | Parameters | Result | Notes |
|---------|-----------|--------|-------|
| `begin_agent_session` | none | `{"state": "agent", "session_id": "..."}` | Refused while the microscope is not free (see below). |
| `take_over` | none | `{"state": "stopping"}` | Returns at once. Read the state with `get_agent_session` until it is `human`. If a human drives already, the result is `{"state": "human"}`. With no session it is the error `no agent session`. |
| `hand_back` | `note` (optional text) | `{"state": "agent"}` | Only while a human drives. |
| `end_agent_session` | none | `{"state": "stopping"}` from `agent`, `{"state": "none"}` from `human` | |
| `get_agent_session` | none | the session status (below) | A read: accepted in every state. |

Example:

```python
from control.microscope_control_server import send_command

send_command("begin_agent_session")
# {"success": True, "result": {"state": "agent", "session_id": "5f0c..."}}

send_command("move_to", {"x_mm": 20.0, "y_mm": 20.0})      # the agent drives

send_command("take_over")
# {"success": True, "result": {"state": "stopping"}}

send_command("move_to", {"x_mm": 21.0, "y_mm": 20.0})
# {"success": False, "error": "a stop is in progress"}     then, when the human drives: "a human is driving"

send_command("hand_back", {"note": "looked at one organism"})
# {"success": True, "result": {"state": "agent"}}

send_command("end_agent_session")
```

### The session status

`get_agent_session` returns:

| Field | Meaning |
|-------|---------|
| `state` | `none`, `agent`, `stopping` or `human` |
| `session_id` | An identifier for this session, or `null` |
| `commands_in_progress` | The action commands that the server runs at this moment |
| `stop_cause` | Why the agent lost the control: `take over`, `end`, or `a dialog opened in Squid` |
| `waiting_for` | While `stopping`: what Squid waits for (`a command is in progress`, `an acquisition is running`, `the stage is moving`) |
| `unlocked_anyway` | `true` if the human did not wait for the end of the stop (see "Take over") |
| `handover_count` | How many times the human gave the control back in this session |
| `handover` | `before`: the microscope state when the human got the control. `after`: the state when the human gave it back. `note`: the line that the human wrote. |

A microscope state has `time`, `x_mm`, `y_mm`, `z_mm`, `objective`, `channel`, `exposure_ms` and `is_live`. The
agent compares `before` and `after` to see what the human changed. A value that the hardware did not give is
`null`, and `before` is `null` if the microscope state could not be read at all: a take over does not wait for it.
The two states are in memory only.

### Which commands are refused

- `ping`, every `get_*` command and the session commands are accepted in every state.
- Every other command is refused with `a stop is in progress` in `stopping` and with `a human is driving` in
  `human`.

### When `begin_agent_session` and `hand_back` are refused

The agent gets the control only when the microscope is free. The error text says what is in the way:

- `an acquisition is running`, `the stage is homing`, `an objective change is in progress`, `autofocus is running`,
  the stage is at the loading position, or the Z lift is moving
- `a command is in progress` or `the console is running a line`
- `a dialog or a menu is open in Squid`
- `this controller cannot lock its control panel (firmware 1.8 or later is necessary)`, or
  `could not lock the control panel` if the controller did not confirm the lock
- `a session exists` (begin), `no human is driving` (hand back)
- `an agent session needs the Squid window` (the server runs with no window)
- `Squid is closing`, `Squid did not answer`, or `the change failed in Squid (...)` with the error, if something
  went wrong inside Squid during the change

## What the human sees

A banner at the top of the Squid window:

| State | Banner | Buttons |
|-------|--------|---------|
| `agent` | "The agent is driving." | Take over |
| `stopping` | "Stopping. Waiting: ..." with the reasons | Unlock anyway (only after the time limit) |
| `human` | "You are driving. The agent is waiting." | Hand back, End session |

While the agent drives:

- The window ignores clicks, keys, shortcuts, the wheel and drag-and-drop everywhere except on the banner. The
  widgets are not disabled, so the window still shows what the agent does.
- The control panel (joystick and focus wheel) is locked. The lock is counted together with the locks of homing and
  of the loading position: the end of a homing run inside a session does not unlock the panel.
- The terminal console answers `Refused: the agent is driving` to each line (`Refused: a stop is in progress`
  during a stop).

### Take over

1. The human clicks **Take over** (or a program sends `take_over`). The state is `stopping` at once.
2. Squid checks at a short interval: no command of the agent is in progress, no acquisition runs (Squid asks the
   acquisition to abort at each check), and the stage has stopped.
3. When the three are true, Squid records the microscope state, the state becomes `human`, and the window and the
   control panel work again.
4. While Squid waits, the banner shows what it waits for. If this takes more than 30 seconds, the banner also has
   the button **Unlock anyway**. With it the human gets the control without waiting; the status then has
   `unlocked_anyway: true`.

Squid leaves live view and the illumination as they are at the take over.

### Hand back

The human clicks **Hand back** and can write one line about what they did. Squid records the microscope state a
second time and the agent drives again. If the microscope is not free, a message says why and the human keeps the
control.

### A dialog opens while the agent drives

A modal dialog blocks the Squid window, and the banner with it. So when one opens while the agent drives, Squid
takes over for the human by itself (`stop_cause` is `a dialog opened in Squid`). The dialog can be answered as soon
as the human drives. If a command of the agent waits for that dialog, the dialog can be answered after the time
limit of the stop.

Closing Squid asks for a confirmation, which is such a dialog: closing while the agent drives is a take over first.

## Operating rules

- The agent is the only client of the control server during a session.
- Do not start a fluidics protocol, tracking or a procedure from the menus inside a session.
- An operator is present while the agent drives.

## Known limits

- The server cannot tell two programs apart. A second script that sends commands is treated as the agent.
- If the agent stops sending commands, nothing happens until a human clicks **Take over**.
- A restart of Squid ends the session. The two recorded microscope states are lost.
- The handover shows the microscope state before and after the human drove. It does not list each action.
- On macOS the menu bar is at the top of the screen, outside the Squid window. The window cannot ignore clicks
  there. A menu item that opens a dialog causes a take over; another item acts at once.
- Closing a window is not input that the window ignores: the close button of a Squid window or dialog still
  works while the agent drives. Closing a dialog that a command of the agent waits for answers that dialog.
- In simulation the controller has no control panel, so a session begins with no panel lock. On a real controller
  with firmware older than 1.8 a session cannot begin, unless the setting below is on.

## A controller that cannot lock its panel

The panel lock needs controller firmware 1.8 or later. For an older controller there is one setting, off by
default, in the `[GENERAL]` section of the configuration file:

```ini
agent_session_allow_unlocked_panel = True
```

With it a session can begin with no panel lock. The banner then also says "The control panel is not locked: do
not touch the joystick." Nothing stops the joystick and the focus wheel: a hand on them moves the stage while the
agent drives. Use the setting only when an operator is present.
