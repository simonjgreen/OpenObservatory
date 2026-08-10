/** Where the spectrogram's detection labels go when there is not room for all of
 *  them (ADR-058).
 *
 *  The overlay used to draw each detection's label at that detection's own left
 *  edge and hope. On the operator's phone two Eurasian Jackdaw detections a
 *  couple of seconds apart produced, in one string of pixels:
 *
 *      Eurasia Eurasian Jackdaw 95%
 *
 *  — the first label overpainted by the second, leaving a fragment of one
 *  species name butted against another. That is not a cosmetic defect. The
 *  charter's honesty constraint says a number or name shown to a human must mean
 *  what its label says, and "Eurasia Eurasian Jackdaw 95%" is not any species,
 *  any score, or any claim the station holds. At 390 px the plot is a few
 *  hundred pixels wide and one woodpigeon calling twice is enough to cause it,
 *  so this is the normal case on a phone rather than an edge case.
 *
 *  Three rules, in this order:
 *
 *  1. **Never two labels overlapping.** A placement that would intersect one
 *     already made is not made.
 *  2. **Never a truncated species name.** No ellipsis, no clipping: a name cut
 *     short can be read as a different, real species ("Northern Rough-winged
 *     Swallow" clipped to "Northern Rough" is not a bird, but "Great Spotted
 *     Woodpecker" clipped to "Great" is a word an operator will finish
 *     themselves, wrongly). The *score* and the *count* may be shed to make a
 *     name fit, because each is a separately-labelled fact whose absence claims
 *     nothing; the name itself may not be shortened.
 *  3. **Dropping a label is honest; overlapping two is not.** When neither of
 *     the above can be satisfied the label is not drawn at all. The box stays,
 *     so the detection is still visible, still countable and still reachable —
 *     it has simply not been named in a place where naming it would lie.
 *
 *  Before any of that, the common case is collapsed rather than fought: a run of
 *  the same title close together in time is one label with a count,
 *  `Eurasian Jackdaw ×3 · best 95%`, which is both what actually happened (one
 *  bird calling repeatedly) and the form the operator already reads in the
 *  suggestions list. The score shown for a run is explicitly labelled `best`,
 *  because an unlabelled 95% next to ×3 would be a number that does not mean
 *  what its label says.
 *
 *  Labels are moved only along the **frequency** axis — y in `scroll`, x in
 *  `waterfall`. Displacement there costs nothing, because a label is already not
 *  a claim about frequency (the box is). Moving one along the *time* axis to
 *  make room would move it to a time at which the sound did not happen, which is
 *  the same class of error this module exists to prevent.
 *
 *  Everything here is pure and works from plain rectangles, so it is testable
 *  without a canvas — which matters, because jsdom stubs `getContext` and the
 *  component tests physically cannot see any of this.
 */

import type { Orientation, Rect, Viewport } from './geometry'

/** Height of a label's background plate, in CSS pixels. */
export const LABEL_HEIGHT = 16
/** Padding between the plate's edge and its text. */
export const LABEL_TEXT_PAD = 4
/** Clearance between a label and the canvas edge, and between two labels. */
const EDGE = 2
const LANE_GAP = 2
/** Least gap along the time axis that can separate two labels of the same title
 *  before they are one run. The real threshold is the label's own size along
 *  that axis — see `runGap` — so that a run collapses to a count exactly when
 *  its labels could not otherwise both be drawn, and not before. This floor only
 *  covers a title so short that its plate is narrower than a detection box. */
const RUN_GAP_PX = 12
/** How many lanes to try before giving up and dropping the label. Bounded so a
 *  crowded frame cannot turn into an unbounded search. */
const MAX_LANES = 9

/** One detection's claim on a label. `rect` is its box on screen, in CSS pixels
 *  and in whatever orientation is being drawn. */
export interface LabelInput {
  /** Identity for merging. Two detections merge into one counted label only if
   *  this matches exactly, so a withdrawn claim never merges with a standing
   *  one and a bat pass at 45 kHz never merges with one at 55 kHz. Passing the
   *  rendered title itself is the safest key: if it matches, the merged label
   *  is literally correct for every member. */
  key: string
  /** The name as it will be shown. Never abbreviated by this module. */
  title: string
  /** 0-1. The best of a run is the one shown, labelled as the best. */
  score: number
  colour: string
  rect: Rect
}

export interface PlacedLabel {
  text: string
  x: number
  y: number
  width: number
  height: number
  colour: string
  /** How many detections this label speaks for. 1 unless it is a run. */
  count: number
}

