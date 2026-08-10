/** The properties that make the detection overlay honest when it is crowded
 *  (ADR-058).
 *
 *  These exist because the component tests cannot see any of this: jsdom stubs
 *  `getContext`, so `Spectrogram.test.tsx` renders the overlay against a proxy
 *  that records nothing, and "Eurasia Eurasian Jackdaw 95%" shipped through a
 *  green suite. Everything asserted here is asserted for both orientations,
 *  because a strategy that only works in `scroll` is half a fix.
 */

import { describe, expect, it } from 'vitest'

import {
  LABEL_HEIGHT,
  layoutDetectionLabels,
  type LabelInput,
  type PlacedLabel,
} from './overlayLabels'
import type { Orientation, Rect, Viewport } from './geometry'

const BOTH: Orientation[] = ['scroll', 'waterfall']
/** The operator's phone: 390 px wide, and the plot a little under it. */
const PHONE: Viewport = { width: 366, height: 250 }
const DESKTOP: Viewport = { width: 1200, height: 400 }

/** A deterministic stand-in for canvas text metrics at 11px semibold: near
 *  enough to the real thing (~5.9 px per character for this font) to make the
 *  width arithmetic in the tests mean something. */
const measure = (text: string) => text.length * 6

function input(
  title: string,
  rect: Rect,
  score = 0.95,
  colour = '#5ce08a',
): LabelInput {
  return { key: title, title, score, colour, rect }
}

/** A detection box for a sound that happened `at` pixels along the time axis. */
function box(at: number, extent: number, orientation: Orientation, freqAt = 100): Rect {
  return orientation === 'scroll'
    ? { x: at, y: freqAt, width: extent, height: 40 }
    : { x: freqAt, y: at, width: 40, height: extent }
}

function layout(
  inputs: LabelInput[],
  viewport: Viewport,
  orientation: Orientation,
): PlacedLabel[] {
  const out: PlacedLabel[] = []
  const placed = layoutDetectionLabels(
    inputs, inputs.length, viewport, orientation, measure, out,
  )
  // Only the first `placed` entries are live; the rest are stale pool slots and
  // drawing them would be a bug in the caller.
  return out.slice(0, placed)
}

function overlaps(a: PlacedLabel, b: PlacedLabel): boolean {
  return (
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height
  )
}

function expectNoOverlaps(labels: PlacedLabel[]): void {
  for (let i = 0; i < labels.length; i++) {
    for (let j = i + 1; j < labels.length; j++) {
      expect(
        overlaps(labels[i], labels[j]),
        `"${labels[i].text}" overlaps "${labels[j].text}"`,
      ).toBe(false)
    }
  }
}

function expectInsideCanvas(labels: PlacedLabel[], viewport: Viewport): void {
  for (const label of labels) {
    expect(label.x).toBeGreaterThanOrEqual(0)
    expect(label.y).toBeGreaterThanOrEqual(0)
    expect(label.x + label.width).toBeLessThanOrEqual(viewport.width)
    expect(label.y + label.height).toBeLessThanOrEqual(viewport.height)
  }
}

