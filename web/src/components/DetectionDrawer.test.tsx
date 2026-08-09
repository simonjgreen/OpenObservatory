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

describe('DetectionDrawer withdrawal (ADR-042)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function withdrawn(): Detection {
    return {
      ...detection(),
      common_name: 'Western Screech-Owl',
      display_name: 'Western Screech-Owl',
      withdrawn: true,
      withdrawal: {
        reason: 'occurrence 8e-06 is at or below the plausibility floor (0.0005)',
        reviewed_utc: '2026-08-09T10:00:00+00:00',
        recomputed_band: 'implausible',
      },
    } as unknown as Detection
  }

  it('says the identification was withdrawn, and why', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ review: null }) }),
    )
    render(<DetectionDrawer detection={withdrawn()} localTimeZone="UTC" onClose={() => {}} />)

    expect(screen.getByText('This identification has been withdrawn.')).toBeTruthy()
    expect(screen.getByText(/plausibility floor/)).toBeTruthy()
    // Charter item 5: the original claim stays visible and attributable, so the
    // species name and the review timestamp are both still on screen.
    expect(screen.getAllByText(/Western Screech-Owl/).length).toBeGreaterThan(0)
    expect(screen.getByText(/2026-08-09T10:00:00/)).toBeTruthy()
  })

  it('says nothing of the kind for an ordinary detection', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ review: null }) }),
    )
    render(<DetectionDrawer detection={detection()} localTimeZone="UTC" onClose={() => {}} />)
    expect(screen.queryByText('This identification has been withdrawn.')).toBeNull()
  })
})
