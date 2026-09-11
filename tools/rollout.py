#!/usr/bin/env python3
"""
rollout.py -- upgrade one neon-mesh unit to a release image over ssh, safely, and check it afterwards.

    python3 router/rollout.py sat1                          # latest release, mainline profile
    python3 router/rollout.py sat1 --release neon-mesh-v1.0.0-rc1 --variant ct
    python3 router/rollout.py base --image /path/or/URL --rollback /path/or/URL
    python3 router/rollout.py sat1 --dry                    # pre-checks and the plan only, no changes
    python3 router/rollout.py --status                      # fleet summary (all three units)

Images normally come from the release server on neon (http://192.168.0.5:8081/neon-mesh/<tag>/...):
the unit fetches them itself with wget and the sha256 is checked against the release's sha256sums.
A local file is pushed with scp -O instead. Steps: pre-checks -> stage the new image in /tmp and the
rollback image in /etc/neon/ -> sysupgrade -T on both (+ an explicit ucert signature check when the
running image has ucert) -> arm neon-watchdog (auto rollback if the unit is not healthy within
--deadline s of uptime after the flash) -> detached `sysupgrade -v` -> wait for the unit -> post-checks
-> `neon-role apply` -> `neon-watchdog disarm`. Everything observed goes to rollout_<unit>_<ts>.json.
The root password is asked once (expect + SSH_ASKPASS) and never stored. Order of the fleet rollout
and the acceptance checks it runs afterwards.
"""
import argparse, getpass, hashlib, json, os, re, socket, subprocess, sys, tempfile, time, urllib.request

UNITS = {"base": "192.168.0.1", "sat1": "192.168.0.2", "sat2": "192.168.0.3"}
SERVER = os.environ.get("NEON_FEED", "http://buildhost.lan:8081/neon-mesh")
LEGACY = "legacy-main-098e599"
BOOTCMD = "ping 192.168.0.10; sf probe 0; sf read 0x84000000 0x180000 0x500000; bootm 0x84000000"
BOARD_CRC = {"20": "17b369b4", "29": "17b369b4", "23": "d6b71c5b"}   # bmi_id -> vendor board file crc32
IMG_GLOB = {"mainline": "-motorola_mh7021-squashfs-sysupgrade.bin", "ct": "-motorola_mh7021-ct-squashfs-sysupgrade.bin"}
MACS = ["eth0", "br-lan", "lan", "wan", "phy0-mesh0", "phy1-ap0", "phy2-ap0"]

EXPECT = r'''
set timeout [lindex $argv 0]
set ip [lindex $argv 1]
set cmd [lindex $argv 2]
log_user 0
spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR root@$ip $cmd
expect {
  -re "(?i)password:" { send "$env(NEON_PW)\r" }
  eof { exit 2 }
  timeout { exit 3 }
}
expect {
  -re "(?i)password:" { exit 4 }
  eof {}
  timeout { exit 3 }
  -re "(.+)" { append out $expect_out(1,string); exp_continue }
}
puts -nonewline $out
catch wait result
exit [lindex $result 3]
'''
ASKPASS = "#!/bin/sh\nprintf '%s\\n' \"$NEON_PW\"\n"


