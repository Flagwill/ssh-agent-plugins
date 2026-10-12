"""初始化数据库 / 设置 Web 控制台密码。

用法:
  python3 -m homelab.initdb                 # 仅建表
  python3 -m homelab.initdb --password X    # 设置密码
  python3 -m homelab.initdb --gen-password  # 生成随机密码并打印(仅一次)
"""
from __future__ import annotations

import argparse
import hashlib
import secrets
import sys

from . import db


def _hash(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=1 << 14, r=8, p=1).hex()
    return f"scrypt${salt.hex()}${dk}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/var/lib/homelab/homelab.db")
    ap.add_argument("--password")
    ap.add_argument("--gen-password", action="store_true")
    args = ap.parse_args()
    db.init_db(args.db)
    if args.password:
        db.meta_set(args.db, "web_password_hash", _hash(args.password))
        print("password set")
    elif args.gen_password:
        if db.meta_get(args.db, "web_password_hash"):
            print("ERROR: password already set; use --password to rotate")
            sys.exit(1)
        pw = secrets.token_urlsafe(9)
        db.meta_set(args.db, "web_password_hash", _hash(pw))
        print(pw)
    else:
        print("db ready")


if __name__ == "__main__":
    main()
