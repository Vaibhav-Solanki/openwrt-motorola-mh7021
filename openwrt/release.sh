#!/bin/bash
# neon-mesh release build: patch set -> config.seed -> keys -> identity files -> signed images + feed
# -> verification -> publish. Run ON NEON (Docker, like build_mh7021.sh):
#     T=~/lab/owrt/openwrt-25.12 TAG=neon-mesh-v1.0.0-rc1 ~/lab/owrt/mh7021/release.sh
# Env: T (tree, default the 25.12 worktree), TAG (required), JOBS (12), FEED_URL (where the release
# dir will be served from), SKIP_BUILD=1 (verify + publish an existing build only).
# Keys live in ~/lab/owrt/keys (never in a tree, never in git) and are symlinked into the tree root:
# key-build(.pub/.ucert) = usign/ucert image + sha256sums signing, private/public-key.pem = apk feed
# index signing. If they are missing they are generated once, inside the container, then kept.
# The two *public* halves are also copied (not symlinked) into the overlay, because base-files copies
# them into the rootfs with `cp -fpR`, which would otherwise ship a symlink that dangles on the unit:
#   files/etc/apk/keys/public-key.pem      apk verifies our feed index with it
#   files/etc/opkg/keys/<usign fingerprint> `sysupgrade -T` verifies the embedded ucert with it
#   (apk builds of 25.12 install only the apk key; lib/upgrade/fwtool.sh still reads /etc/opkg/keys)
# Outputs: ~/lab/releases/neon-mesh/$TAG/ (mirrors the official downloads layout so the apk feed URLs
# look like OpenWrt's own) + the `latest` symlink. Log of the build itself: $T/build-$TAG.log
set -e -o pipefail
T=${T:-~/lab/owrt/openwrt-25.12}; T=$(cd $T && pwd)
TAG=${TAG:?set TAG=neon-mesh-vX.Y.Z[-rcN]}
S=~/lab/owrt/mh7021; K=~/lab/owrt/keys; RELROOT=~/lab/releases/neon-mesh; REL=$RELROOT/$TAG
# 8080 on neon belongs to the example.invalid container (127.0.0.1:8080) and 80 to the host nginx, so the
# release server gets 8081. The URL is baked into the image (neon.list), so changing it needs a rebuild.
FEED_URL=${FEED_URL:-http://FEED_HOST:8081/neon-mesh/$TAG}
JOBS=${JOBS:-12}; NAME=$(basename $T)
ARCH=arm_cortex-a7_neon-vfpv4; B=$T/bin/targets/ipq40xx/generic
log() { echo "== $(date '+%F %T') $*"; }
fail() { echo "!! $*" >&2; FAILED=1; }
# PATH matters as much as LD_LIBRARY_PATH here: ucert -V shells out to `usign` and reports only
# "cannot get signing key fingerprint" when it is not on PATH -- which reads exactly like a bad signature.
HOSTBIN=$T/staging_dir/host/bin; export PATH=$HOSTBIN:$PATH LD_LIBRARY_PATH=$T/staging_dir/host/lib

log "release $TAG from $T"
# ---------------------------------------------------------------- 1. patch set + config
T=$T $S/apply.sh
cp $S/config.seed $T/.config
# ---------------------------------------------------------------- 2. keys
# Secrets stay symlinks into keys/ (never copied into a tree). The public halves are real copies:
# apk resolves --keys-dir against the tree root, and base-files copies them into the rootfs verbatim,
# so a symlink here becomes an untrusted index inside a container without /keys, or a dangling
# /etc/apk/keys/public-key.pem on the unit.
mkdir -p $K; chmod 700 $K
for f in key-build key-build.ucert private-key.pem; do ln -sfn ../keys/$f $T/$f; done
for f in key-build.pub public-key.pem; do rm -f $T/$f; [ -s $K/$f ] && cp -f $K/$f $T/$f || ln -sfn ../keys/$f $T/$f; done
# ---------------------------------------------------------------- 3. identity files (into the overlay apply.sh just refreshed)
TREE_COMMIT=$(git -C $T rev-parse HEAD); TREE_DESC=$(git -C $T describe --tags --always 2>/dev/null)
ROUTER_COMMIT=$(cat $S/ROUTER_COMMIT 2>/dev/null || echo unknown)
mkdir -p $T/files/etc/apk/repositories.d
cat > $T/files/etc/neon-release <<EOT
NEON_RELEASE=$TAG
NEON_BUILD_DATE=$(date -u +%FT%TZ)
NEON_OPENWRT=$TREE_DESC
NEON_OPENWRT_COMMIT=$TREE_COMMIT
NEON_ROUTER_COMMIT=$ROUTER_COMMIT
NEON_PROFILES="motorola_mh7021 (ath10k mainline), motorola_mh7021-ct (ath10k-ct)"
NEON_FEED=$FEED_URL
NEON_USIGN_KEY=$($HOSTBIN/usign -F -p $K/key-build.pub 2>/dev/null || echo pending)
EOT
cat > $T/files/etc/apk/repositories.d/neon.list <<EOT
# neon-mesh release feed ($TAG): kmods with this image's vermagic + everything the release built.
# The official 25.12.5 feeds stay in distfeeds.list. Served by the nginx container on neon (RELEASE.md).
$FEED_URL/targets/ipq40xx/generic/packages/packages.adb
$FEED_URL/packages/$ARCH/base/packages.adb
$FEED_URL/packages/$ARCH/luci/packages.adb
$FEED_URL/packages/$ARCH/packages/packages.adb
EOT
cat $T/files/etc/neon-release
# `sysupgrade -T` verifies the embedded ucert against /etc/opkg/keys (lib/upgrade/fwtool.sh), but the
# apk build of base-files only installs the apk key, so the overlay has to carry the usign one.
FP=$($HOSTBIN/usign -F -p $K/key-build.pub 2>/dev/null || true)
[ -n "$FP" ] && install -D -m 0644 $K/key-build.pub $T/files/etc/opkg/keys/$FP
# ---------------------------------------------------------------- 4. build (Docker, unprivileged, shared dl/)
if [ -z "$SKIP_BUILD" ]; then
  log "build starts (log: $T/build-$TAG.log)"
  docker run --rm --name owrt-build-$NAME --user $(id -u):$(id -g) -e HOME=/src -e JOBS=$JOBS -e TAG=$TAG \
    -v $T:/src -v ~/lab/owrt/openwrt/dl:/src/dl -v $K:/keys -v ~/lab/owrt/openwrt/.git:$HOME/lab/owrt/openwrt/.git:ro \
    -w /src owrt-build bash -c '
    set -e -o pipefail
    export PATH=/src/staging_dir/host/bin:$PATH LD_LIBRARY_PATH=/src/staging_dir/host/lib
    [ -d feeds/packages ] || { echo "== feeds"; ./scripts/feeds update -a >/dev/null && ./scripts/feeds install -a >/dev/null; }
    make defconfig >/dev/null
    echo "== config check"; bad=
    for k in TARGET_ipq40xx_generic=y TARGET_MULTI_PROFILE=y TARGET_PER_DEVICE_ROOTFS=y \
             TARGET_DEVICE_ipq40xx_generic_DEVICE_motorola_mh7021=y TARGET_DEVICE_ipq40xx_generic_DEVICE_motorola_mh7021-ct=y \
             PACKAGE_ipq-wifi-motorola_mh7021=y PACKAGE_kmod-ath10k=m PACKAGE_kmod-ath10k-ct=m PACKAGE_ath10k-firmware-qca9888=m \
             PACKAGE_wpad-mbedtls=y PACKAGE_ucert=y PACKAGE_uboot-envtools=y PACKAGE_irqbalance=y PACKAGE_iperf3=y \
             PACKAGE_luci-theme-aurora=y \
             SIGNED_PACKAGES=y SIGNATURE_CHECK=y VERSION_NUMBER=\"25.12.5\" IB=y TARGET_ROOTFS_INITRAMFS=y JSON_OVERVIEW_IMAGE_INFO=y; do
      grep -q "^CONFIG_$k" .config && echo "   ok       CONFIG_$k" || { echo "   MISSING  CONFIG_$k"; bad=1; }
    done
    for k in PACKAGE_ppp PACKAGE_wpad-basic-mbedtls PACKAGE_wpad-mesh-mbedtls PACKAGE_kmod-usb3 PACKAGE_kmod-usb-dwc3-qcom PACKAGE_luci-proto-ppp PACKAGE_kmod-fs-ext4 PACKAGE_kmod-mmc; do
      grep -q "^CONFIG_$k=y" .config && { echo "   UNWANTED CONFIG_$k=y"; bad=1; } || echo "   ok       CONFIG_$k off"
    done
    [ -z "$bad" ] || { echo "config check failed"; exit 1; }
    if [ ! -s /keys/key-build ] || [ ! -s /keys/key-build.pub ] || [ ! -s /keys/key-build.ucert ]; then
      echo "== generating the usign build key + ucert (once)"
      make -j$JOBS tools/install toolchain/install >/dev/null 2>&1
      make -j$JOBS package/system/usign/host/install package/system/ucert/host/install >/dev/null 2>&1
      [ -s /keys/key-build ] || usign -G -s /keys/key-build -p /keys/key-build.pub -c "neon-mesh build key $(date +%F)"
      ucert -I -c /keys/key-build.ucert -p /keys/key-build.pub -s /keys/key-build
      chmod 600 /keys/key-build
    fi
    # On a first build the identity file was written before host tools and key-build existed.
    # Replace its harmless placeholder now, while it can still be included in the rootfs.
    [ -x /src/staging_dir/host/bin/usign ] || make -j$JOBS package/system/usign/host/install package/system/ucert/host/install >/dev/null 2>&1 || true
    if command -v usign >/dev/null 2>&1; then
      fp=$(usign -F -p /keys/key-build.pub 2>/dev/null || echo "")
      [ -n "$fp" ] && sed -i "s/^NEON_USIGN_KEY=.*/NEON_USIGN_KEY=$fp/" /src/files/etc/neon-release
      # image signature trust anchor for sysupgrade -T (see the note next to step 3)
      [ -n "$fp" ] && install -D -m 0644 /keys/key-build.pub /src/files/etc/opkg/keys/$fp
    fi
    if [ ! -s /keys/private-key.pem ]; then
      echo "== generating the apk feed key (once)"
      make -j$JOBS tools/install >/dev/null 2>&1
      openssl ecparam -name prime256v1 -genkey -noout -out /keys/private-key.pem
    fi
    # A copied-but-incomplete keys/ backup can have the secret without its derived public half.
    # Recreate that public half rather than failing later with an opaque overlay error.
    [ -s /keys/public-key.pem ] || openssl ec -in /keys/private-key.pem -pubout > /keys/public-key.pem
    chmod 600 /keys/private-key.pem
    # apk verifies our private release feed with this key on the target.  It must be in the
    # overlay *before* image assembly; keeping it under /keys avoids ever tracking it in router/.
    install -D -m 0644 /keys/public-key.pem /src/files/etc/apk/keys/public-key.pem
    # Real copies in the tree root: apk resolves --keys-dir /src through them when it verifies the
    # local package index, and base-files copies them into the rootfs with `cp -fpR` (symlink and all).
    for f in key-build.pub public-key.pem; do [ -L /src/$f ] && { rm -f /src/$f; cp /keys/$f /src/$f; }; done
    make package/firmware/ipq-wifi/clean >/dev/null 2>&1 || true   # force prepare, so the local board files are copied in
    echo "== make -j$JOBS"
    make -j$JOBS 2>&1 | tee build-$TAG.log | { grep --line-buffered -E "^ make\[[0-9]\] -C (target/linux|package/(base-files|kernel/(linux|mac80211|ath10k-ct)|network/services/hostapd|firmware/ipq-wifi|system/(apk|ucert))|target/imagebuilder) |WARNING|ERROR" || true; } \
      || { echo "== build FAILED; the error, single-threaded:"; make -j1 V=s 2>&1 | tail -150; exit 1; }
    '
  log "build finished"
fi
# ---------------------------------------------------------------- 5. verify
log "verify"
FAILED=
REL_IMG=$(ls $B/*-motorola_mh7021-squashfs-sysupgrade.bin 2>/dev/null | head -1)
CT_IMG=$(ls $B/*-motorola_mh7021-ct-squashfs-sysupgrade.bin 2>/dev/null | head -1)
REL_RAM=$(ls $B/*-motorola_mh7021-initramfs-zImage.itb 2>/dev/null | head -1)
CT_RAM=$(ls $B/*-motorola_mh7021-ct-initramfs-zImage.itb 2>/dev/null | head -1)
for f in "$REL_IMG" "$CT_IMG" "$REL_RAM" "$CT_RAM"; do [ -s "$f" ] && echo "   image   $(basename $f) $(stat -c%s $f) B" || fail "image missing: $f"; done
for img in "$REL_IMG" "$CT_IMG"; do
  [ -s "$img" ] || continue
  sz=$(python3 -c "import struct,sys; d=open(sys.argv[1],'rb').read(8); m,s=struct.unpack('>II',d); print(s if m==0xd00dfeed else -1)" "$img")
  if [ "$sz" -le 0 ]; then fail "$(basename $img): no FIT header"; elif [ "$sz" -gt $((0x500000)) ]; then fail "$(basename $img): FIT $sz B > 0x500000 (bootcmd read size)"; elif [ "$sz" -gt $((0x400000)) ]; then echo "   WARN    $(basename $img): FIT $sz B > 0x400000 (KERNEL_SIZE)"; else echo "   fit     $(basename $img): $sz B (limit 0x500000)"; fi
done
# Package sets are per device, but under TARGET_MULTI_PROFILE the manifest is not: PROFILE_SANITIZED
# is empty (include/image.mk:343), so the build emits one shared *-generic.manifest and no per-device
# ones. The real per-device package lists are the rootfs staging dirs, whose names are a hash of the
# package set -- so identify them by the driver they carry rather than by name.
DEV_REL= DEV_CT=
for d in $T/build_dir/target-*/linux-ipq40xx_generic/target-dir-*; do
  [ -d "$d/lib/apk/packages" ] || continue
  [ -e "$d/lib/apk/packages/kmod-ath10k.list" ] && DEV_REL=$d
  [ -e "$d/lib/apk/packages/kmod-ath10k-ct.list" ] && DEV_CT=$d
done
chk_pkgs() {	# chk_pkgs <label> <rootfs dir> <must-have...> -- <must-not...>
  local label=$1 d=$2; shift 2; local mode=have
  [ -n "$d" ] && [ -d "$d/lib/apk/packages" ] || { fail "$label: per-device rootfs not found (build_dir/.../target-dir-*)"; return; }
  for p in "$@"; do
    [ "$p" = "--" ] && { mode=not; continue; }
    if [ $mode = have ]; then [ -e "$d/lib/apk/packages/$p.list" ] && echo "   has     $label: $p" || fail "$label: missing package $p"
    else [ -e "$d/lib/apk/packages/$p.list" ] && fail "$label: unwanted package $p" || echo "   no      $label: $p"; fi
  done
}
chk_pkgs release "$DEV_REL" kmod-ath10k ath10k-firmware-qca4019 ath10k-firmware-qca9888 ipq-wifi-motorola_mh7021 wpad-mbedtls ucert uboot-envtools irqbalance iperf3 sqm-scripts luci-mod-admin-full luci-app-package-manager luci-theme-aurora -- kmod-ath10k-ct ath10k-firmware-qca4019-ct ath10k-firmware-qca9888-ct-full-htt ppp ppp-mod-pppoe luci-proto-ppp kmod-usb3 kmod-usb-dwc3-qcom kmod-fs-ext4 kmod-mmc wpad-basic-mbedtls wpad-mesh-mbedtls
chk_pkgs ct "$DEV_CT" kmod-ath10k-ct ath10k-firmware-qca4019-ct ath10k-firmware-qca9888-ct-full-htt ipq-wifi-motorola_mh7021 wpad-mbedtls ucert -- kmod-ath10k ath10k-firmware-qca4019 ath10k-firmware-qca9888 ppp kmod-usb3
# board files: every per-device rootfs (target-dir-*) and the shared one must carry the vendor data
for d in $T/build_dir/target-*/root-ipq40xx $T/build_dir/target-*/linux-ipq40xx_generic/target-dir-*; do
  [ -d "$d" ] || continue
  a=$(stat -c%s $d/lib/firmware/ath10k/QCA4019/hw1.0/board-2.bin 2>/dev/null); b=$(stat -c%s $d/lib/firmware/ath10k/QCA9888/hw2.0/board-2.bin 2>/dev/null)
  [ "$a" = 24468 ] && [ "$b" = 12244 ] && echo "   boards  $(basename $d): 24468 / 12244 (vendor data)" || fail "$(basename $d): board files $a / $b (want 24468 / 12244)"
done
R=$(ls -d $T/build_dir/target-*/root-ipq40xx 2>/dev/null | head -1)
RD=$DEV_REL	# release profile's rootfs; files added per device live only here, not in the shared root
# -e alone is wrong inside a staged rootfs: alternatives land as absolute symlinks (/usr/bin/scp ->
# /usr/sbin/dropbear) that resolve on the unit but not against the build host's /, so accept -L too.
has_path() { [ -n "$1" ] && [ -n "$2" ] && { [ -e "$1/$2" ] || [ -L "$1/$2" ]; }; }	# <rootfs> <path>; empty rootfs = no
for f in etc/opkg/keys/$($HOSTBIN/usign -F -p $K/key-build.pub) etc/apk/keys/public-key.pem usr/bin/ucert usr/bin/usign usr/bin/fwtool usr/bin/scp usr/sbin/fw_setenv usr/sbin/neon-role usr/sbin/neon-watchdog usr/sbin/neon-led etc/init.d/neon-watchdog etc/uci-defaults/50-neon-mesh lib/upgrade/keep.d/neon-mesh etc/neon-release etc/apk/repositories.d/neon.list etc/uci-defaults/30_uboot-envtools; do
  if has_path "$R" "$f" || has_path "$RD" "$f"; then
    echo "   rootfs  /$f"
  else
    fail "rootfs lacks /$f"
  fi
done
(cmp -s "$R/etc/apk/keys/public-key.pem" "$K/public-key.pem" || ([ -n "$RD" ] && cmp -s "$RD/etc/apk/keys/public-key.pem" "$K/public-key.pem")) && echo "   rootfs  /etc/apk/keys/public-key.pem = keys/public-key.pem" || fail "apk public key in rootfs differs from keys/"
(grep -q "motorola,mh7021" "$R/etc/uci-defaults/30_uboot-envtools" 2>/dev/null || ([ -n "$RD" ] && grep -q "motorola,mh7021" "$RD/etc/uci-defaults/30_uboot-envtools" 2>/dev/null)) && echo "   rootfs  uboot-envtools knows motorola,mh7021" || fail "uboot-envtools config lacks motorola,mh7021"
# signatures: embedded ucert in every sysupgrade image, detached usign on sha256sums
for img in "$REL_IMG" "$CT_IMG"; do
  [ -s "$img" ] || continue
  if $HOSTBIN/fwtool -q -s /tmp/rel.ucert "$img" 2>/dev/null; then
    $HOSTBIN/fwtool -q -T -s /dev/null "$img" | $HOSTBIN/ucert -V -q -m - -c /tmp/rel.ucert -p $K/key-build.pub && echo "   signed  $(basename $img): ucert chain verifies against keys/key-build.pub" || fail "$(basename $img): ucert verification FAILED"
  else fail "$(basename $img): no embedded signature (was key-build.ucert in the tree root?)"; fi
done
rm -f /tmp/rel.ucert
# Every image build rewrites sha256sums, so its detached signature has to be made here, unconditionally:
# a .sig left over from the previous build is a valid signature of a file that no longer exists.
if [ -s $B/sha256sums ]; then
  rm -f $B/sha256sums.sig
  $HOSTBIN/usign -S -m $B/sha256sums -s $K/key-build -x $B/sha256sums.sig || fail "signing sha256sums failed"
else fail "sha256sums missing (image build did not finish?)"; fi
$HOSTBIN/usign -V -q -m $B/sha256sums -p $K/key-build.pub -x $B/sha256sums.sig && echo "   signed  sha256sums.sig verifies" || fail "sha256sums.sig does not verify"
[ -s $T/bin/packages/$ARCH/base/packages.adb ] && echo "   feed    bin/packages/$ARCH/*/packages.adb + targets/.../packages/packages.adb present" || fail "package index missing"
ls $B/*imagebuilder* >/dev/null 2>&1 && echo "   ib      $(basename $(ls $B/*imagebuilder* | head -1))" || echo "   WARN    no ImageBuilder tarball"
[ -z "$FAILED" ] || { log "VERIFY FAILED -- not publishing"; exit 1; }
# ---------------------------------------------------------------- 6. publish (official downloads layout)
log "publish -> $REL"
mkdir -p $REL/targets/ipq40xx/generic $REL/packages
rsync -a --delete $B/ $REL/targets/ipq40xx/generic/
rsync -a --delete $T/bin/packages/$ARCH/ $REL/packages/$ARCH/
cp $T/.config $REL/config.full; cp $S/config.seed $REL/config.seed
{
  echo "neon-mesh release $TAG"; echo "built $(date '+%F %T %Z') on $(hostname) from $T"
  echo "openwrt $TREE_DESC ($TREE_COMMIT), router repo $ROUTER_COMMIT"
  echo "profiles: motorola_mh7021 (ath10k mainline, the release) / motorola_mh7021-ct (ath10k-ct fallback)"
  echo "feed: $FEED_URL"; echo "usign key: $($HOSTBIN/usign -F -p $K/key-build.pub)  apk key sha256: $(sha256sum $K/public-key.pem | cut -c1-16)"
  echo; echo "sha256:"; (cd $B && sha256sum *motorola_mh7021*)
} > $REL/RELEASE.txt
ln -sfn $TAG $RELROOT/latest
cat $REL/RELEASE.txt
du -sh $REL
docker ps --format '{{.Names}}' | grep -q '^neon-releases$' && log "served at $FEED_URL/" || \
  log "release server not running -- start it once (user): docker run -d --restart unless-stopped --name neon-releases -p 8081:80 -v $HOME/lab/releases:/usr/share/nginx/html:ro nginx:alpine"
log "done"
