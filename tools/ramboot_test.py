#!/usr/bin/env python3
"""
RAM-boot an OpenWrt initramfs on the MH7021 through the ESP bridge and grade it.
No flash is written. Works from either state: a running OpenWrt shell (sends
`reboot`; the armed trap catches U-Boot) or the U-Boot prompt.

    python3 router/ramboot_test.py mh7021.itb        # file name inside neon:~/lab/tftp

Prints the lines that decide whether the port is right: DT model, the board-file
CRCs each radio loaded (generic QCA4019 data is 9eac77d9 -- ours must differ),
switch port labels, LEDs, keys, and anything that looks like an error.
"""
import re, socket, sys, threading, time, urllib.request
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import ubcmd

HOST, TFTP = "192.168.1.4", "192.168.0.10"
itb = sys.argv[1] if len(sys.argv) > 1 else "mh7021.itb"
OUT = "router/ramboot_%s.txt" % itb.replace(".itb", "")


def tail(n=400):
    return urllib.request.urlopen("http://%s/log?tail=%d" % (HOST, n), timeout=10).read().decode("latin1")


def wait_for(pat, secs, step=2):
    for _ in range(int(secs / step)):
        if re.search(pat, tail(600)):
            return True
        time.sleep(step)
    return False


# --- capture thread -----------------------------------------------------------
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
threading.Thread(target=cap, daemon=True).start(); time.sleep(3)
mark0 = len(buf)

# --- get to the U-Boot prompt ------------------------------------------------
ubcmd.send(""); time.sleep(1.5); t = tail(300)
if "(IPQ40xx) #" in t.rstrip()[-40:]:
    print("at U-Boot prompt")
else:
    urllib.request.urlopen("http://%s/uboot?arm=1" % HOST, timeout=10).read()
    print("running system detected -> reboot, trap armed"); ubcmd.send("reboot")
    if not wait_for(r"\(IPQ40xx\) #\s*$", 90):
        raise SystemExit("no U-Boot prompt within 90 s -- power-cycle the router and re-run")
    print("U-Boot prompt caught by the trap")

# --- load + boot -------------------------------------------------------------
for kv in ("ipaddr 192.168.0.1", "serverip " + TFTP, "netmask 255.255.255.0"):
    ubcmd.send("setenv " + kv)
# Right after a reset the switch is re-initialised and the PHYs need a few
# seconds to negotiate; the first tftpboot can see every PHY "Down". Retry.
for attempt in range(1, 6):
    out = ubcmd.run("tftpboot 0x84000000 %s" % itb, wait=60, tail=14000)
    m = re.search(r"Bytes transferred = (\d+)", out)
    if m:
        break
    print("tftpboot attempt %d: link not ready (%s); waiting 6 s"
          % (attempt, "PHY3 up" if "PHY3 up" in out else "all PHYs down"))
    time.sleep(6)
if not m:
    raise SystemExit("tftpboot failed after retries:\n" + out[-600:])
print("tftpboot: %s bytes" % m.group(1))
ubcmd.send("bootm 0x84000000"); t0 = time.time()
print("bootm sent %s -- capturing 150 s" % time.strftime("%H:%M:%S"))
time.sleep(150); stop = True; time.sleep(2)
txt = bytes(buf[mark0:]).decode("latin1"); open(OUT, "w").write(txt)
print("transcript: %d bytes -> %s\n=== verdict lines ===" % (len(txt), OUT))

pats = [r"Machine model", r"Linux version", r"board_file api", r"ath10k.*(qca4019|qca9888) hw", r"htt-ver.*cal ",
        r"MTD partitions|firmware|rootfs|Creating \d+ MTD", r"switch (lan|wan)|swport|c000000\.switch.*Link",
        r"gpio-keys|input: gpio|leds|led", r"pcie.*link", r"procd: - init complete|Please press Enter",
        r"Kernel panic|Oops|failed to|error|WARN|No such|not found"]
seen = []
for line in txt.replace("\r", "").split("\n"):
    l = line.strip()
    if l and l not in seen and any(re.search(p, l, re.I) for p in pats):
        seen.append(l); print(l[:150])
crcs = re.findall(r"board_file api 2 bmi_id 0:(\d+) crc32 ([0-9a-f]{8})", txt)
print("\nboard files loaded:", crcs or "NONE",
      "\n  -> generic QCA4019 data is 9eac77d9; vendor data must show different CRCs" if crcs else "")
