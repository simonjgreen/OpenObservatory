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
  flags?: { feeding_buzz?: boolean | null; withdrawn?: boolean | null } | null
  withdrawn?: boolean | null
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
  /** True when a plausibility review has withdrawn this claim (ADR-044). The
   *  station still returns the row — the prior verdict stays visible and
   *  attributable — so every render site must show it *as withdrawn* rather
   *  than dropping it or, worse, printing the species name unqualified. */
  withdrawn: boolean
}

/** Compose the three pieces of a detection's title from whatever shape of
 *  detection-like object is at hand — a full `Detection`, a list-response row
 *  (no `native_result`), or a live `detection.created` event's `data`. */
export function formatDetectionTitle(
  detection: DetectionTitleSource | null | undefined,
): DetectionTitle {
  if (!detection) {
    return { label: 'unknown', hint: null, feedingBuzz: false, withdrawn: false }
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
  // Three sources, same reason as feedingBuzz: the list rows carry `flags` and a
  // top-level `withdrawn`, while a live WebSocket envelope carries only the raw
  // `native_result`. Missing the flag would print a retracted species name as
  // fact, so every shape this function accepts is checked.
  const withdrawn = Boolean(
    detection.withdrawn ??
      detection.flags?.withdrawn ??
      isWithdrawnNativeResult(detection.native_result),
  )
  return { label, hint, feedingBuzz, withdrawn }
}

/** The station writes its review under `native_result.plausibility_review`
 *  (`src/open_observatory/plausibility.py`). Read defensively: this is the only
 *  place the raw shape is known in the UI. */
function isWithdrawnNativeResult(native: Record<string, unknown> | null | undefined): boolean {
  const review = native?.plausibility_review
  if (!review || typeof review !== 'object') return false
  return Boolean((review as Record<string, unknown>).implausible)
}

/** Convenience for plain-text contexts (canvas labels, mono summary lines) that
 *  cannot render the hint in its own styled span. Joins with the same
 *  " · " separator used throughout the UI. */
export function formatDetectionTitleText(
  detection: DetectionTitleSource | null | undefined,
): string {
  const { label, hint, feedingBuzz, withdrawn } = formatDetectionTitle(detection)
  const parts = [label]
  if (hint) parts.push(hint)
  if (feedingBuzz) parts.push('feeding buzz')
  // Last, but never omitted: a plain-text context (a canvas label on the
  // spectrogram) is exactly where an unqualified species name would be read as
  // an observation.
  if (withdrawn) parts.push('withdrawn')
  return parts.join(' · ')
}
