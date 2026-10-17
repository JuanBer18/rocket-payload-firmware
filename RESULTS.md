# Characterization results

**Status: EMPTY — no hardware measurements taken yet.**

This file is the deliverable of the bringup phase. The flight firmware gets
written from the numbers in this table, not from the spec. Every `TBD` is a
question the spec answered by assumption and this phase answers by
measurement.

Fill each row from the `For RESULTS.md:` block that every bench script prints
at the end of its run. Record the date and the board, because several of
these are per-unit rather than per-design.

- Board: _(serial / identifier)_
- Firmware: _(`ESP32_GENERIC_S3` version)_
- Date started: _TBD_

---

## Phase 0 — Prerequisites

| Item | Expected | Measured | Notes |
|---|---|---|---|
| `gc.mem_free()` | ~2,058,752 | TBD | <1.8MB means the octal build is flashed |
| Firmware build string | no `OCT` | TBD | N16R2 is **quad** PSRAM |
| I2C @ 400kHz, all parts ACK | yes | TBD | every rate figure below assumes this |
| **IMU SA0 strap** | — | TBD | **the key one — see below** |
| SEN65 datasheet obtained | yes | TBD | settles table 32 scale factors |
| Bench power source | — | TBD | SEN65 pulls 80–100mA measuring |

### The SA0 question

The IMU enumerated at 0x6B, so SA0 is strapped high. If it can be pulled low
the IMU becomes 0x6A and the SEN65 collision disappears with no code change
beyond `config.IMU_ADDR`. Everything about Phase 4 depends on the answer.

- Schematic shows SA0 tied to: _TBD_
- Modifiable? _TBD_
- Result after change: IMU at `0x____`

---

## Phase 1a — LSM6DSO32

### Identity and health

| Check | Expected | Measured |
|---|---|---|
| WHO_AM_I | 0x6C | TBD |
| Address | 0x6A or 0x6B | TBD |
| Accel self-test delta | 50–1700 mg | TBD |
| Gyro self-test delta | 150–700 dps | TBD |
| Die temperature at rest | ambient + a few °C | TBD |

Self-test windows in `drivers/lsm6dso32.py` are **UNVERIFIED**. Confirm
against the datasheet before treating a failure as a dead part.

### Scale factors

At rest, `|a|` must read 1.000g at **every** full-scale setting. A 2× error
here is the 4/8/16/32 vs 2/4/8/16 mistake and looks entirely plausible.

| Full scale | mg/LSB | `\|a\|` measured | Noise (g RMS) |
|---|---|---|---|
| ±4g | 0.122 | TBD | TBD |
| ±8g | 0.244 | TBD | TBD |
| ±16g | 0.488 | TBD | TBD |
| ±32g | 0.976 | TBD | TBD |

Noise ratio ±32g / ±4g: **TBD** — if large, calibrating offsets at ±32g is
mostly fitting noise. Decides whether the pad sits at ±4g and switches at
liftoff.

### Rates

| ODR (Hz) | Polled achieved (Hz) | FIFO words/s | FIFO samples/s | Overrun? |
|---|---|---|---|---|
| 104 | TBD | TBD | TBD | TBD |
| 416 | TBD | TBD | TBD | TBD |
| 833 | TBD | TBD | TBD | TBD |
| 1666 | TBD | TBD | TBD | TBD |
| 3332 | TBD | TBD | TBD | TBD |
| 6664 | TBD | TBD | TBD | TBD |

**Highest ODR with zero overruns: TBD Hz.** This is the headline number of
the whole phase — the real ceiling for flight logging.

Predicted for reference: polled tops out near 2.9kHz at 400kHz I2C (0.35ms
per 12-byte burst); FIFO batching should reach 2–3kHz for accel+gyro before
the bus saturates.

### Power and reconfiguration

| State | Current (mA) |
|---|---|
| Accel + gyro at 1666 Hz | TBD |
| Gyro powered down | TBD |
| Both powered down | TBD |

