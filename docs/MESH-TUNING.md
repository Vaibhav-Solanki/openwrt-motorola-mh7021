# Mesh backhaul TX power — 18 dBm → 30 dBm

**Date:** 2026-09-10 · **Fleet:** all three units on `neon-mesh-v1.0.0-rc1`
(OpenWrt 25.12.5 `r33051-f5dae5ece4`, mainline `ath10k`, kernel 6.12.94)
**Backup taken first:** a `sysupgrade -b` archive per unit (verified)

---

## 1. The problem

The mesh is a chain, not a star. `base ↔ staircase` is the only path to the base
for **both** satellites, and it is the slowest link in the network by an order of
magnitude:

| link | signal | up | down |
|---|---|---|---|
| bedroom ↔ staircase | −56/−61 dBm | 201 Mbit/s | 291 Mbit/s |
| **staircase → base** (slab) | **−85/−86 dBm** | **42.5** | **22.5** |
| bedroom → base (2 hops, via staircase) | — | 35.6 | 18.7 |

`base ↔ bedroom` direct is `BLOCKED` at −92 dBm, so nothing routes around it.

Everything on both satellites is throttled by that one link. The bedroom unit has
a 291 Mbit/s hop sitting next to it and still only reaches the base at 18.7 Mbit/s.

## 2. What it is *not*

Ruled out with measurements, so this doesn't get re-diagnosed later:

- **Not interference.** Channel busy at the base is 4.9%, of which 4.3% is the
  base's own tx/rx — under 1% external. Noise floor −101/−103 dBm (clean; a jammed
  channel shows an elevated floor). Contrast with 2026-09-09, when earlier measurements
  recorded 31–45% busy in the same room.
- **Not packet loss.** `base → satellites` is 0% over 20 pings, both units.
  earlier notes recorded 5–25% loss on 2026-09-09.
- **Not the radios or calibration.** The same hardware does 201/291 Mbit/s on the
  bedroom↔staircase hop, with the vendor board files loading correctly
  (`bmi_id 0:20/23/29`, `cal pre-cal-nvmem` ×3).
- **Not the driver.** Both figures above are from mainline `ath10k`. (The
  2026-09-09 collapse was most likely `ath10k-ct` — see §6.)

**It is path loss.** The counters say so directly:

```
base → staircase:  tx packets 513,844   tx retries 796,216 (155%)   tx failed 137
```

Reliable but grossly inefficient — every packet takes ~2.5 transmissions at a low
modulation. That is the signature of a weak signal, not a broken link. Interference
produces failures; weak signal produces retries.

## 3. Why raise TX power

The backhaul radio is transmitting at **18 dBm into a 30 dBm regulatory ceiling**:

```
configured   : wireless.radio0.txpower = 18       (MESH_TXPOWER=18 in neon-role)
regulatory   : 5745.0 MHz [149] (30.0 dBm)        country IN
AP radios    : phy1-ap0 and phy2-ap0 already at 30.00 dBm
```

So the two client-facing radios already run at the ceiling, and only the backhaul —
the link that actually matters — is held back, by a project default rather than by
any regulatory or hardware constraint. There is up to **12 dB of unused headroom**
on the one link limiting the whole network.

12 dB is large. It should move the slab link from ~−85 dBm toward ~−73 dBm, out of
the marginal zone, allowing a higher MCS and cutting the retry rate.

## 4. Risks considered

- **Hardware may not deliver 30 dBm.** The QCA9888 will cap at whatever its
  calibration allows; the driver silently clamps. We read back the *effective*
  power after applying rather than trusting the configured value.
- **Both ends must be raised.** TX power only affects the direction a node
  transmits. Raising only the base would fix base→staircase and leave the reverse
  unchanged.
- **Shared channel.** All three nodes share ch 149, so raising everyone's power
  raises the floor for everyone. Acceptable here: 3 nodes, <1% external channel
  utilisation, and the alternative is a link that cripples two of them.
