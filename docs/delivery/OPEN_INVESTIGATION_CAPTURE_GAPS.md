# Open investigation: capture gaps and overruns, and the storage move

Written 2026-08-08 for the next session. This is an **unresolved** investigation, not
a record of a fix. Everything below is measured on the live station unless marked as a
hypothesis.

> **Addendum, same day, from the coverage/data-integrity side (ADR-022).** The
> "how much recording was actually lost" question below is now answered for the
> specific 32-hour row, by reading `capture_gap` rows directly rather than guessing:
> all 245 gap rows for that stream fall between 03:38:54 and 06:24:45 on 08-07 (the
> first ~2h46m of the claim), and there are none after that for the remaining ~29
> hours up to the claimed `end_utc`. Combined with the frame count (2.79 h) and the
> absence of any detection in that window, this confirms **a stale row, not hidden
> frame loss on a continuing stream** — but the mechanism is worse than a crash: the
> stream was never closed by a killed process (there is no other `alsa` row in
> between), which means the capture read loop itself stopped delivering blocks around
> 06:25 and then sat there — presumably blocked on `source.read()` against the
> already-bad file descriptor — for 29 hours without raising, until whatever finally
> triggered the `AlsaCaptureError` at 11:36:36 on 08-08. **Hypothesis for this
> session's queue:** the ALSA read call can enter a state where it neither returns
> data nor raises, and nothing currently times it out. If capture reads gain a
> watchdog/timeout, `Station` would have retried far sooner than 29 hours later, and
> would give history a rows in exactly the shape it now expects (`last_frame_at_utc`
> populated up to the real point of failure). This was found while making
> `history.coverage()` unable to be fooled by a row like this one, not fixed on the
> capture side — that's this document's territory, not ADR-022's.

## Symptom

ALSA overruns and capture gaps during daytime operation, which the known-good figures
in `HANDOVER.md` say should be **zero**.

| When | Measurement |
|---|---|
| 2026-08-08 ~13:12 (SD card, throttled) | 15 gaps, 8 overruns, continuity 0.99775 |
| 2026-08-08 ~13:39 (SD card, just restarted) | 1 gap, 0 overruns, continuity 0.99985 |
| 2026-08-08 ~14:10 (**SSD**, throttles lifted, 3 min) | 1 gap, 1 overrun, continuity 0.999004 |
| 2026-08-08 ~14:18 (**SSD**, throttles lifted, ~11 min) | 10 gaps, 6 overruns, continuity 0.998483 |

**The USB SSD did not fix it.** That is the single most important finding here, because
the SD card's write load was the leading hypothesis and it is now largely excluded.

Note that many gap records carry `missing_frames=0` — an ALSA overrun reported with no
lost audio. A smaller number lose real frames (one at 42,505 frames ≈ 0.11 s, one at
61,717 ≈ 0.16 s). Both are logged as `capture.gap` with `reason=overrun`, so counting
log lines overstates the number of events that actually lost audio. **Any triage should
separate the two**; the current `/api/v1/health` counters do not.

## How much recording was actually lost is unresolved

Earlier in this session I told the operator "roughly a day". That is **not** supported
cleanly by the data and should be re-derived, not repeated:

- The last `alsa` stream row claims `start_utc` 2026-08-07 03:38:54 and `end_utc`
  2026-08-08 11:36:36, ending with
  `AlsaCaptureError: ALSA read failed: File descriptor in bad state`.
- But its `frame_count` is 3,852,212,352 frames, which at 384 kHz is only **2.79 hours**
  of audio across a 32-hour window.
- And there are **no detections on any live stream between 2026-08-07 20:00 and
  2026-08-08 12:00**.

Those three facts do not reconcile. Either the stream row was left open across a period
when capture was not actually delivering frames, or frames were lost on a scale the gap
counters did not record. `HANDOVER.md` already documents a related trap — stream rows
left unclosed by killed processes, which once made capture coverage read 1302% — so a
stale row is the more likely explanation, but it has not been confirmed.

**This matters beyond bookkeeping**: capture coverage in the history view is computed
from these rows, so if they are wrong, the coverage bar is wrong, and the coverage bar
is the thing that distinguishes a quiet night from a dead microphone.

## What has been ruled out or made unlikely

- **SD-card write bandwidth.** Clips now go to a dedicated SSD. Gaps persist.
- **Evidence writing blocking the detector.** Fixed 2026-08-05: evidence extraction runs
  through a bounded queue and its own single-thread executor. Detectors show
  `dropped=0`, lag ~0.14 s.
- **Clip write volume as the direct trigger.** Correlation is weak: one gap fell 27 s
  after the last clip write. Write rate is roughly 4.6 MB every 3 s (~1.5 MB/s), which
  is trivial for the SSD.
- **The microphone itself.** Continuity was 0.999842 with zero overruns for a sustained
  period earlier the same afternoon on the same hardware.

## Hypotheses, untested, in the order I would test them

