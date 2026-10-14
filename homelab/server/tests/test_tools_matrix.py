#!/usr/bin/env python3
"""MCP 工具矩阵回归测试：确保每个工具的 tools/call 结果都能通过 MCP 客户端校验。

背景：`hosts` 曾返回 JSON list、错误时又发 `"structuredContent": null`，
两者都会让 pi/codex/opencode 的 MCP SDK 抛
    Invalid MCP tools/call structured content
且报错文案与真实原因无关，极难排查。本脚本守住这条线。

用法（本机，无需 root）：
    python3 homelab/server/tests/test_tools_matrix.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
REPO_CFG = SERVER / "hosts.toml"

sys.path.insert(0, str(SERVER))

from homelab import db, tools  # noqa: E402
from homelab.config import Config  # noqa: E402


def sdk_validate(value):
    """复刻 @pi/mcp client.js 的 validateCallToolResult（只留与客户端兼容相关的部分）。"""
    if not isinstance(value, dict):
        return "Invalid MCP tools/call result"
    if value.get("content") is not None and not isinstance(value["content"], list):
        return "Invalid MCP tools/call result"
    if ("structuredContent" in value
            and value["structuredContent"] is not None
            and not isinstance(value["structuredContent"], dict)):
        return "Invalid MCP tools/call structured content"
    return None


def build_config(tmp: Path) -> Config:
    """把仓库里的 hosts.toml 拷一份，db 指向可写的临时文件。

    不改这一步的话 db.connect() 会去 mkdir /var/lib/homelab，非 root 直接
    PermissionError；那样测的就不是返回值结构了。
    """
    raw = REPO_CFG.read_text(encoding="utf-8")
    assert 'db = "/var/lib/homelab/homelab.db"' in raw, "hosts.toml 的 db 行变了？"
    p = tmp / "hosts.toml"
    p.write_text(raw.replace('db = "/var/lib/homelab/homelab.db"',
                              f'db = "{tmp / "test.db"}"'), encoding="utf-8")
    return Config(str(p))


def call(cfg, name, args):
    """复刻 mcp_stdio.handle 的 tools/call 分支（含线上修复）。"""
    result = tools.call_tool(cfg, "pi", name, args or {}, "127.0.0.1")
    is_err = bool(result.get("error"))
    out = {"content": [{"type": "text", "text": json.dumps(result, default=str)}],
           "isError": is_err}
    if not is_err:
        out["structuredContent"] = result
    return out


# (工具名, 参数)：成功路径 + 错误路径都要覆盖
CASES = [
    ("hosts", {}),
    ("hosts", {"host": "ali"}),                          # 多传参数应被忽略
    ("host_status", {"host": "ali"}),
    ("host_status", {"host": "nope"}),                   # 错误路径
    ("host_status", {}),                                  # 缺必填参数
    ("host_metrics", {"host": "ali", "hours": 1}),
    ("services", {"host": "ali"}),
    ("logs", {"host": "ali", "lines": 2}),
    ("run_read", {"host": "ali", "program": "df", "args": ["-h"]}),
    ("run_read", {"host": "ali", "program": "iptables"}),      # 白名单外 → error
    ("run_read", {"host": "nope", "program": "df"}),           # 未知主机 → error
    ("run_read", {"host": "ali", "program": "grep", "args": ["-i", "x", "y"]}),
    ("run_user", {"host": "ali", "program": "bash", "args": ["-lc", "echo hi"]}),
    ("run_user", {"host": "ali", "program": "shutdown"}),      # 白名单外 → error
    ("sentinel_events", {}),
    ("sentinel_clips", {"limit": 2}),
    ("sudo_help", {}),
    ("pending_approvals", {}),
    ("audit_tail", {"limit": 3}),
    ("agents", {}),
    ("sudo_status", {"nonce": "zzz"}),                   # 错误路径
    ("sudo_status", {}),
    ("no_such_tool", {}),                                # 未知工具
]


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        cfg = build_config(Path(td))
        db.init_db(cfg.db_path)
        bad = 0
        for name, args in CASES:
            try:
                out = call(cfg, name, args)
            except Exception as e:  # noqa: BLE001
                print(f"EXC   {name:20s} {json.dumps(args, ensure_ascii=False)}"
                      f" -> {type(e).__name__}: {e}")
                bad += 1
                continue
            err = sdk_validate(out)
            sc = out.get("structuredContent")
            kind = "ABSENT" if "structuredContent" not in out else type(sc).__name__
            if err:
                bad += 1
                print(f"FAIL  {name:20s} {json.dumps(args, ensure_ascii=False):48s}"
                      f" <-- {err}")
            else:
                print(f"ok    {name:20s} {json.dumps(args, ensure_ascii=False):48s}"
                      f" isError={out['isError']} structuredContent={kind}")

        res = tools.call_tool(cfg, "pi", "hosts", {})
        assert isinstance(res, dict) and isinstance(res.get("hosts"), list), res
        print(f"\nhosts 返回 {len(res['hosts'])} 台: {[h['id'] for h in res['hosts']]}")
        print(f"\n{len(CASES) - bad}/{len(CASES)} 通过")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
