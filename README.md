# OpenWrt on the Motorola MH7021 / MH7022 mesh kit

> ## ⚠️ Disclaimer — read before flashing
>
> **Use of this firmware, and of everything in this repository, is entirely at your own risk.**
>
> Flashing replaces your device's firmware. It can **permanently break (brick) your device**,
> void your warranty, and cause loss of configuration or data. Recovery may require opening
> the case and attaching a serial console, and in some failure modes there is **no recovery
> at all**.
>
> **I accept no responsibility or liability of any kind** for any damage, malfunction, data
> loss, loss of network access, bricked hardware, voided warranty, or any other loss or
> cost — direct or indirect — arising from downloading, building, installing, flashing or
> using anything here. There is **NO WARRANTY**, express or implied, including no implied
> warranty of merchantability or fitness for a particular purpose.
>
> You are solely responsible for what you do to your own hardware. If you are not
> comfortable recovering a device over a serial console, **do not flash it**.
>
> This is an unofficial, unaffiliated hobby project. **Not affiliated with, endorsed by,
> authorised by, or supported by Motorola, Minim, Qualcomm, or the OpenWrt project.** All
> trademarks belong to their respective owners and are used only to describe hardware
> compatibility.
>
> **Before you flash: dump your whole flash chip and keep the dump** — it contains the ART
> partition with your device's radio calibration and factory MAC addresses, which is unique
> to your unit and cannot be regenerated. See [`docs/HARDWARE.md`](docs/HARDWARE.md).

OpenWrt support for the **Motorola MH7021** (sold as the **MH7022** tri-band mesh kit),
an IPQ4019 device whose stock firmware depends on Minim's discontinued cloud service.

Verified working on OpenWrt **25.12.5**: all three radios on vendor calibration, both
Ethernet ports, the RGB status LED, reset and WPS buttons, and an SAE-encrypted 802.11s
mesh backhaul on the dedicated QCA9888 radio.

