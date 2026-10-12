#!/usr/bin/env python3
"""远端采样器：部署到各主机 ~/.homelab/sampler.py，输出单行 JSON。"""
import glob
import json
import os
import subprocess
import sys


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=6).stdout
    except Exception:
        return ""


def parse_units(spec):
    return [u for u in spec.split(",") if u] if spec else []


def parse_procs(spec):
    out = []
    for item in spec.split(",") if spec else []:
        if ":" in item:
            label, pat = item.split(":", 1)
            out.append((label, pat))
    return out


def main():
    argv = sys.argv[1:]
    units, procs = [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--units":
            units = parse_units(argv[i + 1]); i += 2
        elif argv[i] == "--procs":
            procs = parse_procs(argv[i + 1]); i += 2
        else:
            i += 1

    out = {}
    try:
        out["uptime_s"] = float(open("/proc/uptime").read().split()[0])
    except Exception:
        out["uptime_s"] = None
    try:
        out["load"] = [float(x) for x in open("/proc/loadavg").read().split()[:3]]
    except Exception:
        out["load"] = None

    mem = {}
    for line in open("/proc/meminfo"):
        parts = line.split()
        if parts[0] in ("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:"):
            mem[parts[0][:-1]] = int(parts[1]) * 1024
    out["mem"] = mem

    try:
        st = os.statvfs("/")
        out["disk_root"] = {"total": st.f_blocks * st.f_frsize,
                            "free": st.f_bfree * st.f_frsize}
    except Exception:
        out["disk_root"] = {}

    cpu = None
    try:
        fields = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        cpu = {"total": sum(fields), "idle": idle}
    except Exception:
        pass
    out["cpu"] = cpu

    rx = tx = 0
    try:
        for line in open("/proc/net/dev").read().splitlines()[2:]:
            name = line.split(":")[0].strip()
            if name == "lo":
                continue
            nums = line.split(":")[1].split()
            rx += int(nums[0]); tx += int(nums[8])
    except Exception:
        pass
    out["net"] = {"rx_bytes": rx, "tx_bytes": tx}

    temps = []
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            temps.append(int(open(p).read().strip()) / 1000.0)
        except Exception:
            pass
    vc = sh(["vcgencmd", "measure_temp"])
    if "temp=" in vc:
        try:
            temps.append(float(vc.split("=")[1].split("'")[0]))
        except Exception:
            pass
    out["temp_max"] = max(temps) if temps else None

    unit_states = {}
    for u in units:
        r = subprocess.run(["systemctl", "is-active", u],
                           capture_output=True, text=True)
        unit_states[u] = r.stdout.strip() or "unknown"
    out["units"] = unit_states

    proc_states = {}
    if procs:
        cmdlines = []
        for d in glob.glob("/proc/[0-9]*/cmdline"):
            try:
                cmdlines.append(open(d, "rb").read().replace(b"\0", b" ").decode(errors="replace"))
            except Exception:
                pass
        for label, pat in procs:
            proc_states[label] = any(pat in c for c in cmdlines)
    out["procs"] = proc_states

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
