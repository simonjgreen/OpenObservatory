# ADR-050: The counter-top display gets two OTA app slots and updates itself from the station, with its own rollback
**Status:** active. **Written unflashed, then flashed and verified on hardware
the same day, 2026-08-09** — including a deliberate rollback drill. The
"What is unverified" section near the end is kept as it was written, with the
verification appended after it, because what a cable found that a test suite
could not is the transferable part.

**Decision:** Replace the inside observer's single-app-slot partition table with
two equal OTA slots, and give the station a firmware image to serve and a button
to roll it out. The display fetches the image over the WebSocket connection it
already has (ADR-038), verifies a SHA-256 before anything becomes bootable, and
puts the previous build back by itself if the new one cannot reach the station.
**NVS stays exactly where it is**, so the WiFi credentials inherited from the
the board's previous *Aura* firmware — which nobody has ever seen and nobody can retype —
survive.

### Why this is a change worth making on a working display

ADR-023 kept the stock partition table byte for byte, for two reasons that are
both still good: NVS at `0x9000` holds credentials this project never captured,
and a whole-image restore of the stock firmware stays a plain `write_flash 0x0`.
What that ADR did not weigh — because the display was then on a bench next to a
laptop — is that the stock table has **one** app partition. There is no `ota_1`.
`esp_ota_get_next_update_partition` has nowhere to write and `otadata` has
nothing to arbitrate, so ESP32 OTA cannot work at all, and every future firmware
change is a physical trip and a USB cable, forever.

Changing a partition table requires exactly the physical access such a trip
provides. So the cost of *not* doing it during one is not "we do it next time";
it is "every future change costs another trip, until one of them happens to be
the trip where we remember". That asymmetry is the whole argument.

### The layout, and every number in it

```
0x000000  bootloader                       written by upload
0x008000  partition table       0x1000     this table
0x009000  nvs                   0x5000     UNCHANGED - see below
0x00e000  otadata               0x2000     unchanged; now has two slots to arbitrate
0x010000  app0 / ota_0        0x1F0000     1,984 KB
0x200000  app1 / ota_1        0x1F0000     1,984 KB
0x3F0000  coredump             0x10000     unchanged offset and size
                              --------
                              0x400000     4 MB exactly, nothing spare
```

**NVS is not negotiable and is not moved.** Same offset, same size, same
position in the table. `esptool write_flash` writes only the offsets it is
given, and a normal upload writes `0x1000`, `0x8000`, `0xe000` and `0x10000` —
so NVS is untouched by the flash that installs this table. It stays untouched
only while it occupies exactly these bytes: moving or resizing it invalidates
every entry, including `nvs.net80211`, and there is no copy of those credentials
anywhere.

**The stock restore is unaffected.** `write_flash 0x0 firmware-backup.bin`
writes all 4 MB including the stock partition table at `0x8000`, so it
overwrites this table along with everything else. The restore does not need to
know this table exists, and the README's restore command is unchanged.

**Where the space comes from.** The stock 3 MB `app0` and the 896 KB `spiffs`
partition. SPIFFS was reclaimed rather than kept because nothing in `src/` ever
opened a filesystem — unused flash on a device you cannot reach is worth
nothing, whereas headroom on it is worth a journey. `coredump` keeps its stock
offset so a panic dump still comes from the address the old notes name.

**Why 1,984 KB and not 1,536 KB.** Both fit. The build that motivated this
change was 962,437 bytes; adding the OTA client itself — `HTTPClient`, `Update`
and mbedtls' SHA-256 — took it to **1,127,649 bytes, 55.5% of a slot**. At
1.5 MB slots that would already be 75%, and the feature that makes a slot
necessary is also the feature that fills a sixth of it. The remaining 883 KB is
the point of the size, not slack: a slot the firmware outgrows in six months
converts a software update back into a car journey, which is the cost this whole
ADR exists to buy out.

### Push, or check on connect? Both, and they catch different displays

- **Push over the existing channel.** A new frame type on ADR-038's socket
  (`{"t":"u","fv":…,"sha":…,"sz":…,"p":…}`, under 200 bytes). The station is
  already connected to every display it would need to tell, so the alternative —
  the display polling for an update — would put periodic requests back on a wire
  whose entire justification was 11.4 B/s. Push costs nothing when there is
  nothing to say.
- **A version check on connect.** The display appends `&fw=0.2.0` to the socket
  URL and the station offers the published image if it is strictly newer. Not
  redundant with push: push reaches a display that has been connected for a week
  and would never ask; the connect check reaches one that was unplugged,
  rebooting, or on the far side of a Wi-Fi outage while the rollout ran. Neither
  sends a byte when the versions already agree.

