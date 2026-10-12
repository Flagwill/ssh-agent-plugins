"""MCP 工具实现层（Web 审批台与 MCP stdio 共用）。"""
from __future__ import annotations

import json
import re
import socket
import time

from . import approvals, audit, db, sshutil

EVENTS_QUERY = r'''
import json, sqlite3, os
p = "/var/lib/sentinel/events.sqlite3"
out = {}
if not os.path.exists(p):
    out["error"] = "db not found"
else:
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
    con.row_factory = sqlite3.Row
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    out["tables"] = tables
    for t in ("events", "detections", "motion"):
        if t in tables:
            rows = [dict(r) for r in con.execute("select * from %s order by rowid desc limit 20" % t)]
            out[t] = rows
            break
    con.close()
print(json.dumps(out, ensure_ascii=False, default=str))
'''

SERVICE_HINTS = [
    ("systemctl", "systemd 服务操作"),
    ("service", "systemd 服务操作"),
    ("apt", "软件包变更"),
    ("apt-get", "软件包变更"),
    ("dpkg", "软件包变更"),
    ("pip", "Python 环境变更"),
    ("rm", "文件删除"),
    ("mv", "文件移动"),
    ("cp", "文件复制"),
    ("dd", "磁盘写入"),
    ("truncate", "文件截断"),
    ("chmod", "权限变更"),
    ("chown", "属主变更"),
    ("iptables", "防火墙变更"),
    ("nft", "防火墙变更"),
    ("ufw", "防火墙变更"),
    ("certbot", "证书变更"),
    ("xray", "代理服务"),
    ("sing-box", "代理服务"),
    ("nginx", "Web 服务"),
    ("caddy", "Web 服务"),
    ("frps", "frp 服务"),
    ("systemd-run", "任意代码执行"),
    ("bash", "任意 shell"),
    ("sh", "任意 shell"),
    ("eval", "任意代码执行"),
]


class Ctx:
    def __init__(self, cfg, actor: str, ip: str = ""):
        self.cfg = cfg
        self.actor = actor
        self.ip = ip
        self.dbf = cfg.db_path


def _deny_hit(cfg, text: str) -> str | None:
    for pat in cfg.deny_patterns:
        if re.search(pat, text, re.I):
            return pat
    return None


def check_deny(cfg, argv: list[str]) -> str | None:
    joined = " ".join(argv)
    hit = _deny_hit(cfg, joined)
    if hit:
        return f"命中永久拒绝规则 /{hit}/"
    for a in argv:
        if "/etc/homelab/keys" in a or "id_hosts" in a or "authorized_keys" in a:
            return "拒绝访问 homelab 凭据文件"
    return None


def render_summary(host_label: str, argv: list[str]) -> str:
    cmdline = " ".join(argv)
    hints = []
    prog = argv[0] if argv else ""
    for p, h in SERVICE_HINTS:
        if prog == p or prog.endswith("/" + p):
            hints.append(h)
            break
    if argv[:2] == ["systemctl", "restart"] and len(argv) > 2:
        hints.append(f"将重启 {argv[2]}")
    hint = f"（{'、'.join(hints)}）" if hints else ""
    return f"在 {host_label} 上以 root 执行: {cmdline} {hint}".strip()


# ---------------------------------------------------------------- 只读工具

def _latest_metric(cfg, host_id: str) -> dict | None:
    row = db.query_one(
        cfg.db_path,
        "SELECT ts, payload FROM metrics WHERE host=? ORDER BY ts DESC LIMIT 1",
        (host_id,))
    if not row:
        return None
    return {"ts": row["ts"], **json.loads(row["payload"])}


def tool_hosts(ctx: Ctx, **_):
    out = []
    interval = ctx.cfg.collect_interval
    for hid in ctx.cfg.hosts:
        h = ctx.cfg.host(hid)
        m = _latest_metric(ctx.cfg, hid)
        online = bool(m and (time.time() - m["ts"]) < interval * 3 + 30)
        out.append({"id": hid, "label": h["label"], "online": online,
                    "last_seen": m["ts"] if m else None})
    return out


