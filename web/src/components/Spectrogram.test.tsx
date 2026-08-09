/** Component-level regression coverage for the fill-transient bug: a partially
 *  filled history window used to rescale to fill the canvas, and the detection
 *  overlay drifted out of alignment with it while filling. `geometry.test.ts`
 *  proves the underlying maths agree; this proves the component wires the two
 *  drawing loops to the same `windowSeconds` prop, and that changing that prop
 *  does not tear down and re-register the column sink (no reconnect needed).
 */

// @vitest-environment jsdom

import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Spectrogram } from './Spectrogram'
import type { ColumnBatch, SpectrogramSpec } from '../types'

const SPEC: SpectrogramSpec = {
  channel: 0,
  name: 'audible',
  sample_rate: 48000,
  bins: 192,
  min_hz: 80,
  max_hz: 15000,
  hop_s: 0.024,
  fft_size: 1024,
  floor_db: -95,
  ceiling_db: -15,
  columns_emitted: 0,
  history_columns: 1250,
}

type Props = Partial<React.ComponentProps<typeof Spectrogram>>

function renderSpectrogram(props: Props = {}) {
  const register = vi.fn((_channel: number, _sink: unknown) => vi.fn())
  const element = (extra: Props) => (
    <Spectrogram
      spec={SPEC}
      register={register}
      detections={[]}
      palette="observatory"
      windowSeconds={30}
      blackPoint={0.13}
      whitePoint={0.72}
      showDetections
      orientation="scroll"
      height={250}
      {...props}
      {...extra}
    />
  )
  const utils = render(element({}))
  return { ...utils, register, update: (extra: Props) => utils.rerender(element(extra)) }
}

/** Enough of a 2D context for the column sink and the two draw loops to run
 *  under jsdom, which has no canvas at all. Deliberately records nothing: the
 *  pixels are `playhead.test.ts`/`geometry.test.ts`'s business, and this exists
 *  only so the component's *timeline* can be exercised end to end. */
function fakeContext(): CanvasRenderingContext2D {
  const target: Record<string, unknown> = {
    createImageData: (width: number, height: number) => ({
      data: new Uint8ClampedArray(width * height * 4),
    }),
    measureText: () => ({ width: 10 }),
  }
  return new Proxy(target, {
    get: (object, property) =>
      property in object ? object[property as string] : () => undefined,
    set: (object, property, value) => {
      object[property as string] = value
      return true
    },
  }) as unknown as CanvasRenderingContext2D
}

