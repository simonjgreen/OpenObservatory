"""Print the settings tier table, so the docs quote the code rather than
paraphrase it. Run: `PYTHONPATH=src python scripts/settings_table.py`."""

from __future__ import annotations

from open_observatory.site_settings import (
    CATEGORIES,
    EDITABLE_SETTINGS,
    NON_EDITABLE,
    default_for,
    display_value,
    label_for,
    unit_for,
)
from open_observatory.tuning import LIVE_TARGETS

TITLES = {category.id: category.title for category in CATEGORIES}


def main() -> None:
    for category in CATEGORIES:
        fields = [s for s in EDITABLE_SETTINGS if s.category == category.id]
        if not fields:
            continue
        print(f"\n#### {category.title}\n")
        print("| setting | tier | default | notes |")
        print("|---|---|---|---|")
        for spec in fields:
            unit = unit_for(spec)
            default = display_value(default_for(spec.name))
            tier = "live" if spec.tier == "live" else "restart-pinned"
            if spec.tier == "live" and spec.name in LIVE_TARGETS:
                tier = "live (pushed)"
            note = "**warns before saving.** " if spec.danger else ""
            note += label_for(spec)
            print(
                f"| `{spec.name}` | {tier} | `{default}`{f' {unit}' if unit else ''} | {note} |"
            )
    print("\n#### Not editable from a browser\n")
    print("| setting | why |")
    print("|---|---|")
    for name, reason in sorted(NON_EDITABLE.items()):
        print(f"| `{name}` | {reason} |")


if __name__ == "__main__":
    main()