- **Not a substitute for placement.** Ethernet between floors remains
  the real fix. This buys margin on a link that currently has none.

## 5. Procedure

Applied to **all three** units (both ends of every link):

```sh
uci set wireless.radio0.txpower='30'
uci commit wireless
wifi reload
```

`radio0` is the QCA9888 backhaul on every unit (`neon-role` maps radios by DT path:
PCIe = mesh, `a000000` = 2.4 GHz, `a800000` = 5 GHz-low).

## 6. Rollback

```sh
uci set wireless.radio0.txpower='18'
uci commit wireless && wifi reload
```

Or restore the full pre-change config from
the `sysupgrade -b` archive taken beforehand with `sysupgrade -r`.

To make it permanent for future images, change `MESH_TXPOWER` in
`openwrt/files/usr/sbin/neon-role` — otherwise the next `neon-role apply`
reverts it to 18.

---

## 7. RESULT (2026-09-11): txpower 26 dBm + VHT40 — applied

**Controlled sweep, 140 samples over 2h23m, settled the question. Applied to the fleet
and recorded in `neon-role` (`MESH_TXPOWER=26`, `MESH_RSSI_THRESHOLD=-80`).**

Method that made it work, after §7a below failed: power changed with
`iw phy phy0 set txpower fixed` (peers stay up, rate-control and survey counters not
reset), 10 min settle per step, 10 samples per step, both directions, incidents logged.
Raw data: `data/sweep-20260911.csv`.

### Power sweep (VHT40, medians of 10)

| txpower | up | down | total | base_sig | MCS |
|---|---|---|---|---|---|
| 18 dBm | 22.5 | 24.2 | 46.7 | −78 | 2–4 |
| 20 | 26.2 | 32.6 | 58.8 | −75 | 4 |
| 22 | 26.5 | 36.1 | 62.6 | −73 | 4 |
| 24 | 30.3 | 39.3 | 69.6 | −72 | 4–6 |
| **26** | **54.4** | **44.5** | **98.9** | −73 | 4–5 |
| 28 | 52.9 | 44.4 | 97.3 | −72 | 4–5 |
| 30 | 53.2 | 44.8 | 98.0 | −72 | 4–5 |

**26 dBm is the plateau** — nothing above it. Corroborated by RSSI (−78 → −72) and MCS.

Verified after applying (no reload, 3 runs): **81.5 up / 50.8 down, −71 dBm, tx failed 3,
±1% variance** — i.e. 46.7 → 132 Mbit/s total, **2.8×**.

### VHT40 vs VHT80 — VHT40 wins at every power

| txpower | VHT40 up/down | VHT80 up/down | VHT40 advantage |
|---|---|---|---|
| 18 | 22.5 / 24.2 | 10.8 / 9.3 | +109% / +160% |
| 24 | 30.3 / 39.3 | 26.5 / 27.6 | +14% / +43% |
| 30 | 53.2 / 44.8 | 33.2 / 31.0 | +60% / +44% |

Mechanism, not just throughput: **VHT40 had `tx failed = 0` at all 7 power steps**, while
VHT80 had 39–67 failures in 5 of 7, ~1.8× the retries, MCS 2–3 vs 4–5, and 1 dB worse
noise floor. This confirms the project's mesh decision (VHT40, threshold 0 first) and strengthens it: 80 MHz is actively harmful here,
not merely useless.

### `mesh_rssi_threshold` back to −80 (D3's trigger fired)

At threshold 0 the base peered with bedroom directly at **−90 dBm**: 7.2 Mbit/s MCS 0 and
**4524 tx failures**, with `MESH-SAE-AUTH-FAILURE` / `MESH-SAE-AUTH-BLOCKED duration=300`
and `plink … closed with reason 55` churn in the log on both nodes. That is exactly the
condition D3 named for re-applying −80. Done; after it, every link reports `tx failed = 0`
and the clean chain is restored (base ↔ staircase −74 dBm ↔ bedroom −54 dBm).

