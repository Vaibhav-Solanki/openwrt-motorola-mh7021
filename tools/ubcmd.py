#!/usr/bin/env python3
"""Run U-Boot commands through the ESP bridge: transmit via HTTP /send, read the
reply from the /log ring (reliable, unlike live-stream timing). Not for md dumps
(output > 16 KB ring); fine for control/status commands."""
import re, sys, time, urllib.error, urllib.parse, urllib.request

HOST = "192.168.1.4"
PROMPT = "(IPQ40xx) #"

def _get(path, t=12, tries=8):
    # The bridge's HTTP occasionally stops accepting connections for 10-20 s
    # (WiFi at -62 dBm); a dump must ride that out rather than abort.
    for i in range(tries):
        try:
            return urllib.request.urlopen("http://%s%s" % (HOST, path), timeout=t).read()
        except Exception:                 # timeouts, resets, short bodies -- all worth a retry
            if i == tries - 1:
                raise
            time.sleep(3)

def send(cmd):
    _get("/send?s=" + urllib.parse.quote(cmd + "\r"))

def run(cmd, wait=2.0, tail=8000):
    # Unique marker after the command isolates this invocation's output in the ring.
    mark = "Z%dZ" % (int(time.time() * 1000) % 100000)
    send(cmd + "; echo " + mark)
    deadline = time.time() + wait + 8
    while time.time() < deadline:
        time.sleep(min(wait, 1.5)); wait = 0.5
        log = _get("/log?tail=%d" % tail).decode("latin1")   # small reads survive low heap
        # find the echoed 'echo MARK' command, then the MARK line it prints
        ce = log.rfind("echo " + mark)
        if ce < 0:
            continue
        mo = log.find("\n" + mark, ce)   # the marker on its own output line
        if mo < 0:
            continue
        # output sits between the end of the echoed command line and the marker
        start = log.find("\n", ce) + 1
        return log[start:mo].replace("\r", "").strip()
    return "<no marker; last 400 of ring>\n" + log[-400:].replace("\r", "")

if __name__ == "__main__":
    print(run(" ".join(sys.argv[1:]) or "help"))
