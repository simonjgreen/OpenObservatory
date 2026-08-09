/** The playhead marker is a measurement, so these tests are about whether the
 *  number is *earned*, not merely produced.
 *
 *  Three things they hold to:
 *
 *  1. Under an ideal stream the two independent estimators agree exactly, which
 *     is what makes the gap between them a measurement rather than noise.
 *  2. When they part company — server-side chunk drops, or a browser reporting
 *     `buffered` staler than what it holds — the claimed interval contains
 *     both ends of the bracket. It never narrows onto one of them, because
 *     nothing visible from inside the browser can tell those two causes apart.
 *  3. There is no marker at all when there is nothing honest to say: paused,
 *     rebuffering, not started, or off the end of the selected window.
 *
 *  Placement is asserted in both orientations against the same `spanRect` the
 *  detection overlay uses, because a marker that agrees with the maths but not
 *  with the boxes drawn beside it is still wrong on the glass.
 */

import { describe, expect, it } from 'vitest'

import {
  BASE_UNCERTAINTY_S,
  CENTRE_LINE_MAX_UNCERTAINTY_S,
  estimatePlayhead,
  formatPlayheadLabel,
  playheadBand,
  playheadSecondsAgo,
  type PlayheadSample,
} from './playhead'
import { secondsAgoToPixel, spanRect, type Orientation } from './geometry'

const BAND = { minHz: 80, maxHz: 15000, bins: 192 }
const VIEW = { width: 1200, height: 400 }
const WINDOW_S = 30
const BOTH: Orientation[] = ['scroll', 'waterfall']

/** A healthy stream: opened 20 s ago, 2 s of audio buffered ahead of the play
 *  cursor, so the element has played 18 s of a 20 s-old stream. */
function healthy(overrides: Partial<PlayheadSample> = {}): PlayheadSample {
  const openedEpochS = 1_000_000
  const sampledEpochS = openedEpochS + 20
  return {
    bufferedAheadS: 2,
    currentTimeS: 18,
    streamOpenedEpochS: openedEpochS,
    sampledEpochS,
    sampledPerfMs: 500_000,
    paused: false,
    readyState: 4,
    advancing: true,
    ...overrides,
  }
}