describe('Spectrogram', () => {
  it('renders both a plot canvas and a separate overlay canvas', () => {
    const { container } = renderSpectrogram()
    expect(container.querySelector('canvas.spectrogram-canvas')).toBeTruthy()
    expect(container.querySelector('canvas.spectrogram-overlay')).toBeTruthy()
  })

  it('registers exactly one column sink on mount, in both orientations', () => {
    const { register, rerender } = renderSpectrogram({ orientation: 'scroll' })
    expect(register).toHaveBeenCalledTimes(1)
    expect(register).toHaveBeenCalledWith(SPEC.channel, expect.any(Function))

    rerender(
      <Spectrogram
        spec={SPEC}
        register={register}
        detections={[]}
        palette="observatory"
        windowSeconds={30}
        blackPoint={0.13}
        whitePoint={0.72}
        showDetections
        orientation="waterfall"
        height={250}
      />,
    )
    // Orientation is a pure draw-loop input, not a transport concern: switching
    // it must not re-register (and must not, e.g., reconnect anything upstream).
    expect(register).toHaveBeenCalledTimes(1)
  })

  it('does not re-register the column sink when the window (history) setting changes', () => {
    const { register, rerender } = renderSpectrogram({ windowSeconds: 30 })
    expect(register).toHaveBeenCalledTimes(1)

    for (const windowSeconds of [10, 60, 120, 15]) {
      rerender(
        <Spectrogram
          spec={SPEC}
          register={register}
          detections={[]}
          palette="observatory"
          windowSeconds={windowSeconds}
          blackPoint={0.13}
          whitePoint={0.72}
          showDetections
          orientation="scroll"
          height={250}
        />,
      )
    }

    // Changing the history window is display-only: it must never cause the
    // component to unsubscribe and resubscribe from the data feed (which would
    // amount to a reconnect and could lose in-flight columns).
    expect(register).toHaveBeenCalledTimes(1)
  })

  /** ADR-040. The server stops encoding when nobody is watching, so a browser
   *  now routinely opens onto a genuinely empty canvas and fills over ~30 s.
   *  `LiveHub`'s docstring names the hazard exactly: an empty canvas "looks
   *  exactly like a broken pipeline". These assert the blank is labelled, and
   *  that the label is honest about which kind of blank it is.
   */
  describe('a deliberately empty canvas says so', () => {
    it('labels a gated channel as filling, and says why it is empty', () => {
      const { container } = renderSpectrogram({
        spec: { ...SPEC, viewer_gated: true, history_seconds: 0 },
      })
      const notice = container.querySelector('.spectrogram-filling')
      expect(notice?.textContent).toContain('filling')
      expect(notice?.textContent).toContain('this view starts when you open it')
      // The label must not imply the station stops recording when the browser
      // is closed. It does not: detections and evidence are written
      // continuously; only this picture is gated. An earlier wording said
      // "history is recorded only while the live view is open", which the
      // operator read -- correctly -- as the station going deaf.
      expect(notice?.textContent).toContain('Detections are recorded continuously')
      expect(notice?.textContent).not.toContain('history is recorded only')
    })

    it('does not blame gating on a station that is not gated', () => {
      const { container } = renderSpectrogram({
        spec: { ...SPEC, viewer_gated: false, history_seconds: 0 },
      })
      const notice = container.querySelector('.spectrogram-filling')
      expect(notice?.textContent).toContain('filling')
      expect(notice?.textContent).not.toContain('this view starts when you open it')
    })
  })

  /** ADR-051. The playhead badge is the DOM half of the marker; the band and
   *  centre line are canvas, and `playhead.test.ts` proves their placement.
   *  What must be true here is the wiring: nothing at all when nobody is
   *  listening, and the number and its bound together when somebody is.
   */
  describe('the playhead readout', () => {
    const estimate = { utcS: 0, uncertaintyS: 0.24, disagreementS: 0, atPerfMs: 0 }

    it('shows nothing when nobody is listening', () => {
      const { container } = renderSpectrogram()
      expect(container.querySelector('.badge.playhead')).toBeNull()
    })

    it('still shows nothing while listening but before any column has arrived', () => {
      // Without a newest column there is no timeline to place the sound on.
      // ADR-040 means this is a routine state, not an error one.
      const { container } = renderSpectrogram({ playhead: estimate })
      expect(container.querySelector('.badge.playhead')).toBeNull()
    })

    it('drops the readout again the moment playback stops', () => {
      const { container, rerender } = renderSpectrogram({ playhead: estimate })
      rerender(
        <Spectrogram
          spec={SPEC}
          register={vi.fn(() => vi.fn())}
          detections={[]}
          palette="observatory"
          windowSeconds={30}
          blackPoint={0.13}
          whitePoint={0.72}
          showDetections
          orientation="scroll"
          height={250}
          playhead={null}
        />,
      )
      expect(container.querySelector('.badge.playhead')).toBeNull()
    })

    it('reports the offset and its bound once columns are arriving', () => {
      // jsdom has no canvas, and the column sink returns early without a 2D
      // context, so the ring never advances and the panel never learns what
      // time it is showing. A minimal fake context is enough to let a real
      // batch through and prove the badge reads off the *column* timeline
      // rather than off a wall clock of its own.
      const contexts = vi
        .spyOn(HTMLCanvasElement.prototype, 'getContext')
        .mockImplementation(() => fakeContext())
      vi.useFakeTimers()
      try {
        const { container, register, update } = renderSpectrogram({
          playhead: null,
          windowSeconds: 30,
        })
        const sink = register.mock.calls[0][1] as (batch: ColumnBatch) => void
        const columnUtcS = 1_700_000_000
        sink({
          channel: 0,
          bins: SPEC.bins,
          columns: 1,
          firstUtcS: columnUtcS,
          data: new Uint8Array(SPEC.bins),
        })
        // Frozen clocks: `performance.now()` does not advance under fake
        // timers, so the sub-column interpolation and the playhead's own
        // extrapolation are both exactly zero and the expected string is not
        // a range.
        update({
          playhead: {
            utcS: columnUtcS - 2.5,
            uncertaintyS: 0.24,
            disagreementS: 0,
            atPerfMs: performance.now(),
          },
        })
        vi.advanceTimersByTime(250)
        expect(container.querySelector('.badge.playhead')?.textContent).toBe(
          'hearing 2.5 s ago ±0.2 s',
        )
      } finally {
        vi.useRealTimers()
        contexts.mockRestore()
      }
    })

    it('does not re-register the column sink when the playhead updates', () => {
      // The estimate is resampled four times a second. It is a draw-loop
      // input, exactly like `orientation` and `windowSeconds` above, and must
      // never touch the transport.
      const { register, rerender } = renderSpectrogram({ playhead: estimate })
      expect(register).toHaveBeenCalledTimes(1)
      for (const uncertaintyS of [0.24, 0.3, 0.9]) {
        rerender(
          <Spectrogram
            spec={SPEC}
            register={register}
            detections={[]}
            palette="observatory"
            windowSeconds={30}
            blackPoint={0.13}
            whitePoint={0.72}
            showDetections
            orientation="scroll"
            height={250}
            playhead={{ ...estimate, uncertaintyS }}
          />,
        )
      }
      expect(register).toHaveBeenCalledTimes(1)
    })
  })

  it('mounts and updates cleanly across both orientations without throwing', () => {
    expect(() => {
      const { rerender, unmount } = renderSpectrogram({ orientation: 'scroll' })
      rerender(
        <Spectrogram
          spec={SPEC}
          register={vi.fn(() => vi.fn())}
          detections={[]}
          palette="merlin"
          windowSeconds={45}
          blackPoint={0.1}
          whitePoint={0.8}
          showDetections={false}
          orientation="waterfall"
          height={400}
        />,
      )
      unmount()
    }).not.toThrow()
  })
})
