"""审计流水：所有工具调用 / 审批 / 执行 / 登录 全部落库。"""
from __future__ import annotations

import sqlite3

from . import db


def record(db_path: str, actor: str, kind: str, host: str = "",
           argv: list | None = None, detail: str = "", ip: str = "") -> None:
    try:
        db.execute(
            db_path,
            "INSERT INTO audit(ts, actor, kind, host, argv_json, detail, ip)"
            " VALUES(?,?,?,?,?,?,?)",
            (db.now(), actor, kind, host,
             db.jdump(argv or []), detail[:4000], ip),
        )
    except sqlite3.Error:
        pass


def tail(db_path: str, limit: int = 100) -> list[dict]:
    return db.query_all(
        db_path,
        "SELECT id, ts, actor, kind, host, argv_json, detail, ip"
        " FROM audit ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 500)),),
    )