describe('estimatePlayhead', () => {
  it('places the playhead behind now by what the element says is buffered', () => {
    const estimate = estimatePlayhead(healthy(), 0)!
    expect(estimate).not.toBeNull()
    // 2 s buffered, plus the mid-point of the one term that cannot be read
    // from anywhere: the browser's output buffer.
    expect(1_000_020 - estimate.utcS).toBeCloseTo(2 + 0.11, 6)
  })

  it('agrees with the epoch-anchored cross-check exactly on an ideal stream', () => {
    // This is the whole reason the second estimator is computed: on a stream
    // that behaves, the disagreement is zero, so any disagreement at all is
    // information rather than a modelling artefact.
    expect(estimatePlayhead(healthy(), 0)!.disagreementS).toBeCloseTo(0, 9)
    expect(estimatePlayhead(healthy(), 0)!.uncertaintyS).toBeCloseTo(BASE_UNCERTAINTY_S, 9)
  })

  it('holds that agreement whatever the buffer depth happens to be', () => {
    for (const buffered of [0.2, 1, 3.5, 8]) {
      const sample = healthy({ bufferedAheadS: buffered, currentTimeS: 20 - buffered })
      expect(estimatePlayhead(sample, 0)!.disagreementS).toBeCloseTo(0, 9)
    }
  })

  it('converts through the station clock, so the marker lands on column time', () => {
    // Column timestamps are station UTC. A browser clock 4 s slow must not
    // shift the marker by 4 s; that is exactly the kind of confident, wrong
    // placement this feature must not produce.
    const skewed = estimatePlayhead(healthy({ streamOpenedEpochS: 999_996, sampledEpochS: 1_000_016 }), 4)!
    const unskewed = estimatePlayhead(healthy(), 0)!
    expect(skewed.utcS).toBeCloseTo(unskewed.utcS, 9)
  })

  describe('widens rather than lies, when the model stops holding', () => {
    it('widens when the server dropped chunks for this listener', () => {
      // The bounded per-listener queue sheds the *oldest* chunk, so the media
      // timeline is shorter than the real time elapsed: `currentTime` is
      // behind where the epoch anchor expects it. The buffer anchor is the one
      // that stays right, so it stays the reported value — but the gap is
      // charged to the uncertainty.
      const dropped = estimatePlayhead(healthy({ currentTimeS: 18 - 1.2 }), 0)!
      expect(dropped.disagreementS).toBeCloseTo(1.2, 6)
      // Half the gap reaches either end of the bracket.
      expect(dropped.uncertaintyS).toBeCloseTo(BASE_UNCERTAINTY_S + 0.6, 6)
      // The bracket still contains the buffer-anchored end, which is the one
      // that stays right when the cause is dropped chunks.
      const bufferAnchored = estimatePlayhead(healthy(), 0)!.utcS
      expect(Math.abs(dropped.utcS - bufferAnchored)).toBeLessThanOrEqual(dropped.uncertaintyS)
    })

    it('widens when the browser is reporting `buffered` staler than it is', () => {
      // Here it is B that is wrong: `bufferedEnd` has not caught up with what
      // has actually arrived, so B believes the playhead is fresher than it
      // is. We cannot tell which estimator is the honest one from inside, so
      // the band is widened to hold both.
      const lazy = estimatePlayhead(healthy({ bufferedAheadS: 1.2 }), 0)!
      expect(lazy.disagreementS).toBeCloseTo(0.8, 6)
      // The bracket still contains the epoch-anchored end, which is the one
      // that stays right when the cause is a stale `buffered`.
      const epochAnchored = 1_000_000 + 18 - 0.11
      expect(Math.abs(lazy.utcS - epochAnchored)).toBeLessThanOrEqual(lazy.uncertaintyS)
    })

    it('does not need to model output-clock drift, because it measures it', () => {
      // Hardware slower than the 48 kHz source accumulates buffer: the element
      // has played less than the stream delivered. Both estimators see that,
      // because `bufferedAhead` is measured against `currentTime` rather than
      // assumed, so they still agree -- on a playhead that has genuinely
      // fallen further behind.
      const drifted = estimatePlayhead(healthy({ bufferedAheadS: 2.4, currentTimeS: 17.6 }), 0)!
      expect(drifted.disagreementS).toBeCloseTo(0, 9)
      expect(1_000_020 - drifted.utcS).toBeCloseTo(2.4 + 0.11, 6)
    })

    it('never claims a tighter bound than the unmeasurable term allows', () => {
      // The output buffer is estimated, not read, and no arrangement of the
      // measured inputs may talk that away.
      for (const buffered of [0, 0.5, 2, 9]) {
        const estimate = estimatePlayhead(healthy({ bufferedAheadS: buffered }), 0)
        if (estimate) expect(estimate.uncertaintyS).toBeGreaterThanOrEqual(BASE_UNCERTAINTY_S)
      }
      expect(BASE_UNCERTAINTY_S).toBeGreaterThan(0)
    })
  })

  describe('says nothing rather than something stale', () => {
    it.each([
      ['paused', { paused: true }],
      ['rebuffering (readyState below HAVE_FUTURE_DATA)', { readyState: 2 }],
      ['stalled: currentTime did not advance', { advancing: false }],
      ['not started', { currentTimeS: 0, advancing: false }],
    ])('returns null when %s', (_case, overrides) => {
      expect(estimatePlayhead(healthy(overrides as Partial<PlayheadSample>), 0)).toBeNull()
    })
  })
})

describe('playheadSecondsAgo', () => {
  const estimate = { utcS: 1_000_000, uncertaintyS: 0.24, disagreementS: 0, atPerfMs: 500_000 }

  it('measures against the newest column, on the same clock', () => {
    expect(playheadSecondsAgo(estimate, 1_000_003, 500_000)).toBeCloseTo(3, 9)
  })

  it('advances with real time between telemetry samples', () => {
    // Telemetry lands 4 times a second and the overlay draws 60. If the
    // playhead were held still while the live edge glided on, the marker would
    // saw back and forth by a quarter of a second every quarter of a second.
    const still = playheadSecondsAgo(estimate, 1_000_003, 500_000)
    const later = playheadSecondsAgo(estimate, 1_000_003 + 0.2, 500_200)
    expect(later).toBeCloseTo(still, 9)
  })

  it('does not run backwards if the performance clock is read out of order', () => {
    expect(playheadSecondsAgo(estimate, 1_000_003, 499_000)).toBeCloseTo(3, 9)
  })
})

describe('playheadBand', () => {
  it('spans the claimed interval, oldest edge furthest from the live edge', () => {
    const band = playheadBand(3, 0.25, WINDOW_S)!
    expect(band.centreSecondsAgo).toBe(3)
    expect(band.oldestSecondsAgo).toBeCloseTo(3.25, 9)
    expect(band.newestSecondsAgo).toBeCloseTo(2.75, 9)
  })

  it('clamps against the live edge without moving the centre it reports', () => {
    // The audio can legitimately be slightly ahead of the newest drawn column.
    const band = playheadBand(0.1, 0.25, WINDOW_S)!
    expect(band.newestSecondsAgo).toBe(0)
    expect(band.centreSecondsAgo).toBe(0.1)
  })

  it('draws nothing at all once the whole interval is off the window', () => {
    // Pinning it to the edge would assert a position, and the position it
    // asserts is wrong. The label still reports the number.
    expect(playheadBand(45, 0.25, WINDOW_S)).toBeNull()
    expect(playheadBand(-2, 0.25, WINDOW_S)).toBeNull()
    // Shrinking the window is how an operator meets this case.
    expect(playheadBand(8, 0.25, 30)).not.toBeNull()
    expect(playheadBand(8, 0.25, 5)).toBeNull()
  })
})

