import { describe, expect, it } from 'vitest'

import { formatDeficit, formatDuration } from './Pipeline'

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