class Unit:
    def __init__(self, name, ip):
        self.name, self.ip = name, ip

    def ssh(self, cmd, timeout=40, check=True):
        r = subprocess.run(["expect", EXPECT_FILE, str(timeout), self.ip, cmd], capture_output=True, text=True)
        out = r.stdout.replace("\r\n", "\n").strip()
        if r.returncode == 4:
            sys.exit("!! %s: password rejected" % self.name)
        if r.returncode == 3:
            raise TimeoutError("%s: ssh timed out running: %s" % (self.name, cmd[:80]))
        if check and r.returncode not in (0,):
            raise RuntimeError("%s: rc=%d: %s" % (self.name, r.returncode, out[-400:]))
        return out

    def scp(self, local, remote):
        env = dict(os.environ, SSH_ASKPASS=ASKPASS_FILE, SSH_ASKPASS_REQUIRE="force", DISPLAY="none")
        r = subprocess.run(["scp", "-O", "-q", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                            "-o", "LogLevel=ERROR", local, "root@%s:%s" % (self.ip, remote)], env=env, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError("scp to %s failed: %s" % (self.name, r.stderr.strip()[-300:]))

    def ping(self):
        return subprocess.run(["ping", "-c1", "-W1000", self.ip], capture_output=True).returncode == 0

    def ssh_port(self):
        try:
            socket.create_connection((self.ip, 22), timeout=3).close(); return True
        except OSError:
            return False


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------------------------------------------------------- images
def http_get(url, rng=None):
    req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def resolve_image(spec, release, variant):
    """-> (kind, location, sha256 or None). kind: 'url' | 'file'."""
    if spec and not spec.startswith("http"):
        p = os.path.abspath(spec)
        if not os.path.isfile(p):
            sys.exit("!! image not found: " + p)
        return "file", p, hashlib.sha256(open(p, "rb").read()).hexdigest()
    if spec:
        return "url", spec, None
    base = "%s/%s/targets/ipq40xx/generic" % (SERVER, release)
    try:
        sums = http_get(base + "/sha256sums").decode()
    except Exception as e:
        sys.exit("!! cannot read %s/sha256sums (%s) -- is the neon-releases container running? (RELEASE.md §5)" % (base, e))
    want = "-motorola_mh7021-squashfs-sysupgrade.bin" if variant == "mainline" else IMG_GLOB["ct"]
    if release == LEGACY:
        want = "-motorola_mh7021-squashfs-sysupgrade.bin"
    for line in sums.splitlines():
        if line.strip().endswith(want) and "-ct-" not in line if variant == "mainline" else line.strip().endswith(want):
            sha, name = line.split()[0], line.split("*")[-1].split()[-1]
            return "url", "%s/%s" % (base, name), sha
    sys.exit("!! no %s image in %s/sha256sums" % (variant, base))


def image_meta(kind, loc):
    """version/revision from the image's trailing fwtool metadata (works for local files and URLs)."""
    tail = open(loc, "rb").read()[-65536:] if kind == "file" else http_get(loc, "bytes=-65536")
    m = re.search(rb'"version":\s*\{[^}]*"version":\s*"([^"]+)"[^}]*"revision":\s*"([^"]+)"', tail)
    return (m.group(1).decode(), m.group(2).decode()) if m else ("?", "?")


# ---------------------------------------------------------------- unit facts
def facts(u):
    f = {}
    f["board"] = u.ssh("cat /tmp/sysinfo/board_name")
    f["hostname"] = u.ssh("cat /proc/sys/kernel/hostname")
    f["release"] = dict(l.split("=", 1) for l in u.ssh("cat /etc/openwrt_release").splitlines() if "=" in l)
    f["release"] = {k: v.strip("'") for k, v in f["release"].items()}
    nr = u.ssh("cat /etc/neon-release 2>/dev/null || true")
    f["neon_release"] = dict(l.split("=", 1) for l in nr.splitlines() if "=" in l)
    f["kernel"] = u.ssh("uname -r")
    f["driver"] = u.ssh("apk list --installed 2>/dev/null | grep -oE '^kmod-ath10k(-ct)?-' | head -1 | sed 's/-$//'") or "?"
    f["mem_avail_kib"] = int(u.ssh("awk '/MemAvailable/{print $2}' /proc/meminfo"))
    f["tmp_free_kib"] = int(u.ssh("df -k /tmp | awk 'NR==2{print $4}'"))
    f["overlay_free_kib"] = int(u.ssh("df -k /overlay 2>/dev/null | awk 'NR==2{print $4}'") or 0)
    f["macs"] = dict(l.split() for l in u.ssh("for i in %s; do echo $i $(cat /sys/class/net/$i/address 2>/dev/null || echo -); done" % " ".join(MACS)).splitlines())
    f["ifaces"] = u.ssh("iw dev 2>/dev/null | awk '/Interface/{i=$2} /type/{t=$2} /channel/{print i, t, \"ch\", $2, \"width\", $5}'").splitlines()
    mdev = u.ssh("iw dev 2>/dev/null | awk '/Interface/{i=$2} /type mesh point/{print i; exit}'")
    f["mesh_dev"] = mdev
    f["peers"] = u.ssh("iw dev %s station dump 2>/dev/null | awk '/^Station/{m=$2} /mesh plink:/{p=$3} /signal avg:/{s=$3} /tx failed:/{f=$3} /^$/{if(m){print m, p, s, f; m=\"\"}} END{if(m)print m, p, s, f}'" % mdev).splitlines() if mdev else []
    f["ucert"] = u.ssh("[ -x /usr/bin/ucert ] && echo yes || echo no") == "yes"
    f["bootcmd"] = u.ssh("fw_printenv -n bootcmd 2>/dev/null || echo n/a")
    f["light"] = u.ssh("neon-led status 2>/dev/null | sed -n 's/^now    : //p'")
    f["watchdog"] = u.ssh("neon-watchdog status 2>/dev/null | head -1 || echo n/a")
    return f


def brief(f):
    return "%s %s %s (%s, %s) mem %d MiB, %d peer(s) ESTAB, light: %s" % (
        f["hostname"], f["release"].get("DISTRIB_RELEASE"), f["release"].get("DISTRIB_REVISION"),
        f["neon_release"].get("NEON_RELEASE", "no neon-release"), f["driver"], f["mem_avail_kib"] // 1024,
        sum(1 for p in f["peers"] if " ESTAB " in " %s " % p), f["light"] or "?")


# ---------------------------------------------------------------- the rollout
def stage(u, kind, loc, sha, remote, label):
    if kind == "url":
        log("%s: fetching %s image on the unit from %s" % (u.name, label, loc))
        u.ssh("wget -q -O %s '%s'" % (remote, loc), timeout=300)
    else:
        log("%s: pushing %s image %s" % (u.name, label, os.path.basename(loc)))
        u.scp(loc, remote)
    got = u.ssh("sha256sum %s | cut -d' ' -f1" % remote, timeout=120)
    if sha and got != sha:
        raise RuntimeError("%s image sha256 mismatch on the unit: %s != %s" % (label, got, sha))
    log("%s: %s image staged at %s, sha256 %s…" % (u.name, label, remote, got[:12]))
    return got


def check_image(u, remote, has_ucert):
    out = u.ssh("sysupgrade -T %s 2>&1; echo RC=$?" % remote, timeout=120)
    rc = int(re.search(r"RC=(\d+)", out).group(1))
    body = out.replace("RC=%d" % rc, "").strip()
    if rc != 0 or re.search(r"not supported|failed|incompatible|Invalid", body, re.I):
        raise RuntimeError("sysupgrade -T rejected %s: %s" % (remote, body[-300:]))
    sig = "not checked (no ucert on the running image)"
    if has_ucert:
        s = u.ssh("fwtool -q -s /tmp/img.ucert %s 2>/dev/null && fwtool -q -T -s /dev/null %s | ucert -V -m - -c /tmp/img.ucert -P /etc/opkg/keys 2>&1 && echo SIG_OK || echo SIG_ABSENT_OR_BAD; rm -f /tmp/img.ucert" % (remote, remote), timeout=120)
        sig = s.strip().splitlines()[-1]
    log("%s: sysupgrade -T ok on %s%s; signature: %s" % (u.name, remote, (" (" + body + ")") if body else "", sig))
    return sig


def wait_for(u, minutes):
    t0 = time.time(); went_down = False
    while time.time() - t0 < minutes * 60:
        up = u.ping()
        if not went_down and not up:
            went_down = True; log("%s: went down after %d s (flashing)" % (u.name, time.time() - t0))
        if went_down and up and u.ssh_port():
            log("%s: back after %d s" % (u.name, time.time() - t0)); time.sleep(20); return True
        time.sleep(5)
    return False


def rollout(a):
    u = Unit(a.unit, UNITS[a.unit])
    ts = time.strftime("%Y%m%d-%H%M%S"); rec = {"unit": a.unit, "ip": u.ip, "started": ts, "args": vars(a)}
    log("%s (%s): pre-checks" % (u.name, u.ip))
    before = facts(u); rec["before"] = before
    log("   " + brief(before))
    if before["board"] != "motorola,mh7021":
        sys.exit("!! not an MH7021: %r" % before["board"])
    # images
    kind, loc, sha = resolve_image(a.image, a.release, a.variant)
    ver = image_meta(kind, loc); rec["image"] = {"kind": kind, "location": loc, "sha256": sha, "version": ver}
    log("   new image: %s -> OpenWrt %s %s" % (os.path.basename(loc), *ver))
    if a.rollback is None:
        cur = before["neon_release"].get("NEON_RELEASE")
        rb_release = cur if cur else LEGACY
        rb_variant = "ct" if before["driver"].endswith("-ct") else "mainline"
        rkind, rloc, rsha = resolve_image(None, rb_release, rb_variant)
    elif a.rollback == "none":
        rkind = None
    else:
        rkind, rloc, rsha = resolve_image(a.rollback, None, None)
    if rkind:
        rver = image_meta(rkind, rloc); rec["rollback"] = {"kind": rkind, "location": rloc, "sha256": rsha, "version": rver}
        log("   rollback image: %s -> OpenWrt %s %s (neon-watchdog deadline %d s)" % (os.path.basename(rloc), *rver, a.deadline))
    else:
        log("   NO rollback image / watchdog (--rollback none)")
    if before["mem_avail_kib"] < 40 * 1024:
        sys.exit("!! MemAvailable %d KiB < 40 MiB" % before["mem_avail_kib"])
    if before["tmp_free_kib"] < 24 * 1024:
        sys.exit("!! /tmp free %d KiB < 24 MiB" % before["tmp_free_kib"])
    if rkind and before["overlay_free_kib"] < 12 * 1024:
        sys.exit("!! /overlay free %d KiB < 12 MiB (needed for the rollback image)" % before["overlay_free_kib"])
    if not before["ucert"]:
        log("   note: running image has no ucert -> the new image's signature cannot be verified on-device this time")
    if a.dry:
        log("dry run: stopping here"); save(rec, a.unit, ts); return
    if not a.yes and input("flash %s with %s? [y/N] " % (u.name, os.path.basename(loc))).strip().lower() != "y":
        sys.exit("aborted")
    # stage
    stage(u, kind, loc, sha, "/tmp/neon-upgrade.bin", "new")
    rec["check_new"] = check_image(u, "/tmp/neon-upgrade.bin", before["ucert"])
    if rkind:
        u.ssh("mkdir -p /etc/neon; rm -f /etc/neon/rollback.bin /etc/neon/watchdog")
        stage(u, rkind, rloc, rsha, "/etc/neon/rollback.bin", "rollback")
        rec["check_rollback"] = check_image(u, "/etc/neon/rollback.bin", before["ucert"])
        u.ssh("grep -qx /etc/neon/ /etc/sysupgrade.conf 2>/dev/null || echo /etc/neon/ >> /etc/sysupgrade.conf; "
              "printf 'deadline=%d\\nrollback=/etc/neon/rollback.bin\\narmed=%d\\nby=rollout.py-%s\\n' > /etc/neon/watchdog; cat /etc/neon/watchdog" % (a.deadline, int(time.time()), ts))
        log("%s: neon-watchdog armed (kept across the upgrade via /etc/sysupgrade.conf)" % u.name)
    # go
    u.ssh("printf 'sleep 2\\nsysupgrade -v /tmp/neon-upgrade.bin\\n' > /tmp/neon-go.sh; setsid sh /tmp/neon-go.sh </dev/null >/dev/null 2>&1 &", timeout=15)
    log("%s: sysupgrade -v started (detached); waiting up to %d min" % (u.name, a.wait))
    rec["flashed_at"] = time.strftime("%F %T")
    if not wait_for(u, a.wait):
        rec["result"] = "NOT BACK"; save(rec, a.unit, ts)
        sys.exit("!! %s did not come back within %d min. If the watchdog was armed it rolls back at %d s of uptime; "
                 "otherwise: recover over Ethernet, or serial console + U-Boot TFTP (docs/HARDWARE.md)." % (u.name, a.wait, a.deadline))
    # post-checks
    after = facts(u); rec["after"] = after
    log("   " + brief(after))
    ok = True
    def chk(cond, what):
        nonlocal ok
        log("   %s %s" % ("ok  " if cond else "FAIL", what)); ok = ok and cond
    chk(after["release"].get("DISTRIB_RELEASE") == ver[0] and after["release"].get("DISTRIB_REVISION") == ver[1],
        "running %s %s (image says %s %s)" % (after["release"].get("DISTRIB_RELEASE"), after["release"].get("DISTRIB_REVISION"), *ver))
    for i in ["eth0", "br-lan", "lan", "phy0-mesh0", "phy1-ap0", "phy2-ap0"]:
        chk(before["macs"].get(i) == after["macs"].get(i), "MAC %s %s" % (i, after["macs"].get(i)))
    if before["macs"].get("wan") != after["macs"].get("wan"):
        log("   note wan MAC %s -> %s (25.12 takes it from ART+0x0 per the DTS; informational)" % (before["macs"].get("wan"), after["macs"].get("wan")))
    dmesg = u.ssh("dmesg | grep -E 'board_file|cal pre-cal|ath10k.*(failed|error)' | head -20")
    rec["dmesg"] = dmesg
    for bmi, crc in BOARD_CRC.items():
        chk(("bmi_id 0:%s crc32 %s" % (bmi, crc)) in dmesg, "board file bmi_id %s crc32 %s" % (bmi, crc))
    chk(dmesg.count("cal pre-cal-nvmem") >= 3, "cal pre-cal-nvmem x%d" % dmesg.count("cal pre-cal-nvmem"))
    chk(len([l for l in after["ifaces"] if " AP " in l]) >= 2 and after["mesh_dev"] != "", "wifi: %s" % "; ".join(after["ifaces"]))
    est = [p for p in after["peers"] if " ESTAB " in " %s " % p]
    chk(len(est) >= 1, "mesh peers ESTAB: %s" % (", ".join(est) or "none"))
    gw = "8.8.8.8" if a.unit == "base" else "192.168.0.1"
    chk("0% packet loss" in u.ssh("ping -c3 -W2 -q %s 2>&1 | grep loss || true" % gw, timeout=30), "ping %s" % gw)
    chk("0% packet loss" in u.ssh("ping -c3 -W2 -q 8.8.8.8 2>&1 | grep loss || true", timeout=30), "ping 8.8.8.8")
    chk("green" in (after["light"] or ""), "light: %s" % after["light"])
    chk(after["bootcmd"] == BOOTCMD or after["bootcmd"] == "n/a", "bootcmd: %s" % after["bootcmd"])
    apk = u.ssh("apk update 2>&1 | tail -4", timeout=120); rec["apk_update"] = apk
    chk("ERROR" not in apk and "OK:" in apk, "apk update: %s" % apk.replace("\n", " | "))
    if rkind:
        wd = u.ssh("neon-watchdog status 2>/dev/null || echo n/a"); rec["watchdog"] = wd
        log("   watchdog: %s" % wd.replace("\n", " | "))
        u.ssh("neon-watchdog disarm 2>/dev/null || true")
    if a.apply:
        log("%s: neon-role apply (re-applies the release tuning; network restarts detached)" % u.name)
        log("   " + u.ssh("neon-role apply --no-wait 2>&1 | tail -3", timeout=60).replace("\n", "\n   "))
        time.sleep(50)
        st = u.ssh("neon-role status 2>&1", timeout=60); rec["status_after_apply"] = st
        print("   " + st.replace("\n", "\n   "))
    rec["result"] = "OK" if ok else "CHECKS FAILED"
    save(rec, a.unit, ts)
    log("%s: %s" % (u.name, rec["result"]))
    sys.exit(0 if ok else 1)


def save(rec, unit, ts):
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rollout_%s_%s.json" % (unit, ts))
    json.dump(rec, open(p, "w"), indent=1, default=str); log("record: %s" % p)


def status():
    for name, ip in UNITS.items():
        u = Unit(name, ip)
        try:
            f = facts(u); print("%-5s %s" % (name, brief(f)))
            for p in f["peers"]:
                print("        peer %s" % p)
            print("        %s | bootcmd %s | watchdog %s" % ("; ".join(f["ifaces"]), "ok" if f["bootcmd"] == BOOTCMD else f["bootcmd"], f["watchdog"]))
        except Exception as e:
            print("%-5s UNREACHABLE (%s)" % (name, e))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("unit", nargs="?", choices=sorted(UNITS))
    ap.add_argument("--release", default="latest", help="release tag on the server (default latest)")
    ap.add_argument("--variant", default="mainline", choices=["mainline", "ct"])
    ap.add_argument("--image", help="local file or URL instead of --release/--variant")
    ap.add_argument("--rollback", help="local file, URL, or 'none'; default: the unit's current release (or the legacy main image)")
    ap.add_argument("--deadline", type=int, default=600, help="neon-watchdog: seconds of uptime before an unhealthy unit rolls back")
    ap.add_argument("--wait", type=int, default=8, help="minutes to wait for the unit after sysupgrade")
    ap.add_argument("--no-apply", dest="apply", action="store_false", help="skip `neon-role apply` after the post-checks")
    ap.add_argument("--dry", action="store_true"); ap.add_argument("--yes", action="store_true")
    ap.add_argument("--status", action="store_true", help="fleet summary and exit")
    a = ap.parse_args()
    if not a.status and not a.unit:
        ap.error("unit required (or --status)")
    os.environ["NEON_PW"] = os.environ.get("NEON_PW") or getpass.getpass("root password for the units: ")
    d = tempfile.mkdtemp(prefix="neon-rollout-")
    EXPECT_FILE = os.path.join(d, "ssh.exp"); open(EXPECT_FILE, "w").write(EXPECT)
    ASKPASS_FILE = os.path.join(d, "askpass.sh"); open(ASKPASS_FILE, "w").write(ASKPASS); os.chmod(ASKPASS_FILE, 0o700)
    try:
        status() if a.status else rollout(a)
    finally:
        for f in (EXPECT_FILE, ASKPASS_FILE):
            try: os.unlink(f)
            except OSError: pass
        try: os.rmdir(d)
        except OSError: pass