describe('placement on the canvas', () => {
  it.each(BOTH)('puts the band between the live edge and the history end (%s)', (orientation) => {
    const band = playheadBand(3, 0.25, WINDOW_S)!
    const rect = spanRect(
      {
        startSecondsAgo: band.oldestSecondsAgo,
        endSecondsAgo: band.newestSecondsAgo,
        lowHz: null,
        highHz: null,
      },
      BAND,
      VIEW,
      WINDOW_S,
      orientation,
    )
    if (orientation === 'scroll') {
      // Time is horizontal, now at the right: 3 s ago of a 30 s window is 90%
      // of the way across.
      expect(rect.x + rect.width / 2).toBeCloseTo(VIEW.width * 0.9, 6)
      expect(rect.y).toBeCloseTo(0, 6)
      expect(rect.height).toBeCloseTo(VIEW.height, 6)
    } else {
      // Time is vertical, now at the top.
      expect(rect.y + rect.height / 2).toBeCloseTo(VIEW.height * 0.1, 6)
      expect(rect.x).toBeCloseTo(0, 6)
      expect(rect.width).toBeCloseTo(VIEW.width, 6)
    }
  })

  it.each(BOTH)('runs the time axis the right way in this view (%s)', (orientation) => {
    // The two views run time in different directions; a marker that is correct
    // in one and mirrored in the other is worse than useless.
    const nearer = secondsAgoToPixel(1, WINDOW_S, VIEW, orientation)
    const further = secondsAgoToPixel(9, WINDOW_S, VIEW, orientation)
    if (orientation === 'scroll') expect(further).toBeLessThan(nearer)
    else expect(further).toBeGreaterThan(nearer)
  })

  it.each(BOTH)('keeps the centre line inside its own band (%s)', (orientation) => {
    const band = playheadBand(3, 0.25, WINDOW_S)!
    const rect = spanRect(
      {
        startSecondsAgo: band.oldestSecondsAgo,
        endSecondsAgo: band.newestSecondsAgo,
        lowHz: null,
        highHz: null,
      },
      BAND,
      VIEW,
      WINDOW_S,
      orientation,
    )
    const centre = secondsAgoToPixel(band.centreSecondsAgo, WINDOW_S, VIEW, orientation)
    if (orientation === 'scroll') {
      expect(centre).toBeGreaterThanOrEqual(rect.x)
      expect(centre).toBeLessThanOrEqual(rect.x + rect.width)
    } else {
      expect(centre).toBeGreaterThanOrEqual(rect.y)
      expect(centre).toBeLessThanOrEqual(rect.y + rect.height)
    }
  })

  it.each(BOTH)('lands on the same pixel as a detection at the same instant (%s)', (orientation) => {
    // The marker and the detection overlay must not be able to disagree: this
    // is the property that lets the operator match what he hears to what he
    // sees. Same seconds-ago, same function, same pixel.
    const detection = spanRect(
      { startSecondsAgo: 3, endSecondsAgo: 3, lowHz: 2000, highHz: 4000 },
      BAND,
      VIEW,
      WINDOW_S,
      orientation,
    )
    const marker = secondsAgoToPixel(3, WINDOW_S, VIEW, orientation)
    if (orientation === 'scroll') expect(marker).toBeCloseTo(detection.x, 6)
    else expect(marker).toBeCloseTo(detection.y, 6)
  })
})

describe('formatPlayheadLabel', () => {
  it('states the offset and the bound together, never the offset alone', () => {
    expect(formatPlayheadLabel(2.43, 0.24, WINDOW_S)).toBe('hearing 2.4 s ago ±0.2 s')
  })

  it('says so when the sound is ahead of the newest column drawn', () => {
    expect(formatPlayheadLabel(-1.5, 0.24, WINDOW_S)).toContain('ahead of the newest column')
  })

  it('says the sound is older than the window rather than going quiet', () => {
    const label = formatPlayheadLabel(41, 0.24, WINDOW_S)
    expect(label).toContain('41.0 s ago')
    expect(label).toContain('older than this window')
  })

  it('never reports a negative age as an age', () => {
    // Inside the uncertainty of the live edge, "0.0 s ago" is the honest
    // reading; "-0.1 s ago" is a nonsense the operator would have to decode.
    expect(formatPlayheadLabel(-0.1, 0.24, WINDOW_S)).toBe('hearing 0.0 s ago ±0.2 s')
  })

  it('has a bound past which a single line would be overclaiming', () => {
    expect(CENTRE_LINE_MAX_UNCERTAINTY_S).toBeGreaterThan(BASE_UNCERTAINTY_S)
  })
})