> Device support here is complete and in service. It is **not** yet submitted upstream —
> see [Upstreaming](#upstreaming).

## What's here

| path | what |
|---|---|
| `dts/qcom-ipq4019-mh7021.dts` | device tree — GPIOs, MTD layout, `mac-base` nvmem cells |
| `openwrt/generic.mk.snippet` | two device definitions (mainline `ath10k` and `ath10k-ct`) |
| `openwrt/apply.sh` | idempotent patch set applied to an OpenWrt tree |
| `openwrt/config.seed` | build config: both profiles, signing, versioning |
| `openwrt/build.sh`, `release.sh` | build / sign / verify / publish a tagged release |
| `openwrt/files/` | rootfs overlay: `neon-role`, `neon-led`, `neon-watchdog` |
| `ipq-wifi/*.json` | board-file manifests (see [Board files](#board-files)) |
| `tools/` | flashing, dumping, rollout and soak tooling |
| `docs/HARDWARE.md` | hardware reference — the part that took longest to establish |
| `docs/MESH-TUNING.md` | measured mesh tuning: TX power and channel width |
| `data/` | raw measurement data behind the tuning doc |

## Hardware

IPQ4019 quad-core ARMv7, 256 MiB RAM, 32 MiB SPI-NOR (W25Q256), U-Boot 2012.07.
Three radios: two IPQ4019 (2.4 GHz, 5 GHz-low) plus a **QCA9888 on PCIe** for 5 GHz-high,
which is what makes a dedicated mesh backhaul possible. Full details, including the GPIO
map and flash layout, in [`docs/HARDWARE.md`](docs/HARDWARE.md).

## Board files

The `ipq-wifi` **`.json` manifests are included; the `board-*.bin` files are not.** Those
contain per-model calibration data extracted from stock firmware, and redistributing them
is not clearly licensed. Extract them from your own device instead — `docs/HARDWARE.md`
describes where they live in the vendor rootfs and how to package them with
`ath10k-bdencoder`.

**Without the correct board files the radios will not come up.** The kernel logs
`board_file api 2 bmi_id 0:<id>` for each radio; the IDs must match the names in the JSON.

## Building

```sh
# 1. apply the patch set to an OpenWrt tree (idempotent)
T=/path/to/openwrt ./openwrt/apply.sh

# 2. build + sign + verify + publish a tagged release
T=/path/to/openwrt TAG=myrelease-v1.0.0 ./openwrt/release.sh
```

`release.sh` produces both driver profiles, signs images with usign/ucert, signs the apk
package index, verifies the result (board-file sizes, FIT size limits, per-profile package
manifests, signature chains) and publishes a feed laid out like `downloads.openwrt.org`.

Set `FEED_HOST` to the host serving your package feed. Secrets are never stored in this
repo: keys live outside the tree, and passwords are passed as arguments or environment
variables (`NEON_ROOTPW`, `NEON_FEED`).

## Flashing

Two paths, both documented in `tools/`:

- **From U-Boot over TFTP** (`tools/flash_uboot.py`) — for a unit on stock firmware. Stock
  firmware has no console shell, so you need the serial console and the U-Boot prompt.
- **`sysupgrade`** (`tools/rollout.py`) — for a unit already running OpenWrt. Verifies the
  image, checks board identity and free space, upgrades detached, then health-checks.

**Two things that will cost you time if ignored:**

1. **Never erase the ART partition at `0x170000`.** It holds per-unit radio calibration and
   the factory MAC addresses, and it is not reproducible. Every write in this tooling is
   confined to the `firmware` region.
2. **This U-Boot has no `boot` command,** and the vendor's `bootipq` leaves the switch PHYs
   dead after a reboot from OpenWrt. `flash_uboot.py` installs a corrected `bootcmd`.

See [`docs/HARDWARE.md`](docs/HARDWARE.md) for both in detail.

## Mesh roles

`neon-role` configures a unit as router or satellite in one idempotent command — addressing,
mesh, APs, and service trimming for dumb-AP satellites:

```sh
neon-role router      [--key K] [--rootpw P]
neon-role satellite N [--key K] [--rootpw P]
neon-role apply       # re-apply tuning after an upgrade, touching no secrets
neon-role status
```

Fleet defaults live at the top of the script and can be overridden per unit in
`/etc/neon/tunables`, which survives `sysupgrade`. Secrets are arguments only, never stored.

`neon-led` drives the RGB status LED from actual state (mesh peer present, gateway
reachable, internet reachable, link quality), and `neon-watchdog` gives a console-less
satellite an automatic rollback if it boots unhealthy.

## Mesh tuning

[`docs/MESH-TUNING.md`](docs/MESH-TUNING.md) records a measured sweep — TX power 18→30 dBm
in 2 dB steps and VHT40 vs VHT80, 140 samples over 2.5 hours — on a link that crosses a
concrete floor slab. Summary:

- **26 dBm is the plateau**; 18→26 dBm gave **2.8×** throughput, nothing above 26.
- **VHT40 beat VHT80 at every power level**, with zero `tx failed` versus dozens.
- Below roughly −85 dBm, peering churns with SAE auth failures and 300 s blocks; a
  `mesh_rssi_threshold` of −80 suppresses a useless direct peering.

The doc also keeps a **failed** first attempt, because the method errors are the
transferable part: no control for link drift, `wifi reload` resetting rate-control and
survey counters, and too few samples to see a 3–5× natural swing.

## Upstreaming

Not yet submitted. Known gaps for an upstream submission:

- Kernel-side cold init of the PSGMII/PHY block, so `bootcmd` needn't `ping` to bring
  Ethernet up before the kernel starts.
- Clearing the QCA download-mode cookie on shutdown.
- Board files need resolving as above.

## Licence and warranty

DTS and OpenWrt build integration derive from OpenWrt and are **GPL-2.0**. The `neon-*`
overlay scripts and the Python tooling are original to this repository and are also
GPL-2.0. See [`LICENSE`](LICENSE).

**THIS SOFTWARE AND FIRMWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED**, including but not limited to the warranties of merchantability, fitness for
a particular purpose and non-infringement. **In no event shall the author be liable for
any claim, damages, bricked hardware, or other liability**, whether in an action of
contract, tort or otherwise, arising from, out of, or in connection with this software or
its use. See the full disclaimer at the top of this file.

### A note on the binary releases

Released images embed the vendor's ath10k **board files** (radio calibration extracted from
the stock firmware), because the radios do not function without them. Those blobs are not
mine and are not covered by the licence above; they are redistributed here on the same
basis as OpenWrt's own `ipq-wifi` packages. If you would rather not rely on that, build
from source and supply board files extracted from your own device — see
[Board files](#board-files).

Released images also contain **no credentials**: the role scripts take keys and passwords
as arguments only, and site-specific values (SSID, mesh ID, host addresses) ship as
`CHANGEME` placeholders, overridden per unit in `/etc/neon/tunables`. A freshly flashed
unit has **no root password** — set one immediately.
