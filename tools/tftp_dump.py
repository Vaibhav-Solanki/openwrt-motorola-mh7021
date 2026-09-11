#!/usr/bin/env python3
"""
Pull the router's 32 MB SPI-NOR over Ethernet: U-Boot `sf read` into RAM, then
`tftpput` to the macvlan TFTP container on neon (192.168.0.10). Seconds, not
hours. Verified two ways: byte count, and U-Boot's own crc32 of the RAM image
against a local crc32 of the received file. Then copied to router/ on the Mac,
split into the MTD partitions, and summed.

Prereq on neon:  ~/lab/owrt/tftp.sh   (container 'tftp' on enp7s0 as 192.168.0.10)
Run from the project root:  python3 router/tftp_dump.py --tag=unit2   (tag = per-unit file names; no tag = unit 1 names)
"""
import hashlib, os, re, subprocess, sys, time, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ubcmd
from dump_flash import PARTS, LOAD, FLASH

OUT   = os.path.dirname(os.path.abspath(__file__))
TAG   = next((a.split("=",1)[1] for a in sys.argv if a.startswith("--tag=")), "")   # e.g. --tag=unit2
FNAME = "mh7021_%sflash.bin" % (TAG + "_" if TAG else "")
NEON  = "neon"


def log(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def ub(cmd, wait, must=None, tail=8000):
    out = ubcmd.run(cmd, wait=wait, tail=tail)
    if must and must not in out:
        raise SystemExit("U-Boot: %r did not produce %r:\n%s" % (cmd, must, out[-600:]))
    return out


def main():
    # Router's factory defaults for recovery -- the container was given exactly this serverip.
    for kv in ("ipaddr 192.168.0.1", "serverip 192.168.0.10", "netmask 255.255.255.0"):
        ubcmd.send("setenv " + kv)
    ub("ping 192.168.0.10", 8, must="is alive")
    log("router -> 192.168.0.10 (tftp container) reachable over the wire")

    ub("sf probe 0", 5, must="W25Q256")
    t = time.time()
    # U-Boot 2012.07 prints nothing on a successful sf read; ask hush for the rc.
    out = ub("sf read %x 0 %x; echo sfrc=$?" % (LOAD, FLASH), 120)
    if "sfrc=" in out and "sfrc=0" not in out:
        raise SystemExit("sf read failed: %s" % out[-300:])
    log("sf read: 32 MiB flash -> RAM @%08x in %.0f s (%s)" % (LOAD, time.time() - t,
        "rc=0" if "sfrc=0" in out else "no rc reported; content checks below decide"))
    m = re.search(r"==> ([0-9a-f]{8})", ub("crc32 %x %x" % (LOAD, FLASH), 15))
    if not m:
        raise SystemExit("no crc32 from U-Boot")
    crc_ub = int(m.group(1), 16)
    log("U-Boot crc32 of RAM image: %08x" % crc_ub)

    t = time.time()
    out = ub("tftpput %x %x %s" % (LOAD, FLASH, FNAME), 240, tail=14000)   # progress hashes are long
    m = re.search(r"Bytes transferred = (\d+)", out)
    if not m or int(m.group(1)) != FLASH:
        raise SystemExit("tftpput did not report %d bytes:\n%s" % (FLASH, out[-800:]))
    log("tftpput: %d bytes in %.0f s" % (FLASH, time.time() - t))

    # Verify on neon, then bring it home.
    remote = "~/lab/tftp/" + FNAME
    r = subprocess.run(["ssh", NEON, "python3 -c \"import zlib,sys; d=open('%s','rb').read(); "
                        "print(len(d), '%%08x' %% (zlib.crc32(d) & 0xffffffff))\"" % remote.replace("~", "$HOME")],
                       capture_output=True, text=True)
    size, crc_remote = r.stdout.split()
    if int(size) != FLASH or int(crc_remote, 16) != crc_ub:
        raise SystemExit("file on neon: size %s crc %s -- expected %d / %08x" % (size, crc_remote, FLASH, crc_ub))
    log("neon has the file: %s bytes, crc32 %s == U-Boot's. VERIFIED" % (size, crc_remote))

    local = os.path.join(OUT, "MH7021_%sspinor_32MB.bin" % (TAG + "_" if TAG else ""))
    parts_dir, sums = ("parts_" + TAG, "SHA256SUMS_" + TAG) if TAG else ("parts", "SHA256SUMS")
    subprocess.run(["scp", "-q", "%s:lab/tftp/%s" % (NEON, FNAME), local], check=True)
    full = open(local, "rb").read()
    assert len(full) == FLASH and (zlib.crc32(full) & 0xffffffff) == crc_ub
    # Content sanity: a stale RAM image would be self-consistent yet wrong.
    checks = [("SBL1 is an ELF (0x0)", full[:4] == b"\x7fELF"),
              ("U-Boot 2012.07 inside APPSBL (0xf0000)", b"U-Boot 2012.07" in full[0xf0000:0x170000]),
              ("bootcmd=bootipq in APPSBLENV (0xe0000)", b"bootcmd=bootipq" in full[0xe0000:0xf0000]),
              ("FIT image magic at Kernel (0x180000)", full[0x180000:0x180004] == b"\xd0\r\xfe\xed")]
    for name, ok in checks:
        log("  check %-42s %s" % (name, "ok" if ok else "FAIL"))
    if not all(ok for _, ok in checks):
        raise SystemExit("image content checks failed -- not trusting this dump")
    sha = hashlib.sha256(full).hexdigest()
    os.makedirs(os.path.join(OUT, parts_dir), exist_ok=True)
    with open(os.path.join(OUT, sums), "w") as f:
        f.write("%s  %s\n" % (sha, os.path.basename(local)))
        for name, s, e in PARTS:
            p = os.path.join(OUT, parts_dir, name + ".bin")
            with open(p, "wb") as pf:
                pf.write(full[s:e])
            f.write("%s  %s/%s.bin\n" % (hashlib.sha256(full[s:e]).hexdigest(), parts_dir, name))
    log("wrote %s  sha256 %s  (+ %d partition files in parts/)" % (local, sha, len(PARTS)))

    if full[:FLASH // 2] == full[FLASH // 2:]:
        log("WARNING: upper 16 MiB == lower 16 MiB -- looks like 3-byte address wrap; >16 MiB is suspect")
    else:
        log("upper/lower halves differ: full 32 MiB addressing OK")
    blank = sum(1 for o in range(0, FLASH, 0x10000) if full[o:o + 0x10000] == b"\xff" * 0x10000)
    log("blank 64 KiB sectors: %d of 512" % blank)
    log("DONE -- router still at the U-Boot prompt")


if __name__ == "__main__":
    main()