interface Run {
  key: string
  title: string
  colour: string
  bestScore: number
  count: number
  /** Extent along the time axis: x in `scroll`, y in `waterfall`. */
  alongLow: number
  alongHigh: number
  /** The box edge the label hangs off on the frequency axis: the top of the
   *  highest box in `scroll`, the left of the lowest-frequency box in
   *  `waterfall`. */
  laneAnchor: number
}

/** Scratch, reused across frames. This module is called once per animation
 *  frame from the overlay's draw loop and is not re-entrant, so pooling here
 *  keeps a 60 Hz loop from allocating a fresh working set sixty times a second
 *  (charter item 8: the live view may not cost the station anything meaningful).
 *  Nothing outside this module ever sees these objects — results are copied into
 *  the caller's own output array. */
const runs: Run[] = []
const order: number[] = []

function emptyRun(): Run {
  return {
    key: '',
    title: '',
    colour: '',
    bestScore: 0,
    count: 0,
    alongLow: 0,
    alongHigh: 0,
    laneAnchor: 0,
  }
}

/** How close along the time axis two same-title detections have to be before one
 *  label has to speak for both.
 *
 *  It is the label's own extent along that axis, not a fixed number of pixels,
 *  and that matters: on the operator's phone two jackdaws two seconds apart are
 *  30 px apart and their labels cannot both be drawn, while on a 1440 px desktop
 *  the same two sounds are 120 px apart and, if the name is short, both fit.
 *  Same audio, different pictures, both honest — and a count that appeared on a
 *  desktop where there was room for the detail would be hiding it for no reason.
 *
 *  In `scroll` the time axis is horizontal, so the extent is the plate's width;
 *  in `waterfall` it is vertical, so it is the plate's height and the same run
 *  collapses at almost any viewport.
 */
function runGap(
  title: string,
  orientation: Orientation,
  measure: (text: string) => number,
): number {
  if (orientation !== 'scroll') return LABEL_HEIGHT + LANE_GAP
  return Math.max(RUN_GAP_PX, measure(title) + LABEL_TEXT_PAD * 2 + EDGE)
}

/** Collapse same-title detections that sit close together in time into runs.
 *  Returns how many entries of `runs` are live. */
function collectRuns(
  inputs: readonly LabelInput[],
  inputCount: number,
  orientation: Orientation,
  measure: (text: string) => number,
): number {
  const scroll = orientation === 'scroll'
  let used = 0
  for (let index = 0; index < inputCount; index++) {
    const input = inputs[index]
    const rect = input.rect
    const alongLow = scroll ? rect.x : rect.y
    const alongHigh = alongLow + (scroll ? rect.width : rect.height)
    const laneAnchor = scroll ? rect.y : rect.x

    const gap = runGap(input.title, orientation, measure)
    let target = -1
    for (let candidate = 0; candidate < used; candidate++) {
      const run = runs[candidate]
      if (run.key !== input.key) continue
      // Near in time, in either direction. Detections arrive newest-first as
      // often as oldest-first, so this cannot assume an order.
      if (alongLow > run.alongHigh + gap) continue
      if (alongHigh < run.alongLow - gap) continue
      target = candidate
      break
    }

    if (target < 0) {
      if (runs.length <= used) runs.push(emptyRun())
      const run = runs[used]
      run.key = input.key
      run.title = input.title
      run.colour = input.colour
      run.bestScore = input.score
      run.count = 1
      run.alongLow = alongLow
      run.alongHigh = alongHigh
      run.laneAnchor = laneAnchor
      used++
    } else {
      const run = runs[target]
      run.count++
      if (input.score > run.bestScore) run.bestScore = input.score
      if (alongLow < run.alongLow) run.alongLow = alongLow
      if (alongHigh > run.alongHigh) run.alongHigh = alongHigh
      // Highest box in scroll (smallest y), leftmost in waterfall (smallest x):
      // either way the edge the label hangs off, so a run's label clears every
      // box in the run rather than only the first one seen.
      if (laneAnchor < run.laneAnchor) run.laneAnchor = laneAnchor
    }
  }
  return used
}

/** Order runs by which most deserves the space: the strongest claim first, a
 *  longer run breaking a tie. Insertion sort over indices — `used` is a handful
 *  of labels, and `Array.prototype.sort` with a comparator closure would
 *  allocate on every frame. */
function orderRuns(used: number): void {
  for (let index = 0; index < used; index++) {
    if (order.length <= index) order.push(index)
    order[index] = index
  }
  for (let index = 1; index < used; index++) {
    const value = order[index]
    const run = runs[value]
    let position = index - 1
    while (position >= 0) {
      const other = runs[order[position]]
      const better =
        other.bestScore < run.bestScore ||
        (other.bestScore === run.bestScore && other.count < run.count)
      if (!better) break
      order[position + 1] = order[position]
      position--
    }
    order[position + 1] = value
  }
}

