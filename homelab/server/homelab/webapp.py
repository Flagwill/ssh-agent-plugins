"""homelab web 控制台 + REST API（PWA）。由 ali Caddy 反代 /home → 127.0.0.1:8790。"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import approvals, audit, collector, db, sshutil
from .config import Config

COOKIE = "hl_session"
CFG = Config(os.environ.get("HOMELAB_CONFIG", "/opt/homelab/hosts.toml"))
DBF = CFG.db_path
APP = FastAPI(title="homelab", docs_url=None, redoc_url=None)


# Caddy 以 handle /home* 原样转发; 此处剥掉 /home 前缀, 路由与静态挂载保持根路径
@APP.middleware("http")
async def _strip_base(request: Request, call_next):
    path = request.scope.get("path", "")
    if path == CFG.web_base or path.startswith(CFG.web_base + "/"):
        request.scope["path"] = path[len(CFG.web_base):] or "/"
        request.scope["raw_path"] = request.scope["path"].encode()
    return await call_next(request)


# 应用外壳(HTML/JS/CSS/SW)不缓存: 部署后刷新即生效, 避免 PWA 拿旧外壳。
# 中间件顺序不保证, 故两种路径(带/不带 /home 前缀)都归一化后判断。
_SHELL_PATHS = {"/", "/index.html", "/app.js", "/style.css", "/sw.js",
                "/manifest.webmanifest"}


@APP.middleware("http")
async def _cache_control(request: Request, call_next):
    resp = await call_next(request)
    p = request.scope.get("path", "")
    base = CFG.web_base
    if base and (p == base or p.startswith(base + "/")):
        p = p[len(base):] or "/"
    if p.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif p in _SHELL_PATHS:
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp

# ---------------------------------------------------------------- 工具函数

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def _csrf_ok(request: Request) -> bool:
    if request.headers.get("x-hl-request") != "1":
        return False
    origin = request.headers.get("origin", "")
    if origin and "flagw.apay.eu.cc" not in origin:
        return False
    return True


def _session(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    row = db.query_one(DBF, "SELECT * FROM sessions WHERE token_hash=?", (th,))
    if not row or row["expires_at"] < int(time.time()):
        return None
    db.execute(DBF, "UPDATE sessions SET last_seen=? WHERE token_hash=?",
               (int(time.time()), th))
    return row


def _hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=1 << 14, r=8, p=1).hex()
    return f"scrypt${salt.hex()}${dk}"


def _verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt_hex, dk = stored.split("$")
        cand = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex),
                              n=1 << 14, r=8, p=1).hex()
        return hmac.compare_digest(cand, dk)
    except Exception:
        return False


def _login_locked(ip: str) -> int:
    row = db.query_one(DBF, "SELECT * FROM login_attempts WHERE ip=?", (ip,))
    if not row:
        return 0
    if row["locked_until"] > int(time.time()):
        return row["locked_until"] - int(time.time())
    return 0


def _note_login(ip: str, ok: bool) -> None:
    now = int(time.time())
    row = db.query_one(DBF, "SELECT * FROM login_attempts WHERE ip=?", (ip,))
    if ok:
        db.execute(DBF, "DELETE FROM login_attempts WHERE ip=?", (ip,))
        return
    fails = (row["fails"] if row else 0) + 1
    lock = 0
    if fails >= 5:
        lock = now + min(3600, 300 * (fails - 4))
    db.execute(DBF,
               "INSERT OR REPLACE INTO login_attempts(ip, fails, last_ts, locked_until)"
               " VALUES(?,?,?,?)", (ip, fails, now, lock))


def _need_session(request: Request) -> dict | JSONResponse:
    s = _session(request)
    if s is None:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return s


async def _json(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}

# ---------------------------------------------------------------- 认证

@APP.post("/api/login")
async def login(request: Request, response: Response):
    if not _csrf_ok(request):
        return JSONResponse({"error": "CSRF check failed"}, status_code=403)
    ip = _client_ip(request)
    wait = _login_locked(ip)
    if wait:
        return JSONResponse({"error": f"尝试过于频繁, {wait}s 后重试"},
                            status_code=429)
    body = await _json(request)
    stored = db.meta_get(DBF, "web_password_hash")
    if not stored:
        return JSONResponse({"error": "尚未设置密码"}, status_code=500)
    if not _verify_password(body.get("password", ""), stored):
        _note_login(ip, False)
        audit.record(DBF, "anonymous", "login_fail", "", [], f"ip={ip}", ip)
        return JSONResponse({"error": "密码错误"}, status_code=401)
    _note_login(ip, True)
    token = secrets.token_urlsafe(32)
    ttl = CFG.session_ttl_days * 86400
    now = int(time.time())
    db.execute(DBF,
               "INSERT INTO sessions(token_hash, created_at, last_seen, expires_at, ip, ua)"
               " VALUES(?,?,?,?,?,?)",
               (hashlib.sha256(token.encode()).hexdigest(), now, now,
                now + ttl, ip, request.headers.get("user-agent", "")[:200]))
    audit.record(DBF, "web-user", "login_ok", "", [], f"ip={ip}", ip)
    resp = JSONResponse({"ok": True, "expires_days": CFG.session_ttl_days})
    resp.set_cookie(COOKIE, token, max_age=ttl, httponly=True, secure=True,
                    samesite="lax", path=CFG.web_base)
    return resp


@APP.post("/api/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(DBF, "DELETE FROM sessions WHERE token_hash=?",
                   (hashlib.sha256(token.encode()).hexdigest(),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path=CFG.web_base)
    return resp


@APP.get("/api/me")
async def me(request: Request):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    return {"user": "flagw", "expires_at": s["expires_at"],
            "session_ttl_days": CFG.session_ttl_days}

# ---------------------------------------------------------------- 监控 API

def _latest(host_id: str) -> dict | None:
    row = db.query_one(
        DBF, "SELECT ts, payload FROM metrics WHERE host=? ORDER BY ts DESC LIMIT 1",
        (host_id,))
    if not row:
        return None
    d = {"ts": row["ts"]}
    d.update(json.loads(row["payload"]))
    return d


def _host_summary(hid: str) -> dict:
    h = CFG.host(hid)
    m = _latest(hid)
    online = bool(m and "error" not in m and
                  time.time() - m["ts"] < CFG.collect_interval * 3 + 30)
    out = {"id": hid, "label": h["label"], "kind": h["kind"], "online": online}
    if m:
        out["last_seen"] = m["ts"]
        out["uptime_s"] = m.get("uptime_s")
        out["load"] = m.get("load")
        out["cpu_pct"] = m.get("cpu_pct")
        out["temp_max"] = m.get("temp_max")
        mem = m.get("mem", {})
        if mem.get("MemTotal"):
            used = mem["MemTotal"] - mem.get("MemAvailable", 0)
            out["mem_used_pct"] = round(100 * used / mem["MemTotal"], 1)
            out["mem_used"] = used
            out["mem_total"] = mem["MemTotal"]
        disk = m.get("disk_root", {})
        if disk.get("total"):
            out["disk_used_pct"] = round(
                100 * (disk["total"] - disk.get("free", 0)) / disk["total"], 1)
        out["net_rx_Bps"] = m.get("net_rx_Bps")
        out["net_tx_Bps"] = m.get("net_tx_Bps")
        if "error" in m:
            out["error"] = m["error"]
    return out


@APP.get("/api/overview")
async def overview(request: Request):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    pending = approvals.list_recent(DBF, "pending", 50)
    return {
        "hosts": [_host_summary(h) for h in CFG.hosts],
        "pending_count": len([p for p in pending
                              if p["expires_at"] > int(time.time())]),
        "auto_approve": approvals.auto_approve_view(
            DBF, CFG.auto_approve_max_minutes),
        "audit_recent": audit.tail(DBF, 8),
    }


@APP.get("/api/hosts")
async def hosts_api(request: Request):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    return {"hosts": [_host_summary(h) for h in CFG.hosts]}


@APP.get("/api/hosts/{hid}/metrics")
async def host_metrics(request: Request, hid: str, hours: float = 6):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    since = int(time.time()) - int(hours * 3600)
    rows = db.query_all(
        DBF, "SELECT ts, payload FROM metrics WHERE host=? AND ts>? ORDER BY ts",
        (hid, since))
    return {"host": hid, "points": [
        {"ts": r["ts"], **json.loads(r["payload"])} for r in rows]}


@APP.get("/api/hosts/{hid}/services")
async def host_services(request: Request, hid: str):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    m = _latest(hid)
    if not m or "error" in m:
        return {"host": hid, "error": "no data"}
    return {"host": hid, "units": m.get("units", {}), "procs": m.get("procs", {}),
            "sampled_at": m["ts"]}


@APP.get("/api/hosts/{hid}/logs")
async def host_logs(request: Request, hid: str, unit: str = "", lines: int = 150):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    argv = ["journalctl", "--no-pager", "-n", str(max(1, min(lines, 400))),
            "--output=short-iso"]
    if unit:
        argv += ["-u", unit]
    res = sshutil.run(CFG, hid, argv, timeout=45)
    audit.record(DBF, "web-user", "view_logs", hid, argv[:4], "", _client_ip(request))
    return {"host": hid, "log": (res.get("stdout") or "")[-20000:],
            "error": res.get("error", "")}

# ---------------------------------------------------------------- 审批 API

def _approval_view(r: dict) -> dict:
    state = r["state"]
    if state == "pending" and r["expires_at"] < int(time.time()):
        state = "expired"
    return {"id": r["id"], "host": r["host"], "argv": json.loads(r["argv_json"]),
            "reason": r["reason"], "summary": r["summary"], "agent": r["agent"],
            "state": state, "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "seconds_left": max(0, r["expires_at"] - int(time.time())),
            "decided_by": r["decided_by"], "decided_at": r["decided_at"]}


@APP.get("/api/approvals")
async def approvals_api(request: Request, state: str = "", limit: int = 50):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    rows = approvals.list_recent(DBF, state, limit)
    return {"approvals": [_approval_view(r) for r in rows],
            "auto_approve": approvals.auto_approve_view(
                DBF, CFG.auto_approve_max_minutes)}


@APP.get("/api/auto-approve")
async def auto_approve_get(request: Request):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    return approvals.auto_approve_view(DBF, CFG.auto_approve_max_minutes)


@APP.post("/api/auto-approve")
async def auto_approve_set(request: Request):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    if not _csrf_ok(request):
        return JSONResponse({"error": "CSRF check failed"}, status_code=403)
    body = await _json(request)
    try:
        minutes = int(body.get("minutes", 0))
    except (TypeError, ValueError):
        return JSONResponse({"error": "minutes 必须为整数"}, status_code=400)
    max_min = CFG.auto_approve_max_minutes
    if minutes > max_min:
        return JSONResponse({"error": f"最长 {max_min} 分钟"}, status_code=400)
    view = approvals.set_auto_approve(DBF, minutes, "web-user", max_min)
    approved = []
    if minutes > 0 and body.get("approve_pending", True):
        approved = approvals.auto_approve_pending(DBF, "policy:auto-approve-window")
    audit.record(DBF, "web-user",
                 "auto_approve_on" if minutes > 0 else "auto_approve_off", "", [],
                 f"minutes={minutes} until={view['until']} "
                 f"pending_approved={len(approved)}", _client_ip(request))
    return {"ok": True, "auto_approve": view,
            "approved_count": len(approved)}


@APP.post("/api/approvals/{rid}/approve")
async def approve(request: Request, rid: str):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    if not _csrf_ok(request):
        return JSONResponse({"error": "CSRF check failed"}, status_code=403)
    row = approvals.decide(DBF, rid, "web-user", True)
    if row is None:
        return JSONResponse({"error": "审批不存在"}, status_code=404)
    audit.record(DBF, "web-user", "approve", row["host"],
                 json.loads(row["argv_json"]), f"id={rid}", _client_ip(request))
    return {"ok": True, "approval": _approval_view(row)}


@APP.post("/api/approvals/{rid}/deny")
async def deny(request: Request, rid: str):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    if not _csrf_ok(request):
        return JSONResponse({"error": "CSRF check failed"}, status_code=403)
    row = approvals.decide(DBF, rid, "web-user", False)
    if row is None:
        return JSONResponse({"error": "审批不存在"}, status_code=404)
    audit.record(DBF, "web-user", "deny", row["host"],
                 json.loads(row["argv_json"]), f"id={rid}", _client_ip(request))
    return {"ok": True, "approval": _approval_view(row)}

# ---------------------------------------------------------------- 审计 / agent

@APP.get("/api/audit")
async def audit_api(request: Request, limit: int = 100):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    return {"audit": audit.tail(DBF, limit)}


@APP.get("/api/agents")
async def agents_api(request: Request):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    rows = db.query_all(DBF, "SELECT * FROM agents ORDER BY last_seen DESC")
    return {"agents": rows}


@APP.post("/api/agents/{label}/enable")
async def agent_enable(request: Request, label: str):
    return _agent_set(request, label, 1)


@APP.post("/api/agents/{label}/disable")
async def agent_disable(request: Request, label: str):
    return _agent_set(request, label, 0)


def _agent_set(request: Request, label: str, enabled: int):
    s = _need_session(request)
    if isinstance(s, JSONResponse):
        return s
    if not _csrf_ok(request):
        return JSONResponse({"error": "CSRF check failed"}, status_code=403)
    row = db.query_one(DBF, "SELECT * FROM agents WHERE key=?", (label,))
    if not row:
        return JSONResponse({"error": "agent 不存在"}, status_code=404)
    db.execute(DBF, "UPDATE agents SET enabled=? WHERE key=?", (enabled, label))
    audit.record(DBF, "web-user",
                 "agent_enable" if enabled else "agent_disable", "", [],
                 f"agent={label}", _client_ip(request))
    return {"ok": True}

# ---------------------------------------------------------------- 启动

_stop = threading.Event()


def _sweeper():
    while True:
        try:
            approvals.sweep_expired(DBF)
            db.execute(DBF,
                       "DELETE FROM sessions WHERE expires_at<?", (int(time.time()),))
        except Exception:
            pass
        time.sleep(60)


@APP.on_event("startup")
def _startup():
    db.init_db(DBF)
    collector.start_background(CFG)
    threading.Thread(target=_sweeper, daemon=True).start()
    audit.record(DBF, "web", "daemon_start", "", [], "web up")


@APP.on_event("shutdown")
def _shutdown():
    _stop.set()


APP.mount("/", StaticFiles(directory=CFG.static_dir, html=True), name="static")


def main():
    import uvicorn
    host, _, port = CFG.listen.rpartition(":")
    uvicorn.run(APP, host=host or "127.0.0.1", port=int(port or 8790),
                log_level="info")


if __name__ == "__main__":
    main()
