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
agent_notebook_end_at_close = True
```

- `agent_notebook_command`: the program and its arguments. Put quotes around an argument that has spaces.
  `{timestamp}` becomes the time of the start (`YYYYMMDD-HHMMSS`), so that each start can have its own folder.
  Empty (the default): no menu entry.
- `agent_notebook_url`: the address at which the program serves its page. Default `http://127.0.0.1:5003/`.
- `agent_notebook_end_at_close`: `True` (the default): a notebook program that Squid started ends when Squid
  closes. `False`: the program goes on. Use `False` for a notebook that keeps its own list of sessions, so that a
  person can read them while Squid is closed.

Restart Squid after a change.

## What the menu entry does

1. Squid asks the program at the address what it is (see "What the notebook program must do").
2. If a different program answers at the address, a message says so. Squid starts nothing and opens nothing.
3. Else Squid starts the MCP control server, each time. The agent of the notebook talks to it, and a notebook
   that someone started by hand can run while the server is off. The entry `Settings > Enable MCP Control Server`
   then shows as on.
4. If the notebook runs already, Squid opens its page in the browser.
5. If not, Squid starts the program, waits until it says that it is the notebook (at most 20 s), and opens the
   page.
6. If the program does not start, ends at once, or does not answer, a message says why and shows the last lines
   of the program's output. The output is in `agent_notebook.log` in Squid's log folder.

A notebook program that Squid started ends when Squid closes, unless `agent_notebook_end_at_close` is `False`.
On Linux and macOS, the programs that it started end too. A program that someone else started is left alone.

## What the notebook program must do

The program must answer `GET <agent_notebook_url>api/service` with a JSON object that has
`"role": "agent-notebook"`, for example:

```json
{"role": "agent-notebook", "version": "1.0"}
```

Squid uses this to tell the notebook from a different program that has the same port. Until the program is ready,
it gives a different answer or none.