`fw` is also how `/api/v1/station` and the settings page can say which build is
on the glass — and say **"unknown"** for a display predating this ADR, which
does not report one. Unknown is not the same claim as out of date, and the UI
says so.

### Safety is the requirement, not a feature

A bricked display is a physical trip, which is the exact cost being bought out.
So the mechanism is designed around its failures rather than its successes:

- **The digest is checked before anything is committed.** SHA-256 is computed
  over every byte as it arrives and compared *before* `Update.end()`. A truncated
  or corrupted download costs ninety seconds and nothing else. The size is
  checked twice — against the offer, and against the response's `Content-Length`
  — and a chunked response is refused outright, because an image of unknown
  length cannot be checked before it is committed.
- **Rollback is the bootloader's, so it does not depend on this firmware being
  correct.** `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` in
  `framework-arduinoespressif32` 3.20017 (read out of that package's
  `tools/sdk/esp32/sdkconfig`, not assumed), so a freshly written image boots
  once in `ESP_OTA_IMG_PENDING_VERIFY`. If it reboots without being marked
  valid, the previous slot comes back with no help from the application. **A
  crash loop therefore fixes itself on the second boot.**
- **The case the bootloader cannot see is a build that runs happily and cannot
  reach the station.** Nothing reboots it, so nothing rolls it back. Hence a
  ten-minute deadline: no hello frame and no completed portal by then, and the
  firmware calls `esp_ota_mark_app_invalid_rollback_and_reboot()` itself. Ten
  minutes rather than one because a station restarting at the same moment, a DHCP
  lease and a domestic 2.4 GHz band having a bad afternoon are each several
  minutes of not-the-firmware's-fault.
- **What counts as proof is doing the job, not being alive.** Not "WiFi
  associated" and not "socket opened" — a *hello frame understood*. That is what
  this firmware exists to render.
- **Never while somebody is looking at it.** An update is deferred while the
  settings, keypad or portal screens are up, while the glass has been touched in
  the last two minutes, and while the newest row on the feed is under a minute
  old. An ambient display's one time-critical moment is a detection appearing;
  going black a second after a barn owl lands is the worst possible ninety
  seconds to choose.
- **A refusal and a deferral are different.** A malformed offer, an older version
  or an oversized image is dropped and logged with its reason; a deferral keeps
  the offer and re-evaluates it a minute later. Getting this backwards either
  retries a hopeless offer forever or throws away a good one because somebody
  walked past.

**Power loss, specifically, because it is a question with a real answer:**

| When | What happens |
|---|---|
| Mid-download | `Update.write()` fills the *inactive* slot; `otadata` is untouched until `Update.end()`. The board reboots into the image it was already running. The half-written spare slot is simply overwritten next time. Nothing is lost and nothing is decided. |
| Between the digest check and `Update.end()` | The same: nothing has been committed. |
| Between `Update.end()` and the reboot | `otadata` points at the new slot and the new image boots in `PENDING_VERIFY` — exactly where it would have been anyway. Probation proceeds normally. |
| During the *first* boot of a new image | Still `PENDING_VERIFY`. The bootloader rolls back on the next boot. |

### The provisioning portal is not broken, and is treated as a pass

A display that has lost its WiFi must still be recoverable without a cable, so
the portal path is untouched: an update is never started while the portal is
running, and OTA is only attempted with a station host configured and a socket
that has spoken.

One trap this raised, and how it is answered: if the operator reprovisions WiFi
through the portal on a probationary build, the restart that follows would make
the bootloader roll back — throwing away credentials they had just typed. So **a
completed portal submission counts as proof and marks the image valid.** Not for
convenience: the portal is the recovery path that needs no cable, the build
served it, and a build that gets a human out of a WiFi hole is by definition not
bricked.

The converse is deliberate too. A *settings save* on a probationary build that
has never reached the station does roll back, because an image that renders a
settings page but cannot reach the station is precisely the failure this exists
to undo.

### What this does not defend against, said plainly

The image is fetched over plain HTTP on the LAN, and the digest that proves
integrity is supplied by the same party that supplies the image. So this defends
against **corruption**, not **substitution**: anyone who can already answer for
the station's address on this LAN can offer a display a binary, and the digest
they supply will match the binary they supply. The station's own upload
validation (0xE9 magic, chip id, `esp_app_desc_t`) is about the likely *mistake*
— uploading `firmware.elf`, a whole-flash backup, or an ESP32-S3 build — not
about intent.

