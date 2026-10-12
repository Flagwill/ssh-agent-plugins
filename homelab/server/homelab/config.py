"""配置加载：/opt/homelab/hosts.toml"""
from __future__ import annotations

import tomllib
from pathlib import Path

DEFAULT_CONFIG = "/opt/homelab/hosts.toml"


class Config:
    def __init__(self, path: str = DEFAULT_CONFIG):
        self.path = path
        with open(path, "rb") as f:
            self.data = tomllib.load(f)

    # ---- general ----
    @property
    def db_path(self) -> str:
        return self.data["general"]["db"]

    @property
    def static_dir(self) -> str:
        return self.data["general"]["static"]

    @property
    def listen(self) -> str:
        return self.data["general"].get("listen", "127.0.0.1:8790")

    @property
    def agent_key(self) -> str:
        return self.data["general"]["key"]

    @property
    def known_hosts(self) -> str:
        return self.data["general"].get("known_hosts", "/etc/homelab/known_hosts")

    @property
    def control_dir(self) -> str:
        return self.data["general"].get("control_dir", "/tmp/homelab-ssh")

    @property
    def web_base(self) -> str:
        return self.data.get("web", {}).get("base_path", "/home")

    @property
    def session_ttl_days(self) -> int:
        return int(self.data["general"].get("session_ttl_days", 30))

    @property
    def approval_ttl_minutes(self) -> int:
        return int(self.data["general"].get("approval_ttl_minutes", 15))

    @property
    def auto_approve_max_minutes(self) -> int:
        """Web 控制台"自动批准窗口"单次可开启的最长分钟数。"""
        return int(self.data["general"].get("auto_approve_max_minutes", 120))

    @property
    def collect_interval(self) -> int:
        return int(self.data["general"].get("collect_interval", 60))

    @property
    def metrics_retention_days(self) -> int:
        return int(self.data["general"].get("metrics_retention_days", 7))

    # ---- hosts ----
    @property
    def hosts(self) -> dict:
        return self.data.get("hosts", {})

    def host(self, host_id: str) -> dict | None:
        h = self.hosts.get(host_id)
        if h is None:
            return None
        h = dict(h)
        h["id"] = host_id
        h.setdefault("kind", "ssh")
        h.setdefault("label", host_id)
        h.setdefault("user", "root")
        h.setdefault("host", host_id)
        h.setdefault("port", 22)
        h.setdefault("units", [])
        h.setdefault("procs", [])
        h.setdefault("readonly_whitelist", self.data["exec"]["readonly_whitelist"])
        return h

    # ---- exec ----
    @property
    def deny_patterns(self) -> list[str]:
        return self.data["exec"].get("deny_patterns", [])

    @property
    def readonly_whitelist(self) -> list[str]:
        return self.data["exec"]["readonly_whitelist"]