describe('the reported bug: two Eurasian Jackdaws seconds apart', () => {
  it.each(BOTH)(
    'never renders the two labels on top of each other (%s)',
    (orientation) => {
      // The reported case, reconstructed: two detections of the same species a
      // couple of seconds apart on a 390 px phone. Before ADR-058 both drew at
      // their own box's edge and produced "Eurasia Eurasian Jackdaw 95%".
      const labels = layout(
        [
          input('Eurasian Jackdaw', box(120, 34, orientation), 0.95),
          input('Eurasian Jackdaw', box(150, 30, orientation), 0.91),
        ],
        PHONE,
        orientation,
      )
      expectNoOverlaps(labels)
      expectInsideCanvas(labels, PHONE)
    },
  )

  it.each(BOTH)('says one bird called twice, not two birds (%s)', (orientation) => {
    const labels = layout(
      [
        input('Eurasian Jackdaw', box(120, 34, orientation), 0.95),
        input('Eurasian Jackdaw', box(150, 30, orientation), 0.91),
      ],
      PHONE,
      orientation,
    )
    expect(labels).toHaveLength(1)
    expect(labels[0].count).toBe(2)
    expect(labels[0].text).toContain('Eurasian Jackdaw')
    expect(labels[0].text).toContain('×2')
  })

  it.each(BOTH)(
    'labels the score of a run as the best of it, never as the run\'s (%s)',
    (orientation) => {
      // "Eurasian Jackdaw ×3 95%" would be a number that does not mean what its
      // label says: the run holds a 95%, a 91% and a 60%.
      const labels = layout(
        [
          input('Eurasian Jackdaw', box(100, 30, orientation), 0.6),
          input('Eurasian Jackdaw', box(135, 30, orientation), 0.95),
          input('Eurasian Jackdaw', box(170, 30, orientation), 0.91),
        ],
        DESKTOP,
        orientation,
      )
      expect(labels).toHaveLength(1)
      expect(labels[0].count).toBe(3)
      expect(labels[0].text).toBe('Eurasian Jackdaw ×3 · best 95%')
    },
  )

  it.each(BOTH)('does not merge two different species (%s)', (orientation) => {
    const labels = layout(
      [
        input('Eurasian Jackdaw', box(120, 30, orientation)),
        input('European Robin', box(150, 30, orientation)),
      ],
      DESKTOP,
      orientation,
    )
    expect(labels).toHaveLength(2)
    expect(labels.map((label) => label.count)).toEqual([1, 1])
    expectNoOverlaps(labels)
  })

  it.each(BOTH)(
    'does not merge a withdrawn claim into a standing one (%s)',
    (orientation) => {
      // The merge key is the rendered title, and a withdrawn detection renders
      // with its marker (see detectionTitle.ts). Counting the two together would
      // present a retracted claim as evidence for a standing one.
      const labels = layout(
        [
          input('Eurasian Jackdaw', box(120, 30, orientation)),
          input('Eurasian Jackdaw · withdrawn', box(150, 30, orientation)),
        ],
        DESKTOP,
        orientation,
      )
      expect(labels).toHaveLength(2)
      expect(labels.every((label) => label.count === 1)).toBe(true)
    },
  )

  it('keeps both labels on a wide plot where both fit, and counts them on a narrow one', () => {
    // The same two sounds, two seconds apart, at two viewport widths. A count is
    // a response to crowding, not a preference: where there is room for the
    // detail the detail is shown, and the desktop picture does not change.
    const calls = () => [
      input('Great Tit', box(300, 26, 'scroll', 100), 0.95),
      input('Great Tit', box(420, 26, 'scroll', 104), 0.91),
    ]
    const wide = layout(calls(), DESKTOP, 'scroll')
    expect(wide).toHaveLength(2)
    expect(wide.every((label) => label.count === 1)).toBe(true)
    expectNoOverlaps(wide)

    const narrow = layout(
      [
        input('Great Tit', box(120, 26, 'scroll', 100), 0.95),
        input('Great Tit', box(150, 26, 'scroll', 104), 0.91),
      ],
      PHONE,
      'scroll',
    )
    expect(narrow).toHaveLength(1)
    expect(narrow[0].count).toBe(2)
  })

  it.each(BOTH)(
    'keeps two calls far apart in time as two labels (%s)',
    (orientation) => {
      // A run is a bird calling repeatedly. Two calls at opposite ends of the
      // window are two events, and collapsing them would hide one of them.
      const labels = layout(
        [
          input('Eurasian Jackdaw', box(20, 30, orientation)),
          input('Eurasian Jackdaw', box(700, 30, orientation)),
        ],
        DESKTOP,
        orientation,
      )
      expect(labels).toHaveLength(2)
      expectNoOverlaps(labels)
    },
  )
})