/** The text for a run at a given level of detail. Level 0 is everything; each
 *  step sheds a separately-labelled fact, never a character of the name. */
function runText(run: Run, level: number): string {
  const percent = Math.round(run.bestScore * 100)
  if (run.count > 1) {
    if (level === 0) return `${run.title} ×${run.count} · best ${percent}%`
    if (level === 1) return `${run.title} ×${run.count}`
    return run.title
  }
  return level === 0 ? `${run.title} ${percent}%` : run.title
}

function clamp(value: number, low: number, high: number): number {
  if (high < low) return low
  return value < low ? low : value > high ? high : value
}

/** Do two label plates touch? A shared edge counts: two labels flush against
 *  each other read as one longer label, which is the failure being fixed. */
function collides(
  x: number,
  y: number,
  width: number,
  out: readonly PlacedLabel[],
  placed: number,
): boolean {
  for (let index = 0; index < placed; index++) {
    const other = out[index]
    if (x + width + EDGE <= other.x) continue
    if (other.x + other.width + EDGE <= x) continue
    if (y + LABEL_HEIGHT + LANE_GAP <= other.y) continue
    if (other.y + other.height + LANE_GAP <= y) continue
    return true
  }
  return false
}

/** Place as many detection labels as can be placed truthfully.
 *
 *  `inputs`/`inputCount` and `out` are caller-owned buffers so the draw loop can
 *  reuse them frame to frame; `out` is filled from index 0 and the return value
 *  says how many entries are live. Entries past it are stale and must not be
 *  drawn.
 *
 *  `measure` reports the pixel width of a string in the font the caller will
 *  draw with — supplied rather than taken from a canvas so this is testable
 *  under jsdom, where there is no text metric at all.
 */
export function layoutDetectionLabels(
  inputs: readonly LabelInput[],
  inputCount: number,
  viewport: Viewport,
  orientation: Orientation,
  measure: (text: string) => number,
  out: PlacedLabel[],
): number {
  const used = collectRuns(inputs, inputCount, orientation, measure)
  orderRuns(used)
  const scroll = orientation === 'scroll'
  const available = viewport.width - EDGE * 2
  let placed = 0

  for (let index = 0; index < used; index++) {
    const run = runs[order[index]]

    // Widest form that fits the canvas at all. Falling off the end of the ladder
    // means even the bare name is wider than the plot, and the only honest
    // options left are to truncate it or to leave it out.
    let text = ''
    let width = 0
    let level = 0
    for (; level <= 2; level++) {
      const candidate = runText(run, level)
      const candidateWidth = measure(candidate) + LABEL_TEXT_PAD * 2
      if (candidateWidth <= available || level === 2) {
        text = candidate
        width = candidateWidth
        break
      }
    }
    if (width > available) continue

    // Position along the time axis is fixed: moving a label there would put a
    // name at a time nothing happened. It is only clamped into the canvas so an
    // edge detection's label is not half off-screen.
    const alongMax = scroll
      ? viewport.width - width - EDGE
      : viewport.height - LABEL_HEIGHT - EDGE
    const alongPos = clamp(
      scroll ? run.alongLow : run.alongLow - LABEL_HEIGHT - LANE_GAP,
      EDGE,
      alongMax,
    )

    // Lanes run along the frequency axis, starting just clear of the box and
    // stepping alternately away from it — up first in `scroll`, because a label
    // below its box sits over the band the sound occupies.
    const laneMax = scroll
      ? viewport.height - LABEL_HEIGHT - EDGE
      : viewport.width - width - EDGE
    const laneBase = scroll ? run.laneAnchor - LABEL_HEIGHT - LANE_GAP : run.laneAnchor
    const laneStep = scroll ? LABEL_HEIGHT + LANE_GAP : width + LANE_GAP * 3

    for (let lane = 0; lane < MAX_LANES; lane++) {
      const step = Math.ceil(lane / 2) * laneStep
      const lanePos = clamp(laneBase + (lane % 2 === 1 ? -step : step), EDGE, laneMax)
      const x = scroll ? alongPos : lanePos
      const y = scroll ? lanePos : alongPos
      if (collides(x, y, width, out, placed)) continue

      if (out.length <= placed) {
        out.push({ text: '', x: 0, y: 0, width: 0, height: LABEL_HEIGHT, colour: '', count: 1 })
      }
      const label = out[placed]
      label.text = text
      label.x = x
      label.y = y
      label.width = width
      label.height = LABEL_HEIGHT
      label.colour = run.colour
      label.count = run.count
      placed++
      break
    }
  }

  return placed
}
