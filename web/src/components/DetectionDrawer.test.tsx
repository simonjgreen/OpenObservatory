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
    canonical_taxon_id: 'sci:erithacus_rubecula',
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
    review: null,
    identification_source: 'model',
    effective_common_name: 'European Robin',
    effective_scientific_name: 'Erithacus rubecula',
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
        json: async () => ({
          status: 'confirmed',
          note: '',
          actor: 'local',
          corrected_taxon_id: null,
          corrected_common_name: null,
          corrected_scientific_name: null,
          created_at: '2026-08-08T03:05:00Z',
        }),
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

  it('posts held when the hold button is clicked', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url.endsWith('/review') && !init) {
        return Promise.resolve({ ok: true, json: async () => ({ review: null }) })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          status: 'held',
          note: '',
          actor: 'local',
          corrected_taxon_id: null,
          corrected_common_name: null,
          corrected_scientific_name: null,
          created_at: '2026-08-08T03:05:00Z',
        }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DetectionDrawer detection={detection()} localTimeZone="UTC" onClose={() => {}} />)

    fireEvent.click(await screen.findByText('★ hold'))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/detections/abc-123/review',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ status: 'held' }),
        }),
      ),
    )
  })

  it('searches taxa and submits a correction when a match is picked', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url.startsWith('/api/v1/taxa/search')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            taxa: [
              {
                taxon_id: 'sci:turdus_merula',
                common_name: 'Common Blackbird',
                scientific_name: 'Turdus merula',
                taxonomic_group: 'bird',
                detections: 12,
              },
            ],
            source: 'station_history',
          }),
        })
      }
      if (typeof url === 'string' && url.endsWith('/review') && !init) {
        return Promise.resolve({ ok: true, json: async () => ({ review: null }) })
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({
          status: 'corrected',
          note: '',
          actor: 'local',
          corrected_taxon_id: 'sci:turdus_merula',
          corrected_common_name: 'Common Blackbird',
          corrected_scientific_name: 'Turdus merula',
          created_at: '2026-08-08T03:05:00Z',
        }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DetectionDrawer detection={detection()} localTimeZone="UTC" onClose={() => {}} />)

    fireEvent.click(await screen.findByText('✎ correct identification'))
    const input = await screen.findByPlaceholderText('common or scientific name…')
    fireEvent.change(input, { target: { value: 'blackbird' } })

    const match = await screen.findByText('Common Blackbird')
    fireEvent.click(match)

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/detections/abc-123/review',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ status: 'corrected', corrected_taxon_id: 'sci:turdus_merula' }),
        }),
      ),
    )

    await screen.findByText('Common Blackbird', { selector: 'strong' })
  })

  it('shows an existing correction without re-fetching a new one', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url.endsWith('/review') && !init) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            review: {
              id: 'r1',
              detection_id: 'abc-123',
              status: 'corrected',
              note: 'heard it myself',
              actor: 'observer',
              corrected_taxon_id: 'sci:turdus_merula',
              corrected_common_name: 'Common Blackbird',
              corrected_scientific_name: 'Turdus merula',
              created_at: '2026-08-08T03:05:00Z',
            },
          }),
        })
      }
      return Promise.resolve({ ok: true, json: async () => ({}) })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<DetectionDrawer detection={detection()} localTimeZone="UTC" onClose={() => {}} />)

    await screen.findByText('Common Blackbird', { selector: 'strong' })
    expect(screen.getByText(/last reviewed: corrected by observer/)).toBeTruthy()
  })
})
