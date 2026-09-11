#!/usr/bin/env python3
"""
Flash OpenWrt onto the MH7021 from U-Boot -- the one irreversible step.

Why U-Boot: the OpenWrt initramfs has no tftp client and neon cannot route to
the wire without sudo, but U-Boot's tftpboot / sf / crc32 / cmp were all proven
today. The sysupgrade image (kernel FIT + squashfs + metadata trailer) is what
sysupgrade itself would write to the "firmware" region; the trailer ends up in
the area OpenWrt formats as rootfs_data on first boot.

Flow through the ESP bridge:
  1. reference size + crc32 (zlib == U-Boot's) of the image on neon:~/lab/tftp
  2. get to the U-Boot prompt (reboot from a running OpenWrt into the armed trap)
  3. tftpboot 0x84000000 <image>; crc32 must match     <-- dry run stops here
  4. --yes: disarm trap; sf erase 0x180000 0x1a70000; sf write; sf read back to
     0x85000000; cmp.b + crc32 of the readback; run bootcmd; capture 180 s and grade.
ART (0x170000) and the vendor factory partitions are never touched.
Recovery: README section 7 (the full dump is on the same TFTP server).

    python3 router/flash_uboot.py          # dry run: load + verify only
    python3 router/flash_uboot.py --yes    # flash
"""
import re, socket, subprocess, sys, threading, time, urllib.request
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import ubcmd

HOST, TFTP, NEON = "192.168.1.4", "192.168.0.10", "neon"
IMG = "openwrt-ipq40xx-generic-motorola_mh7021-squashfs-sysupgrade.bin"
LOAD, RB, FW_OFF, FW_LEN = 0x84000000, 0x85000000, 0x180000, 0x1a70000
OUT = "router/flash_console.txt"

def log(m): print(time.strftime("%H:%M:%S ") + m, flush=True)
def ub(c, wait=8, tail=8000): return ubcmd.run(c, wait=wait, tail=tail).strip()
def tail(n=400): return urllib.request.urlopen("http://%s/log?tail=%d" % (HOST, n), timeout=10).read().decode("latin1")
def wait_prompt(secs):
    for _ in range(int(secs / 2)):
        if re.search(r"\(IPQ40xx\) #\s*$", tail(600)): return True
        time.sleep(2)
    return False

# 1. stage the CURRENT build in the TFTP dir, then reference size + crc32
SRC = "$HOME/lab/owrt/openwrt/bin/targets/ipq40xx/generic/" + IMG
r = subprocess.run(["ssh", NEON, "cp %s $HOME/lab/tftp/%s && ls -l --time-style=+%%H:%%M %s | awk '{print $6}' && "
                    "python3 -c \"import zlib; d=open('%s','rb').read(); print(len(d), '%%08x' %% (zlib.crc32(d)&0xffffffff))\"" % (SRC, IMG, SRC, "$HOME/lab/tftp/" + IMG)],
                   capture_output=True, text=True, check=True)
built = r.stdout.split()[0]; r.stdout = " ".join(r.stdout.split()[1:])
log("staged build from %s" % built)
size, crc_ref = r.stdout.split(); size = int(size)
if size > FW_LEN: raise SystemExit("image larger than the firmware region")
log("image on neon: %d bytes (0x%x), crc32 %s" % (size, size, crc_ref))

# 2. U-Boot prompt
ubcmd.send(""); time.sleep(1.5)
if re.search(r"\(IPQ40xx\) #\s*$", tail(300)):
    log("at the U-Boot prompt")
else:
    urllib.request.urlopen("http://%s/uboot?arm=1" % HOST, timeout=10).read()
    log("running system -> reboot into the armed trap"); ubcmd.send("reboot")
    if not wait_prompt(90): raise SystemExit("no U-Boot prompt within 90 s")
    log("U-Boot prompt caught")
for kv in ("ipaddr 192.168.0.1", "serverip " + TFTP, "netmask 255.255.255.0"): ubcmd.send("setenv " + kv)

# 3. load + verify in RAM
for attempt in range(1, 6):
    out = ub("tftpboot 0x%x %s" % (LOAD, IMG), wait=90, tail=14000)
    m = re.search(r"Bytes transferred = (\d+)", out)
    if m: break
    log("tftpboot attempt %d: link not ready, retrying" % attempt); time.sleep(6)
if not m or int(m.group(1)) != size: raise SystemExit("tftpboot failed: " + out[-400:])
m = re.search(r"==> ([0-9a-f]{8})", ub("crc32 0x%x 0x%x" % (LOAD, size), wait=15))
if not m or m.group(1) != crc_ref: raise SystemExit("crc32 in RAM does not match neon's copy: %r" % (m and m.group(1)))
log("image in RAM at 0x%x, %d bytes, crc32 %s == reference" % (LOAD, size, crc_ref))

