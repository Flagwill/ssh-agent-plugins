"""argv-only SSH 执行封装：不过 shell，ControlMaster 复用连接。

所有远程命令都以参数数组传递；远端必须执行的 shell 字符串一律经 shlex.quote。
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time

from .config import Config


def ssh_base_args(cfg: Config) -> list[str]:
    return [
        "-i", cfg.agent_key,
        "-o", "IdentitiesOnly=yes",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={cfg.known_hosts}",
        "-o", "ConnectTimeout=8",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2",
    ]


def _control_path(cfg: Config, host_id: str, tag: str) -> str:
    d = cfg.control_dir if os.geteuid() != 0 else "/run/homelab-execd/cm"
    os.makedirs(d, mode=0o700, exist_ok=True)
    return f"{d}/cm-{tag}-{host_id}-{os.geteuid()}.sock"


def run(cfg: Config, host_id: str, argv: list[str], *,
        as_root: bool = False, timeout: float = 30.0,
        stdin_data: str | None = None) -> dict:
    """执行只读/白名单命令。本地主机直接 exec；远端走 SSH argv-only。"""
    t0 = time.time()
    h = cfg.host(host_id)
    if h is None:
        return {"ok": False, "error": f"unknown host: {host_id}"}
    tag = "root" if as_root else "agent"
    try:
        if h["kind"] == "local":
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
                input=stdin_data, env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
                cwd="/")
        else:
            user = "root" if as_root else h["user"]
            target = f"{user}@{h['host']}"
            remote_cmd = " ".join(shlex.quote(a) for a in argv)
            full = ["ssh", "-p", str(h["port"]), *ssh_base_args(cfg),
                    "-o", "ControlMaster=auto",
                    "-o", f"ControlPath={_control_path(cfg, host_id, tag)}",
                    "-o", "ControlPersist=180",
                    target, remote_cmd]
            proc = subprocess.run(
                full, capture_output=True, text=True, timeout=timeout,
                input=stdin_data)
        return {
            "ok": True,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration_ms": int((time.time() - t0) * 1000),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s",
                "exit_code": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def sampler_argv(cfg: Config, host_id: str, units: list[str],
                 procs: list[str]) -> list[str]:
    h = cfg.host(host_id)
    # 必须用绝对路径: 远端命令经 shlex.quote 后 ~ 不会被 shell 展开
    if h["kind"] == "local":
        script = "/opt/homelab/homelab/sampler.py"
    else:
        script = f"/home/{h['user']}/.homelab/sampler.py"
    argv = ["python3", script]
    if units:
        argv += ["--units", ",".join(str(u) for u in units)]
    if procs:
        norm = [p if isinstance(p, str) else f"{p['label']}:{p['match']}"
                for p in procs]
        argv += ["--procs", ",".join(norm)]
    return argv