| Check | Result |
|---|---|
| ODR changes live without reset | TBD |
| Full-scale changes live without reset | TBD |
| Garbage samples across a change | TBD |
| `SW_RESET` duration | TBD ms |
| Time to valid data after reset | TBD ms |

### Calibration — gyro zero-rate offset

**The question: is this repeatable across power cycles?** If not, it must be
measured every boot rather than stored.

| Power cycle | bias X (dps) | bias Y | bias Z | die temp (°C) |
|---|---|---|---|---|
| 1 | TBD | TBD | TBD | TBD |
| 2 | TBD | TBD | TBD | TBD |
| 3 | TBD | TBD | TBD | TBD |
| 4 | TBD | TBD | TBD | TBD |
| 5 | TBD | TBD | TBD | TBD |
| **Spread** | **TBD** | **TBD** | **TBD** | — |

**Conclusion: TBD** — re-measure every boot, or store once?

### Calibration — gyro drift vs temperature (30 min from cold)

| Metric | Value |
|---|---|
| Temperature rise | TBD °C |
| Bias drift X / Y / Z | TBD dps |
| Tempco X / Y / Z | TBD dps/°C |
| Warm-up needed before arming? | TBD |

### Calibration — accel six-position tumble

| Axis | Bias (g) | Scale (1.0 ideal) | Stable across power cycle? |
|---|---|---|---|
| X | TBD | TBD | TBD |
| Y | TBD | TBD | TBD |
| Z | TBD | TBD | TBD |

---

## Phase 1b — BME280

| Check | Result |
|---|---|
| Chip ID | TBD (0x60 = BME280, 0x58 = BMP280, no humidity) |
| Calibration coefficients loaded | TBD |
| Coefficients survive reset | TBD |

### Rate vs oversampling

| T / P / H | Predicted (Hz) | Measured (Hz) |
|---|---|---|
| ×1 / ×1 / ×1 | TBD | TBD |
| ×1 / ×2 / ×1 | TBD | TBD |
| ×2 / ×4 / ×1 | TBD | TBD |
| ×2 / ×8 / ×1 | TBD | TBD |
| ×2 / ×16 / ×1 | ~22 | TBD |
| ×16 / ×16 / ×16 | TBD | TBD |

The datasheet's 157Hz applies only at minimum oversampling.

### Noise → altitude resolution

| Pressure oversampling | RMS noise (Pa) | Resolution (m) | Rate (Hz) |
|---|---|---|---|
| ×1 | TBD | TBD | TBD |
| ×2 | TBD | TBD | TBD |
| ×4 | TBD | TBD | TBD |
| ×8 | TBD | TBD | TBD |
| ×16 | TBD | TBD | TBD |

### IIR filter — the noise/lag trade

A rocket climbing at 200 m/s turns filter lag directly into altitude error.

| IIR | Noise (Pa) | τ (63%, ms) | Lag at 200 m/s (m) |
|---|---|---|---|
| off | TBD | TBD | TBD |
| ×2 | TBD | TBD | TBD |
| ×4 | TBD | TBD | TBD |
| ×8 | TBD | TBD | TBD |
| ×16 | TBD | TBD | TBD |

**Chosen setting: TBD** — the largest IIR whose lag is smaller than the
acceptable altitude error.

### Modes

| Mode | Current (mA) | Notes |
|---|---|---|
| Sleep | TBD | |
| Forced (one-shot) | TBD | conversion took TBD ms |
| Normal | TBD | |

---

## Phase 1c — SEN65

### Command inventory — **RESOLVED from the datasheet (D1 v0.92)**

Codes confirmed against SEN6x datasheet section 4.8. "Works?" is still for
the bench to fill in, but the codes themselves are no longer guesses.

