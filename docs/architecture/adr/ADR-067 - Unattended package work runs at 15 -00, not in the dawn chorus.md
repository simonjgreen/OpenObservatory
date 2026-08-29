# ADR-067: Unattended package work runs at 15:00, not in the dawn chorus
**Status:** accepted, 2026-08-21

### The problem

Ubuntu ships `apt-daily.timer` and `apt-daily-upgrade.timer` as:

    OnCalendar=6:00
    RandomizedDelaySec=60m
    Persistent=true

so package downloads and installs fire at a random moment between 06:00 and
07:00 local. On this station that is close to the worst available choice.
Detections per hour over the preceding fourteen days:

| local hour | share | |
|---|---|---|
| 05:00 | 6.8% | ████████ |
| **06:00** | **10.0%** | ████████████ |
| **07:00** | **11.3%** | ██████████████ |
| 08:00 | 9.3% | ███████████ |
| … | | |
| **15:00** | **2.7%** | ███ |
| 16:00 | 3.8% | ████ |

06:00 and 07:00 are the two busiest hours of the day — the peak of the dawn
chorus — and they carry **four times** the activity of the afternoon trough.

The randomness makes it worse rather than better. A sixty-minute jitter means
an apt run that competes with the pipeline lands somewhere different every
morning, so the days it cost something and the days it did not are
indistinguishable after the fact. This project has spent enough time on
instruments that could not tell those apart.

### Why not the middle of the night, which is quieter still

Bird detections bottom out at 01:00-04:00 (0.5-0.8% per hour), and that looks
like the obvious answer. It is not, because nothing else about those hours is
idle:

* `OO_ULTRASONIC_SCHEDULE=night` puts the ultrasonic detector under observation
  from dusk to dawn. A low *bird* count at 02:00 is not an idle station; it is
  the bat window, and bat passes are the evidence the ultrasonic path exists
  for.
* `open-observatory-refine.timer` fires at 02:02 and runs 30-40 minutes of
  BatDetect2 inference (measured: 1,843 s and 2,344 s on the two most recent
  passes).

So the quietest hours by detection count are the most contended hours by CPU.

### Decision

Both timers move to **15:00 local**, with `RandomizedDelaySec=15m` and
`Persistent=false`, via a drop-in installed over each
(`deploy/apt-daily-quiet-hours.conf`).

* **15:00** is the free trough: lowest daytime activity, clear of the dawn
  chorus, clear of the dusk rise from 17:00, and clear of the bat and
  refinement window overnight.
* **15 minutes of jitter, not 60.** An hour of jitter reaches 16:00, by which
  point activity is already climbing back toward dusk. Fifteen keeps every run
  inside the measured trough while still spreading load across mirrors.
* **`Persistent=false`.** A catch-up run after a reboot is the one thing that
  could put apt back into the dawn chorus without warning — exactly what this
  ADR exists to prevent. The station runs continuously, so it meets the window
  on its own; there is nothing to catch up.
* **Mid-afternoon is also when a human is plausibly awake.** A package update
  that breaks something at 06:30 is discovered hours later, by its
  consequences. At 15:00 it is discovered by the person who is already there.

### Not applied by `deploy.sh`

This is host policy, not application deployment. `deploy.sh` installs the
station's own units and should not quietly reconfigure the operating system's
package schedule underneath an operator who did not ask for it. The drop-in
lives in `deploy/` so it is version-controlled and reproducible after a
reimage, and the install command is in
`docs/operations/DEPLOYMENT_AND_OPERATIONS.md`.

### Consequences

- Security updates land up to nine hours later than they would have. Accepted:
  this is a LAN-only station with no inbound exposure, and the charter ranks
  capture correctness above it.
- Applying the drop-in with `systemctl restart apt-daily-upgrade.timer`
  triggered one immediate run of the service (observed 2026-08-21 19:14). It
  was a no-op — 727 ms, exit 0, nothing added to `/var/log/apt/history.log` —
  because that day's upgrade had already run at 07:02. Worth knowing before
  doing this during a measurement: prefer `stop` then `start`, or apply it
  outside a soak.
- The staged `linux-image-6.8.0-1061-raspi` still needs a manual reboot to take
  effect. Nothing reboots this station automatically:
  `Unattended-Upgrade::Automatic-Reboot` is unset, confirmed with
  `apt-config dump` rather than by grepping the config files.
