# Broken LangGraph + MCP Agent (Demo)

This repository demonstrates a **broken** LangGraph agent that calls a real MCP (Model Context Protocol) server over stdio, along with a **fixed** version that addresses the issues.

## Overview

This project showcases common pitfalls when building LangGraph agents that interact with MCP servers, and how to fix them. The demo includes:

- **`broken_agent.py`**: An intentionally flawed agent with several bugs
- **`agent_fixed.py`**: A corrected version that implements proper error handling, retry logic, and human-in-the-loop (HITL) approval gates
- **`mcp_server.py`**: A mock MCP server that provides file system and database operations

## What This Project Demonstrates

### Broken Behaviors (in `broken_agent.py`)

1. **No error handling**: Tool errors crash the entire agent run
2. **Brittle input parsing**: Ambiguous commands (e.g., `update user:123` without field/value) cause `IndexError` crashes
3. **No safety gates**: Destructive actions (delete, update) execute immediately without approval

### Fixed Behaviors (in `agent_fixed.py`)

1. **Graceful error handling**: Tool failures are captured and handled without crashing
2. **Input validation**: Ambiguous commands prompt for clarification instead of crashing
3. **Human-in-the-loop (HITL)**: Destructive tools require explicit `APPROVE`/`DENY` confirmation
4. **Retry logic**: Failed operations trigger recovery strategies (e.g., if `read_file` fails, try `search_files` to find alternatives)
5. **Better output**: Clear error messages and audit trails

## MCP Server Tools

The `mcp_server.py` provides the following tools:

- **`read_file(path)`**: Read a file from the mock filesystem
- **`search_files(query)`**: Search for files by name
- **`delete_file(path)`**: Delete a file (destructive)
- **`get_record(key)`**: Retrieve a database record
- **`update_record(key, patch)`**: Update a database record (destructive)

## Setup

### Prerequisites

- Python 3.8+
- macOS / zsh (or adjust commands for your shell)

### Installation

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Run the Broken Agent

```bash
python3 broken_agent.py
```

Try these inputs to see the bugs:
- `read /configs/missing.yaml` - crashes on file not found
- `update user:123` - crashes with IndexError (missing field/value)
- `delete /configs/app.yaml` - deletes without asking

### Run the Fixed Agent

```bash
python3 agent_fixed.py
```

Try the same inputs to see how they're handled:
- `read /configs/missing.yaml` - gracefully handles error and attempts recovery
- `update user:123` - asks for clarification
- `delete /configs/app.yaml` - requires approval before executing

### Example Session (Fixed Agent)

```
You> read /configs/missing.yaml
Agent> Recovered gracefully from a tool failure:
1) Tried read_file('/configs/missing.yaml') → File not found
2) Reflected and tried search_files('missing.yaml') → no matches
Outcome: I could not find an alternative file to read.
AUDIT: [{'tool': 'read_file', 'args': {'path': '/configs/missing.yaml'}, 'ok': False, 'error': 'File not found: /configs/missing.yaml'}, {'reflection': "read_file failed; trying search_files for 'missing.yaml'"}, {'tool': 'search_files', 'args': {'query': 'missing.yaml'}, 'ok': True, 'result': []}]

You> delete /configs/app.yaml
Agent> Approval required before running delete_file with args={'path': '/configs/app.yaml'}.
Type APPROVE or DENY.

You> APPROVE
Agent> OK: deleted
AUDIT: [{'tool': 'delete_file', 'args': {'path': '/configs/app.yaml'}, 'ok': True, 'result': 'deleted'}]
```

## Architecture

### LangGraph State Graph

The fixed agent uses a state graph with the following nodes:

1. **`plan`**: Parses user input and creates a tool execution plan
2. **`gate`**: Checks if the planned tool is destructive
3. **`approval_router`**: Routes based on approval status
4. **`ask_approval`**: Prompts user for approval (ends run, waits for next input)
5. **`call_tool`**: Executes the MCP tool call
6. **`reflect_retry`**: Implements recovery strategies on failure
7. **`finalize`**: Formats the final output

### MCP Integration

Both agents communicate with the MCP server via stdio:
- Each tool call spawns a new MCP server subprocess
- Uses the `mcp.client.stdio` client library
- Server provides tools via the FastMCP framework

## Key Learnings

1. **Always handle tool errors gracefully** - Don't let exceptions crash your agent
2. **Validate user input** - Check for required parameters before parsing
3. **Implement approval gates** - Require explicit confirmation for destructive operations
4. **Add retry logic** - When operations fail, try alternative strategies
5. **Provide clear feedback** - Include audit trails and meaningful error messages

## License

This is a demo/educational project.