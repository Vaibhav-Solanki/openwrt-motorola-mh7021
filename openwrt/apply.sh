#!/bin/bash
# Install Motorola MH7021 support into an OpenWrt tree. Run ON NEON, idempotent: the tracked files it
# patches are first restored from git and then patched, so re-running after a snippet change is safe.
#     ~/lab/owrt/mh7021/apply.sh                              # tree ~/lab/owrt/openwrt (main, bring-up)
#     T=~/lab/owrt/openwrt-25.12 ~/lab/owrt/mh7021/apply.sh   # release worktree (openwrt-25.12)
# Then build with  T=... TAG=neon-mesh-vX.Y.Z ~/lab/owrt/mh7021/release.sh   (release, signed) or the
# old build_mh7021.sh (bring-up only).
set -e
T=${T:-~/lab/owrt/openwrt}; S=~/lab/owrt/mh7021; W=~/lab/owrt/ipq-wifi
export T S
# main keeps DTS files in target/linux/ipq40xx/dts/, release branches in files-<kver>/arch/arm/boot/dts/qcom/
DTSDIR=$T/target/linux/ipq40xx/dts; [ -d $DTSDIR ] || DTSDIR=$(ls -d $T/target/linux/ipq40xx/files-*/arch/arm/boot/dts/qcom | head -1)
# uboot-envtools board configs moved under uboot-tools/ in 2025; support both layouts
ENVF=$(ls $T/package/boot/uboot-tools/uboot-envtools/files/ipq40xx $T/package/boot/uboot-envtools/files/ipq40xx 2>/dev/null | head -1)
export ENVF

