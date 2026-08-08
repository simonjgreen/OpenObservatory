import { describe, expect, it } from 'vitest'
import {
  appendDetection,
  appendEvent,
  captureHealth,
  reconcileSpecs,
  routeColumns,
} from './liveState'
import type { ColumnBatch, Detection, Envelope, SpectrogramSpec, StationStatus } from '../types'

function spec(overrides: Partial<SpectrogramSpec> = {}): SpectrogramSpec {
  return {
    channel: 0,
    name: 'audible',
    sample_rate: 48000,
    bins: 256,
    min_hz: 0,
    max_hz: 24000,
    hop_s: 0.01,
    fft_size: 1024,
    floor_db: -95,
    ceiling_db: -15,
    columns_emitted: 0,
    history_columns: 0,
    ...overrides,
  }
}

function detection(id: string): Detection {
  return { id } as Detection
}

function event(type: string, id = type): Envelope {
  return { event_id: id, event_type: type, occurred_at: '2026-01-01T00:00:00Z', data: {} } as Envelope
}

describe('reconcileSpecs', () => {
  it('keeps the same array reference when the channel set is unchanged', () => {
    const current = [spec({ channel: 0 }), spec({ channel: 1 })]
    const next = [spec({ channel: 0 }), spec({ channel: 1 })]
    expect(reconcileSpecs(current, next)).toBe(current)
  })

  it('replaces when a channel is added', () => {
    const current = [spec({ channel: 0 })]
    const next = [spec({ channel: 0 }), spec({ channel: 1 })]
    expect(reconcileSpecs(current, next)).toBe(next)
  })

  it('replaces when bins change on an existing channel', () => {
    const current = [spec({ channel: 0, bins: 256 })]
    const next = [spec({ channel: 0, bins: 512 })]
    expect(reconcileSpecs(current, next)).toBe(next)
  })
})

describe('appendDetection', () => {
  it('appends a new detection', () => {
    const result = appendDetection([detection('a')], detection('b'), 10)
    expect(result.map((d) => d.id)).toEqual(['a', 'b'])
  })

  it('deduplicates by id and keeps the original array', () => {
    const current = [detection('a')]
    expect(appendDetection(current, detection('a'), 10)).toBe(current)
  })

  it('drops the oldest once past the bound', () => {
    const current = [detection('a'), detection('b')]
    const result = appendDetection(current, detection('c'), 2)
    expect(result.map((d) => d.id)).toEqual(['b', 'c'])
  })
})

describe('appendEvent', () => {
  it('suppresses per-second level telemetry and status snapshots', () => {
    expect(appendEvent([], event('capture.levels'), 10)).toEqual([])
    expect(appendEvent([], event('station.status'), 10)).toEqual([])
  })

  it('prepends everything else, newest first', () => {
    const result = appendEvent([event('a')], event('b'), 10)
    expect(result.map((e) => e.event_id)).toEqual(['b', 'a'])
  })

  it('bounds the log from the tail', () => {
    const current = [event('b'), event('a')]
    const result = appendEvent(current, event('c'), 2)
    expect(result.map((e) => e.event_id)).toEqual(['c', 'b'])
  })
})

describe('routeColumns', () => {
  it('calls only sinks registered for the batch channel', () => {
    const calledOn0: ColumnBatch[] = []
    const calledOn1: ColumnBatch[] = []
    const sinks = new Map<number, Set<(batch: ColumnBatch) => void>>([
      [0, new Set([(b) => calledOn0.push(b)])],
      [1, new Set([(b) => calledOn1.push(b)])],
    ])
    const batch = { channel: 0 } as ColumnBatch
    routeColumns(sinks, batch)
    expect(calledOn0).toEqual([batch])
    expect(calledOn1).toEqual([])
  })

  it('is a no-op when nothing is registered for the channel', () => {
    const sinks = new Map<number, Set<(batch: ColumnBatch) => void>>()
    expect(() => routeColumns(sinks, { channel: 5 } as ColumnBatch)).not.toThrow()
  })
})

describe('captureHealth', () => {
  it('reports no connection when there is no status yet', () => {
    expect(captureHealth(null)).toEqual({
      listening: false,
      synthetic: false,
      label: 'no station connection',
    })
  })

  it('flags synthetic audio loudly, regardless of capture state', () => {
    const status = {
      capture: { state: 'capturing', is_live_hardware: false, source_kind: 'wav_replay', detail: '' },
    } as unknown as StationStatus
    const health = captureHealth(status)
    expect(health.synthetic).toBe(true)
    expect(health.label).toMatch(/NOT LIVE AUDIO/)
  })

  it('reports listening on real hardware', () => {
    const status = {
      capture: { state: 'capturing', is_live_hardware: true, source_kind: 'alsa', detail: '' },
    } as unknown as StationStatus
    expect(captureHealth(status)).toEqual({
      listening: true,
      synthetic: false,
      label: 'listening on the microphone',
    })
  })

  it('reports not listening when capture has stopped on real hardware', () => {
    const status = {
      capture: { state: 'stopped', is_live_hardware: true, source_kind: 'alsa', detail: 'device closed' },
    } as unknown as StationStatus
    expect(captureHealth(status)).toEqual({
      listening: false,
      synthetic: false,
      label: 'device closed',
    })
  })
})
