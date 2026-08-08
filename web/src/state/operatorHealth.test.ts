import { describe, expect, it } from 'vitest'
import { operatorCards } from './operatorHealth'
import type { StationStatus } from '../types'

function baseStatus(overrides: Partial<StationStatus> = {}): StationStatus {
  return {
    station: { id: null, name: 'x', timezone: 'UTC', latitude: null, longitude: null, software_version: '', uptime_s: 10 },
    capture: {
      state: 'capturing',
      detail: '',
      stream_detail: {},
      stream_id: null,
      source_kind: 'alsa',
      is_live_hardware: true,
      device_key: null,
      device_label: 'AudioMoth',
      sample_rate: 384000,
      sample_format: 's16',
      channels: 1,
      started_utc: null,
      blocks: 1,
      frames: 1,
      expected_frames: null,
      continuity_ratio: 0.999,
      discontinuities: 0,
      estimated_missing_frames: 0,
      stream_restarts: 0,
      open_failures: 0,
      block_age_s: 0.1,
      hot_path_cpu_ratio: 0.1,
      observed_rate_hz: null,
      rate_offset_ppm: null,
      overruns: 0,
    },
    resampler: null,
    rings: { native: null, audible: null },
    levels: { native: null, audible: null, note: '' },
    spectrograms: [],
    segmenters: [],
    leases: { outstanding: 0, granted: 0, released: 0, expired: 0, consumers: [] },
    detectors: [],
    normaliser: { normalised: 0, duplicates_suppressed: 0, claim_violations: 0 },
    clips: {
      requested: 0,
      written: 0,
      skipped_low_score: 0,
      skipped_plugin_not_clipped: 0,
      skipped_rate_limited: 0,
      skipped_disk_guard: 0,
      failed_not_in_ring: 0,
      failed_io: 0,
      bytes_written: 0,
      writes_last_minute: 0,
      policy: {},
      disk_guard_active: null,
    },
    storage: { clip_dir: '/', clip_count: 12, clip_bytes: 0, disk_total_bytes: 100, disk_free_bytes: 80, disk_used_ratio: 0.2 },
    live_audio: { sample_rate: 48000, chunk_ms: 20, chunk_frames: 960, listeners: 0, chunks_published: 0, last_peak: 0 },
    bus: { published: 0, subscribers: 0, history: 0, per_subscriber: [] },
    persistence: { written: 0, queued: 0, dropped: 0, failures: 0 },
    ...overrides,
  } as StationStatus
}

describe('operatorCards', () => {
  it('shows a waiting card when there is no status yet', () => {
    const cards = operatorCards(null)
    expect(cards).toHaveLength(1)
    expect(cards[0].tone).toBe('warn')
  })

  it('flags synthetic audio as danger, loudly, even while "listening"', () => {
    const status = baseStatus({
      capture: { ...baseStatus().capture, is_live_hardware: false, source_kind: 'wav_replay' },
    })
    const listening = operatorCards(status).find((c) => c.key === 'listening')!
    expect(listening.tone).toBe('danger')
    expect(listening.headline).toContain('NOT LIVE AUDIO')
  })

  it('reports healthy listening on real hardware with fresh audio', () => {
    const listening = operatorCards(baseStatus()).find((c) => c.key === 'listening')!
    expect(listening.tone).toBe('ok')
  })

  it('warns when the last audio block is stale', () => {
    const status = baseStatus({ capture: { ...baseStatus().capture, block_age_s: 5 } })
    const listening = operatorCards(status).find((c) => c.key === 'listening')!
    expect(listening.tone).toBe('warn')
  })

  it('escalates storage tone with disk_used_ratio', () => {
    const ok = operatorCards(baseStatus()).find((c) => c.key === 'storage')!
    expect(ok.tone).toBe('ok')
    const warn = operatorCards(
      baseStatus({ storage: { ...baseStatus().storage, disk_used_ratio: 0.9 } }),
    ).find((c) => c.key === 'storage')!
    expect(warn.tone).toBe('warn')
    const danger = operatorCards(
      baseStatus({ storage: { ...baseStatus().storage, disk_used_ratio: 0.99 } }),
    ).find((c) => c.key === 'storage')!
    expect(danger.tone).toBe('danger')
  })

  it('names degraded detectors in plain language', () => {
    const status = baseStatus({
      detectors: [
        {
          plugin_id: 'birdnet',
          plugin_version: '1',
          model_id: 'm',
          model_version: '1',
          licence_name: '',
          licence_url: null,
          claim: '',
          calibrated: false,
          resource_class: '',
          state: 'error',
          detail: 'model load failed',
          window: { stream_kind: 'audible', sample_rate: 48000, duration_s: 3, stride_s: 1 },
          queue_depth: 0,
          queue_capacity: 10,
          windows_analysed: 0,
          windows_dropped_queue_full: 0,
          windows_dropped_stale: 0,
          detections_emitted: 0,
          failures: 1,
          last_runtime_ms: null,
          p95_runtime_ms: null,
          realtime_factor: null,
          lag_s: null,
          circuit_open: false,
        },
      ],
    })
    const detectors = operatorCards(status).find((c) => c.key === 'detectors')!
    expect(detectors.tone).toBe('danger')
    expect(detectors.detail).toContain('birdnet')
    expect(detectors.detail).toContain('model load failed')
  })
})
