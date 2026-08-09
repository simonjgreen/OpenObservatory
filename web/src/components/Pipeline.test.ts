import { describe, expect, it } from 'vitest'

import { describeDeficit, formatDeficit, formatDuration } from './Pipeline'
import type { CaptureStatus } from '../types'

describe('formatDuration', () => {
  it('reads as hours for a long run', () => {
    // 16,462,694,400 frames at 384 kHz -- the real figure after a night's
    // capture, and the eleven-digit number this replaced.
    expect(formatDuration(16_462_694_400 / 384_000)).toBe('11.9 h')
  })

  it('steps down through minutes to seconds', () => {
    expect(formatDuration(3600)).toBe('1.0 h')
    expect(formatDuration(3599)).toBe('60.0 m')
    expect(formatDuration(90)).toBe('1.5 m')
    expect(formatDuration(42)).toBe('42 s')
  })

  it('does not pretend to know about nothing', () => {
    expect(formatDuration(0)).toBe('—')
    expect(formatDuration(Number.NaN)).toBe('—')
  })
})

describe('formatDeficit', () => {
  it('says none rather than 0 s, so a clean run reads as clean', () => {
    expect(formatDeficit(0)).toBe('none')
  })

  it('keeps millisecond resolution below a second', () => {
    // A single lost 100 ms block must not round away to "0 s". This is the
    // whole reason it is formatted differently from a duration.
    expect(formatDeficit(0.1)).toBe('100 ms')
    expect(formatDeficit(0.055)).toBe('55 ms')
  })

  it('reports the real deficit measured on the station', () => {
    // 2026-08-08: true loss 4.06 s over 11.9 h. The estimator claimed 52.4 s,
    // which is what this row used to show (ADR-033).
    expect(formatDeficit(4.061)).toBe('4.06 s')
  })

  it('uses minutes once loss is serious', () => {
    expect(formatDeficit(90)).toBe('1.5 m')
  })
})

/** A capture status shaped like the live station's, with the fields
 *  `describeDeficit` reads set from a real reading. */
function capture(overrides: Partial<CaptureStatus> = {}): CaptureStatus {
  return {
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
    blocks: 0,
    frames: 0,
    expected_frames: null,
    continuity_ratio: 1,
    discontinuities: 0,
    estimated_missing_frames: 0,
    estimated_missing_seconds: 0,
    gaps_with_loss: 0,
    gaps_without_loss: 0,
    stream_restarts: 0,
    open_failures: 0,
    block_age_s: 0.14,
    hot_path_cpu_ratio: 0.09,
    observed_rate_hz: null,
    rate_offset_ppm: null,
    overruns: 0,
    late_reads: 0,
    late_read_max_frames: 0,
    alsa_buffer_frames: 192_000,
    ...overrides,
  }
}

describe('describeDeficit', () => {
  it('does not report lost audio on the reading that started this investigation', () => {
    // The live station, 2026-08-09, 7.5 minutes after a restart, with zero
    // gaps and zero overruns: the deficit read 0.104 s and the row labelled
    // "audio lost" showed it. Nothing had been lost.
    const result = describeDeficit(
      capture({
        frames: 172_800_000,
        expected_frames: 172_839_781, // deficit 39,781 frames = 0.104 s
        rate_offset_ppm: -49.88,
        estimated_missing_seconds: 0,
      }),
    )!
    expect(formatDeficit(result.lostSeconds)).toBe('none')
    expect(result.deficitSeconds).toBeCloseTo(0.104, 3)
    // 450 s at 49.88 ppm. Legitimate, and no audio is missing.
    expect(result.driftSeconds).toBeCloseTo(0.0225, 3)
    // What is left is anchor bias plus sampling phase, and it is smaller than
    // the one block of phase a single reading is uncertain by.
    expect(result.residualSeconds).toBeLessThan(result.phaseSeconds * 2)
    expect(result.phaseSeconds).toBeCloseTo(0.05, 3)
  })

  it('shows drift dominating the deficit over a night, with nothing lost', () => {
    // 11 hours at 50 ppm is 1.98 s of pure crystal offset. Shown raw under
    // "audio lost", that is what a slow leak of audio would look like.
    const elapsed = 11 * 3600
    const result = describeDeficit(
      capture({
        expected_frames: elapsed * 384_000,
        frames: elapsed * 384_000 - Math.round(elapsed * 384_000 * 50e-6),
        rate_offset_ppm: -50,
      }),
    )!
    expect(result.deficitSeconds).toBeCloseTo(1.98, 2)
    expect(result.driftSeconds).toBeCloseTo(1.98, 2)
    expect(result.lostSeconds).toBe(0)
    expect(Math.abs(result.residualSeconds)).toBeLessThan(0.001)
  })

  it('reports real loss from the estimator, not from the deficit', () => {
    // A deficit that is drift plus a genuine 250 ms dropout. Only the dropout
    // may reach the "audio lost" row.
    const elapsed = 3600
    const result = describeDeficit(
      capture({
        expected_frames: elapsed * 384_000,
        frames: elapsed * 384_000 - Math.round(elapsed * 384_000 * 50e-6) - 96_000,
        rate_offset_ppm: -50,
        estimated_missing_frames: 96_000,
        estimated_missing_seconds: 0.25,
      }),
    )!
    expect(formatDeficit(result.lostSeconds)).toBe('250 ms')
    expect(result.deficitSeconds).toBeCloseTo(0.43, 2)
    expect(Math.abs(result.residualSeconds)).toBeLessThan(0.001)
  })

  it('never reports negative drift when the device runs fast', () => {
    // A device faster than nominal delivers more frames than wall time implies.
    // That is not "negative drift" and must not be shown as a credit.
    const result = describeDeficit(
      capture({ expected_frames: 384_000_000, frames: 384_020_000, rate_offset_ppm: 52 }),
    )!
    expect(result.driftSeconds).toBe(0)
    expect(result.deficitSeconds).toBeLessThan(0)
  })

  it('declines to guess before a stream exists', () => {
    expect(describeDeficit(capture({ expected_frames: null }))).toBeNull()
    expect(describeDeficit(capture({ sample_rate: null }))).toBeNull()
  })

  it('has no phase figure when the block size is not published', () => {
    const result = describeDeficit(
      capture({ stream_detail: {}, expected_frames: 384_000, frames: 384_000 }),
    )!
    expect(result.phaseSeconds).toBe(0)
  })
})