Signing the image with a key baked into the firmware is the fix, and it is
deliberately **not** in this change: it needs key custody the operator has not
been asked about, and a signing scheme with a lost key turns every future update
back into a cable. It is recorded here as the next thing, not as an oversight.
Mitigating in the meantime: the offer frame carries a *path*, never a host, and
the firmware refuses a `p` that does not begin with `/`, so a frame cannot
redirect the fetch off the station it is already talking to.

### Two implementations of one rule, on purpose

Version ordering exists in C++ (`model/ota_policy.cpp`) and in Python
(`firmware_store.py`), with the same cases asserted on both sides. They are
different processes on different machines, so this is not duplication to be
refactored away — but it *is* a drift hazard, so the rule was made as small as it
can be: 1–4 dot-separated runs of digits, and a refusal to parse anything else.
A suffix scheme ("0.2.0-rc1") is rejected rather than ordered by guesswork, on
both sides, because guessing which of two images is newer is how a display
installs a release candidate over a release and then declines to take the
release back. A station that accepts a version the display refuses to parse is a
rollout that silently never lands — which is also why the Python side rejects
the non-ASCII digits `str.isdigit()` would otherwise accept.

### What was unverified when this was written, and it was not a footnote

> **Superseded the same day — see "Verified on hardware" immediately below.**
> Kept verbatim because it named exactly the right risks, and because three of
> the things it said only a cable could verify turned out to be defects.

**Nothing here has been flashed.** The device was not connected while this was
written. Verified: the host test suite (89 cases, up from 53), the `cyd` build
and its size against the new table, the station's 50 new tests, and that
`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` in the pinned framework package. **Not**
verified, and only a cable can verify it: that the new partition table installs
cleanly, that NVS survives it in practice, that a display downloads and installs
an image from the station, that the SHA-256 path passes on real bytes, that a
deliberately broken build rolls back, and that the update screen renders.

This matters more than usual because the *first* flash is the one that installs
the new table, and it is the one that must be right — after it, a mistake is
recoverable over the air; before it, nothing is.

### Verified on hardware, 2026-08-09 — and what only a cable could find

Every item the section above said only a cable could settle was settled the same
day, in this order:

1. **The partition table took.** Flashed over the cable and then *read back off
   the device* rather than inferred from the upload: `app1` at `0x200000`,
   subtype `0x11`, two slots. NVS at `0x9000` was never inside an erase range,
   and the display rejoined WiFi on its inherited credentials with no
   reprovisioning — the property this ADR was most careful about, confirmed in
   practice rather than by reading offsets.
2. **Two images installed over the air from the Pi.** `0.2.1` then `0.2.2`:
   digest verified against real bytes before anything became bootable, slots
   alternating `app1` → `app0`, ending
   `[ota] running image marked valid (ESP_OK)`. The update screen rendered.
3. **A deliberate rollback drill, with no cable.** A build that could never reach
   the station booted `(PENDING VERIFY - on probation)` at 17:51:41 and rolled
   *itself* back at 18:01:40 — exactly the 600 s deadline — returning to the
   previous known-good image, which rejoined WiFi six seconds later.
4. `0.2.4` shipped over the air afterwards and is what the device runs now.

**Two real defects were found by doing this, and neither could have been found
any other way.** They are recorded here rather than quietly fixed, because an ADR
that reads as though it worked first time teaches nothing:

1. **The rollback net was disarmed by the framework, and the whole probation
   machinery was unreachable code.** `arduino-esp32` declares
   `verifyRollbackLater()` as a weak symbol returning `false` and acts on it
   inside `initArduino()`, *before* `setup()` runs — so a `PENDING_VERIFY` image
   was marked valid immediately, and `evaluateProbation` could only ever return
   `kNotOnProbation`. Overriding the weak symbol hands the decision back. This is
   the most important sentence in this ADR: **a safety net that never arms is
   indistinguishable, from the outside, from one that never needs to fire.** The
   89 host tests all passed against it.
2. **This ADR's own verification step would have reported failure on success.**
   It told the operator to read `sketch <n> of 2031616` and treat `3145728` as
   the failure signature. The banner did not print that — it printed
   `ESP.getFreeSketchSpace() + ESP.getSketchSize()`, and `getFreeSketchSpace()`
   returns the size of the *next* OTA partition, so a correctly repartitioned
   board read `1134224 of 3165840`. An operator following these instructions
   would have concluded a successful flash had failed and reflashed. The banner
   now prints the running slot's own size and states the two-slot question
   outright. Separately, `scripts/serial_capture.py` — which the firmware README
   told the operator to run at the one moment that matters — did not exist. It
   does now.
