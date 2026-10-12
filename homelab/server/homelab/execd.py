"""execd: root 执行守护。unix socket, 只接受 mcp/root 身份连接。

职责:
  1. 校验一次性 nonce 与 (host, argv) 逐字匹配（原子核销）
  2. 永久拒绝清单二次校验（不可被审批覆盖）
  3. argv-only 执行（本地直 exec; 远端 shlex.quote 后 ssh root@host）
  4. 输出审计 + I/O 留痕
"""
from __future__ import annotations

import json
import os
import pwd
import shlex
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time

from . import approvals, audit, db
from .config import Config

SOCKET_PATH = "/run/homelab-execd.sock"
MAX_OUTPUT = 60000


def _allowed_uid(cfg: Config) -> set[int]:
    uids = {0}
    try:
        uids.add(pwd.getpwnam("mcp").pw_uid)
    except KeyError:
        pass
    return uids


def check_deny(cfg: Config, argv: list[str]) -> str | None:
    joined = " ".join(argv)
    for pat in cfg.deny_patterns:
        import re
        if re.search(pat, joined, re.I):
            return pat
    for a in argv:
        if "/etc/homelab/keys" in a or "id_hosts" in a or "authorized_keys" in a:
            return "credential-access"
    return None


def execute(cfg: Config, host_id: str, argv: list[str],
            timeout: int) -> dict:
    t0 = time.time()
    h = cfg.host(host_id)
    if h is None:
        return {"ok": False, "error": f"unknown host: {host_id}"}
    try:
        if h["kind"] == "local":
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
                cwd="/", env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
        else:
            user = h.get("exec_user", "root")
            remote = " ".join(shlex.quote(a) for a in argv)
            key_path = cfg.data["general"].get("root_key", cfg.agent_key)
            full = ["ssh", "-p", str(h["port"]),
                    "-i", key_path,
                    "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", f"UserKnownHostsFile={cfg.known_hosts}",
                    "-o", "ConnectTimeout=10",
                    "-o", "ControlMaster=auto",
                    "-o", f"ControlPath=/run/homelab-execd/cm-{host_id}.sock",
                    "-o", "ControlPersist=300",
                    f"{user}@{h['host']}", remote]
            proc = subprocess.run(full, capture_output=True, text=True,
                                  timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s",
                "exit_code": None, "stdout": "", "stderr": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "exit_code": None, "stdout": "", "stderr": ""}
    return {
        "ok": True, "exit_code": proc.returncode,
        "stdout": proc.stdout[-MAX_OUTPUT:],
        "stderr": proc.stderr[-MAX_OUTPUT:],
        "duration_ms": int((time.time() - t0) * 1000),
    }


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        cfg: Config = self.server.cfg  # type: ignore[attr-defined]
        # 对端身份校验
        try:
            cred = self.connection.getsockopt(socket.SOL_SOCKET,
                                              socket.SO_PEERCRED,
                                              struct.calcsize("3i"))
            pid, uid, gid = struct.unpack("3i", cred)
        except Exception:
            uid = -1
        if uid not in _allowed_uid(cfg):
            self._reply({"ok": False, "error": "peer uid not allowed"})
            return
        line = self.rfile.readline(1 << 20)
        if not line:
            return
        try:
            req = json.loads(line.decode())
        except json.JSONDecodeError:
            self._reply({"ok": False, "error": "bad json"})
            return

        if req.get("action") == "ping":
            self._reply({"ok": True, "pong": True})
            return

        nonce = req.get("nonce", "")
        host_id = req.get("host", "")
        argv = req.get("argv", [])
        agent = req.get("agent", "unknown")
        timeout = min(int(req.get("timeout", 120)), 600)

        if not (nonce and host_id and isinstance(argv, list) and argv):
            self._reply({"ok": False, "error": "missing nonce/host/argv"})
            return

        # 1) 永久拒绝清单（不可覆盖）
        deny = check_deny(cfg, argv)
        if deny:
            audit.record(cfg.db_path, agent, "exec_denied", host_id, argv,
                         f"hard-deny rule: {deny}")
            self._reply({"ok": False, "error": f"命中永久拒绝规则: {deny}"})
            return

        # 2) nonce 原子核销（与批准记录逐字匹配）
        ok, row, msg = approvals.try_consume(cfg.db_path, nonce, host_id, argv)
        if not ok:
            audit.record(cfg.db_path, agent, "exec_rejected", host_id, argv, msg)
            self._reply({"ok": False, "error": msg, "rejected": True})
            return

        # 3) 执行
        result = execute(cfg, host_id, argv, timeout)
        result["ok"] = result.get("ok", False)
        result["nonce"] = nonce[:6] + "…"
        audit.record(cfg.db_path, agent, "exec", host_id, argv,
                     f"rc={result.get('exit_code')} {result.get('duration_ms', 0)}ms "
                     f"by={row.get('decided_by') if row else '?'}")
        self._reply(result)

    def _reply(self, obj: dict) -> None:
        self.wfile.write(
            (json.dumps(obj, ensure_ascii=False) + "\n").encode())


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, cfg: Config):
        self.cfg = cfg
        os.makedirs("/run/homelab-execd/cm", mode=0o700, exist_ok=True)
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        super().__init__(SOCKET_PATH, Handler)
        os.chmod(SOCKET_PATH, 0o660)
        try:
            import grp
            os.chown(SOCKET_PATH, 0, grp.getgrnam("mcp").gr_gid)
        except Exception:
            pass


def main() -> None:
    cfg = Config(os.environ.get("HOMELAB_CONFIG", "/opt/homelab/hosts.toml"))
    db.init_db(cfg.db_path)
    audit.record(cfg.db_path, "execd", "daemon_start", "", [], "execd up")
    srv = Server(cfg)
    srv.serve_forever()


if __name__ == "__main__":
    main()
