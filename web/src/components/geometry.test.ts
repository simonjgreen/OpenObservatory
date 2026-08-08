/** Both orientations, tested to the same standard.
 *
 *  The point of these is that a display which puts a sound in the wrong place is
 *  worse than no display: it produces confident, wrong conclusions. Each property is
 *  asserted for `scroll` and `waterfall` so adding the second view cannot silently
 *  break the first, and so the second is not merely "plausible looking".
 */

import { describe, expect, it } from 'vitest'

import {
  applyMatrix,
  frequencyFraction,
  frequencyToPixel,
  fractionToFrequency,
  pixelToFrequency,
  pixelToSecondsAgo,
  pixelsPerColumn,
  orderPanels,
  ringTransform,
  secondsAgoToPixel,
  spanRect,
  type Orientation,
} from './geometry'

const AUDIBLE = { minHz: 80, maxHz: 15000, bins: 192 }
const ULTRASONIC = { minHz: 15000, maxHz: 150000, bins: 128 }
const VIEW = { width: 1200, height: 400 }
const WINDOW_S = 30
const BOTH: Orientation[] = ['scroll', 'waterfall']

describe('frequency mapping', () => {
  it('is logarithmic, so the octave 100-200 Hz occupies the same span as 1-2 kHz', () => {
    const lowOctave =
      frequencyFraction(200, AUDIBLE) - frequencyFraction(100, AUDIBLE)
    const highOctave =
      frequencyFraction(2000, AUDIBLE) - frequencyFraction(1000, AUDIBLE)
    expect(lowOctave).toBeCloseTo(highOctave, 10)
  })

  it('pins the band edges to 0 and 1', () => {
    expect(frequencyFraction(AUDIBLE.minHz, AUDIBLE)).toBeCloseTo(0, 10)
    expect(frequencyFraction(AUDIBLE.maxHz, AUDIBLE)).toBeCloseTo(1, 10)
  })

  it.each(BOTH)('round-trips a frequency through pixels (%s)', (orientation) => {
    for (const hz of [80, 250, 1000, 4000, 14_000]) {
      const pixel = frequencyToPixel(hz, AUDIBLE, VIEW, orientation)
      expect(pixelToFrequency(pixel, AUDIBLE, VIEW, orientation)).toBeCloseTo(hz, 3)
    }
  })

  it('puts high frequency at the top in scroll, and at the right in waterfall', () => {
    const low = frequencyToPixel(100, AUDIBLE, VIEW, 'scroll')
    const high = frequencyToPixel(10_000, AUDIBLE, VIEW, 'scroll')
    expect(high).toBeLessThan(low) // smaller y is higher up

    const lowX = frequencyToPixel(100, AUDIBLE, VIEW, 'waterfall')
    const highX = frequencyToPixel(10_000, AUDIBLE, VIEW, 'waterfall')
    expect(highX).toBeGreaterThan(lowX)
  })

  it.each(BOTH)('uses the full extent of its axis (%s)', (orientation) => {
    const atMin = frequencyToPixel(AUDIBLE.minHz, AUDIBLE, VIEW, orientation)
    const atMax = frequencyToPixel(AUDIBLE.maxHz, AUDIBLE, VIEW, orientation)
    const extent = orientation === 'scroll' ? VIEW.height : VIEW.width
    expect(Math.abs(atMax - atMin)).toBeCloseTo(extent, 6)
  })

  it('maps the ultrasonic band independently of the audible one', () => {
    // 45 kHz is inside the ultrasonic band and outside the audible one; it must not
    // be silently clamped into the audible display.
    expect(frequencyFraction(45_000, ULTRASONIC)).toBeGreaterThan(0)
    expect(frequencyFraction(45_000, ULTRASONIC)).toBeLessThan(1)
    expect(frequencyFraction(45_000, AUDIBLE)).toBeGreaterThan(1)
  })

  it('round-trips fractions to frequencies', () => {
    expect(fractionToFrequency(0, AUDIBLE)).toBeCloseTo(80, 6)
    expect(fractionToFrequency(1, AUDIBLE)).toBeCloseTo(15000, 6)
  })
})

