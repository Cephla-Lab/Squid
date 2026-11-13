# Agent Notebook

`Utils > Agent Notebook` opens the lab notebook of an agent session: a local web page with a chat, a notebook
that is written while an agent operates the microscope, and who is driving. The notebook is a separate program.
Squid does not contain it; Squid starts it with a command line that you give in the configuration.

The menu entry is there only when the configuration has a command. By default it has none, and nothing changes.

## Configuration

In the `[GENERAL]` section of the machine configuration (`configuration_*.ini`):

```ini
agent_notebook_command = python3 -m my_notebook serve --run-dir /data/notebook-runs/run-{timestamp}
agent_notebook_url = http://127.0.0.1:5003/
```

- `agent_notebook_command`: the program and its arguments. Put quotes around an argument that has spaces.
  `{timestamp}` becomes the time of the start (`YYYYMMDD-HHMMSS`), so that each start can have its own folder.
  Empty (the default): no menu entry.
- `agent_notebook_url`: the address at which the program serves its page. Default `http://127.0.0.1:5003/`.

Restart Squid after a change.

## What the menu entry does

1. If a program answers at the address already, Squid only opens the page in the browser.
2. If not, Squid starts the MCP control server (the agent of the notebook talks to it; the entry
   `Settings > Enable MCP Control Server` then shows as on), starts the program, waits for its page (at most
   20 s), and opens the page.
3. If the program does not start, ends at once, or does not answer, a message says why and shows the last lines
   of the program's output. The output is in `agent_notebook.log` in Squid's log folder.

A notebook program that Squid started ends when Squid closes. A program that someone else started is left alone.

A notebook program can begin an agent session when it starts. The Squid window then shows the banner of the
session and ignores input until you press Take over.
