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

def _auto_allowed(cfg, host_id: str, argv: list) -> str | None:
    """命中 auto_allow(全局或按主机配置)则免审批自动执行。"""
    cmd = " ".join(argv)
    h = cfg.host(host_id) or {}
    for pat in list(cfg.data["exec"].get("auto_allow", [])) + list(h.get("auto_allow", [])):
        if cmd == pat or cmd.startswith(pat + " "):
            return pat
    return None


def _exec_approved(ctx: Ctx, nonce: str, host_id: str, argv: list) -> dict:
    """已批准的请求 → execd 执行(execd 内部再做原子核销与硬拒绝校验)。"""
    payload = {"action": "exec", "nonce": nonce, "host": host_id,
               "argv": argv, "agent": ctx.actor, "timeout": 120}
    try:
        resp = execd_call(ctx.cfg, payload, timeout=180)
    except Exception as e:  # noqa: BLE001
        return {"error": f"execd 调用失败: {type(e).__name__}: {e}"}
    audit.record(ctx.dbf, ctx.actor, "sudo_exec", host_id, argv,
                 f"nonce={nonce[:6]}… ok={resp.get('ok')} rc={resp.get('exit_code')}",
                 ctx.ip)
    return resp


def tool_sudo_run(ctx: Ctx, host: str = "", argv: list | None = None,
                  reason: str = "", **_):
    """以 root 执行命令的唯一入口(自动处理审批)。

    流程:
      1. 调用本工具。命中 auto_allow(如常用 systemctl restart) → 立即执行,
         返回 {status: executed, exit_code, stdout, ...};
         否则返回 {status: pending, nonce}(审批请求已创建, 15 分钟过期)。
      2. 若 pending: 把返回里 tell_user 的话原样转告用户, 然后调用
         sudo_wait(nonce) —— 每次阻塞约 25 秒; 用户一旦在 Web 控制台批准,
         当场以 root 执行并返回结果; 未决则返回 pending, 继续轮询即可。
    不要自己拼 ssh/sudo 命令, 也不要去服务器上读 homelab 源码: 本描述即全部协议。
    示例: sudo_run(host="cone", argv=["systemctl","restart","nginx"],
                   reason="用户要求重启 nginx")
    """
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
    # 频率熔断: 同一 agent 10 分钟内 pending > 8 拒发
    since = int(time.time()) - 600
    row = db.query_one(
        ctx.dbf,
        "SELECT COUNT(*) AS n FROM approvals WHERE agent=? AND state='pending' AND created_at>?",
        (ctx.actor, since))
    if row and row["n"] >= 8:
        return {"error": "pending 审批过多, 已触发熔断(10 分钟内最多 8 个待批请求)"}
    ttl = ctx.cfg.approval_ttl_minutes
    summary = render_summary(h["label"], argv)

    # auto_allow: 免审批, 但仍走 nonce+execd+审计(decided_by=policy:auto-allow)
    hit = _auto_allowed(ctx.cfg, host, list(argv))
    if hit:
        ap = approvals.create(ctx.dbf, ctx.actor, host, list(argv),
                              reason or "(auto-allow)", summary, ttl, ip=ctx.ip)
        approvals.decide(ctx.dbf, ap["id"], "policy:auto-allow", True)
        audit.record(ctx.dbf, ctx.actor, "sudo_auto_allow", host, list(argv),
                     f"matched: {hit}", ctx.ip)
        result = dict(_exec_approved(ctx, ap["nonce"], host, list(argv)))
        if result.get("rejected"):
            result["status"] = "rejected"
        elif result.get("ok") is False:
            result["status"] = "failed"
        else:
            result["status"] = "executed"
        result["auto_allowed"] = True
        result["matched_rule"] = hit
        return result

    ap = approvals.create(ctx.dbf, ctx.actor, host, list(argv), reason or "(未填)",
                          summary, ttl, ip=ctx.ip)
    audit.record(ctx.dbf, ctx.actor, "sudo_request", host, list(argv),
                 f"reason={reason} nonce={ap['nonce'][:6]}…", ctx.ip)
    return {
        "status": "pending",
        "request_id": ap["id"],
        "nonce": ap["nonce"],
        "summary": summary,
        "expires_in_minutes": ttl,
        "tell_user": f"需要 root 权限: {summary}(原因: {reason or '未填'})。"
                     f"请打开 https://flagw.apay.eu.cc/home 在「审批」页点批准, "
                     f"批准后我会自动继续执行。",
        "next_step": f"调用 sudo_wait(nonce=\"{ap['nonce']}\") 开始轮询; "
                     "用户批准后会自动执行并返回结果",
    }


def tool_sudo_wait(ctx: Ctx, nonce: str = "", wait_seconds: int = 25, **_):
    """轮询审批并执行: 阻塞 wait_seconds 秒(默认 25, 最大 60)。

    期间用户批准 → 立即以 root 执行, 返回 {status: executed, exit_code, stdout...};
    被拒/过期 → 返回对应终态(denied/expired)与原因;
    到时仍未决 → 返回 {status: pending, seconds_left}, 继续调用本工具即可。
    """
    if not nonce:
        return {"error": "nonce 必填(来自 sudo_run 的返回)"}
    wait_seconds = max(5, min(int(wait_seconds), 60))
    deadline = time.time() + wait_seconds
    while True:
        v = approvals.status_view(ctx.dbf, nonce)
        if not v:
            return {"error": "nonce 不存在"}
        st = v["state"]
        if st == "approved":
            resp = _exec_approved(ctx, nonce, v["host"], v["argv"])
            if resp.get("rejected"):
                return {"status": "rejected", **resp}
            if resp.get("ok") is False:
                return {"status": "failed", **resp}
            return {"status": "executed", **resp}
        if st == "consumed":
            return {"status": "consumed", "hint": "该请求已执行过(nonce 一次性)"}
        if st in ("denied", "expired"):
            return {"status": st,
                    "hint": "用户已拒绝" if st == "denied" else "审批已过期(15 分钟)",
                    "summary": v["summary"]}
        if time.time() >= deadline:
            return {"status": "pending", "seconds_left": v["seconds_left"],
                    "summary": v["summary"],
                    "next_step": "继续调用 sudo_wait 轮询; 或提醒用户去控制台批准"}
        time.sleep(2)