describe('crowding', () => {
  it.each(BOTH)(
    'never overlaps two labels, however many pile up (%s)',
    (orientation) => {
      const species = [
        'Eurasian Jackdaw',
        'European Robin',
        'Great Tit',
        'Common Woodpigeon',
        'Eurasian Blackbird',
        'Northern Rough-winged Swallow',
        'Goldcrest',
        'Eurasian Wren',
      ]
      const inputs = species.map((name, index) =>
        input(name, box(60 + index * 12, 26, orientation, 40 + index * 5), 0.9 - index * 0.05),
      )
      const labels = layout(inputs, PHONE, orientation)
      expectNoOverlaps(labels)
      expectInsideCanvas(labels, PHONE)
      // Some are necessarily dropped at this width — that is the honest
      // outcome — but the strongest claim is never one of them.
      expect(labels.length).toBeGreaterThan(0)
      expect(labels[0].text).toContain('Eurasian Jackdaw')
    },
  )

  it.each(BOTH)('drops the weakest claim first, not the strongest (%s)', (orientation) => {
    const labels = layout(
      [
        input('Goldcrest', box(100, 24, orientation, 60), 0.31),
        input('Common Woodpigeon', box(104, 24, orientation, 62), 0.99),
      ],
      { width: 200, height: 60 },
      orientation,
    )
    expect(labels.map((label) => label.text.split(' ').slice(0, 2).join(' '))).toContain(
      'Common Woodpigeon',
    )
  })

  it.each(BOTH)('places every label on a desktop-width plot (%s)', (orientation) => {
    const inputs = [
      input('Eurasian Jackdaw', box(80, 30, orientation, 60)),
      input('European Robin', box(300, 30, orientation, 120)),
      input('Great Tit', box(600, 30, orientation, 180)),
    ]
    const labels = layout(inputs, DESKTOP, orientation)
    expect(labels).toHaveLength(3)
    expectNoOverlaps(labels)
    expectInsideCanvas(labels, DESKTOP)
  })
})

describe('a label wider than its box', () => {
  it.each(BOTH)('is drawn in full rather than cut short (%s)', (orientation) => {
    // "Northern Rough-winged Swallow" is wider than any detection box will ever
    // be at 390 px. A name cut to fit can be read as a different, real species,
    // so the box does not constrain the label's width at all.
    const labels = layout(
      [input('Northern Rough-winged Swallow', box(100, 8, orientation, 90))],
      PHONE,
      orientation,
    )
    expect(labels).toHaveLength(1)
    expect(labels[0].text).toContain('Northern Rough-winged Swallow')
    expect(labels[0].width).toBeGreaterThan(40)
    expectInsideCanvas(labels, PHONE)
  })

  it.each(BOTH)('sheds the score before it sheds the name (%s)', (orientation) => {
    // Just wide enough for the name and not for the score. The score's absence
    // claims nothing; a shortened name claims something false.
    const name = 'Northern Rough-winged Swallow'
    const width = measure(name) + 8 + 4
    const labels = layout(
      [input(name, box(4, 8, orientation, 20))],
      { width, height: 120 },
      orientation,
    )
    expect(labels).toHaveLength(1)
    expect(labels[0].text).toBe(name)
  })

  it.each(BOTH)(
    'drops the label entirely when even the bare name will not fit (%s)',
    (orientation) => {
      const labels = layout(
        [input('Northern Rough-winged Swallow', box(4, 8, orientation, 10))],
        { width: 90, height: 120 },
        orientation,
      )
      // No label at all. The box is still drawn by the caller, so the detection
      // is visible and reachable — it is simply not named where naming it would
      // have to lie about which species it is.
      expect(labels).toHaveLength(0)
    },
  )
})