describe('time mapping', () => {
  it('puts now at the right edge in scroll and the top edge in waterfall', () => {
    expect(secondsAgoToPixel(0, WINDOW_S, VIEW, 'scroll')).toBeCloseTo(VIEW.width, 6)
    expect(secondsAgoToPixel(WINDOW_S, WINDOW_S, VIEW, 'scroll')).toBeCloseTo(0, 6)

    expect(secondsAgoToPixel(0, WINDOW_S, VIEW, 'waterfall')).toBeCloseTo(0, 6)
    expect(secondsAgoToPixel(WINDOW_S, WINDOW_S, VIEW, 'waterfall')).toBeCloseTo(
      VIEW.height,
      6,
    )
  })

  it.each(BOTH)('round-trips an age through pixels (%s)', (orientation) => {
    for (const age of [0, 1, 7.5, 29.9]) {
      const pixel = secondsAgoToPixel(age, WINDOW_S, VIEW, orientation)
      expect(pixelToSecondsAgo(pixel, WINDOW_S, VIEW, orientation)).toBeCloseTo(age, 6)
    }
  })

  it.each(BOTH)('advances monotonically into the past (%s)', (orientation) => {
    const recent = secondsAgoToPixel(1, WINDOW_S, VIEW, orientation)
    const older = secondsAgoToPixel(20, WINDOW_S, VIEW, orientation)
    // Scroll moves left into the past, waterfall moves down.
    expect(orientation === 'scroll' ? older < recent : older > recent).toBe(true)
  })

  it('scales columns to the axis carrying time', () => {
    expect(pixelsPerColumn(1200, VIEW, 'scroll')).toBeCloseTo(1, 6)
    expect(pixelsPerColumn(400, VIEW, 'waterfall')).toBeCloseTo(1, 6)
  })
})