def tool_sudo_status(ctx: Ctx, nonce: str = "", **_):
    """只查询审批状态(不执行): pending/approved/denied/expired/consumed。"""
    if not nonce:
        return {"error": "nonce 必填"}
    v = approvals.status_view(ctx.dbf, nonce)
    if not v:
        return {"error": "nonce 不存在"}
    return {"request_id": v["id"], "state": v["state"],
            "seconds_left": v["seconds_left"], "host": v["host"],
            "argv": v["argv"], "summary": v["summary"],
            "decided_by": v["decided_by"]}


SUDO_PLAYBOOK = """
# homelab 使用指南(拿不准就先读完这段)

四台服务器: ali(阿里云网关, 本机) / raspi(树莓派+摄像头) / asus(Clash 客户端) / cone(境外入口)。

## 只读操作 → 免审批, 直接调用
- host_status(host) / services(host) / logs(host, unit) / host_metrics(host)
- run_read(host, program, args): 白名单命令(df/free/systemctl status/ss/journalctl 等)
- sentinel_events / sentinel_clips: raspi 摄像头事件与录像

## 变更操作(重启服务/装包/改文件) → 必须走审批, 就两步
1. sudo_run(host, argv, reason) —— 唯一入口。命中 auto_allow(见下)立即执行;
   否则返回 pending, 把 tell_user 的话转告用户。
2. sudo_wait(nonce) 轮询(每次阻塞 25s)。用户在 https://flagw.apay.eu.cc/home
   点批准后自动执行并返回结果。被拒/过期会如实返回, 不要重复申请同一命令。

## 铁律
- 不要自己拼 ssh/sudo 命令绕过审批, 也不要去服务器上读 homelab 源码——
  本指南即全部协议, 没有隐藏步骤。
- rm -rf / 等破坏性命令会被永久拒绝(人工批准也不执行), 收到即为终态。
- 每个 nonce 只能用一次、绑定 (host, argv) 逐字匹配、15 分钟过期。
""".strip()


def tool_sudo_help(ctx: Ctx, **_):
    """完整使用指南: 何时要审批/审批流怎么走/示例/铁律。对流程不确定时先调用本工具。"""
    return {"playbook": SUDO_PLAYBOOK,
            "auto_allow_now": sorted(set(
                ctx.cfg.data["exec"].get("auto_allow", []))),
            "deny_note": "rm -rf /、dd of=/dev/*、掐断 ssh/审计/fail2ban 等永久拒绝, 批准也不执行"}


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
     "description": "主机 systemd unit 与关键进程状态(来自巡检)。需要重启服务时用 sudo_run(host, [\"systemctl\",\"restart\",unit])",
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
     "description": "在主机上执行白名单只读命令(df/free/systemctl status/ss 等), 免审批。注意: 需要【变更】系统(重启服务/安装/改文件)时不要用本工具, 用 sudo_run",
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
    {"name": "sudo_run",
     "description": "以 root 执行命令的唯一入口(自动审批)。命中 auto_allow 立即执行; "
                    "否则创建 15 分钟一次性审批请求并返回 pending+nonce, "
                    "此时把 tell_user 转告用户, 然后调用 sudo_wait(nonce) 轮询, "
                    "批准后自动执行并返回结果。例: "
                    "sudo_run(host='cone', argv=['systemctl','restart','nginx'], reason='重启 nginx')",
     "inputSchema": _schema(
         {"host": {"type": "string"},
          "argv": {"type": "array", "items": {"type": "string"},
                   "description": "命令参数数组, 不过 shell"},
          "reason": {"type": "string", "description": "为什么要执行(展示给审批人)"}},
         ["host", "argv"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True}},
    {"name": "sudo_wait",
     "description": "轮询审批并执行: 阻塞最多 wait_seconds 秒(默认25)。用户批准后立即以 root "
                    "执行并返回结果; 被拒/过期返回终态; 未决返回 pending(继续调用即可)。"
                    "nonce 来自 sudo_run。",
     "inputSchema": _schema(
         {"nonce": {"type": "string"}, "wait_seconds": {"type": "integer"}},
         ["nonce"]),
     "annotations": {"readOnlyHint": False, "destructiveHint": True,
                     "idempotentHint": False, "openWorldHint": True}},
    {"name": "sudo_status",
     "description": "只查询审批状态(不执行): pending/approved/denied/expired/consumed",
     "inputSchema": _schema({"nonce": {"type": "string"}}, ["nonce"]),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
    {"name": "sudo_help",
     "description": "完整使用指南: 只读工具清单/审批两步流程/auto_allow 名单/铁律。"
                    "对'某操作要不要审批、下一步调什么'不确定时, 先调用本工具",
     "inputSchema": _schema({}, []),
     "annotations": {"readOnlyHint": True, "openWorldHint": True}},
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
    "sudo_run": tool_sudo_run,
    "sudo_wait": tool_sudo_wait,
    "sudo_status": tool_sudo_status,
    "sudo_help": tool_sudo_help,
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
