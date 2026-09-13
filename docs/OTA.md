# Over-the-air updates and the release server

From **neon-mesh v1.2.0**, every image carries `neon-ota`: a unit checks a signed manifest on the
release server, and installs a newer release — from LuCI (**System → Firmware OTA**), from the
shell, or unattended in a maintenance window. Units on v1.1.0 or older have none of this; the move to
the first OTA-capable release is an ordinary `sysupgrade` / LuCI flash with settings kept.

## 1. The release server

`release.sh` bakes two URLs into the image:

| | default | used by |
|---|---|---|
| `PUBURL` | `https://ujjain.today/neon-mesh` | `neon-ota` (`/etc/config/neon_ota` → `base_url`), `tools/rollout.py` (`NEON_FEED`) |
| `FEED_URL` | `$PUBURL/<tag>` | the apk feed in `/etc/apk/repositories.d/neon.list` |

To serve your own builds, set `PUBURL` (and `PUBROOT`, the directory your web server serves it from)
when you run `release.sh`, and rebuild — the URLs are compiled into the image.

`openwrt/nginx/` adds `/neon-mesh/` to an existing HTTPS site on the build host:

```sh
sudo SITE=/etc/nginx/sites-available/mysite SERVER_NAME=example.org bash openwrt/nginx/install.sh
```

It creates `/srv/neon-releases/neon-mesh/` owned by the account that runs `release.sh` (a web server
usually cannot enter that account's home directory), installs `neon-mesh.conf` as an nginx snippet and
includes it in the site's server block — backing the site file up first and restoring it if `nginx -t`
fails. The snippet serves the tree read-only (GET/HEAD, no directory listing) with
`Cache-Control: max-age=3600` on release directories and `no-store` on `ota/` and `latest/`, because
CDNs such as Cloudflare cache `.bin`/`.apk` by file extension.

After that, `release.sh` publishes each tag twice: the full output to `~/lab/releases/neon-mesh/<tag>/`
and the same **without the ImageBuilder tarball** to `$PUBROOT/<tag>/`, then checks the public
`sha256sums` answers. The layout mirrors `downloads.openwrt.org` (`targets/ipq40xx/generic/`,
`packages/<arch>/<feed>/`).

**Everything under `$PUBROOT` is public.** Publish only images that carry no site values or credentials
— the released images ship `CHANGEME` placeholders and take secrets as arguments (see the README).

## 2. Sending a release to units

Publishing a tag does **not** send it anywhere. Promotion is a separate, explicit step:

```sh
T=/path/to/openwrt TAG=neon-mesh-v1.2.1 ./openwrt/release.sh     # build + verify + publish
python3 tools/rollout.py sat1 --release neon-mesh-v1.2.1 --rollback none --yes   # try it on one unit
TAG=neon-mesh-v1.2.1 ./openwrt/ota-promote.sh                     # now OTA offers it
CHANNEL=stable ./openwrt/ota-promote.sh --show                    # what the channel says
```

`ota-promote.sh` refuses a tag whose `sha256sums.sig` does not verify or whose images do not match
`sha256sums`. It writes `$PUBROOT/ota/<channel>.json` — release, publish time, board, and path / sha256 /
size of both profiles' sysupgrade images — and signs it with **`key-build`**, the same usign key that
signs the images, which units already trust in `/etc/opkg/keys`. No new key reaches the units. Each
promotion is appended to `ota/history.log`. Channels (`stable`, `testing`, …) are independent.

## 3. What a unit does

`neon-ota check | upgrade | status`, configured in `/etc/config/neon_ota`:

1. Fetch `<base_url>/ota/<channel>.json` and its `.sig`, and **verify the signature with `usign`
   against `/etc/opkg/keys` before reading any field**. A manifest for another channel or board is
   rejected.
2. Pick the image for the running profile (`kmod-ath10k-ct` installed → the `-ct` image) and compare
   versions. **Only a strictly newer release installs** (`-rcN` sorts below its final), so promoting an
   older tag never downgrades a unit — it only stops units that have not upgraded yet.
3. Pre-check: board `motorola,mh7021`, MemAvailable ≥ 40 MiB, room in `/tmp`, and a clock that is not
   behind the manifest's publish time (a unit that has not synced NTP yet waits).
4. Download to **`/tmp` only**, require the size and sha256 from the signed manifest, then run
   `sysupgrade -T`. No rollback image is staged: a second ~10 MB image does not fit the 17.7 MiB overlay
   and starves the first boot after the upgrade.
5. Record the attempt in `/etc/neon/ota-last`, start `sysupgrade` detached, **settings kept**.
   `neon-role apply` is not run, so per-unit settings such as a custom hostname survive.
6. On the first boot after, `/etc/init.d/neon-ota` waits up to 10 minutes for `neon-watchdog check`
   and records `ok`, `unhealthy: …` or `failed (still running …)` in `ota-last` and syslog.

A failed check says why — for example `HTTP error 404 -- no release has been promoted to channel
'stable' yet`, a network error, or a rejected TLS certificate.

## 4. Automatic installs

The image ships **`enabled=1`** (check every `check_hours`, 6 by default, and log when a newer release
exists) and **`auto_install=0`**. Turn automatic installs on per unit — the setting survives upgrades
with the rest of `/etc/config`:

```sh
uci set neon_ota.main.auto_install=1; uci commit neon_ota      # or the checkbox in LuCI
```

Units install in slots from `window_start` (03:00) in steps of `slot_minutes` (20): satellite 1 at
03:00–03:20, satellite 2 at 03:20–03:40, satellite 3 at 03:40–04:00, and **the router last**,
04:00–04:20, so the mesh root and the internet go down only after every satellite is back. The role
comes from the configuration the same way `neon-role` derives it: a `wan` interface means router,
otherwise satellite `lan.ipaddr − 1`. A unit that fails `neon-watchdog check` does not auto-install,
and each release gets at most two attempts per boot.

## 5. Manual install

- LuCI: **System → Firmware OTA → Check now → Install**. The page confirms, shows progress and
  reconnects when the unit is back.
- Shell: `neon-ota upgrade --yes`; `neon-ota upgrade --dry-run` downloads and verifies without flashing.

## 6. Limits

- **No automatic rollback** after an OTA flash — the overlay cannot hold a second image. Recovery is
  the previous image via `tools/rollout.py`, or Ethernet / serial console (`docs/HARDWARE.md`).
- Packages added with `apk add` after flashing are lost on every upgrade, as with any sysupgrade.
- Units need internet access to reach the release server; with the WAN down, neither OTA nor the
  package feed works.
