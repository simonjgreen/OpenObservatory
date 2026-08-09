# Test fixture audio — provenance and licence

This directory holds a committed reference recording used by
`tests/test_birdnet_fixture.py` to close the Milestone 3 exit gate: a known
species from a known recording must produce the expected candidate label and
an aligned, playable clip. Unlike the BirdNET *model* assets (ADR-006, fetched
separately via `oo models fetch` and never committed), this is a short
third-party audio recording, not model code or weights, and its own licence
explicitly permits redistribution — so it is committed directly rather than
fetched on demand.

**Do not add a recording here without checking the licence of that specific
file.** Xeno-canto licences vary per recording; many are CC BY-NC-SA, which is
not freely redistributable for this purpose. This one was individually
checked.

## `erithacus_rubecula_XC441752.mp3`

| Field | Value |
|---|---|
| Species | European Robin (*Erithacus rubecula*) |
| Recording type | song |
| Xeno-canto ID | [XC441752](https://xeno-canto.org/441752) |
| Recordist | Jan Cibulka |
| Date recorded | 2018-11-07 |
| Location | Prague, Czech Republic (50.0634, 14.4263) |
| Notes from recordist | bird-seen: yes; playback-used: no |
| Licence | CC BY-SA 4.0 — https://creativecommons.org/licenses/by-sa/4.0/ |
| Source URL (downloaded from) | https://www.xeno-canto.org/441752/download |
| Mirror (independently verified, byte-identical) | https://commons.wikimedia.org/wiki/File:Erithacus_rubecula_-_European_Robin_XC441752.mp3 |
| SHA-256 | `21d116a92365cf6f753ef90f166eaeeebedf46300dd212a050cfdc28ce2d68ca` |
| File size | 85,817 bytes |
| Format as downloaded | MP3, 44.1 kHz, mono, ~32 kbps, 6.68 s |

**Attribution required by CC BY-SA 4.0:** "European Robin (*Erithacus
rubecula*) song, XC441752, recorded by Jan Cibulka, Prague, Czech Republic,
2018-11-07" — via [Xeno-canto](https://xeno-canto.org/441752), licensed under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). No changes
were made to the audio; the file committed here is bit-identical to both the
xeno-canto download and the Wikimedia Commons mirror (verified by SHA-256,
above) — this repository's copy is not itself a derivative work.

Because this recording is CC BY-SA (share-alike), redistributing it — as this
repository does — keeps it under CC BY-SA 4.0. It is not CC0/public domain;
that was preferred but no suitably clear, well-labelled CC0 UK-robin recording
of adequate quality for a reliable classifier fixture was found during the
research for this test. CC BY-SA still permits redistribution with
attribution, which is what this file and this note provide.

The recording's own date and location are irrelevant to the test: the test
assigns its own explicit date and reference coordinates (the Royal
Observatory, Greenwich — 51.4769, -0.0005, a neutral published reference
location, not any deployment's site) to the BirdNET range/occurrence model,
independent of when or where this clip was originally recorded. See the test
module docstring for why that date was chosen and what the range model says
about it.