| Command | Code | Mode | Works? | Returns |
|---|---|---|---|---|
| Start measurement | 0x0021 | idle | TBD | — (50 ms) |
| Stop measurement | 0x0104 | measuring | TBD | — (1400 ms) |
| Data ready | 0x0202 | measuring | TBD | padding + bool |
| Read measured values | 0x0446 | measuring | TBD | 8 words |
| **Read raw values** | **0x0455** | measuring | TBD | 4 words |
| **Number concentration** | **0x0316** | measuring | TBD | 5 words, incl. PM0.5 |
| Device status | 0xD206 | any | TBD | uint32 |
| Read + clear status | 0xD210 | any | TBD | uint32 |
| **Get version** | **0xD100** | any | TBD | major.minor |
| Product name | 0xD014 | any | TBD | string<32> |
| Serial number | 0xD033 | any | TBD | string<32> |
| Device reset | 0xD304 | idle | TBD | — (1200 ms) |
| Fan clean | 0x5607 | idle | TBD | — (10 s + 10 s guard) |
| SHT heater | 0x6765 | idle | TBD | — |
| VOC algorithm state | 0x6181 | read any / **write idle** | TBD | 8 bytes |

Three corrections this found:

- **`0x0455`, not `0x0405`**, is raw values on SEN65. `0x0405` is the SEN66
  command — v1's guess would have failed or returned a foreign layout.
- **`0x8004` (auto-cleaning interval) does not exist on SEN6x.** It is a
  SEN5x command. Removed from the driver.
- **`0x0316` and `0xD100` were missed entirely.** Number concentration
  exposes PM0.5, which no other command provides.

### Scale factors — **RESOLVED, table 32**

Every factor v1 guessed was correct. This closes the largest open question
carried over from the previous build.

| Channel | Type | Scale | Unknown sentinel | Status |
|---|---|---|---|---|
| pm1p0 / pm2p5 / pm4p0 / pm10 | uint16 | ÷10 → µg/m³ | 0xFFFF | ✅ confirmed |
| rh | int16 | ÷100 → % | 0x7FFF | ✅ confirmed |
| temp | int16 | ÷200 → °C | 0x7FFF | ✅ confirmed |
| voc / nox index | int16 | ÷10 | 0x7FFF | ✅ confirmed |
| raw_rh | int16 | ÷100 → % | 0x7FFF | ✅ table 37 |
| raw_temp | int16 | ÷200 → °C | 0x7FFF | ✅ table 37 |
| raw_voc / raw_nox | **uint16** | **no scale — ticks** | 0xFFFF | ✅ table 37 |
| nc0p5 … nc10 | uint16 | ÷10 → particles/cm³ | 0xFFFF | ✅ table 39 |

Note the raw gas channels are **unsigned ticks with no scale factor**, unlike
the signed, scaled index values.

**Still genuinely unverified:** the device status *bit positions*. Table 44
defines the register as 32 flags and defers their meanings to a separate
document. Get a healthy-part baseline before treating any set bit as a fault.

### The VOC/NOx baseline problem

The index outputs come from an adaptive algorithm that learns over ~1 hour
and reports 100 as neutral. A 10-minute flight from cold produces a
meaningless index.

| Question | Answer |
|---|---|
| Index at cold boot | TBD (expect ~100) |
| Algorithm state readable (0x6181)? | TBD |
| Algorithm state writable (**idle mode only**)? | TBD |
| **Workaround viable?** | TBD — warm up on ground, save, restore before flight |
| Raw ticks available (**0x0455**)? | TBD |

Datasheet facts that shape this:

- The state **is retained across stop/start** of measurement. Only a device
  reset or a real power cycle clears it — so a duty-cycled mission keeps its
  baseline for free.
- It is **readable during measurement**, so it can be re-saved each cycle and
  survive a sudden power loss.
- Writing is **idle-mode only** and is *silently ignored* in measurement mode,
  applying once at the next start. Easy to think it worked when it did not.