# 0. tracked files we patch start from pristine (so the patch set is exactly what this script says)
for f in target/linux/ipq40xx/image/generic.mk package/firmware/ipq-wifi/Makefile \
         target/linux/ipq40xx/base-files/etc/board.d/02_network ${ENVF#$T/}; do
  [ -n "$f" ] && [ -e "$T/$f" ] && git -C "$T" checkout -q -- "$f" 2>/dev/null || true
done

# 1. DTS, device stanzas, board files
cp $S/qcom-ipq4019-mh7021.dts $DTSDIR/
grep -q "Device/motorola_mh7021" $T/target/linux/ipq40xx/image/generic.mk || \
  cat $S/generic.mk.snippet >> $T/target/linux/ipq40xx/image/generic.mk
cp $W/board-motorola_mh7021.qca4019 $W/board-motorola_mh7021.qca9888 $T/package/firmware/ipq-wifi/

# 2. rootfs overlay (files/ is entirely ours: status light, neon-role, neon-watchdog, uci-defaults, keep.d;
#    release.sh adds the generated /etc/neon-release and /etc/apk/repositories.d/neon.list afterwards)
rm -rf $T/files; mkdir -p $T/files && cp -R $S/files/. $T/files/
find $T/files -type f \( -path '*/usr/sbin/*' -o -path '*/etc/init.d/*' -o -path '*/etc/uci-defaults/*' -o -name diag.sh \) -exec chmod 755 {} +

# 3. third-party packages: luci-theme-aurora, vendored at a pinned commit (luci-theme-aurora/VENDORED.md).
#    Dropped straight into package/ -- OpenWrt scans that tree recursively -- rather than added as a feed,
#    so the build needs no extra feed config and no network. config.seed selects it; the theme's own
#    uci-defaults (30_luci-theme-aurora) makes it the default skin on a unit that has not chosen one.
rm -rf $T/package/luci-theme-aurora
cp -R $S/luci-theme-aurora $T/package/luci-theme-aurora

python3 - <<'PY'
import os, re
T = os.environ["T"]; ENVF = os.environ.get("ENVF", "")

# --- package/firmware/ipq-wifi/Makefile: (a) Build/Prepare override so the local board-* files are
#     copied into PKG_BUILD_DIR (the install rule only looks there; without this the image ships the
#     generic qca-wireless board data), (b) ALLWIFIBOARDS (alphabetical), (c) the package eval line
p = T + "/package/firmware/ipq-wifi/Makefile"; s = open(p).read()
if "$(CP) ./board-* $(PKG_BUILD_DIR)/" not in s:
    a = "define Build/Compile\n"; assert a in s, "Build/Compile anchor moved"
    s = s.replace(a, "# MH7021: local board files. The board data normally comes from the qca-wireless\n"
                     "# checkout in PKG_BUILD_DIR and the install rule only looks there, so files dropped\n"
                     "# into this directory are copied in after the default prepare. (Upstream them once\n"
                     "# the port is proven.)\ndefine Build/Prepare\n\t$(call Build/Prepare/Default)\n"
                     "\t$(CP) ./board-* $(PKG_BUILD_DIR)/\nendef\n\n" + a, 1)
if "motorola_mh7021" not in s:
    lines = s.split("\n"); i = next(i for i, l in enumerate(lines) if l.startswith("ALLWIFIBOARDS:="))
    j = i + 1; inserted = False
    while j < len(lines):
        m = re.match(r"\t([\w.-]+) \\$", lines[j])
        if not m:
            break
        if m.group(1) > "motorola_mh7021":
            lines.insert(j, "\tmotorola_mh7021 \\"); inserted = True; break
        j += 1
    assert inserted, "could not place motorola_mh7021 in ALLWIFIBOARDS"
    s = "\n".join(lines)
    k = s.rfind("$(eval $(call generate-ipq-wifi-package,"); e = s.find("\n", k) + 1
    s = s[:e] + "$(eval $(call generate-ipq-wifi-package,motorola_mh7021,Motorola MH7021))\n" + s[e:]
open(p, "w").write(s)

# --- 02_network: two jacks, join the "lan"/"wan" group that gl-b2200 is in
p = T + "/target/linux/ipq40xx/base-files/etc/board.d/02_network"; s = open(p).read()
if "motorola,mh7021" not in s:
    a = "\tglinet,gl-b2200|\\\n"; assert a in s
    s = s.replace(a, a + "\tmotorola,mh7021|\\\n", 1); open(p, "w").write(s)
# --- 02_network ipq40xx_setup_macs: wan gets ART 0x0 (vendor wanaddr), label = ART 0x6 (ethaddr;
#     the DTS already feeds it to the gmac, so lan/br-lan inherit it)
s = open(p).read()
if "motorola,mh7021)" not in s:
    a = "\tnetgear,rbr40|\\\n"; assert a in s, "setup_macs anchor moved"
    s = s.replace(a, "\tmotorola,mh7021)\n\t\twan_mac=$(mtd_get_mac_binary ART 0x0)\n\t\tlabel_mac=$(mtd_get_mac_binary ART 0x6)\n\t\t;;\n" + a, 1)
    open(p, "w").write(s)

# --- uboot-envtools: APPSBLENV is /dev/mtd5, a single 64 KiB copy without a flags byte (CRC then data,
#     verified with fw_printenv -c on a live unit). Same geometry as the gl-b1300/gl-b2200 group, so
#     join it (sorted position inside the group).
if ENVF and os.path.exists(ENVF):
    s = open(ENVF).read()
    if "motorola,mh7021" not in s:
        lines = s.split("\n"); i = next(i for i, l in enumerate(lines) if l.strip() == "glinet,gl-b2200|\\")
        j = i + 1; inserted = False
        while j < len(lines):
            m = re.match(r"([\w.,-]+)(\|\\|\))$", lines[j].strip())
            if not m:
                break
            if m.group(1) > "motorola,mh7021" or m.group(2) == ")":
                lines.insert(j, "motorola,mh7021|\\"); inserted = True; break
            j += 1
        assert inserted, "could not place motorola,mh7021 in the uboot-envtools ipq40xx case"
        open(ENVF, "w").write("\n".join(lines))

# --- 01_leds: nothing to add -- the only LEDs are the RGB status light, driven by the
#     led-boot/failsafe/running/upgrade aliases in the DTS
print("patched")
PY

echo "=== verify ==="
echo "device stanzas (expect 2): $(grep -c '^define Device/motorola_mh7021' $T/target/linux/ipq40xx/image/generic.mk)"
echo "ipq-wifi Build/Prepare override (expect 1): $(grep -c 'CP) ./board-\* \$(PKG_BUILD_DIR)' $T/package/firmware/ipq-wifi/Makefile)"
grep -n "motorola" $T/package/firmware/ipq-wifi/Makefile | sed 's/^/ipq-wifi: /'
ls -la $T/package/firmware/ipq-wifi/board-motorola_mh7021.* $DTSDIR/qcom-ipq4019-mh7021.dts
grep -c "label-mac-device" $DTSDIR/qcom-ipq4019-mh7021.dts | sed 's/^/dts label-mac-device (expect 1): /'
awk '/motorola,mh7021/{f=1} f&&/ucidef_set_interfaces/{print "02_network action:", $0; exit}' $T/target/linux/ipq40xx/base-files/etc/board.d/02_network
grep -n -A3 "motorola,mh7021)" $T/target/linux/ipq40xx/base-files/etc/board.d/02_network | sed "s/^/setup_macs: /"
if [ -n "$ENVF" ]; then echo "uboot-envtools entry (expect 1): $(grep -c 'motorola,mh7021' $ENVF) in ${ENVF#$T/}"; else echo "uboot-envtools: NO ipq40xx config file found in this tree"; fi
grep -c "motorola,mh7021" $T/target/linux/ipq40xx/base-files/etc/board.d/01_leds | sed "s/^/01_leds mentions (expect 0): /"
for f in usr/sbin/neon-led usr/sbin/neon-role usr/sbin/neon-watchdog etc/init.d/neon-led etc/init.d/neon-watchdog etc/uci-defaults/50-neon-mesh lib/upgrade/keep.d/neon-mesh etc/diag.sh etc/config/neon_led; do
  [ -e $T/files/$f ] && echo "overlay ok: $f" || echo "overlay MISSING: $f"
done
for f in Makefile ucode/template/themes/aurora/header.ut htdocs/luci-static/aurora/main.css htdocs/luci-static/resources/menu-aurora.js root/etc/uci-defaults/30_luci-theme-aurora; do
  [ -e $T/package/luci-theme-aurora/$f ] && echo "aurora ok: $f" || echo "aurora MISSING: $f"
done
git -C $T status --short | sed 's/^/git: /'
