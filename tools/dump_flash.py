#!/usr/bin/env python3
"""
Pull the router's 32 MB SPI-NOR out through the ESP UART bridge, from U-Boot.

Method: `sf read` copies flash -> RAM at LOAD once, then the RAM image is walked
with `md.l` in chunks and parsed back into bytes. Every chunk is checked against
U-Boot's own `crc32` of the same RAM range before it is accepted; a dropped WiFi
packet shows up as a missing line (re-requested) or a CRC mismatch (chunk
re-dumped). Verified chunks are kept in router/chunks/ so the run is resumable.

Speed: md.l costs ~4.25 serial chars per byte, so 32 MB at 115200 8N1 takes
about 3.5 h. The console UART clock is 1.8432 MHz, so 115200 is its ceiling.

Usage: ./dump_flash.py [--chunk 0x80000] [--no-boot]
       (run from the project root; writes into router/)
"""
import argparse, hashlib, os, re, socket, struct, sys, time, urllib.request, zlib

HOST, PORT = "192.168.1.4", 23
PROMPT = b"(IPQ40xx) #"
LOAD   = 0x84000000      # RAM scratch; bootipq loads the FIT image here, so it is free
FLASH  = 0x2000000       # W25Q256: 32 MiB
OUT    = "router"
RAWLOG = os.path.join(OUT, "console_raw.log")

# From the kernel's MTD table (router_boot_log). "user" overlaps "Kernel"
# (rootfs inside the FIT region) and is split out separately; 0x1bf0000-0x1d00000
# is unmapped but the full image covers it anyway.
PARTS = [("0_SBL1", 0x000000, 0x040000), ("0_MIBIB", 0x040000, 0x060000),
         ("0_QSEE", 0x060000, 0x0c0000), ("0_CDT", 0x0c0000, 0x0d0000),
         ("0_DDRPARAMS", 0x0d0000, 0x0e0000), ("0_APPSBLENV", 0x0e0000, 0x0f0000),
         ("0_APPSBL", 0x0f0000, 0x170000), ("0_ART", 0x170000, 0x180000),
         ("Kernel", 0x180000, 0x1bf0000), ("user", 0x620000, 0x1bf0000),
         ("cli_factory_ext", 0x1d00000, 0x1f00000), ("storage", 0x1f00000, 0x2000000)]

LINE = re.compile(rb"^([0-9a-f]{8}): ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})", re.M)
CRC  = re.compile(rb"==> ([0-9a-f]{8})")


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


class Console:
    """Raw TCP to the bridge; the router echoes, we never echo."""
    def __init__(self):
        self.s = socket.create_connection((HOST, PORT), timeout=10)
        self.s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.raw = open(RAWLOG, "ab")

    def _recv(self, timeout):
        self.s.settimeout(timeout)
        try:
            d = self.s.recv(65536)
        except socket.timeout:
            return b""
        if not d:
            raise ConnectionError("bridge closed the connection")
        self.raw.write(d)
        return d

    def drain(self, secs):
        end = time.time() + secs
        while time.time() < end:
            self._recv(0.5)

    def read_until_prompt(self, timeout):
        """Everything up to a prompt that sits at the start of a line and is the
        last thing received. The line-start test matters: U-Boot's own binary is
        in the flash image, so the prompt string also scrolls past inside md's
        ASCII column."""
        buf, end = b"", time.time() + timeout
        while time.time() < end:
            d = self._recv(1.0)
            if not d:
                continue
            buf += d
            i = buf.rfind(PROMPT)
            if i >= 0 and (i == 0 or buf[i - 1:i] == b"\n") \
                    and buf[i + len(PROMPT):].strip(b" \r\n") == b"":
                return buf[:i]
        raise TimeoutError("no prompt within %ds" % timeout)

    def sync(self, timeout=420):
        # Drain the 16 KB backlog replay (which may include an old prompt), then
        # ask for a fresh one. If a previous md is still scrolling, wait it out.
        self.drain(2.5)
        self.s.sendall(b"\r")
        self.read_until_prompt(timeout)

    def cmd(self, c, timeout):
        self.s.sendall(c.encode() + b"\r")
        return self.read_until_prompt(timeout)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def parse(out):
    d = {}
    for m in LINE.finditer(out):
        d[int(m.group(1), 16)] = b"".join(struct.pack("<I", int(m.group(k), 16)) for k in range(2, 6))
    return d


def runs(addrs):
    """Group sorted 16-byte-aligned addresses into (start, nlines) runs."""
    out, start, n = [], None, 0
    for a in addrs:
        if start is not None and a == start + n * 16:
            n += 1
        else:
            if start is not None:
                out.append((start, n))
            start, n = a, 1
    if start is not None:
        out.append((start, n))
    return out


def md_timeout(nbytes):
    return int(nbytes * 4.4 / 11520) + 90


def uboot_crc(con, addr, size):
    out = con.cmd("crc32 %x %x" % (addr, size), 120)
    m = CRC.search(out)
    if not m:
        raise RuntimeError("crc32 gave no result: %r" % out[-200:])
    return int(m.group(1), 16)