def tool_host_status(ctx: Ctx, host: str = "", **_):
    if not host:
        return {"error": "host 必填"}
    m = _latest_metric(ctx.cfg, host)
    if not m:
        return {"host": host, "online": False, "error": "尚无采集数据"}
    return m


def tool_host_metrics(ctx: Ctx, host: str = "", hours: float = 6, **_):
    if not host:
        return {"error": "host 必填"}
    since = int(time.time()) - int(hours * 3600)
    rows = db.query_all(
        ctx.cfg.db_path,
        "SELECT ts, payload FROM metrics WHERE host=? AND ts>? ORDER BY ts",
        (host, since))
    return {"host": host, "points": [
        {"ts": r["ts"], **json.loads(r["payload"])} for r in rows]}


def tool_services(ctx: Ctx, host: str = "", **_):
    m = _latest_metric(ctx.cfg, host) if host else None
    if not m:
        return {"error": "尚无采集数据"}
    return {"host": host, "units": m.get("units", {}), "procs": m.get("procs", {}),
            "sampled_at": m["ts"]}


def tool_logs(ctx: Ctx, host: str = "", unit: str = "", lines: int = 100,
              grep: str = "", **_):
    if not host:
        return {"error": "host 必填"}
    lines = max(1, min(int(lines), 500))
    argv = ["journalctl", "--no-pager", "-n", str(lines), "--output=short-iso"]
    if unit:
        argv += ["-u", unit]
    res = sshutil.run(ctx.cfg, host, argv, timeout=45)
    if not res.get("ok"):
        return res
    out = res["stdout"]
    if grep:
        pat = re.compile(grep, re.I)
        out = "\n".join(l for l in out.splitlines() if pat.search(l))
    return {"host": host, "exit_code": res["exit_code"], "log": out[-20000:]}


def tool_run_read(ctx: Ctx, host: str = "", program: str = "",
                  args: list | None = None, **_):
    """白名单只读命令：自动执行、免审批。"""
    args = args or []
    h = ctx.cfg.host(host) if host else None
    if h is None:
        return {"error": f"unknown host: {host}"}
    wl = set(ctx.cfg.readonly_whitelist) | set(h.get("readonly_whitelist", []))
    if program not in wl:
        return {"error": f"program '{program}' 不在只读白名单", "allowed": sorted(wl)}
    joined = " ".join([program, *args])
    hit = _deny_hit(ctx.cfg, joined)
    if hit:
        return {"error": f"命中拒绝规则 /{hit}/"}
    if program == "journalctl" and "--no-pager" not in args:
        args = ["--no-pager", *args]
    if any(a.startswith("-i") or a in ("-w", "--follow") for a in args):
        return {"error": "交互/跟踪参数不允许"}
    res = sshutil.run(ctx.cfg, host, [program, *args], timeout=60)
    if res.get("ok"):
        res["stdout"] = res["stdout"][-20000:]
    return res


def tool_sentinel_events(ctx: Ctx, **_):
    res = sshutil.run(ctx.cfg, "raspi", ["python3", "-c", EVENTS_QUERY], timeout=20)
    if not res.get("ok"):
        return res
    try:
        return json.loads(res["stdout"].strip().splitlines()[-1])
    except Exception:
        return {"raw": res["stdout"][-4000:]}


def tool_sentinel_clips(ctx: Ctx, limit: int = 20, **_):
    res = sshutil.run(
        ctx.cfg, "raspi",
        ["ls", "-lt", "--time-style=+%Y-%m-%d %H:%M", "/var/lib/sentinel"],
        timeout=15)
    if not res.get("ok"):
        return res
    clips = [l for l in res["stdout"].splitlines() if l.endswith(".mp4")]
    return {"clips": clips[:max(1, min(limit, 100))]}


# ---------------------------------------------------------------- 审批工具