### NSS: a red herring

NSS stayed **1** in ~93% of samples regardless of power or width. This path does not sustain
two spatial streams; earlier speculation that power changes were "dropping it to NSS 1" was
wrong — it was never reliably at 2.

### Honest confound

Phases ran sequentially (VHT40 01:45–02:55, VHT80 02:57–04:08) and power rose monotonically
within each, so a pure time trend is not excluded by design. Against that: the
power→throughput relation **replicated independently in both phases** (VHT80 also rose
10.8 → 33.2), and the VHT40 advantage held at all three compared power levels with a
mechanism-level difference (0 vs dozens of failures) that time cannot explain.

### Unchanged conclusion

The link is still path-loss limited at −71…−80 dBm, and the bedroom↔staircase hop still does
~400 Mbit/s at −54 dBm. Power and width tuning bought 2.8×; **placement or Ethernet between
floors remains the real fix** for the rest.

---

## 7a. First attempt (same day, earlier) — INCONCLUSIVE, reverted

**Applied 30 dBm to all three, measured, reverted to 18 dBm. Config is back to the
pre-change state. Do not repeat this experiment the same way — it cannot isolate
the variable.**

### What happened

The hardware accepted the full 30 dBm (no clamping) and **RSSI improved as predicted**:

| link | 18 dBm | 30 dBm |
|---|---|---|
| base ↔ staircase | −85/−86 dBm | **−74/−78 dBm** (+8 to +11 dB) |
| bedroom ↔ staircase | −56/−61 dBm | −52/−54 dBm |

**But throughput did not follow — and kept falling after the revert:**

| setting | staircase → base | base → staircase |
|---|---|---|
| 18 dBm (baseline, 22:49) | 42.5 | 22.5 |
| 30 dBm run 1 | 35.9 | 16.6 |
| 30 dBm run 2 | 31.4 | 11.8 |
| **18 dBm (reverted)** | **19.8 / 23.1** | **4.72 / 5.24** |

### Why it proves nothing

Throughput declined **monotonically across the whole session regardless of the
setting**, including after the rollback restored the original config. So the
decline is not attributable to TX power. The slab link is simply unstable on a
timescale shorter than the experiment.

An intermediate reading suggested the link had dropped from VHT-NSS 2 to NSS 1 and
that max-power PA non-linearity was to blame. That was over-confident: NSS 1 was
still present after the revert, so the link oscillates between 1 and 2 streams on
its own.

### Method errors to avoid next time

1. **No control for drift.** A single before/after pair cannot separate the change
   from a link that moves on its own. Needs interleaved A/B/A/B runs, or a long
   baseline first.
2. **`wifi reload` resets the survey counters**, destroying the channel-busy
   comparison and forcing rate-control to re-converge — so the first measurements
   after any reload are not comparable to anything.
3. **Two 8-second runs is not a sample** on a link this variable.

### What to do instead

Get a baseline before tuning anything. `soak.py` exists for exactly this and has
never been run:

```sh
python3 tools/soak.py sat1 sat2 --interval 600 --iperf <base-ip>
```

It samples plink state, RSSI, `tx failed`, and memory every 10 minutes into
`soak_<unit>.csv`, with iperf3 every 8th sample. A day of that would show whether
this link is steadily degrading, oscillating, or interference-driven — and would
give any future change something real to be measured against.

The underlying conclusion from §2 is unchanged and unaffected by this experiment:
**the slab link is path-loss limited, and the fix is placement or Ethernet between
floors, not radio tuning.**

### Also noticed

`htmode` is inconsistent across the fleet — base `VHT80`, both satellites `VHT40`.
The base kept its pre-upgrade config; the satellites were rebuilt by `neon-role`,
whose default is `MESH_HTMODE=VHT40`. Worth reconciling deliberately rather than
by accident.

