# AudioMoth firmware, switch positions and USB access

Everything here was established on the actual device; see [[TARGET_DIAGNOSTICS]]
for the recorded values.

## The switch is the thing that confuses people

An AudioMoth running USB-Microphone firmware presents a **different USB device**
depending on the three-position side switch:

| Position | Enumerates as | Use it for |
|---|---|---|
| `USB/OFF` | `10c4:0002`, HID + vendor-specific | Configuring sample rate, gain and filters. Flashing firmware. **No audio.** |
| `DEFAULT` | `16d0:06f3`, USB Audio Class | **Normal running.** Streams at the configured rate and gain. |
| `CUSTOM` | `16d0:06f3`, USB Audio Class | Streams, additionally applying the configured filter and advanced settings. |

So: **`DEFAULT` to run the station, `USB/OFF` to change anything about the device.**

A device in `USB/OFF` produces no ALSA card at all. If `oo audio probe` reports no
capture device while the AudioMoth is plainly plugged in and visible in `dmesg`,
check the switch before suspecting anything else.

## Is the right firmware installed?

With the switch in `USB/OFF`:

```bash
oo audiomoth info
```

You want `firmware description: AudioMoth-USB-Microphone`. Anything else (for
example the standard recorder firmware) cannot act as a live microphone.

This command talks to `/dev/hidraw*` directly. Open Acoustic Devices' own helper,
`usbhidtool-linux` bundled with `AudioMoth-HID`, is an **x86-64 ELF** and will not
run on the Pi's aarch64 userland, which is why
`src/open_observatory/hardware/audiomoth_hid.py` exists.

## Flashing USB-Microphone firmware, if needed

Not needed on this station — it shipped with 1.3.2 — but recorded for the next unit.

1. Fetch the firmware binary from the
   [AudioMoth-USB-Microphone releases](https://github.com/OpenAcousticDevices/AudioMoth-USB-Microphone).
2. Set the switch to `USB/OFF` and connect USB.
3. Build the vendor flashing tool, which does compile cleanly on aarch64:

   ```bash
   git clone --depth 1 https://github.com/OpenAcousticDevices/EFM32-Flash.git
   cd EFM32-Flash
   gcc -Wall -std=c99 -I./src/ ./src/main.c ./src/linux/rs232.c -o flash
   ```

4. Put the device into its serial bootloader. `oo audiomoth info` reports whether
   the running firmware supports being asked; the HID command is implemented as
   `AudioMothHid.enter_serial_bootloader()`. The device then re-enumerates as
   `10c4:0003`, a CDC ACM serial port (`/dev/ttyACM0`).
5. Upload, leaving the bootloader in place:

   ```bash
   ./flash -u /dev/ttyACM0 AudioMoth-USB-Microphone.bin
   ```

   Do **not** use `-d`; that overwrites the bootloader and recovery then needs a
   JTAG programmer.

6. Move the switch to `DEFAULT`. A new ALSA card should appear within a second or
   two; confirm with `oo audio probe`.

## Gain and sample rate

These live **on the device**, not in this software. The configured values are
baked into what the firmware advertises — this unit reports itself as
`384kHz AudioMoth USB Microphone` because 384 kHz is what it is set to.

To change them you need the
[AudioMoth USB Microphone app](https://www.openacousticdevices.info/usb-microphone)
with the switch in `USB/OFF`. `oo audiomoth` reads identity only; the app-packet
format for writing configuration is not implemented here, and guessing at it
risks writing nonsense to the device.

**This station's gain is currently hot** — loud nearby sounds clip (see
[[TARGET_DIAGNOSTICS]]). Clipping is visible live in the debug UI's level meters
and counted in the `oo_audio_clipping_ratio` metric. Lowering the device gain one
step would trade a little sensitivity on quiet distant calls for headroom on close
ones.

Note that changing the rate has consequences: below 96 kHz the ultrasonic detector
correctly reports itself unavailable, and the ultrasonic spectrogram channel
disappears, because a band-limited stream cannot contain bat calls.

## USB permissions

`deploy/99-audiomoth.rules` grants non-root access to all three identities and is
installed by `deploy/deploy.sh`. Without it, `oo audiomoth info` needs `sudo`.
The rules deliberately reference USB vendor/product ids and never card numbers,
which are not stable.

## Power

The Pi 5 needs a supply that can actually deliver its rated current. An
underpowered PSU limits USB current, and an AudioMoth that enumerates
intermittently or drops off the bus is a plausible symptom. One was replaced during
commissioning of this station.
