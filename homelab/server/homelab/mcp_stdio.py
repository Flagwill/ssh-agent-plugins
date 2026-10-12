"""MCP stdio JSON-RPC 服务（newline-delimited JSON-RPC 2.0）。

启动:  python3 -m homelab.mcp_stdio [--agent LABEL]
stdout 只承载协议; 日志走 stderr -> 由 homelab-mcpd 重定向到文件。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

from . import audit, db
from .config import Config
from . import tools

PROTOCOL_VERSION = "2024-11-05"


def _agent_label(argv: list[str]) -> str:
    if "--agent" in argv:
        i = argv.index("--agent")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.environ.get("HOMELAB_AGENT", "unknown")


def _peer_ip() -> str:
    # stdio 下拿不到对端; SSH_CONNECTION 由 sshd 传入
    conn = os.environ.get("SSH_CONNECTION", "")
    return conn.split()[0] if conn else "stdio"


class Server:
    def __init__(self, cfg: Config, actor: str):
        self.cfg = cfg
        self.actor = actor
        self._heartbeat = time.time()
        self._ensure_agent()

    # ---- agent 注册 / 熔断 ----
    def _ensure_agent(self) -> None:
        now = int(time.time())
        row = db.query_one(self.cfg.db_path,
                           "SELECT * FROM agents WHERE key=?", (self.actor,))
        if row is None:
            db.execute(self.cfg.db_path,
                       "INSERT OR REPLACE INTO agents(key,label,enabled,created_at,last_seen)"
                       " VALUES(?,?,1,?,?)", (self.actor, self.actor, now, now))
        else:
            db.execute(self.cfg.db_path,
                       "UPDATE agents SET last_seen=? WHERE key=?",
                       (now, self.actor))

    def agent_enabled(self) -> bool:
        row = db.query_one(self.cfg.db_path,
                           "SELECT enabled FROM agents WHERE key=?", (self.actor,))
        return bool(row and row["enabled"])

    # ---- 协议处理 ----
    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method", "")
        mid = msg.get("id")
        if method == "initialize":
            params = msg.get("params", {})
            ver = params.get("protocolVersion", PROTOCOL_VERSION)
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "homelab", "version": "1.0.0"},
                "instructions":
                    "统一管理 raspi/asus/ali/cone 四台服务器。"
                    "【只读】host_status/services/logs/host_metrics/run_read/sentinel_* 免审批直接用。"
                    "【变更系统】必须用 sudo_run(host, argv, reason): 命中 auto_allow 立即执行; "
                    "否则返回 pending —— 把 tell_user 转告用户去 https://flagw.apay.eu.cc/home 批准, "
                    "然后 sudo_wait(nonce) 轮询, 批准后自动执行。禁止自行拼 ssh/sudo 命令, "
                    "也不要去服务器读 homelab 源码; 流程疑问先调 sudo_help。",
            }}
        if method in ("notifications/initialized", "initialized",
                      "notifications/cancelled", "notifications/roots/list_changed"):
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"tools": tools.TOOLS}}
        if method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            if not self.agent_enabled():
                return {"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32000, "message": "该 agent 已被禁用(见 Web 控制台)"}}
            result = tools.call_tool(self.cfg, self.actor, name, arguments,
                                     _peer_ip())
            text = json.dumps(result, ensure_ascii=False, default=str)
            is_err = isinstance(result, dict) and bool(result.get("error"))
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": text}],
                "structuredContent": result if not is_err else None,
                "isError": is_err}}
        if method in ("resources/list", "prompts/list"):
            return {"jsonrpc": "2.0", "id": mid, "result": {method.split("/")[0]: []}}
        return {"jsonrpc": "2.0", "id": mid, "error": {
            "code": -32601, "message": f"method not found: {method}"}}

    def serve(self, stdin, stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32700, "message": "parse error"}}) + "\n")
                stdout.flush()
                continue
            try:
                resp = self.handle(msg)
            except Exception as e:  # noqa: BLE001
                resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                    "code": -32603, "message": f"{type(e).__name__}: {e}"}}
            if resp is not None:
                stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                stdout.flush()


def main() -> None:
    cfg = Config(os.environ.get("HOMELAB_CONFIG", "/opt/homelab/hosts.toml"))
    db.init_db(cfg.db_path)
    actor = _agent_label(sys.argv[1:])
    ip = _peer_ip()
    audit.record(cfg.db_path, actor, "session_start", "", [],
                 f"stdio session from {ip}", ip)
    srv = Server(cfg, actor)
    # 心跳线程: 更新 last_seen
    def beat():
        while True:
            db.execute(cfg.db_path,
                       "UPDATE agents SET last_seen=?, last_ip=? WHERE key=?",
                       (int(time.time()), ip, actor))
            time.sleep(30)
    threading.Thread(target=beat, daemon=True).start()
    srv.serve(sys.stdin, sys.stdout)
    audit.record(cfg.db_path, actor, "session_end", "", [], "", ip)


if __name__ == "__main__":
    main()
