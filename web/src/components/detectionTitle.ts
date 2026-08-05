/** Single source of truth for how a detection's title is composed on screen.
 *
 *  Five render sites (Pipeline, Suggestions x2, History, DetectionDrawer,
 *  Spectrogram) used to print `detection.display_name` directly. That was fine
 *  while `display_name` was the whole story, but it no longer is: a bat pass now
 *  carries a presentational `title_hint` (frequency + candidate species, e.g.
 *  "45 kHz · common pipistrelle?") and a `flags.feeding_buzz` marker, and every
 *  site needs to render both the same way — the hint visually distinct from the
 *  label, never a candidate name without its mandatory '?'.
 *
 *  This module owns composition only. It does not invent a candidate name or a
 *  frequency band itself — those come from the backend's `display_title()`
 *  (`src/open_observatory/display.py`), which is the one place that logic lives.
 */

/** The subset of `Detection` this module needs. Declared narrowly (rather than
 *  importing the full `Detection` type) so it also accepts the loosely-typed
 *  `event.data` payload of a live `detection.created` envelope. */
export interface DetectionTitleSource {
  display_name: string
  title_hint?: string | null
  flags?: { feeding_buzz?: boolean | null } | null
  native_result?: Record<string, unknown> | null
}

export interface DetectionTitle {
  /** The plain label, e.g. "bat pass" or "European Robin". Never null/empty. */
  label: string
  /** Presentational hint, e.g. "45 kHz · common pipistrelle?". Null unless this
   *  is a bat pass with a usable frequency. */
  hint: string | null
  /** True when this pass contains a feeding/terminal buzz. */
  feedingBuzz: boolean
}

/** Compose the three pieces of a detection's title from whatever shape of
 *  detection-like object is at hand — a full `Detection`, a list-response row
 *  (no `native_result`), or a live `detection.created` event's `data`. */
export function formatDetectionTitle(
  detection: DetectionTitleSource | null | undefined,
): DetectionTitle {
  if (!detection) {
    return { label: 'unknown', hint: null, feedingBuzz: false }
  }
  const label = detection.display_name || 'unknown'
  const hint = detection.title_hint ?? null
  // `flags.feeding_buzz` is the list-friendly marker (added so history/list rows
  // can show it without shipping the whole native_result blob); fall back to
  // native_result directly for shapes that carry it but not flags (e.g. the live
  // WebSocket envelope and the detection detail response).
  const feedingBuzz = Boolean(
    detection.flags?.feeding_buzz ?? detection.native_result?.has_feeding_buzz ?? false,
  )
  return { label, hint, feedingBuzz }
}

/** Convenience for plain-text contexts (canvas labels, mono summary lines) that
 *  cannot render the hint in its own styled span. Joins with the same
 *  " · " separator used throughout the UI. */
export function formatDetectionTitleText(
  detection: DetectionTitleSource | null | undefined,
): string {
  const { label, hint, feedingBuzz } = formatDetectionTitle(detection)
  const parts = [label]
  if (hint) parts.push(hint)
  if (feedingBuzz) parts.push('feeding buzz')
  return parts.join(' · ')
}
