"""Independent audit of the week index fed to the BirdNET range model (ADR-044).

HANDOVER.md 6.3 item 0 left this open: "the week index passed to the range model
was not re-audited; a wrong week would make the priors wrong globally". Checking
the arithmetic is not enough, because a plausible-looking formula can still be
off by a fortnight. So this runs the *real* V2.4 MData model at the station's
coordinates for every week of the year and asks whether the seasonality it
produces matches what is actually true in a the development area garden: swifts and cuckoos
in late spring, fieldfares in winter, woodpigeons all year round. An offset, an
ISO week (1-53) in place of a BirdNET week (1-48), or a cap in the wrong place
would displace those seasons visibly.

Needs the model assets (`oo models fetch`) and the `birdnet` extra; it is
deliberately a script rather than a test, because the assets are never committed
(ADR-006) and a test that needs them would only skip.

    ./.venv/bin/python scripts/birdnet_week_audit.py

Result, 2026-08-09: correct. See ADR-044 for the recorded output.
"""

from datetime import UTC, datetime
from pathlib import Path

from open_observatory.detectors.birdnet import birdnet_week, load_range_model_for_repair

# The Royal Observatory, Greenwich: the repository's neutral reference
# location (ADR-047). The audit checks UK phenology, so any UK location
# serves; no real deployment's coordinates are committed.
LAT, LON = 51.4769, -0.0005

labels, parsed, model = load_range_model_for_repair(Path("models"), LAT, LON)
index = {label: i for i, label in enumerate(labels)}

WATCH = [
    "Apus apus_Common Swift",          # summer visitor: ~May to early August
    "Hirundo rustica_Barn Swallow",    # summer visitor
    "Cuculus canorus_Common Cuckoo",   # late April to July
    "Turdus pilaris_Fieldfare",        # winter visitor
    "Columba palumbus_Common Woodpigeon",  # resident
    "Strix aluco_Tawny Owl",           # resident
    "Megascops kennicottii_Western Screech-Owl",  # North America
    "Psiloscops flammeolus_Flammulated Owl",      # North America
]

print(f"{'species':42s} " + " ".join(f"w{w:02d}" for w in range(1, 49, 2)))
for name in WATCH:
    if name not in index:
        print(f"{name:42s} NOT IN LABELS")
        continue
    row = []
    peak_week, peak = 0, -1.0
    for week in range(1, 49):
        value = float(model.probabilities(week)[index[name]])
        if value > peak:
            peak, peak_week = value, week
        if week % 2 == 1:
            row.append(f"{value:4.2f}")
    print(f"{name:42s} " + " ".join(row) + f"   peak w{peak_week} ({peak:.3f})")

print()
for stamp in ("2026-08-08T21:30:00+00:00", "2026-01-15T08:00:00+00:00"):
    when = datetime.fromisoformat(stamp).astimezone(UTC)
    week = birdnet_week(when)
    priors = model.probabilities(week)
    top = sorted(range(len(labels)), key=lambda i: -float(priors[i]))[:8]
    print(f"{stamp} -> week {week}: " + ", ".join(
        f"{parsed[i][1]} {float(priors[i]):.2f}" for i in top
    ))