describe('ring transform', () => {
  const columns = 1250
  const bins = 192
  const device = { deviceWidth: 1200, deviceHeight: 400 }

  it('places the newest column at the right edge in scroll', () => {
    const matrix = ringTransform({
      orientation: 'scroll', ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
    })
    const newest = applyMatrix(matrix, columns, 0)
    const oldest = applyMatrix(matrix, 0, 0)
    expect(newest.x).toBeCloseTo(device.deviceWidth, 6)
    expect(oldest.x).toBeCloseTo(0, 6)
  })

  it('places the highest frequency at the top in scroll', () => {
    const matrix = ringTransform({
      orientation: 'scroll', ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
    })
    // Ring row 0 holds the highest frequency.
    expect(applyMatrix(matrix, 0, 0).y).toBeCloseTo(0, 6)
    expect(applyMatrix(matrix, 0, bins).y).toBeCloseTo(device.deviceHeight, 6)
  })

  it('transposes for waterfall: time down the screen, frequency across', () => {
    const matrix = ringTransform({
      orientation: 'waterfall', ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
    })
    const newest = applyMatrix(matrix, columns, 0)
    const oldest = applyMatrix(matrix, 0, 0)
    // Newest at the top, oldest at the bottom.
    expect(newest.y).toBeCloseTo(0, 6)
    expect(oldest.y).toBeCloseTo(device.deviceHeight, 6)
    // Ring row 0 is the highest frequency, and belongs at the right.
    expect(applyMatrix(matrix, 0, 0).x).toBeCloseTo(device.deviceWidth, 6)
    expect(applyMatrix(matrix, 0, bins).x).toBeCloseTo(0, 6)
  })

  it('is a genuine reflection for waterfall, not a rotation', () => {
    const [a, b, c, d] = ringTransform({
      orientation: 'waterfall', ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
    })
    // A reflection about the diagonal has a negative determinant; a pure rotation
    // would be positive, and would put frequency and time on the wrong axes.
    expect(a * d - b * c).toBeLessThan(0)
    expect(a).toBeCloseTo(0, 12)
    expect(d).toBeCloseTo(0, 12)
  })

  it.each(BOTH)('interpolation moves content away from the live edge (%s)', (orientation) => {
    const still = ringTransform({
      orientation, ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
    })
    const moved = ringTransform({
      orientation, ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 3,
    })
    const before = applyMatrix(still, columns, 0)
    const after = applyMatrix(moved, columns, 0)
    if (orientation === 'scroll') {
      // The newest column slides left of the right edge as time passes.
      expect(after.x).toBeLessThan(before.x)
      expect(after.y).toBeCloseTo(before.y, 6)
    } else {
      // ...and downwards from the top edge.
      expect(after.y).toBeGreaterThan(before.y)
      expect(after.x).toBeCloseTo(before.x, 6)
    }
  })

  it.each(BOTH)('scales one column to one pixel at matching sizes (%s)', (orientation) => {
    const matrix = ringTransform({
      orientation,
      deviceWidth: orientation === 'scroll' ? columns : bins,
      deviceHeight: orientation === 'scroll' ? bins : columns,
      windowColumns: columns,
      columnsDrawn: columns,
      bins,
      shiftColumns: 0,
    })
    const a = applyMatrix(matrix, 10, 0)
    const b = applyMatrix(matrix, 11, 0)
    const moved = Math.hypot(b.x - a.x, b.y - a.y)
    expect(moved).toBeCloseTo(1, 6)
  })

  describe('partial fill (the window has not finished filling yet)', () => {
    // A column's on-screen width must be a function of windowColumns alone — never
    // of columnsDrawn — so a 10%-full window renders at the same per-column scale
    // as a 100%-full one, with the unfilled remainder left blank rather than the
    // filled part being stretched to cover the whole axis.
    it.each(BOTH)('keeps the same pixels-per-column at every fill level (%s)', (orientation) => {
      const full = ringTransform({
        orientation, ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
      })
      const tenPercent = ringTransform({
        orientation,
        ...device,
        windowColumns: columns,
        columnsDrawn: Math.round(columns * 0.1),
        bins,
        shiftColumns: 0,
      })
      // The scale terms (a for scroll's x, b for waterfall's y) don't depend on
      // columnsDrawn at all.
      expect(tenPercent[0]).toBeCloseTo(full[0], 10)
      expect(tenPercent[1]).toBeCloseTo(full[1], 10)
    })

    it('anchors a partially-filled window to the live (right) edge in scroll, blank to the left', () => {
      const drawn = Math.round(columns * 0.1)
      const matrix = ringTransform({
        orientation: 'scroll', ...device, windowColumns: columns, columnsDrawn: drawn, bins, shiftColumns: 0,
      })
      // The newest drawn column (source x = drawn) still lands at the right edge...
      expect(applyMatrix(matrix, drawn, 0).x).toBeCloseTo(device.deviceWidth, 6)
      // ...but the oldest drawn column (source x = 0) lands well short of the left
      // edge, at true scale, not stretched out to reach it.
      const oldest = applyMatrix(matrix, 0, 0).x
      expect(oldest).toBeCloseTo(device.deviceWidth * 0.9, 1)
      expect(oldest).toBeGreaterThan(0)
    })

    it('anchors a partially-filled window to the live (top) edge in waterfall, blank at the bottom', () => {
      const drawn = Math.round(columns * 0.1)
      const matrix = ringTransform({
        orientation: 'waterfall', ...device, windowColumns: columns, columnsDrawn: drawn, bins, shiftColumns: 0,
      })
      expect(applyMatrix(matrix, drawn, 0).y).toBeCloseTo(0, 6)
      const oldest = applyMatrix(matrix, 0, 0).y
      expect(oldest).toBeCloseTo(device.deviceHeight * 0.1, 1)
      expect(oldest).toBeLessThan(device.deviceHeight)
    })

    // The core property from the bug report: a column a given *age* (distance from
    // the live edge, in columns) occupies the same screen position whether the
    // window is 10% full or 100% full — it must never move as the buffer fills.
    it.each(BOTH)(
      'places a column of a given age at the same position relative to the live edge regardless of fill (%s)',
      (orientation) => {
        const ageInColumns = 50 // 50 columns back from the newest drawn column
        const atTenPercent = ringTransform({
          orientation,
          ...device,
          windowColumns: columns,
          columnsDrawn: Math.round(columns * 0.1),
          bins,
          shiftColumns: 0,
        })
        const atFull = ringTransform({
          orientation, ...device, windowColumns: columns, columnsDrawn: columns, bins, shiftColumns: 0,
        })
        const drawnAtTenPercent = Math.round(columns * 0.1)
        const p1 = applyMatrix(atTenPercent, drawnAtTenPercent - ageInColumns, 0)
        const p2 = applyMatrix(atFull, columns - ageInColumns, 0)
        expect(p1.x).toBeCloseTo(p2.x, 6)
        expect(p1.y).toBeCloseTo(p2.y, 6)
      },
    )
  })
})