if "--yes" not in sys.argv:
    raise SystemExit("dry run complete -- nothing written. Router parked at U-Boot with the image in RAM; re-run with --yes to flash.")

# 4. flash
urllib.request.urlopen("http://%s/uboot?arm=0" % HOST, timeout=10).read(); log("trap disarmed")
if "W25Q256" not in ub("sf probe 0", wait=6): raise SystemExit("sf probe failed")
t = time.time(); out = ub("sf erase 0x%x 0x%x; echo erc=$?" % (FW_OFF, FW_LEN), wait=240, tail=3000)
if "erc=0" not in out: raise SystemExit("sf erase failed: " + out[-300:])
log("erased firmware region 0x%x+0x%x in %.0f s" % (FW_OFF, FW_LEN, time.time() - t))
t = time.time(); out = ub("sf write 0x%x 0x%x 0x%x; echo wrc=$?" % (LOAD, FW_OFF, size), wait=300, tail=3000)
if "wrc=0" not in out: raise SystemExit("sf write reported failure: " + out[-300:])
log("wrote %d bytes in %.0f s" % (size, time.time() - t))
out = ub("sf read 0x%x 0x%x 0x%x; cmp.b 0x%x 0x%x 0x%x" % (RB, FW_OFF, size, LOAD, RB, size), wait=120, tail=3000)
m2 = re.search(r"Total of (\d+) byte", out)
m3 = re.search(r"==> ([0-9a-f]{8})", ub("crc32 0x%x 0x%x" % (RB, size), wait=15))
if not (m2 and int(m2.group(1)) == size and "same" in out and m3 and m3.group(1) == crc_ref):
    raise SystemExit("READBACK VERIFY FAILED -- do not reboot; see README section 7. Output: " + out[-400:])
log("readback verified: %d/%d bytes identical, crc32 %s" % (size, size, crc_ref))

# 5. boot from flash, capture
buf, stop = bytearray(), False
def cap():
    s = socket.create_connection((HOST, 23), timeout=10); s.settimeout(1.0)
    while not stop:
        try:
            d = s.recv(65536)
            if not d: break
            buf.extend(d)
        except socket.timeout: pass
    s.close()
threading.Thread(target=cap, daemon=True).start(); time.sleep(2); mark0 = len(buf)
BOOTCMD = "ping 192.168.0.10; sf probe 0; sf read 0x84000000 0x180000 0x500000; bootm 0x84000000"
cur = ubcmd.run("printenv bootcmd", wait=3)
if BOOTCMD not in cur:
    # A fresh unit still has bootcmd=bootipq, which skips U-Boot's Ethernet init on
    # autoboot and leaves the switch PHYs dead for the kernel (see docs/HARDWARE.md).
    ubcmd.send("setenv bootcmd '%s'; saveenv" % BOOTCMD); time.sleep(8)
    cur = ubcmd.run("printenv bootcmd", wait=3)
    if BOOTCMD not in cur:
        raise SystemExit("could not install bootcmd: %r" % cur[-200:])
    log("bootcmd installed and saved to the U-Boot env")
else:
    log("bootcmd already correct")
ubcmd.send("run bootcmd"); log("run bootcmd sent (ping + sf read + bootm) -- capturing up to 180 s")
for _ in range(180):                      # stop as soon as the shell prompt is up
    time.sleep(1)
    if b"Please press Enter" in buf[mark0:]: time.sleep(3); break  # new data only, not the backlog replay
stop = True; time.sleep(2)
txt = bytes(buf[mark0:]).decode("latin1"); open(OUT, "w").write(txt)
log("transcript: %d bytes -> %s" % (len(txt), OUT))
pats = [r"Booting kernel|FIT|config@|Verifying", r"Machine model", r"Linux version", r"fit-fw|rootfs|firmware", r"board_file api",
        r"htt-ver.*cal ", r"switch (lan|wan)|Link is Up", r"jffs2|overlay|mount_root|rootfs_data", r"procd: - init complete|Please press Enter",
        r"Kernel panic|Oops|failed|error|Bad|invalid|Wrong"]
seen = []
for line in txt.replace("\r", "").split("\n"):
    l = line.strip()
    if l and l not in seen and any(re.search(p, l, re.I) for p in pats): seen.append(l); print(l[:150])
ok = "Motorola MH7021" in txt and "Please press Enter" in txt.split("Machine model")[-1]
print("\nVERDICT:", "BOOTED FROM FLASH into OpenWrt" if ok else "expected boot markers missing -- read the transcript")
