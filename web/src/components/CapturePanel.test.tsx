/** The honesty regression, asserted where a human would read it.
 *
 *  `Pipeline.test.ts` proves `describeDeficit`'s arithmetic. This proves the
 *  panel actually wires it up — which is the part that was wrong. The numbers
 *  are the live station's own reading of 2026-08-09 (ADR-046): 0.104 s of
 *  deficit on a stream that had lost nothing, shown under the label
 *  "audio lost".
 */

// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CapturePanel } from './Pipeline'
import type { StationStatus } from '../types'

function status(capture: Partial<StationStatus['capture']> = {}): StationStatus {
  return {
    capture: {
      state: 'capturing',
      detail: '',
      stream_detail: { block_frames: 38400 },
      stream_id: 's',
      source_kind: 'alsa',
      is_live_hardware: true,
      device_key: null,
      device_label: '384kHz AudioMoth USB Microphone',
      sample_rate: 384_000,
      sample_format: 'S16_LE',
      channels: 1,
      started_utc: null,
      blocks: 4500,
      frames: 172_800_000,
      expected_frames: 172_839_781,
      continuity_ratio: 0.99977,
      discontinuities: 0,
      estimated_missing_frames: 0,
      estimated_missing_seconds: 0,
      gaps_with_loss: 0,
      gaps_without_loss: 0,
      stream_restarts: 0,
      open_failures: 0,
      block_age_s: 0.14,
      hot_path_cpu_ratio: 0.0946,
      observed_rate_hz: 383_980.6,
      rate_offset_ppm: -49.88,
      overruns: 0,
      late_reads: 7,
      late_read_max_frames: 64_652,
      alsa_buffer_frames: 192_000,
      ...capture,
    },
    resampler: null,
    rings: { native: null, audible: null },
  } as unknown as StationStatus
}

/** The `<dd>` next to the `<dt>` with this label. */
function valueFor(label: string): string {
  const term = screen.getByText(label)
  return term.parentElement?.querySelector('dd')?.textContent ?? ''
}

describe('CapturePanel: what "audio lost" is allowed to mean', () => {
  it('says no audio was lost when none was, despite a 0.104 s deficit', () => {
    render(<CapturePanel status={status()} />)
    // Before ADR-046 this row read "104 ms" and was the reason the operator
    // believed the station was leaking audio.
    expect(valueFor('audio lost')).toBe('none')
  })

  it('reports the deficit under its own name, with the drift term shown', () => {
    render(<CapturePanel status={status()} />)
    const behind = valueFor('behind clock')
    expect(behind).toContain('104 ms')
    // 450 s at 49.88 ppm.
    expect(behind).toContain('22 ms drift')
    expect(behind).toContain('±50 ms phase')
  })
})

describe('CapturePanel: confirmed loss and absorbed stalls', () => {
  it('shows the estimator figure, not the deficit, when audio really went', () => {
    render(
      <CapturePanel
        status={status({ estimated_missing_frames: 96_000, estimated_missing_seconds: 0.25 })}
      />,
    )
    expect(valueFor('audio lost')).toBe('250 ms')
  })

  it('separates absorbed stalls from overruns, which are different claims', () => {
    render(<CapturePanel status={status()} />)
    // ALSA reported nothing; the ring absorbed seven late reads at no cost.
    expect(valueFor('overruns')).toBe('0 · 7 late reads')
  })

  it('shows nothing rather than a guess before a stream exists', () => {
    render(<CapturePanel status={status({ expected_frames: null })} />)
    expect(valueFor('audio lost')).toBe('—')
    expect(valueFor('behind clock')).toBe('—')
  })
})