3. **Two smaller findings from the drill itself.** The provisioning AP was named
   `Aura` -- the name of the weather-display project this board previously ran,
   not the manufacturer's: two boards in one house would raise two
   identically named open networks. It is now
   `Observatory-<last three MAC bytes>`. And the WiFi failure path printed "no
   usable credentials" for *any* association failure including a plain timeout —
   it fired on a transient radio failure mid-drill and sent the operator hunting
   for wiped NVS when the credentials were fine and the device rejoined by itself
   two minutes later. It now reports the actual `wl_status_t` and says outright
   that this path clears nothing.

**Three defects surfaced that the 89 host tests, the size check and the
framework-config check could not have surfaced.** They are the argument for the
"and this is not a footnote" heading above, so they are recorded rather than
quietly fixed:

1. **The entire rollback machinery was unreachable code.** `arduino-esp32`
   declares `verifyRollbackLater()` as a weak symbol returning `false` and acts
   on it inside `initArduino()`, *before* `setup()` runs — so a pending image was
   marked valid immediately, `evaluateProbation` could only ever return
   `kNotOnProbation`, and nothing this ADR designed could fire. Overriding the
   weak symbol hands the decision back. Confirmed with `0.2.2`: the display boots
   `(PENDING VERIFY - on probation)`, alternates `app1` → `app0`, logs
   `this build reached the station (hello frame); confirming` and then
   `running image marked valid (ESP_OK)`, and `otadata` reads VALID afterwards.
   **The first flash is not the only thing a cable is needed for; a safety net
   that never arms looks exactly like one that never fires.**
2. **The check meant to verify the flash would have reported failure on
   success.** The boot banner printed
   `ESP.getFreeSketchSpace() + ESP.getSketchSize()`, and `getFreeSketchSpace()`
   returns the size of the *next* OTA partition — so a correctly repartitioned
   board read `1134224 of 3165840`, and an operator following this ADR's own
   instructions would have concluded the flash had failed. It now prints the
   running slot's own size and states the two-slot question outright. Also,
   `scripts/serial_capture.py`, which the firmware README told the operator to
   run at the one moment that matters, did not exist. It does now.
3. **Two findings from the drill itself.** The provisioning AP was named `Aura`
   (the previous *Aura* firmware's name, chosen for recognisability), which gives two boards
   in one house two identically named open networks; it is now
   `Observatory-<last three MAC bytes>`. And the WiFi failure path printed
   "no usable credentials" for *any* association failure including a plain
   timeout — it fired on a transient radio failure mid-drill and sent the
   operator hunting for wiped NVS when the credentials were fine and the device
   rejoined by itself two minutes later. It now reports the actual `wl_status_t`
   and says outright that this path clears nothing.

### Rollback and smoke test (ADR-050)

The station half reverts cleanly: `git revert` removes the endpoints, and the
display never receives an offer. `data/firmware/` can be deleted by hand or with
`DELETE /api/v1/firmware`; nothing else reads it. No schema change, no migration,
no new dependency on either side.

The firmware half is asymmetric, in the safe direction. Once the new partition
table is installed, reverting the *application* is an over-the-air update or a
cable flash exactly as before; reverting the *table* means a cable, and there is
no reason to — the old table is a strict subset of what this one can do, and the
whole-image stock restore works either way.

```bash
# 1. Host logic, on a laptop. 89 cases now: the 53 from ADR-038 plus version
#    ordering, the digest rule, the deferral gate and the rollback deadline.
cd firmware/inside-observer && pio test -e native

# 2. The station's half, without a display.
pytest -q tests/test_firmware.py

# 3. Publish a build and see who is behind.
curl -s -X POST --data-binary @firmware/inside-observer/.pio/build/cyd/firmware.bin \
  -H 'content-type: application/octet-stream' \
  'http://<station-host>:8080/api/v1/firmware?version=0.2.1'
curl -s http://<station-host>:8080/api/v1/firmware | python3 -m json.tool

# 4. Roll out. "offered" is how many were *told*; it is not "installed".
curl -s -X POST http://<station-host>:8080/api/v1/firmware/rollout

# 5. What actually landed, which only the display can say.
curl -s http://<station-host>:8080/api/v1/station \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["display_channel"]["per_client"])'
```

On the serial monitor, the lines that constitute a pass:

```
app slot   : app1  (PENDING VERIFY - on probation)
[ota] station offers 0.2.1 (1127649 bytes)
[ota] 1127649 bytes verified against the offered digest
[ota] this build reached the station (hello frame); confirming
[ota] running image marked valid (ESP_OK)
```

And the one that constitutes a *successful failure*, worth provoking deliberately
once with a build pointed at a nonexistent station host:

```
[ota] this build never reached the station; rolling back
```