/** The spectrogram (via ringTransform) and the detection overlay (via spanRect /
 *  secondsAgoToPixel) are two independent code paths that must agree pixel-for-pixel
 *  at every fill level, or a detection box drifts relative to the audio underneath
 *  it during the fill transient — the second, more serious defect in the bug report.
 *  This models both paths for a given fill fraction and checks they land the same
 *  timestamp at the same x (scroll) or y (waterfall).
 */
describe('spectrogram/overlay agreement during a partially-filled window', () => {
  const windowColumns = 1250
  const hopS = WINDOW_S / windowColumns
  const bins = 192
  const device = { deviceWidth: 1200, deviceHeight: 400 }
  const viewport = { width: device.deviceWidth, height: device.deviceHeight }

  function spectrogramPixel(orientation: Orientation, fillFraction: number, secondsAgo: number) {
    const columnsDrawn = Math.round(windowColumns * fillFraction)
    const matrix = ringTransform({
      orientation, ...device, windowColumns, columnsDrawn, bins, shiftColumns: 0,
    })
    // A column `secondsAgo` old sits this many source-columns back from the
    // newest drawn column.
    const sourceColumn = columnsDrawn - secondsAgo / hopS
    const point = applyMatrix(matrix, sourceColumn, 0)
    return orientation === 'scroll' ? point.x : point.y
  }

  function overlayPixel(orientation: Orientation, secondsAgo: number) {
    return secondsAgoToPixel(secondsAgo, WINDOW_S, viewport, orientation)
  }

  it.each(BOTH)(
    'agree at 10%%, 50%% and 100%% fill for a column within the filled region (%s)',
    (orientation) => {
      for (const fillFraction of [0.1, 0.5, 1.0]) {
        // A timestamp safely inside the filled region at every fraction tested.
        const secondsAgo = fillFraction * WINDOW_S * 0.5
        const fromSpectrogram = spectrogramPixel(orientation, fillFraction, secondsAgo)
        const fromOverlay = overlayPixel(orientation, secondsAgo)
        expect(fromSpectrogram).toBeCloseTo(fromOverlay, 0)
      }
    },
  )
})