def dump_chunk(con, addr, size, tries=3):
    for t in range(1, tries + 1):
        lines = parse(con.cmd("md.l %x %x" % (addr, size // 4), md_timeout(size)))
        missing = [a for a in range(addr, addr + size, 16) if a not in lines]
        if missing:
            log("  %d lines missing, re-requesting %d run(s)" % (len(missing), len(runs(missing))))
            for a0, n in runs(missing):
                lines.update(parse(con.cmd("md.l %x %x" % (a0, n * 4), md_timeout(n * 16))))
            missing = [a for a in range(addr, addr + size, 16) if a not in lines]
            if missing:
                log("  still %d missing after refill, retry %d" % (len(missing), t))
                continue
        data = b"".join(lines[a] for a in range(addr, addr + size, 16))
        ub, local = uboot_crc(con, addr, size), zlib.crc32(data) & 0xffffffff
        if ub == local:
            return data
        log("  crc mismatch (uboot %08x, local %08x), retry %d" % (ub, local, t))
    raise RuntimeError("chunk %x failed %d times" % (addr, tries))


def connect():
    for attempt in range(1, 31):
        try:
            con = Console()
            con.sync()
            return con
        except (OSError, TimeoutError) as e:
            log("connect/sync failed (%s), retry %d in 10 s" % (e, attempt))
            time.sleep(10)
    raise RuntimeError("bridge unreachable")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=lambda x: int(x, 0), default=0x80000)
    ap.add_argument("--no-boot", action="store_true", help="leave the router at the U-Boot prompt when done")
    a = ap.parse_args()
    chunk, n = a.chunk, FLASH // a.chunk
    os.makedirs(os.path.join(OUT, "chunks"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "parts"), exist_ok=True)

    con = connect()
    out = con.cmd("sf probe 0", 30)
    if b"W25Q256" not in out:
        raise SystemExit("sf probe did not find the W25Q256: %r" % out[-300:])
    log("sf probe: W25Q256 detected")
    t0 = time.time()
    con.cmd("sf read %x 0 %x" % (LOAD, FLASH), 300)
    log("sf read 32 MiB -> RAM at %08x in %.0f s" % (LOAD, time.time() - t0))

    done = sum(1 for i in range(n) if os.path.getsize(os.path.join(OUT, "chunks", "%03d.bin" % i)) == chunk
               if os.path.exists(os.path.join(OUT, "chunks", "%03d.bin" % i)))
    log("%d chunks of %d KiB, %d already verified" % (n, chunk // 1024, done))
    times = []
    for i in range(n):
        path = os.path.join(OUT, "chunks", "%03d.bin" % i)
        if os.path.exists(path) and os.path.getsize(path) == chunk:
            continue
        addr = LOAD + i * chunk
        while True:
            t1 = time.time()
            try:
                data = dump_chunk(con, addr, chunk)
                break
            except (OSError, TimeoutError, ConnectionError) as e:
                log("  link problem on chunk %d (%s); reconnecting" % (i, e))
                con.close()
                con = connect()
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        times.append(time.time() - t1)
        left = sum(1 for j in range(i + 1, n) if not os.path.exists(os.path.join(OUT, "chunks", "%03d.bin" % j)))
        eta = left * (sum(times) / len(times))
        log("chunk %2d/%d  flash %07x-%07x  ok in %3.0f s   ETA %dh%02dm"
            % (i + 1, n, i * chunk, (i + 1) * chunk - 1, times[-1], eta // 3600, (eta % 3600) // 60))

    full = b"".join(open(os.path.join(OUT, "chunks", "%03d.bin" % i), "rb").read() for i in range(n))
    assert len(full) == FLASH
    ub, local = uboot_crc(con, LOAD, FLASH), zlib.crc32(full) & 0xffffffff
    log("whole image crc32: uboot %08x local %08x  %s" % (ub, local, "MATCH" if ub == local else "MISMATCH"))
    if ub != local:
        raise SystemExit("full-image CRC mismatch; not writing the image")

    img = os.path.join(OUT, "MH7021_spinor_32MB.bin")
    with open(img, "wb") as f:
        f.write(full)
    sha = hashlib.sha256(full).hexdigest()
    with open(os.path.join(OUT, "SHA256SUMS"), "w") as f:
        f.write("%s  %s\n" % (sha, os.path.basename(img)))
        for name, s, e in PARTS:
            p = os.path.join(OUT, "parts", name + ".bin")
            with open(p, "wb") as pf:
                pf.write(full[s:e])
            f.write("%s  parts/%s.bin\n" % (hashlib.sha256(full[s:e]).hexdigest(), name))
    log("wrote %s  sha256 %s" % (img, sha))
    # A W25Q256 needs 4-byte addressing above 16 MiB; if the driver silently
    # wrapped, the upper half would be a copy of the lower half.
    if full[:FLASH // 2] == full[FLASH // 2:]:
        log("WARNING: upper 16 MiB is byte-identical to the lower 16 MiB -- looks like address wrap, treat >16 MiB as suspect")
    else:
        log("upper/lower 16 MiB differ: 4-byte addressing looks fine")
    log("erased-looking (all 0xFF) 64 KiB sectors: %d of 512"
        % sum(1 for o in range(0, FLASH, 0x10000) if full[o:o + 0x10000] == b"\xff" * 0x10000))

    if not a.no_boot:
        try:
            urllib.request.urlopen("http://%s/uboot?arm=0" % HOST, timeout=5).read()
        except Exception as e:
            log("could not disarm trap: %s" % e)
        con.s.sendall(b"boot\r")
        log("sent 'boot' -- router resuming normal startup; trap disarmed")
    con.close()
    log("DONE")


if __name__ == "__main__":
    main()
