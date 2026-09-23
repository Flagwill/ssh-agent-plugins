#!/usr/bin/env python3
"""Small stdio MCP server exposing a general-purpose SSH tool to Codex."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PLUGIN_DIR = Path(__file__).resolve().parent
HELPER = PLUGIN_DIR / "board_ssh_exec.py"
TOOL_NAMES = ("ssh", "board_ssh")

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["run", "put", "get"],
            "description": "run (default), put (upload), or get (download)",
        },
        "host": {
            "type": "string",
            "description": "Hostname, IP address, or user@host; overrides SSH_HOST",
        },
        "user": {
            "type": "string",
            "description": "SSH login user when host does not already contain user@",
        },
        "port": {
            "type": "integer",
            "description": "SSH port; defaults to SSH_PORT or 22",
        },
        "identity_file": {
            "type": "string",
            "description": "Path to a private key; overrides SSH_IDENTITY_FILE",
        },
        "env_file": {
            "type": "string",
            "description": "Local env file containing SSH_* connection settings",
        },
        "command": {
            "type": "string",
            "description": "Shell command to run on the remote host (action=run)",
        },
        "src": {
            "type": "string",
            "description": "Source path: local for put, remote for get",
        },
        "dst": {
            "type": "string",
            "description": "Destination path: remote for put, local for get",
        },
    },
    "additionalProperties": False,
}


def tool_definition(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": (
            "Connect to any remote machine over SSH, run a shell command, or "
            "transfer files with SCP. Provide host/user/port per call or use "
            "SSH_* environment variables. `board_ssh` is a compatibility alias."
        ),
        "inputSchema": TOOL_SCHEMA,
    }


TOOLS = [tool_definition(name) for name in TOOL_NAMES]


def response(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    sys.stdout.flush()


def error(request_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def tool_result(text: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return result


def append_option(argv: list[str], option: str, value: Any) -> None:
    if value is not None and value != "":
        argv.extend([option, str(value)])


def call_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    action = arguments.get("action", "run")
    if action == "run":
        command = arguments.get("command")
        if not command:
            return tool_result("ssh: `command` is required for action=run", True)
        values = ["run", command]
    elif action in ("put", "get"):
        src = arguments.get("src")
        dst = arguments.get("dst")
        if not src or not dst:
            return tool_result(f"ssh: `src` and `dst` are required for action={action}", True)
        values = [action, src, dst]
    else:
        return tool_result(f"ssh: unknown action {action}", True)

    if not HELPER.is_file():
        return tool_result(f"ssh: helper not found: {HELPER}", True)

    # Use the interpreter that launched the MCP server.  `python3` is not a
    # standard command name on Windows, and the Codex runtime may be bundled
    # rather than exposed on PATH.
    argv = [sys.executable, str(HELPER)]
    append_option(argv, "--host", arguments.get("host"))
    append_option(argv, "--user", arguments.get("user"))
    append_option(argv, "--port", arguments.get("port"))
    append_option(argv, "--identity-file", arguments.get("identity_file"))
    append_option(argv, "--env-file", arguments.get("env_file"))
    argv.extend(values)

    try:
        proc = subprocess.run(
            argv,
            cwd=str(PLUGIN_DIR),
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return tool_result(f"ssh: failed to start helper: {exc}", True)

    output = f"exit={proc.returncode}\n{proc.stdout}{proc.stderr}"
    return tool_result(output, proc.returncode != 0)


def handle(request: dict[str, Any]) -> None:
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    # JSON-RPC notifications do not receive a response.
    if "id" not in request:
        return
    if method == "initialize":
        response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ssh", "version": "0.2.0"},
            },
        )
    elif method == "tools/list":
        response(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            error(request_id, -32602, f"Unknown tool: {name}")
        else:
            response(request_id, call_tool(params.get("arguments") or {}))
    elif method == "ping":
        response(request_id, {})
    else:
        error(request_id, -32601, f"Method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if isinstance(request, dict):
                handle(request)
        except json.JSONDecodeError as exc:
            error(None, -32700, f"Parse error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
