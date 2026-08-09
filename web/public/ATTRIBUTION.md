# Favicon and app icons — provenance and licence

`favicon.svg`, `favicon.ico`, `apple-touch-icon.png`, `icon-192.png` and
`icon-512.png` in this directory are all derived from a single upstream
glyph: the Material Design Icons **`bird`** icon, from the
[Pictogrammers](https://pictogrammers.com/) icon set. Per the operator's
instruction (HANDOVER.md §6.3a) and this project's rule against fabricating
assets from memory, the path data below was fetched from the canonical
upstream package rather than hand-drawn or reconstructed, in the same manner
`models/manifest.tsv` records provenance for model assets.

## Upstream source

| Field | Value |
|---|---|
| Package | [`@mdi/svg`](https://www.npmjs.com/package/@mdi/svg) |
| Package version | `7.4.47` |
| Tarball | `https://registry.npmjs.org/@mdi/svg/-/svg-7.4.47.tgz` |
| Tarball SHA-256 | `de92e5dc9ce46c392ab5c53aa7190b19f82b40cb48872a083f788c7e13e91fef` |
| File within package | `package/svg/bird.svg` |
| `bird.svg` SHA-256 | `70e0790bd69196c357bf47fe353941eb5e3614a46058a8622f3f4661048deec1` |
| Icon author (per package `meta.json`) | Michael Irigoyen |
| Icon added in MDI version | 5.6.55 |
| Licence | Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0 |
| Upstream project | https://github.com/Templarian/MaterialDesign |

The tarball was downloaded directly from the npm registry and extracted; the
`<path>` data in `favicon.svg` below is copied verbatim from
`package/svg/bird.svg`'s single `<path d="...">`, unmodified:

```
M23 11.5L19.95 10.37C19.69 9.22 19.04 8.56 19.04 8.56C17.4 6.92 14.75 6.92 13.11 8.56L11.63 10.04L5 3C4 7 5 11 7.45 14.22L2 19.5C2 19.5 10.89 21.5 16.07 17.45C18.83 15.29 19.45 14.03 19.84 12.7L23 11.5M17.71 11.72C17.32 12.11 16.68 12.11 16.29 11.72C15.9 11.33 15.9 10.7 16.29 10.31C16.68 9.92 17.32 9.92 17.71 10.31C18.1 10.7 18.1 11.33 17.71 11.72Z
```

## What was changed, and why

Apache-2.0 permits redistribution and modification with attribution; the
only changes made here are presentational, not to the path geometry itself:

- **Background tile.** The bare glyph is a single colour with no background;
  at 16px (favicon size) an unfilled outline on a transparent/white page
  background reads poorly and was hard to distinguish from browser chrome.
  A rounded dark tile (`#08090d`, this project's own `--bg` token from
  `web/src/styles.css`) was added behind it so the icon reads as an app tile
  rather than a stray mark, matching how the OS renders `apple-touch-icon`
  and PWA icons regardless of the browser theme.
- **Colour.** The glyph is filled with `#5ce08a`, this project's own
  `--bird` token — the same green already used elsewhere in the dashboard
  for bird-taxon UI (`web/src/styles.css`). Upstream MDI icons ship with no
  fixed colour (they inherit `currentColor` by convention); picking the
  app's own bird-taxon colour is a design choice, not a licence obligation.
- **Scale and centring.** The glyph's own bounding box already runs almost
  edge-to-edge within its 24x24 canvas. It was scaled to 80% and centred
  (`translate(2.4,2.4) scale(0.8)`) to leave a margin against the new
  background tile — otherwise the wingtips touch the tile's rounded
  corners at small sizes. This is a `transform`, not a path edit; the
  underlying `d` attribute is untouched from upstream.
- **Rasterisation.** `favicon.ico` (16/32/48px), `apple-touch-icon.png`
  (180px) and `icon-192.png`/`icon-512.png` were rendered from
  `favicon.svg` with Inkscape 1.x, run locally — no external rasterisation
  service was used.

## Attribution

Material Design Icons, `bird` (icon ID `18622D72-42B2-4919-BDB5-DCC77310045B`,
added in MDI 5.6.55), by Michael Irigoyen and the Pictogrammers project —
https://pictogrammers.com/library/mdi/icon/bird/ — licensed under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). No claim
of a new copyright interest in the underlying glyph geometry is made by the
presentational changes above.
