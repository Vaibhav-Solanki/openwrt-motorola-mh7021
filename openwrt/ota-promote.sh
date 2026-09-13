#!/bin/bash
# Point an OTA channel at a published release. Run ON NEON after release.sh has published TAG and it has
# been tried on one unit (docs/OTA.md) -- this is the step that makes the fleet install it:
#     TAG=neon-mesh-v1.2.0 ~/lab/owrt/mh7021/ota-promote.sh              # channel "stable"
#     TAG=neon-mesh-v1.3.0-rc1 CHANNEL=testing ~/lab/owrt/mh7021/ota-promote.sh
#     CHANNEL=stable ~/lab/owrt/mh7021/ota-promote.sh --show             # what the channel says now
# Writes $PUBROOT/ota/$CHANNEL.json + .json.sig, signed with keys/key-build -- the same usign key units
# already trust in /etc/opkg/keys for image signatures. Refuses a release whose sha256sums.sig does not
# verify, whose images do not match sha256sums, or that is not under PUBROOT. Promoting an older tag does
# not downgrade anything (units only move forward); it only stops units that have not upgraded yet.
set -e -o pipefail
T=${T:-~/lab/owrt/openwrt-25.12}; K=~/lab/owrt/keys
PUBROOT=${PUBROOT:-/srv/neon-releases/neon-mesh}
PUBURL=${PUBURL:-https://ujjain.today/neon-mesh}
CHANNEL=${CHANNEL:-stable}
BOARD=motorola,mh7021
HOSTBIN=$T/staging_dir/host/bin; export LD_LIBRARY_PATH=$T/staging_dir/host/lib
die() { echo "!! $*" >&2; exit 1; }
[[ $CHANNEL =~ ^[A-Za-z0-9_-]+$ ]] || die "bad CHANNEL '$CHANNEL'"
M=$PUBROOT/ota/$CHANNEL.json

if [ "$1" = --show ]; then
	[ -s "$M" ] || die "no manifest for channel $CHANNEL ($M)"
	cat "$M"; $HOSTBIN/usign -V -q -m "$M" -x "$M.sig" -p $K/key-build.pub && echo "signature ok" || echo "SIGNATURE DOES NOT VERIFY"
	exit 0
fi

TAG=${TAG:?set TAG=neon-mesh-vX.Y.Z[-rcN]}
[[ $TAG =~ ^neon-mesh-v[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$ ]] || die "TAG '$TAG' is not neon-mesh-vX.Y.Z[-rcN] (units could not order it)"
REL=$PUBROOT/$TAG; D=$REL/targets/ipq40xx/generic
[ -s $D/sha256sums ] || die "$D/sha256sums missing -- publish $TAG with release.sh first"
[ -x $HOSTBIN/usign ] || die "no usign at $HOSTBIN (T=$T)"
$HOSTBIN/usign -V -q -m $D/sha256sums -x $D/sha256sums.sig -p $K/key-build.pub || die "$TAG: sha256sums.sig does not verify against keys/key-build.pub"

python3 - "$TAG" "$CHANNEL" "$BOARD" "$D" "$REL" "$M.new" <<'PY'
import hashlib, json, os, re, sys, time
tag, channel, board, d, rel, out = sys.argv[1:]
sums = {}
for line in open(os.path.join(d, "sha256sums")):
    h, name = line.split(None, 1)
    sums[name.strip().lstrip("*")] = h
images = {}
for profile in ("motorola_mh7021", "motorola_mh7021-ct"):
    names = [n for n in sums if re.fullmatch(r"openwrt-.*-%s-squashfs-sysupgrade\.bin" % re.escape(profile), n)]
    if len(names) != 1:
        sys.exit("!! %s: expected one %s sysupgrade image in sha256sums, found %d" % (tag, profile, len(names)))
    p = os.path.join(d, names[0])
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != sums[names[0]]:
        sys.exit("!! %s does not match sha256sums" % p)
    images[profile] = {"path": os.path.relpath(p, os.path.dirname(rel)), "sha256": h, "size": os.path.getsize(p)}
m = {"schema": 1, "channel": channel, "board": board, "release": tag, "published": int(time.time()),
     "notes": "%s/RELEASE.txt" % tag, "images": images}
json.dump(m, open(out, "w"), indent=1, sort_keys=True)
open(out, "a").write("\n")
for k, v in images.items():
    print("   %-20s %s  %d B  %s" % (k, v["sha256"][:16], v["size"], v["path"]))
PY

OLD=$(sed -n 's/.*"release": "\(.*\)".*/\1/p' "$M" 2>/dev/null || true)
rm -f "$M.new.sig"
$HOSTBIN/usign -S -m "$M.new" -s $K/key-build -x "$M.new.sig"
$HOSTBIN/usign -V -q -m "$M.new" -x "$M.new.sig" -p $K/key-build.pub || die "fresh manifest signature does not verify"
# signature first: a unit that reads in between sees the old manifest with the new signature, fails
# verification and simply tries again later -- it can never accept an unsigned manifest
mv "$M.new.sig" "$M.sig"; mv "$M.new" "$M"
echo "$(date -u +%FT%TZ) $CHANNEL ${OLD:-<none>} -> $TAG" >> $PUBROOT/ota/history.log
echo "== channel $CHANNEL: ${OLD:-<none>} -> $TAG"
code=$(curl -s -o /dev/null -w '%{http_code}' "$PUBURL/ota/$CHANNEL.json" || true)
[ "$code" = 200 ] && echo "== served: $PUBURL/ota/$CHANNEL.json" || echo "!! $PUBURL/ota/$CHANNEL.json answers HTTP $code -- is nginx/install.sh done?"