def tool_sudo_request(ctx: Ctx, host: str = "", argv: list | None = None,
                      reason: str = "", **_):
    argv = argv or []
    if not host or not argv:
        return {"error": "host 与 argv 必填"}
    h = ctx.cfg.host(host)
    if h is None:
        return {"error": f"unknown host: {host}"}
    if len(argv) < 1 or not isinstance(argv[0], str):
        return {"error": "argv 必须是非空字符串数组"}
    deny = check_deny(ctx.cfg, argv)
    if deny:
        return {"error": f"请求被拒绝: {deny}"}
    # 频率熔断：同一 agent 10 分钟内 pending > 8 拒发
    since = int(time.time()) - 600
    row = db.query_one(
        ctx.dbf,
        "SELECT COUNT(*) AS n FROM approvals WHERE agent=? AND state='pending' AND created_at>?",
        (ctx.actor, since))
    if row and row["n"] >= 8:
        return {"error": "pending 审批过多，已触发熔断（10 分钟内最多 8 个待批请求）"}
    ttl = ctx.cfg.approval_ttl_minutes
    summary = render_summary(h["label"], argv)
    ap = approvals.create(ctx.dbf, ctx.actor, host, list(argv), reason or "(未填)",
                          summary, ttl, ip=ctx.ip)
    audit.record(ctx.dbf, ctx.actor, "sudo_request", host, argv,
                 f"reason={reason} nonce={ap['nonce'][:6]}…", ctx.ip)
    return {
        "status": "pending",
        "request_id": ap["id"],
        "nonce": ap["nonce"],
        "summary": summary,
        "expires_in_minutes": ttl,
        "how_to_approve": "在 PWA 控制台 https://flagw.apay.eu.cc/home 的「审批」页点批准；"
                          "然后用 sudo_status 轮询、sudo_confirm 执行",
    }


def tool_sudo_status(ctx: Ctx, nonce: str = "", **_):
    if not nonce:
        return {"error": "nonce 必填"}
    v = approvals.status_view(ctx.dbf, nonce)
    if not v:
        return {"error": "nonce 不存在"}
    return {"request_id": v["id"], "state": v["state"],
            "seconds_left": v["seconds_left"], "host": v["host"],
            "argv": v["argv"], "summary": v["summary"],
            "decided_by": v["decided_by"]}


def tool_sudo_confirm(ctx: Ctx, nonce: str = "", **_):
    if not nonce:
        return {"error": "nonce 必填"}
    v = approvals.status_view(ctx.dbf, nonce)
    if not v:
        return {"error": "nonce 不存在"}
    if v["state"] != "approved":
        return {"status": v["state"],
                "hint": "需先在 Web 控制台批准；批准后调用本工具执行"}
    # 调 root 执行守护（它做二次校验 + 原子核销 + 执行）
    payload = {"action": "exec", "nonce": nonce, "host": v["host"],
               "argv": v["argv"], "agent": ctx.actor, "timeout": 120}
    try:
        resp = execd_call(ctx.cfg, payload, timeout=180)
    except Exception as e:  # noqa: BLE001
        return {"error": f"execd 调用失败: {type(e).__name__}: {e}"}
    audit.record(ctx.dbf, ctx.actor, "sudo_confirm", v["host"], v["argv"],
                 f"nonce={nonce[:6]}… ok={resp.get('ok')} rc={resp.get('exit_code')}",
                 ctx.ip)
    return resp


def tool_pending(ctx: Ctx, **_):
    return {"pending": [
        {"request_id": r["id"], "host": r["host"], "argv": json.loads(r["argv_json"]),
         "agent": r["agent"], "reason": r["reason"], "summary": r["summary"],
         "state": r["state"]}
        for r in approvals.list_recent(ctx.dbf, "pending", 50)]}


def tool_audit_tail(ctx: Ctx, limit: int = 50, **_):
    return {"audit": audit.tail(ctx.dbf, limit)}


def tool_agents(ctx: Ctx, **_):
    rows = db.query_all(ctx.dbf, "SELECT * FROM agents ORDER BY last_seen DESC")
    return {"agents": rows}


# ---------------------------------------------------------------- execd 客户端

def execd_call(cfg, payload: dict, timeout: float = 180) -> dict:
    sock = cfg.data["general"].get("execd_socket", "/run/homelab-execd.sock")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock)
        s.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode() or "{}")
    finally:
        s.close()


# ---------------------------------------------------------------- 工具表