1. **USB bus or host-controller contention between the AudioMoth and the SSD.**
   *This is the one I was about to test when the session ended.* The AudioMoth is an
   isochronous USB audio device; isochronous transfers are exactly what gets starved
   when a bulk-transfer storage device shares a controller. From `lsusb` earlier today:
   the AudioMoth enumerated on **Bus 002** and the SanDisk SSD on **Bus 004**, and both
   of those buses were listed as `1d6b:0002` — USB **2.0** root hubs. If the SSD has
   negotiated USB 2.0 rather than 3.0, it is both slower than it should be and more
   likely to be sharing bandwidth with the audio device.
   - Check: `lsusb -t`, and `cat /sys/bus/usb/devices/*/speed` alongside `product`.
   - Likely fix: move the SSD to a **USB 3.0 port** (blue) on a different controller
     from the AudioMoth, then re-measure.
   - This costs nothing to try and would explain why better storage did not help.

2. **Capture reads share the default thread pool.** `alsa_source.read` uses
   `asyncio.to_thread`, which is the default executor. Evidence extraction and the
   retention sweep were moved to their own executor on 2026-08-05, but database inserts,
   gap-row writes and health-event writes still use the default pool. Giving the capture
   read a dedicated executor would apply "capture always wins" to thread scheduling as
   well as to queue policy. This is a code change in `station.py` / `alsa_source.py`.

3. **ALSA period/buffer sizing too tight for scheduling jitter.** 100 ms blocks at
   384 kHz is a large period; if the buffer is only a couple of periods deep, any
   scheduling delay overruns it. Worth reading what `alsa_source` actually requests and
   whether a deeper ring would absorb jitter without adding latency to detection.

4. **CPU contention from restored settings.** Ultrasonic rendering was returned to
   `both` (heterodyne *and* time expansion) and clips to 20/minute at 14:12. The 11-minute
   sample above is the first measured under those settings, and it is the worst of the
   day. **Reverting these is the cheapest experiment** and the fastest way to isolate
   cause from coincidence.

## Configuration state on the station right now

`config/runtime.env` on the Pi (not in version control):

```
OO_ULTRASONIC_SCHEDULE=night
OO_CLIP_MAX_PER_MINUTE=20          # was temporarily 6 to protect the SD card
OO_ULTRASONIC_AUDIBLE_METHOD=both  # was temporarily heterodyne-only
OO_CLIPS_REQUIRE_MOUNT=true
OO_CLIP_MAX_TOTAL_GB=300
OO_BIRDNET_USE_LOCATION_FILTER=true
```

Backups of the previous state are at `config/runtime.env.bak` and `.bak2` on the Pi.

## Storage, as it now stands

- SanDisk Extreme Portable SSD, 465.8 GB, `/dev/sda`, connected via the UAS driver.
- Wiped 2026-08-08 (it held an unrelated Ubuntu Server 26.04 **amd64** installer).
- Single ext4 partition, label `oo-clips`, UUID `005ab10e-7a3b-4b7d-baa0-07b9aeacddc5`,
  formatted with `-m 1` so 1% is reserved rather than the default 5%.
- Mounted at `/home/observer/open-observatory/data/clips` from `/etc/fstab`:
  `defaults,noatime,nofail,x-systemd.device-timeout=10`.
- Mounted **over the existing clips path deliberately**: `media_asset.storage_uri` holds
  absolute paths for 17,273 assets, so this made the migration a no-op for the database.
  Verified by fetching a clip recorded the previous day through `/api/v1/media/{id}`,
  which returned a valid 384 kHz WAV.
- The database stays on the SD card, so losing the SSD costs evidence, not the station.
- **`data/clips.sdcard-backup` still exists on the SD card and holds the pre-migration
  copy (~21 GB).** It is safe to delete once you are satisfied, and doing so returns
  that space to the system disk. It has deliberately been left in place for one cycle.

## Traps worth knowing for this work

- The mount must exist **before** the service starts. The unit runs in a systemd mount
  namespace (`ProtectHome=read-only`), so a mount made on the host while the station is
  running is invisible to it until a restart. `OO_CLIPS_REQUIRE_MOUNT=true` makes
  `/api/v1/health` report this by name instead of silently writing to the SD card.
- Deploying restarts capture, which resets every counter and voids any measurement in
  progress. Continuity is cumulative from frame zero, so a figure taken seconds after a
  restart is meaningless — take it over minutes.
- `sudo journalctl -u open-observatory --since "-10 min" | grep -c capture.gap` counts
  log lines, not lost audio. See the `missing_frames=0` caveat above.

## How to measure

```bash
# on the Pi
curl -s localhost:8080/api/v1/health | python3 -m json.tool | head -30
sudo journalctl -u open-observatory --since "-10 min" -o short-iso | grep capture.gap
curl -s localhost:8080/api/v1/debug/pipeline   # evidence queue, persistence, rings
```

Known-good targets, from `HANDOVER.md`: continuity 0.9990–0.9997, and **zero** gaps or
overruns in normal running.
