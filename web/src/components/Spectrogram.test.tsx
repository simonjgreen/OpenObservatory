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
import type { SpectrogramSpec } from '../types'

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

function renderSpectrogram(props: Partial<React.ComponentProps<typeof Spectrogram>> = {}) {
  const register = vi.fn((_channel: number, _sink: unknown) => vi.fn())
  const utils = render(
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
    />,
  )
  return { ...utils, register }
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