- VOC and NOx return their unknown sentinels for the **first 10–11 s** after
  power-on or reset regardless of anything else. Not a fault.

**Flight channel decision: TBD** — raw ticks, or index with a restored
baseline?

### Timings and power

| Item | Spec | Measured |
|---|---|---|
| Warm-up to first valid reading | ~1100 ms | TBD |
| Stop command execution | 1400 ms | TBD |
| Stop → start round trip | ≥2800 ms | TBD |
| Distinct values per second | 1.0 Hz | TBD |
| Idle current | 3.3 mA | TBD |
| Measuring current | 80–100 mA | TBD |

### Device status baseline

Healthy-part status word: `0x________` — record this so a later nonzero
value can be told apart from a decode error, since the bit positions are
themselves unverified.

---

## Phase 1d — SD card

| Block size | Throughput (MB/s) |
|---|---|
| 512 B | TBD |
| 4 KB | TBD |
| 16 KB | TBD |
| 64 KB | TBD |

**Best block size: TBD. Implied max IMU rate: TBD Hz.**

| Flush every | Throughput (MB/s) |
|---|---|
| 1 write | TBD |
| 10 writes | TBD |
| 100 writes | TBD |
| never | TBD |

### Power-loss survival

| Run | Bytes survived | Whole blocks | Partial tail |
|---|---|---|---|
| 1 | TBD | TBD | TBD |
| 2 | TBD | TBD | TBD |
| 3 | TBD | TBD | TBD |
| 4 | TBD | TBD | TBD |
| 5 | TBD | TBD | TBD |

**Chosen `LOG_FLUSH_EVERY`: TBD** — justified by the worst case above.

---

## Phase 1e — Board capabilities

| Capability | Available? | Notes |
|---|---|---|
| Full chip reset (`machine.reset`) | TBD | |
| Soft reset (`machine.soft_reset`) | TBD | peripherals keep state? |
| IMU soft reset alone | TBD | |
| BME280 soft reset alone | TBD | |
| SEN65 device reset alone | TBD | |
| I2C general-call reset (0x00 ← 0x06) | TBD | which parts honour it? |
| **Per-sensor power gating** | **NO** | no load switch on this board |
| Bus recovery by clocking SCL | TBD | |
| Watchdog minimum timeout | TBD | |
| RTC memory survives WDT reset | TBD | |
| RTC memory cleared by power cycle | TBD | |

### Timebase

| Check | Result |
|---|---|
| Monotonic over 30s | TBD |
| **Monotonic across the ~17.9 min wrap** | **TBD** |
| Drift vs wall clock over 20 min | TBD |

The wrap test is the one that matters — it is the correctness property every
cross-sensor alignment claim depends on.

---

## Phase 4 — Integration

| Check | Result |
|---|---|
| All three sensors on one bus | TBD |
| Slow reads stall the FIFO drain? | TBD |
| Combined sustained logging rate | TBD |
| ODR where words start dropping | TBD |
| 1-hour soak: fragmentation | TBD |
| 1-hour soak: file corruption | TBD |
| 1-hour soak: timestamp drift | TBD |

---

## Decisions this unblocks

Answer these once the tables above are filled, then write the flight firmware.

1. **Flight IMU ODR and full-scale** — TBD
2. **Pad at ±4g, switch to ±32g at liftoff?** — TBD
3. **Gyro calibration: every boot or stored?** — TBD
4. **Barometer oversampling + IIR for flight** — TBD
5. **SEN65: raw ticks or restored-baseline index?** — TBD
6. **`LOG_FLUSH_EVERY`** — TBD
7. **Liftoff threshold** (v1 used an unvalidated 3g/50ms, single channel) — TBD
8. **Liftoff redundancy** — accel only, or a second independent signal? — TBD
9. **Mission end condition** (v1 logged at full rate until power-off) — TBD
10. **Are SEN65 absolute PM readings trustworthy in flight?** — TBD