describe('the canvas edges', () => {
  it.each(BOTH)('keeps a label at the live edge on screen (%s)', (orientation) => {
    // A detection at the newest end of the window: `scroll` puts it hard against
    // the right edge, `waterfall` against the top.
    const at = orientation === 'scroll' ? PHONE.width - 6 : 0
    const labels = layout(
      [input('Common Woodpigeon', box(at, 6, orientation, 30))],
      PHONE,
      orientation,
    )
    expect(labels).toHaveLength(1)
    expectInsideCanvas(labels, PHONE)
  })

  it.each(BOTH)('keeps a label at the oldest edge on screen (%s)', (orientation) => {
    const labels = layout(
      [input('Common Woodpigeon', box(-4, 10, orientation, 30))],
      PHONE,
      orientation,
    )
    expect(labels).toHaveLength(1)
    expectInsideCanvas(labels, PHONE)
  })

  it.each(BOTH)(
    'keeps a label on screen for a box at the top of the frequency axis (%s)',
    (orientation) => {
      // In `scroll` the natural place for a label is above its box; for a box at
      // the top of the plot there is no "above" left.
      const labels = layout(
        [input('Common Woodpigeon', box(80, 30, orientation, 0))],
        PHONE,
        orientation,
      )
      expect(labels).toHaveLength(1)
      expectInsideCanvas(labels, PHONE)
    },
  )
})

describe('the output buffer', () => {
  it('is reused between calls and reports how much of it is live', () => {
    // The draw loop calls this on every animation frame and must not allocate a
    // working set each time (charter item 8).
    const out: PlacedLabel[] = []
    const many = ['Great Tit', 'European Robin', 'Goldcrest'].map((name, index) =>
      input(name, box(100 + index * 200, 30, 'scroll', 40 + index * 40)),
    )
    expect(layoutDetectionLabels(many, many.length, DESKTOP, 'scroll', measure, out)).toBe(3)
    const slots = out.length

    const one = [input('Great Tit', box(100, 30, 'scroll'))]
    expect(layoutDetectionLabels(one, one.length, DESKTOP, 'scroll', measure, out)).toBe(1)
    expect(out.length).toBe(slots)
    expect(out[0].text).toContain('Great Tit')
  })

  it('respects the caller\'s input count rather than the array length', () => {
    // The input array is a pool too: entries past `inputCount` are last frame's.
    const inputs = [
      input('Great Tit', box(100, 30, 'scroll', 40)),
      input('European Robin', box(400, 30, 'scroll', 90)),
    ]
    const out: PlacedLabel[] = []
    expect(layoutDetectionLabels(inputs, 1, DESKTOP, 'scroll', measure, out)).toBe(1)
    expect(out[0].text).toContain('Great Tit')
  })

  it('gives every label the same height, which is what the plate is drawn at', () => {
    const labels = layout(
      [
        input('Great Tit', box(100, 30, 'scroll', 40)),
        input('European Robin', box(400, 30, 'scroll', 90)),
      ],
      DESKTOP,
      'scroll',
    )
    expect(labels.every((label) => label.height === LABEL_HEIGHT)).toBe(true)
  })
})

describe('placement stays truthful about time', () => {
  it('never moves a label along the time axis to make room (scroll)', () => {
    // Two species at the same instant. One of them must move; it may only move
    // across frequency, because moving it across time would put a name at a
    // moment nothing happened.
    const inputs = [
      input('Eurasian Jackdaw', box(300, 30, 'scroll', 100), 0.95),
      input('European Robin', box(300, 30, 'scroll', 140), 0.9),
    ]
    const labels = layout(inputs, DESKTOP, 'scroll')
    expect(labels).toHaveLength(2)
    expect(labels[0].x).toBe(labels[1].x)
    expect(labels[0].y).not.toBe(labels[1].y)
    expectNoOverlaps(labels)
  })

  it('never moves a label along the time axis to make room (waterfall)', () => {
    // Time is vertical here, so the lane axis is x.
    const inputs = [
      input('Eurasian Jackdaw', box(300, 30, 'waterfall', 100), 0.95),
      input('European Robin', box(300, 30, 'waterfall', 140), 0.9),
    ]
    const labels = layout(inputs, DESKTOP, 'waterfall')
    expect(labels).toHaveLength(2)
    expect(labels[0].y).toBe(labels[1].y)
    expect(labels[0].x).not.toBe(labels[1].x)
    expectNoOverlaps(labels)
  })
})
