#!/usr/bin/env python3
"""
Install / update the status-light driver (owrt/mh7021/files/: neon-led daemon,
init script, /etc/config/neon_led, /etc/diag.sh) on running units over ssh,
then start it and print what each light shows and why.

    python3 router/led_deploy.py                      # all three: 192.168.0.1 .2 .3
    python3 router/led_deploy.py 192.168.0.2          # one unit
    python3 router/led_deploy.py --status             # only print `neon-led status`

Uses the root password (env NEON_ROOTPW, else prompted) through expect: macOS has no
sshpass. Files go over as printf'ed text in <9 KB chunks because dropbear caps a
command at 9000 bytes and the busybox on the units has neither scp nor base64.
The same files are also in the OpenWrt tree's files/ overlay (apply.sh), so a
rebuilt image carries them; this script is for units already in service.
"""
import os, subprocess, sys, tempfile, getpass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "owrt", "mh7021", "files")
UNITS = ["192.168.0.1", "192.168.0.2", "192.168.0.3"]
PASSWORD = os.environ.get("NEON_ROOTPW") or getpass.getpass("root password for the units: ")
FILES = {  # dest: (src under files/, mode)
    "/usr/sbin/neon-led":   ("usr/sbin/neon-led", "755"),
    "/etc/init.d/neon-led": ("etc/init.d/neon-led", "755"),
    "/etc/config/neon_led": ("etc/config/neon_led", "644"),
    "/etc/diag.sh":         ("etc/diag.sh", "755"),
}
# NB: expect needs the pattern/action pairs on separate lines; on one line the
# whole brace block is taken as a single glob pattern and nothing is ever sent.
EXPECT = r'''
set timeout 60
set ip [lindex $argv 0]
set cmd [lindex $argv 1]
log_user 0
spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 root@$ip $cmd
expect {
  -re "(?i)password:" { send "%s\r" }
  eof { exit 2 }
  timeout { exit 3 }
}
log_user 1
expect {
  eof {}
  timeout { exit 3 }
}
catch wait result
exit [lindex $result 3]
''' % PASSWORD


def rsh(ip, cmd):
    r = subprocess.run(["expect", expect_file, ip, cmd], capture_output=True, text=True)
    if r.returncode:
        sys.exit("!! %s rc=%d: %s" % (ip, r.returncode, r.stdout[-300:].strip()))
    return r.stdout


def deploy(ip):
    for dest, (src, mode) in FILES.items():
        data = open(os.path.join(SRC, src)).read()
        rsh(ip, "rm -f /tmp/nl.part")
        for i in range(0, len(data), 6000):
            rsh(ip, "printf '%%s' '%s' >> /tmp/nl.part" % data[i:i + 6000].replace("'", "'\\''"))
        got = rsh(ip, "wc -c < /tmp/nl.part && mv /tmp/nl.part %s && chmod %s %s" % (dest, mode, dest)).strip()
        if got != str(len(data)):
            sys.exit("!! %s: %s is %s bytes on the unit, expected %d" % (ip, dest, got, len(data)))
    rsh(ip, "/etc/init.d/neon-led enable; /etc/init.d/neon-led restart 2>/dev/null; sleep 6")


with tempfile.NamedTemporaryFile("w", suffix=".exp", delete=False) as f:
    f.write(EXPECT); expect_file = f.name
try:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for ip in args or UNITS:
        if "--status" not in sys.argv:
            deploy(ip)
        print("%s %s: %s" % (ip, rsh(ip, "cat /proc/sys/kernel/hostname").strip(),
                             rsh(ip, "neon-led status").strip().replace("\n", "\n" + " " * 26)))
finally:
    os.unlink(expect_file)
