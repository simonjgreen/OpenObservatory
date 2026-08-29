---
aliases:
  - ADR-021
tags:
  - adr
---
# ADR-021: Evidence clips live on their own device, mounted over the clips directory
**Decision:** Evidence clips are stored on a dedicated USB SSD, mounted at
`data/clips` — the path they already occupied. The SQLite database stays on the SD
card. `clips_require_mount` makes the station report itself degraded, by name, when
that mount is absent, rather than quietly writing evidence to the system disk.

**Reason — the SD card could not sustain the write load.** A busy bat night writes
roughly 15 MB per pass across four clips: 15 GB in one night, against a 20 GB
budget already exceeded. Worse, it was competing with capture: ALSA reads go through
`asyncio.to_thread`, so clip writes and the capture read share the default thread
pool, and on **2026-08-05** that produced 11 gaps and 8 overruns in five minutes with
continuity down to 0.997. Note that moving evidence to the SSD on 2026-08-08 did
**not** eliminate overruns — see [[OPEN_INVESTIGATION_CAPTURE_GAPS]].
The device was a real constraint, but it was not the whole cause. Isolating evidence onto its own executor helped but could
not overcome the device.

**Why mounted over the existing path rather than relocated.** `media_asset.storage_uri`
holds **absolute** paths, across 17,273 assets. Moving the files anywhere else would
have orphaned every existing evidence link or required rewriting all of them.
Mounting the new device at the path the data already used made the migration a
no-op for the database: verified afterwards by fetching a clip recorded the previous
day through `/api/v1/media/{id}`, which returned a valid 384 kHz WAV.

**Why the database stays on the SD card.** It is small, and the SD card is the
system disk, which is always present. If the SSD is unplugged the station keeps
capturing, detecting, and serving history — it simply cannot write new evidence.
That confines the failure to the component that can best absorb it.

**Why the service does not depend on the mount.** A `RequiresMountsFor` dependency
would stop the station from starting at all if the SSD were missing, and capture
always wins. The station therefore starts regardless and reports the problem in
`/api/v1/health`, in the same spirit as the synthetic-source fallback: keep
recording, and say loudly what is wrong. `nofail` in `/etc/fstab` matches this.

**Constraint — the mount must exist before the service starts.** The unit runs in a
systemd mount namespace (`ProtectHome=read-only`, with `ReadWritePaths` covering
`data`), so a mount created on the host *after* the service starts is not visible
inside it. Mounting the SSD while the station is running requires a restart before
it takes effect, and the health check is what makes that state visible rather than
silent.

> **Not the cause of the missing clips ([[ADR-057]]).** 8,067 `media_asset` rows
> claim files that are gone, and the boundary lines up with this ADR closely
> enough to look damning. It is the opposite: the SSD raised the clip budget
> from 20 GB to 300 GB and is what *stopped* `ClipManager.enforce_retention`
> deleting a rolling 24-hour window off the SD card without telling the
> database. The migration verified in the paragraph above did exactly what it
> claims; the files it did not copy had already been unlinked days earlier.

**Consequence:** the throttles imposed to protect the SD card were lifted — clips
per minute restored from 6 to 20, ultrasonic rendering restored from heterodyne-only
to both heterodyne and time expansion, and the budget raised from 20 GB to 300 GB
against 458 GB of storage. The full analysis view of every bat pass is available
again.

---
Part of the [[ADRS|Architecture Decision Record index]].
