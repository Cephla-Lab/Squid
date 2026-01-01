# IPython Kernel Integration (Future)

Design document for embedding an IPython kernel in the Squid GUI for human+AI collaboration.

## Use Cases

- Human and Claude share the same session state
- Long exploratory/analysis sessions where state persists
- Interruptible execution for safety
- Integration with existing Jupyter workflows

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  Jupyter        │     │  Claude Code    │
│  Notebook       │     │  (MCP tool)     │
└────────┬────────┘     └────────┬────────┘
         │ ZMQ                   │ ZMQ
         └───────────┬───────────┘
                     ▼
            ┌─────────────────┐
            │  IPython Kernel │
            │  (in GUI)       │
            │                 │
            │  microscope     │
            │  stage, camera  │
            └─────────────────┘
```

## Implementation

### 1. Embed Kernel in GUI

```python
# In gui_hcs.py or similar
from ipykernel.embed import embed_kernel
import threading

class SquidGUI:
    def __init__(self, ...):
        # ... existing init ...

        # Start IPython kernel in background thread
        self._start_ipython_kernel()

    def _start_ipython_kernel(self):
        """Start embedded IPython kernel with microscope access."""
        kernel_ns = {
            'microscope': self.microscope,
            'stage': self.microscope.stage,
            'camera': self.microscope.camera,
            'live_controller': self.microscope.live_controller,
            'multipoint_controller': self.multipointController,
            'scan_coordinates': self.scanCoordinates,
            'gui': self,
        }

        # embed_kernel blocks, so run in thread
        def run_kernel():
            embed_kernel(local_ns=kernel_ns)

        self._kernel_thread = threading.Thread(target=run_kernel, daemon=True)
        self._kernel_thread.start()
```

### 2. MCP Tool for Kernel Communication

```python
# In mcp_microscope_server.py or new file
from jupyter_client import BlockingKernelClient

class KernelMCPTool:
    def __init__(self, connection_file: str):
        self.client = BlockingKernelClient(connection_file=connection_file)
        self.client.start_channels()

    def execute(self, code: str, timeout: float = 30.0) -> dict:
        """Execute code in the kernel and return results."""
        msg_id = self.client.execute(code)

        # Collect outputs
        outputs = []
        while True:
            try:
                msg = self.client.get_iopub_msg(timeout=timeout)
                msg_type = msg['msg_type']
                content = msg['content']

                if msg_type == 'execute_result':
                    outputs.append({'type': 'result', 'data': content['data']})
                elif msg_type == 'stream':
                    outputs.append({'type': 'stream', 'name': content['name'], 'text': content['text']})
                elif msg_type == 'display_data':
                    outputs.append({'type': 'display', 'data': content['data']})
                elif msg_type == 'error':
                    outputs.append({'type': 'error', 'ename': content['ename'], 'evalue': content['evalue']})
                elif msg_type == 'status' and content['execution_state'] == 'idle':
                    break
            except Exception:
                break

        return {'outputs': outputs}

    def interrupt(self):
        """Interrupt currently running code."""
        self.client.interrupt()

    def complete(self, code: str, cursor_pos: int) -> list:
        """Get tab completions."""
        msg_id = self.client.complete(code, cursor_pos)
        reply = self.client.get_shell_msg(timeout=5.0)
        return reply['content'].get('matches', [])
```

### 3. MCP Server Integration

```python
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "microscope_kernel_execute":
        result = kernel_tool.execute(arguments['code'])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "microscope_kernel_interrupt":
        kernel_tool.interrupt()
        return [TextContent(type="text", text="Kernel interrupted")]
```

## Dependencies

```
ipykernel>=6.0
jupyter_client>=7.0
pyzmq>=22.0
```

## Advantages Over python_exec

1. **Shared state** - Human in Jupyter + Claude share variables
2. **Interruptible** - Can stop runaway code without killing server
3. **Rich outputs** - Images, plots returned natively in protocol
4. **Standard protocol** - Any Jupyter client can connect

## When to Implement

Consider implementing when:
- Users want human+AI collaboration on same session
- Long stateful analysis sessions are needed
- Current python_exec becomes limiting
