// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DetectionDrawer } from './DetectionDrawer'
import type { Detection } from '../types'

function detection(): Detection {
  return {
    id: 'abc-123',
    event_start_utc: '2026-08-08T03:00:00Z',
    event_end_utc: '2026-08-08T03:00:03Z',
    duration_s: 3,
    label: 'robin',
    display_name: 'European Robin',
    title_hint: null,
    flags: {},
    common_name: 'European Robin',
    scientific_name: 'Erithacus rubecula',
    canonical_taxon_id: null,
    rank: 'species',
    taxonomic_group: 'bird',
    score: 0.82,
    calibrated_probability: null,
    peak_frequency_hz: null,
    source_start_frame: 0,
    source_end_frame: 100,
    stream_id: 's1',
    source_kind: 'alsa',
    is_live_source: true,
    detector: {
      plugin_id: 'birdnet',
      plugin_version: '1',
      model_id: 'birdnet',
      model_version: '2.4',
      licence_name: 'x',
      calibrated: false,
    },
    media: [],
    native_result: {},
  } as unknown as Detection
}

describe('DetectionDrawer review controls', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts confirmed when the confirm button is clicked', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url.endsWith('/review') && !init) {
        return Promise.resolve({ ok: true, json: async () => ({ review: null }) })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ status: 'confirmed', note: '', created_at: '2026-08-08T03:05:00Z' }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DetectionDrawer detection={detection()} localTimeZone="UTC" onClose={() => {}} />)

    const confirmBtn = await screen.findByText('✓ confirm')
    fireEvent.click(confirmBtn)

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/detections/abc-123/review',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
  })
})
