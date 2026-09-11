#!/usr/bin/env python3
"""
soak.py -- sample a unit's mesh health every N seconds into a CSV (for soak testing / tuning baselines).

    NEON_PW=... nohup python3 router/soak.py sat1 --interval 600 > soak_sat1.log 2>&1 &
    python3 router/soak.py sat1 sat2 --interval 600 --iperf 192.168.0.1     # iperf3 -c <server> every 8th sample

Columns: time, unit, uptime_s, mem_avail_kib, light, peers (mac:plink:sigavg:txfailed;...), hops_to_base,
sae_log, plink_log, oom_log, ath10k_err_log (cumulative logread counts), iperf_mbit. Appends to
soak_<unit>.csv next to this file (gitignored). Read-only on the units.
"""
import argparse, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rollout

ap = argparse.ArgumentParser()
ap.add_argument("units", nargs="+", choices=sorted(rollout.UNITS))
ap.add_argument("--interval", type=int, default=600)
ap.add_argument("--iperf", help="iperf3 server to test against every 8th sample (needs iperf3 on the unit)")
a = ap.parse_args()
os.environ["NEON_PW"] = os.environ.get("NEON_PW") or __import__("getpass").getpass("root password for the units: ")
import tempfile
d = tempfile.mkdtemp(prefix="neon-soak-")
rollout.EXPECT_FILE = os.path.join(d, "ssh.exp"); open(rollout.EXPECT_FILE, "w").write(rollout.EXPECT)
rollout.ASKPASS_FILE = os.path.join(d, "askpass.sh")

CMD = r'''
up=$(cut -d. -f1 /proc/uptime); mem=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
light=$(neon-led status 2>/dev/null | sed -n 's/^now    : \([a-z]* [a-z]*\).*/\1/p')
mdev=$(iw dev 2>/dev/null | awk '/Interface/{i=$2} /type mesh point/{print i; exit}')
peers=$(iw dev $mdev station dump 2>/dev/null | awk '/^Station/{m=$2} /mesh plink:/{p=$3} /signal avg:/{s=$3} /tx failed:/{f=$3} /^$/{if(m){printf "%s:%s:%s:%s;", m, p, s, f; m=""}} END{if(m)printf "%s:%s:%s:%s", m, p, s, f}')
hops=$(iw dev $mdev mpath dump 2>/dev/null | awk 'NR>1 && $1 ~ /:81$/ {print $11; exit}')
L=$(logread 2>/dev/null)
sae=$(echo "$L" | grep -ci 'sae'); plink=$(echo "$L" | grep -ci 'plink'); oom=$(echo "$L" | grep -ci 'oom\|out of memory'); err=$(echo "$L" | grep -ci 'ath10k.*\(error\|failed\|timeout\|crash\)')
echo "$up,$mem,$light,$peers,${hops:-?},$sae,$plink,$oom,$err"
'''
n = 0
while True:
    for name in a.units:
        u = rollout.Unit(name, rollout.UNITS[name]); p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soak_%s.csv" % name)
        try:
            row = u.ssh(CMD, timeout=60)
            ip = ""
            if a.iperf and n % 8 == 0:
                ip = u.ssh("iperf3 -c %s -t 5 -J 2>/dev/null | sed -n 's/.*\"bits_per_second\":\\s*\\([0-9.e+]*\\).*/\\1/p' | tail -1" % a.iperf, timeout=90)
                try: ip = "%.1f" % (float(ip) / 1e6)
                except ValueError: ip = "?"
            line = "%s,%s,%s,%s" % (time.strftime("%F %T"), name, row, ip)
        except Exception as e:
            line = "%s,%s,ERROR %s" % (time.strftime("%F %T"), name, str(e).replace("\n", " ")[:120])
        new = not os.path.exists(p)
        with open(p, "a") as f:
            if new: f.write("time,unit,uptime_s,mem_avail_kib,light,peers,hops_to_base,sae_log,plink_log,oom_log,ath10k_err_log,iperf_mbit\n")
            f.write(line + "\n")
        print(line, flush=True)
    n += 1
    time.sleep(a.interval)
