"""后台巡检: 每 interval 秒采集一次所有主机, 存 metrics 表。"""
from __future__ import annotations

import json
import threading
import time

from . import db, sshutil


def _prev(cfg, host_id: str) -> dict | None:
    row = db.query_one(
        cfg.db_path,
        "SELECT payload FROM metrics WHERE host=? ORDER BY ts DESC LIMIT 1",
        (host_id,))
    return json.loads(row["payload"]) if row else None


def sample_host(cfg, host_id: str, timeout: float = 20.0) -> dict | None:
    h = cfg.host(host_id)
    if h is None:
        return None
    argv = sshutil.sampler_argv(cfg, host_id, h["units"], h["procs"])
    res = sshutil.run(cfg, host_id, argv, timeout=timeout)
    if not res.get("ok") or not res["stdout"].strip():
        return None
    try:
        data = json.loads(res["stdout"].strip().splitlines()[-1])
    except Exception:
        return None

    prev = _prev(cfg, host_id)
    now_ts = int(time.time())
    dt = max(1, now_ts - prev["sampled_at"]) if prev and prev.get("sampled_at") else None

    payload = {"sampled_at": now_ts, "uptime_s": data.get("uptime_s"),
               "load": data.get("load"),
               "mem": data.get("mem", {}),
               "disk_root": data.get("disk_root", {}),
               "temp_max": data.get("temp_max"),
               "units": data.get("units", {}),
               "procs": data.get("procs", {})}

    # CPU%: 与上次 /proc/stat 差
    if data.get("cpu") and prev and prev.get("_cpu"):
        dtot = data["cpu"]["total"] - prev["_cpu"]["total"]
        didle = data["cpu"]["idle"] - prev["_cpu"]["idle"]
        if dtot > 0:
            payload["cpu_pct"] = round(100.0 * (dtot - didle) / dtot, 1)
    if data.get("cpu"):
        payload["_cpu"] = data["cpu"]

    # 网络速率
    if data.get("net") and prev and prev.get("net") and dt:
        payload["net_rx_Bps"] = max(0, int((data["net"]["rx_bytes"] - prev["net"]["rx_bytes"]) / dt))
        payload["net_tx_Bps"] = max(0, int((data["net"]["tx_bytes"] - prev["net"]["tx_bytes"]) / dt))
    if data.get("net"):
        payload["net"] = data["net"]

    return payload


def store(cfg, host_id: str, payload: dict | None, error: str = "") -> None:
    if payload is None:
        payload = {"sampled_at": int(time.time()), "error": error or "unreachable"}
    db.execute(cfg.db_path,
               "INSERT INTO metrics(ts, host, payload) VALUES(?,?,?)",
               (int(time.time()), host_id, db.jdump(payload)))


def prune(cfg) -> None:
    cutoff = int(time.time()) - cfg.metrics_retention_days * 86400
    db.execute(cfg.db_path, "DELETE FROM metrics WHERE ts<?", (cutoff,))


def run_forever(cfg, stop_event: threading.Event | None = None) -> None:
    while True:
        for host_id in list(cfg.hosts):
            try:
                err = ""
                p = sample_host(cfg, host_id)
                if p is None:
                    err = "collect failed"
                store(cfg, host_id, p, err)
            except Exception as e:  # noqa: BLE001
                store(cfg, host_id, None, f"{type(e).__name__}: {e}")
            time.sleep(2)  # 主机间错峰
        try:
            prune(cfg)
        except Exception:
            pass
        if stop_event:
            stop_event.wait(cfg.collect_interval)
        else:
            time.sleep(cfg.collect_interval)


def start_background(cfg) -> threading.Thread:
    t = threading.Thread(target=run_forever, args=(cfg,), daemon=True,
                         name="homelab-collector")
    t.start()
    return t
