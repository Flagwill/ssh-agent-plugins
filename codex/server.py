#!/usr/bin/env python3
"""Small stdio MCP server exposing a general-purpose SSH tool to Codex."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO


PLUGIN_DIR = Path(__file__).resolve().parent
HELPER = PLUGIN_DIR / "board_ssh_exec.py"
TOOL_NAMES = ("ssh", "board_ssh")

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["run", "put", "get", "terminal"],
            "description": "run (default), put (upload), get (download), or terminal (persistent SSH shell)",
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
        "password": {
            "type": "string",
            "format": "password",
            "writeOnly": True,
            "description": "SSH password for this call; used by SSH askpass and never passed as an argument",
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
        "session_id": {
            "type": "string",
            "description": "Existing action=terminal session to continue",
        },
        "input": {
            "type": "string",
            "description": "Text or terminal control characters to send to the session",
        },
        "close": {
            "type": "boolean",
            "description": "Send EOF to an interactive terminal",
        },
        "wait_ms": {
            "type": "integer",
            "description": "Time to collect terminal output, 0-5000 ms (default 500)",
        },
    },
    "additionalProperties": False,
}


def tool_definition(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": (
            "Connect to any remote machine over SSH, run a shell command, or "
            "transfer files with SCP. Open a persistent interactive terminal "
            "with action=terminal and continue it by session_id. Provide host/user/port per call or use "
            "SSH_* environment variables. Password is passed in the helper "
            "askpass flow and redacted from returned output. The MCP "
            "writeOnly hint does not guarantee that every client masks it. "
            "board_ssh is a compatibility alias."
        ),
        "inputSchema": TOOL_SCHEMA,
    }


TOOLS = [tool_definition(name) for name in TOOL_NAMES]


class TerminalSession:
    def __init__(self, process: subprocess.Popen[bytes], password: str = ""):
        self.process = process
        self.password = password
        self.output = bytearray()
        self.lock = threading.Lock()

    def append(self, data: bytes) -> None:
        with self.lock:
            self.output.extend(data)
            if len(self.output) > 1_048_576:
                del self.output[:-1_048_576]

    def drain(self) -> str:
        with self.lock:
            data = bytes(self.output)
            self.output.clear()
        return data.decode("utf-8", errors="replace")


TERMINALS: dict[str, TerminalSession] = {}


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


def terminal_call(arguments: dict[str, Any], password: str) -> dict[str, Any]:
    session_id = arguments.get("session_id")
    terminal_input = arguments.get("input", "")
    close = arguments.get("close", False)
    wait_ms = arguments.get("wait_ms", 500)
    command = arguments.get("command")

    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        return tool_result("ssh: session_id must be a non-empty string", True)
    if not isinstance(terminal_input, str):
        return tool_result("ssh: input must be a string", True)
    if not isinstance(close, bool):
        return tool_result("ssh: close must be a boolean", True)
    if isinstance(wait_ms, bool) or not isinstance(wait_ms, int):
        return tool_result("ssh: wait_ms must be an integer", True)
    if command is not None and not isinstance(command, str):
        return tool_result("ssh: command must be a string", True)
    wait_ms = max(0, min(wait_ms, 5000))

    if session_id:
        session = TERMINALS.get(session_id)
        if session is None:
            return tool_result(f"ssh: terminal session not found: {session_id}", True)
        try:
            if terminal_input and session.process.stdin and not session.process.stdin.closed:
                session.process.stdin.write(terminal_input.encode("utf-8"))
                session.process.stdin.flush()
            if close and session.process.stdin and not session.process.stdin.closed:
                session.process.stdin.close()
        except (BrokenPipeError, OSError, ValueError) as exc:
            return tool_result(f"ssh: terminal input failed: {exc}", True)
    else:
        if close:
            return tool_result("ssh: close can only be used with an existing session_id", True)
        if not HELPER.is_file():
            return tool_result(f"ssh: helper not found: {HELPER}", True)
        if command is not None and not isinstance(command, str):
            return tool_result("ssh: command must be a string", True)

        argv = [sys.executable, str(HELPER)]
        append_option(argv, "--host", arguments.get("host"))
        append_option(argv, "--user", arguments.get("user"))
        append_option(argv, "--port", arguments.get("port"))
        append_option(argv, "--identity-file", arguments.get("identity_file"))
        append_option(argv, "--env-file", arguments.get("env_file"))
        argv.append("terminal")
        if command:
            argv.append(command)

        child_env = os.environ.copy()
        if password:
            child_env["SSH_PASSWORD"] = password
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(PLUGIN_DIR),
                env=child_env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except OSError as exc:
            message = f"ssh: failed to start terminal helper: {exc}"
            if password:
                message = message.replace(password, "")
            return tool_result(message, True)

        session_id = secrets.token_urlsafe(18)
        session = TerminalSession(process, password)
        TERMINALS[session_id] = session

        def read_output(stream: BinaryIO | None) -> None:
            if stream is None:
                return
            while True:
                try:
                    data = os.read(stream.fileno(), 4096)
                except OSError:
                    break
                if not data:
                    break
                session.append(data)

        threading.Thread(
            target=read_output, args=(process.stdout,), daemon=True
        ).start()
        if terminal_input and process.stdin:
            try:
                process.stdin.write(terminal_input.encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass

    if wait_ms:
        time.sleep(wait_ms / 1000)
    output = session.drain()
    if session.password:
        output = output.replace(session.password, "[REDACTED]")
    status = session.process.poll()
    state = "running" if status is None else f"exited={status}"
    result = f"session_id={session_id}\n{state}\n{output}"
    return tool_result(result, status not in (None, 0))


def shutdown_terminals() -> None:
    sessions = list(TERMINALS.values())
    for session in sessions:
        stream = session.process.stdin
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass
    for session in sessions:
        try:
            session.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            session.process.terminate()
            try:
                session.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                session.process.kill()
    TERMINALS.clear()


def call_tool(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return tool_result("ssh: arguments must be an object", True)

    password_supplied = "password" in arguments
    password = arguments.get("password")
    if password_supplied:
        if not isinstance(password, str) or not password:
            return tool_result("ssh: password must be a non-empty string", True)
        if any(char in password for char in (chr(0), chr(13), chr(10))):
            return tool_result("ssh: password must not contain NUL or line breaks", True)

    def result(message: str, is_error: bool = False) -> dict[str, Any]:
        if password_supplied:
            message = message.replace(password, "")
        return tool_result(message, is_error)

    action = arguments.get("action", "run")
    if action == "terminal":
        return terminal_call(arguments, password if password_supplied else "")
    if action == "run":
        command = arguments.get("command")
        if not command:
            return result("ssh: `command` is required for action=run", True)
        values = ["run", command]
    elif action in ("put", "get"):
        src = arguments.get("src")
        dst = arguments.get("dst")
        if not src or not dst:
            return result(f"ssh: `src` and `dst` are required for action={action}", True)
        values = [action, src, dst]
    else:
        return result(f"ssh: unknown action {action}", True)

    if not HELPER.is_file():
        return result(f"ssh: helper not found: {HELPER}", True)

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

    # Keep per-call credentials out of argv and the server's shared environment.
    child_env = os.environ.copy()
    if password_supplied:
        child_env["SSH_PASSWORD"] = password

    try:
        proc = subprocess.run(
            argv,
            cwd=str(PLUGIN_DIR),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return result(f"ssh: failed to start helper: {exc}", True)

    output = f"exit={proc.returncode}\n{proc.stdout}{proc.stderr}"
    return result(output, proc.returncode != 0)


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
                "serverInfo": {"name": "ssh", "version": "0.3.0"},
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
    try:
        for line in sys.stdin:
            try:
                request = json.loads(line)
                if isinstance(request, dict):
                    handle(request)
            except json.JSONDecodeError as exc:
                error(None, -32700, f"Parse error: {exc}")
    finally:
        shutdown_terminals()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
