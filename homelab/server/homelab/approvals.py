"""审批流：nonce 一次性凭证。创建 → Web 批准 → MCP confirm 时被 execd 核销。"""
from __future__ import annotations

import json
import secrets
import time

from . import db


def _now() -> int:
    return int(time.time())


def create(dbf: str, agent: str, host_id: str, argv: list[str],
           reason: str, summary: str, ttl_minutes: int, ip: str = "") -> dict:
    rid = secrets.token_hex(6)
    nonce = secrets.token_urlsafe(18)
    created = _now()
    # argv/host 的规范化指纹存入 argv_json（逐字匹配用）
    db.execute(
        dbf,
        "INSERT INTO approvals(id, nonce, agent, host, argv_json, reason, summary,"
        " state, created_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (rid, nonce, agent, host_id, db.jdump(argv), reason, summary,
         "pending", created, created + ttl_minutes * 60))
    return {"id": rid, "nonce": nonce, "state": "pending",
            "expires_at": created + ttl_minutes * 60}


def get_by_nonce(dbf: str, nonce: str) -> dict | None:
    return db.query_one(dbf, "SELECT * FROM approvals WHERE nonce=?", (nonce,))


def get(dbf: str, rid: str) -> dict | None:
    return db.query_one(dbf, "SELECT * FROM approvals WHERE id=?", (rid,))


def sweep_expired(dbf: str) -> None:
    db.execute(
        dbf,
        "UPDATE approvals SET state='expired' WHERE state='pending' AND expires_at<?",
        (_now(),))


def list_recent(dbf: str, state: str = "", limit: int = 50) -> list[dict]:
    if state:
        rows = db.query_all(
            dbf,
            "SELECT * FROM approvals WHERE state=? ORDER BY created_at DESC LIMIT ?",
            (state, limit))
    else:
        rows = db.query_all(
            dbf, "SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))
    for r in rows:
        if r["state"] == "pending" and r["expires_at"] < _now():
            r["state"] = "expired"
    return rows


def decide(dbf: str, rid: str, by: str, approve: bool) -> dict | None:
    row = db.query_one(dbf, "SELECT * FROM approvals WHERE id=?", (rid,))
    if not row or row["state"] != "pending":
        return row
    if row["expires_at"] < _now():
        db.execute(dbf, "UPDATE approvals SET state='expired' WHERE id=?", (rid,))
        row["state"] = "expired"
        return row
    db.execute(
        dbf,
        "UPDATE approvals SET state=?, decided_at=?, decided_by=? WHERE id=? AND state='pending'",
        ("approved" if approve else "denied", _now(), by, rid))
    row = dict(row)
    row["state"] = "approved" if approve else "denied"
    row["decided_by"] = by
    row["decided_at"] = _now()
    return row


# ---------------------------------------------------------------- 自动批准窗口
# 用户可在 Web 控制台开启一段时间的"全量自动批准": 窗口内所有 root 审批请求
# 自动批准并执行(仍走 nonce+execd+硬拒绝+全审计, decided_by=policy:auto-approve-window)。

AUTO_UNTIL_KEY = "auto_approve_until"
AUTO_BY_KEY = "auto_approve_set_by"
AUTO_AT_KEY = "auto_approve_set_at"


def auto_approve_until(dbf: str) -> int:
    raw = db.meta_get(dbf, AUTO_UNTIL_KEY)
    try:
        return int(raw) if raw else 0
    except (TypeError, ValueError):
        return 0


def auto_approve_active(dbf: str) -> bool:
    return auto_approve_until(dbf) > _now()


def auto_approve_view(dbf: str, max_minutes: int = 480) -> dict:
    until = auto_approve_until(dbf)
    left = max(0, until - _now())
    active = left > 0
    return {
        "active": active,
        "until": until if active else 0,
        "seconds_left": left,
        "max_minutes": int(max_minutes),
        "set_by": (db.meta_get(dbf, AUTO_BY_KEY) or "") if active else "",
        "set_at": (int(db.meta_get(dbf, AUTO_AT_KEY) or 0)) if active else 0,
    }


def set_auto_approve(dbf: str, minutes: int, by: str,
                     max_minutes: int = 480) -> dict:
    """开启(minutes>0)或关闭(minutes<=0)自动批准窗口。minutes 上限受配置约束。"""
    if minutes <= 0:
        db.meta_set(dbf, AUTO_UNTIL_KEY, "0")
        return auto_approve_view(dbf, max_minutes)
    minutes = max(1, min(int(minutes), max(1, int(max_minutes))))
    now = _now()
    db.meta_set(dbf, AUTO_UNTIL_KEY, str(now + minutes * 60))
    db.meta_set(dbf, AUTO_BY_KEY, by)
    db.meta_set(dbf, AUTO_AT_KEY, str(now))
    return auto_approve_view(dbf, max_minutes)


def auto_approve_pending(dbf: str, by: str) -> list[dict]:
    """把当前所有未过期的 pending 请求立即批准(返回已批准的行)。"""
    rows = db.query_all(
        dbf, "SELECT id FROM approvals WHERE state='pending' AND expires_at>?",
        (_now(),))
    out = []
    for r in rows:
        row = decide(dbf, r["id"], by, True)
        if row and row.get("state") == "approved":
            out.append(row)
    return out


def status_view(dbf: str, nonce: str) -> dict | None:
    row = get_by_nonce(dbf, nonce)
    if not row:
        return None
    out = dict(row)
    if out["state"] == "pending" and out["expires_at"] < _now():
        out["state"] = "expired"
    out["argv"] = json.loads(out.pop("argv_json"))
    out["seconds_left"] = max(0, out["expires_at"] - _now())
    return out


def try_consume(dbf: str, nonce: str, host_id: str, argv: list[str]):
    """原子核销：仅当 (nonce, host, argv) 与批准记录逐字一致时成功。"""
    row = get_by_nonce(dbf, nonce)
    if not row:
        return False, None, "nonce 不存在"
    if row["state"] == "expired":
        return False, None, "审批已过期"
    if row["expires_at"] < _now():
        db.execute(dbf, "UPDATE approvals SET state='expired' WHERE nonce=?", (nonce,))
        return False, None, "审批已过期"
    if row["state"] != "approved":
        return False, None, f"审批状态为 {row['state']}（需先在 Web 控制台批准）"
    stored = json.loads(row["argv_json"])
    if stored != list(argv) or row["host"] != host_id:
        return False, None, "argv/host 与批准内容不一致，拒绝执行"
    cur = db.execute(
        dbf,
        "UPDATE approvals SET state='consumed', consumed_at=? WHERE nonce=? AND state='approved'",
        (_now(), nonce))
    if cur.rowcount != 1:
        return False, None, "nonce 已被使用"
    return True, dict(row), ""