describe('detection span rectangles', () => {
  const span = { startSecondsAgo: 10, endSecondsAgo: 8, lowHz: 2000, highHz: 5000 }

  it.each(BOTH)('covers the same audio in both views (%s)', (orientation) => {
    const rect = spanRect(span, AUDIBLE, VIEW, WINDOW_S, orientation)
    const timeExtent = orientation === 'scroll' ? rect.width : rect.height
    const freqExtent = orientation === 'scroll' ? rect.height : rect.width
    // Two seconds of a thirty second window.
    const axis = orientation === 'scroll' ? VIEW.width : VIEW.height
    expect(timeExtent).toBeCloseTo((2 / WINDOW_S) * axis, 6)
    // And the frequency extent matches the band mapping.
    const expected = Math.abs(
      frequencyToPixel(5000, AUDIBLE, VIEW, orientation) -
        frequencyToPixel(2000, AUDIBLE, VIEW, orientation),
    )
    expect(freqExtent).toBeCloseTo(expected, 6)
  })

  it.each(BOTH)('spans the whole band when frequency is unknown (%s)', (orientation) => {
    const rect = spanRect(
      { ...span, lowHz: null, highHz: null }, AUDIBLE, VIEW, WINDOW_S, orientation,
    )
    const freqExtent = orientation === 'scroll' ? rect.height : rect.width
    const axis = orientation === 'scroll' ? VIEW.height : VIEW.width
    expect(freqExtent).toBeCloseTo(axis, 6)
  })

  it.each(BOTH)('stays visible for an instantaneous event (%s)', (orientation) => {
    const rect = spanRect(
      { startSecondsAgo: 5, endSecondsAgo: 5, lowHz: 4000, highHz: 4000 },
      AUDIBLE, VIEW, WINDOW_S, orientation,
    )
    expect(rect.width).toBeGreaterThanOrEqual(3)
    expect(rect.height).toBeGreaterThanOrEqual(3)
  })

  it.each(BOTH)('positions the box at the age given (%s)', (orientation) => {
    const rect = spanRect(span, AUDIBLE, VIEW, WINDOW_S, orientation)
    const edge = secondsAgoToPixel(8, WINDOW_S, VIEW, orientation)
    if (orientation === 'scroll') {
      // The newer edge of the event is the right-hand edge of the box.
      expect(rect.x + rect.width).toBeCloseTo(edge, 6)
    } else {
      // The newer edge is the top of the box.
      expect(rect.y).toBeCloseTo(edge, 6)
    }
  })
})


describe('panel ordering', () => {
  // Deliberately supplied in the wrong order, and with ids that would sort the
  // opposite way, so the test cannot pass by accident.
  const panels = [
    { name: 'audible', channel: 0, min_hz: 80 },
    { name: 'ultrasonic', channel: 1, min_hz: 15000 },
  ]

  it('stacks the highest band first in scroll, so the page reads high to low', () => {
    expect(orderPanels(panels, 'scroll').map((p) => p.name)).toEqual([
      'ultrasonic',
      'audible',
    ])
  })

  it('places the highest band last in waterfall, so the page reads low to high', () => {
    expect(orderPanels(panels, 'waterfall').map((p) => p.name)).toEqual([
      'audible',
      'ultrasonic',
    ])
  })

  it.each(BOTH)('forms one continuous frequency axis across the panels (%s)', (orientation) => {
    const ordered = orderPanels(panels, orientation)
    // Read in display order, each panel's low edge must continue from the previous
    // panel's, in whichever direction that orientation runs.
    const edges = ordered.map((p) => p.min_hz)
    const expected = orientation === 'waterfall'
      ? [...edges].sort((a, b) => a - b)
      : [...edges].sort((a, b) => b - a)
    expect(edges).toEqual(expected)
  })

  it('does not mutate its input', () => {
    const original = [...panels]
    orderPanels(panels, 'waterfall')
    expect(panels).toEqual(original)
  })

  it.each(BOTH)('orders three bands correctly (%s)', (orientation) => {
    const three = [
      { name: 'mid', min_hz: 15000 },
      { name: 'low', min_hz: 80 },
      { name: 'high', min_hz: 150000 },
    ]
    expect(orderPanels(three, orientation).map((p) => p.name)).toEqual(
      orientation === 'waterfall' ? ['low', 'mid', 'high'] : ['high', 'mid', 'low'],
    )
  })
})
