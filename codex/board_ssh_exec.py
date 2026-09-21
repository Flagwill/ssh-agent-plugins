#!/usr/bin/env python3
"""Run commands and transfer files on arbitrary hosts over SSH/SCP.

Connection settings can come from per-call options, SSH_* environment
variables, or a local env file. BOARD_SSH_* names remain supported as a
backward-compatible alias for the original board plugin.
"""

import os
import pty
import select
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_PORT = "22"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args(argv: list[str]) -> tuple[dict[str, str], str, list[str]]:
    """Parse options before the action so command strings may contain dashes."""
    options: dict[str, str] = {}
    i = 1
    names = {
        "--host": "host",
        "--user": "user",
        "--port": "port",
        "--identity-file": "identity_file",
        "--env-file": "env_file",
    }
    while i < len(argv) and argv[i] in names:
        if i + 1 >= len(argv):
            raise ValueError(f"{argv[i]} requires a value")
        options[names[argv[i]]] = argv[i + 1]
        i += 2
    if i >= len(argv):
        raise ValueError("missing action")
    return options, argv[i], argv[i + 1 :]


def first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return ""


def config(options: dict[str, str]) -> tuple[str, str, str, str, str]:
    env_file = options.get("env_file") or first_env("SSH_ENV_FILE", "BOARD_SSH_ENV_FILE")
    candidates: list[Path] = []
    if env_file:
        candidates.append(Path(env_file).expanduser())
    candidates.append(HERE / "board.env")
    for path in candidates:
        load_env_file(path)

    host = options.get("host") or first_env("SSH_HOST", "BOARD_SSH_HOST")
    user = options.get("user") or first_env("SSH_USER", "BOARD_SSH_USER")
    port = options.get("port") or first_env("SSH_PORT", "BOARD_SSH_PORT") or DEFAULT_PORT
    password = first_env("SSH_PASSWORD", "BOARD_SSH_PASSWORD")
    identity_file = options.get("identity_file") or first_env(
        "SSH_IDENTITY_FILE", "BOARD_SSH_IDENTITY_FILE"
    )
    return host, user, port, password, identity_file


def endpoint(host: str, user: str) -> str:
    host = host.strip()
    if not host:
        raise ValueError("host is required; pass host or set SSH_HOST")
    if user and "@" not in host:
        return f"{user}@{host}"
    return host


def pty_run(argv: list[str], password: str) -> int:
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(argv[0], argv)
        except Exception:
            os._exit(127)

    sent = False
    while True:
        try:
            readable, _, _ = select.select([fd], [], [], 1.0)
        except (OSError, ValueError):
            break
        if readable:
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                break
            os.write(1, data)
            low = data.lower()
            if (b"password:" in low or b"passphrase:" in low or b"password" in low) and not sent:
                time.sleep(0.25)
                os.write(fd, (password + "\n").encode())
                sent = True
        try:
            waited_pid, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            break
        if waited_pid == pid:
            try:
                while True:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    os.write(1, data)
            except OSError:
                pass
            return os.waitstatus_to_exitcode(status)

    try:
        _, status = os.waitpid(pid, 0)
        return os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        return 1


def main(argv: list[str]) -> int:
    try:
        options, mode, values = parse_args(argv)
        host, user, port, password, identity_file = config(options)
        target = endpoint(host, user)
    except (ValueError, OSError) as exc:
        print(f"ssh: {exc}", file=sys.stderr)
        return 2

    common = [
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=20",
    ]
    if identity_file:
        common.extend(["-i", str(Path(identity_file).expanduser())])

    if mode == "run":
        if not values or not values[0]:
            print("ssh: command is required for action=run", file=sys.stderr)
            return 2
        command = values[0]
        return pty_run(["ssh", *common, "-p", port, target, command], password)

    if mode == "put" and len(values) >= 2:
        local, remote = values[0], values[1]
        return pty_run(
            ["scp", *common, "-P", port, local, f"{target}:{remote}"], password
        )

    if mode == "get" and len(values) >= 2:
        remote, local = values[0], values[1]
        return pty_run(
            ["scp", *common, "-P", port, f"{target}:{remote}", local], password
        )

    print(f"ssh: expected action={mode} arguments", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