def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOLS = [
    {"name": "hosts",
     "description": "列出所有受管主机及在线状态",
     "inputSchema": _schema({}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "host_status",
     "description": "单台主机最新状态: uptime/load/内存/磁盘/CPU/温度/网络速率",
     "inputSchema": _schema({"host": {"type": "string"}}, ["host"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "host_metrics",
     "description": "主机指标历史(用于画图), hours 默认 6",
     "inputSchema": _schema(
         {"host": {"type": "string"}, "hours": {"type": "number"}}, ["host"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "services",
     "description": "主机 systemd unit 与关键进程状态(来自巡检)",
     "inputSchema": _schema({"host": {"type": "string"}}, ["host"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "logs",
     "description": "查看主机 journalctl 日志(只读)",
     "inputSchema": _schema(
         {"host": {"type": "string"}, "unit": {"type": "string"},
          "lines": {"type": "integer"}, "grep": {"type": "string"}},
         ["host"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "run_read",
     "description": "在主机上执行白名单只读命令(df/free/systemctl status/ss 等), 免审批",
     "inputSchema": _schema(
         {"host": {"type": "string"}, "program": {"type": "string"},
          "args": {"type": "array", "items": {"type": "string"}}},
         ["host", "program"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "sentinel_events",
     "description": "raspi Sentinel 摄像头最近事件(数据库查询)",
     "inputSchema": _schema({}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "sentinel_clips",
     "description": "raspi Sentinel 录像片段列表",
     "inputSchema": _schema({"limit": {"type": "integer"}}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "sudo_request",
     "description": "申请 root 提权执行命令(白名单外)。提交后在 PWA 控制台等人工批准; "
                    "批准后用 sudo_status 轮询、sudo_confirm 执行。一次性、15 分钟过期。",
     "inputSchema": _schema(
         {"host": {"type": "string"},
          "argv": {"type": "array", "items": {"type": "string"},
                   "description": "命令参数数组, 不过 shell"},
          "reason": {"type": "string", "description": "为什么要执行(展示给审批人)"}},
         ["host", "argv"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True}},
    {"name": "sudo_status",
     "description": "查询审批请求状态(pending/approved/denied/expired/consumed)",
     "inputSchema": _schema({"nonce": {"type": "string"}}, ["nonce"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "sudo_confirm",
     "description": "批准后执行已批准的 root 命令(一次性核销)",
     "inputSchema": _schema({"nonce": {"type": "string"}}, ["nonce"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True}},
    {"name": "pending_approvals",
     "description": "当前 pending 的审批请求列表",
     "inputSchema": _schema({}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "audit_tail",
     "description": "最近审计流水(工具调用/审批/执行)",
     "inputSchema": _schema({"limit": {"type": "integer"}}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "agents",
     "description": "已注册的 agent 身份列表(可在 Web 控制台禁用)",
     "inputSchema": _schema({}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
]

_DISPATCH = {
    "hosts": tool_hosts,
    "host_status": tool_host_status,
    "host_metrics": tool_host_metrics,
    "services": tool_services,
    "logs": tool_logs,
    "run_read": tool_run_read,
    "sentinel_events": tool_sentinel_events,
    "sentinel_clips": tool_sentinel_clips,
    "sudo_request": tool_sudo_request,
    "sudo_status": tool_sudo_status,
    "sudo_confirm": tool_sudo_confirm,
    "pending_approvals": tool_pending,
    "audit_tail": tool_audit_tail,
    "agents": tool_agents,
}


def call_tool(cfg, actor: str, name: str, arguments: dict, ip: str = "") -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool: {name}"}
    ctx = Ctx(cfg, actor, ip)
    t0 = time.time()
    try:
        result = fn(ctx, **(arguments or {}))
        err = isinstance(result, dict) and result.get("error")
        audit.record(cfg.db_path, actor, f"tool:{name}",
                     str(arguments.get("host", "")) if arguments else "",
                     [], f"ok={not err} {int((time.time()-t0)*1000)}ms", ip)
        return result
    except Exception as e:  # noqa: BLE001
        audit.record(cfg.db_path, actor, f"tool:{name}", "", [],
                     f"EXC {type(e).__name__}: {e}", ip)
        return {"error": f"{type(e).__name__}: {e}"}
