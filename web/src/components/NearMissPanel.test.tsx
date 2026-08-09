// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { NearMissPanel, bandLabel, thresholdBin } from './NearMissPanel'

function band(overrides: Record<string, unknown> = {}) {
  return {
    band: 'in_range',
    threshold: 0.55,
    threshold_unreachable: false,
    rejected: 400,
    admitted: 9,
    histogram: {
      bin_width: 0.05,
      counts: [0, 0, 380, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    ...overrides,
  }
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    detectors: [
      {
        plugin_id: 'birdnet-v2.4',
        capacity: 200,
        held: 2,
        rejected_total: 400,
        admitted_total: 9,
        species_tracked: 2,
        species_omitted: 0,
        windows_analysed: 998,
        min_confidence: 0.12,
        range_model_loaded: true,
        week: 30,
        note: 'Candidates only. Scores are model outputs, not probabilities.',
        bands: [band(), band({ band: 'implausible', threshold: null, threshold_unreachable: true, rejected: 152, admitted: 0 })],
        species: [
          {
            label_index: 42,
            common_name: 'Eurasian Blackbird',
            scientific_name: 'Turdus merula',
            band: 'in_range',
            occurrence_probability: 0.9312,
            rejected: 41,
            admitted: 0,
            best_score: 0.538,
            shortfall: 0.012,
            last_at_ns: 1_700_000_000_000_000_000,
          },
        ],
        recent: [
          {
            at_ns: 1_700_000_000_000_000_000,
            common_name: 'Eurasian Blackbird',
            score: 0.538,
            occurrence_probability: 0.9312,
            band: 'in_range',
            threshold: 0.55,
            shortfall: 0.012,
          },
        ],
        ...overrides,
      },
    ],
  }
}

describe('thresholdBin', () => {
  it('marks the bin the bar sits in', () => {
    expect(thresholdBin(band() as never)).toBe(11) // 0.55 / 0.05
  })

  it('has no bin for a bar nothing can clear (ADR-032 implausible)', () => {
    expect(
      thresholdBin(band({ threshold: null, threshold_unreachable: true }) as never),
    ).toBeNull()
  })
})

describe('bandLabel', () => {
  it('spells out the two bands whose raw name misleads', () => {
    expect(bandLabel('no_prior')).toBe('no prior for species')
    expect(bandLabel('non_biological')).toBe('sound category')
    expect(bandLabel('out_of_range')).toBe('out of range')
  })
})

describe('NearMissPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('names the species that was refused, its score, prior and the bar it missed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload() }),
    )
    render(<NearMissPanel localTimeZone="Europe/London" />)

    await waitFor(() =>
      expect(screen.getAllByText('Eurasian Blackbird').length).toBeGreaterThan(0),
    )
    // The three numbers that let a person decide where to put the bar.
    expect(screen.getByText('0.538')).toBeInTheDocument()
    expect(screen.getByText('0.012')).toBeInTheDocument()
    expect(screen.getByText('9.3e-1')).toBeInTheDocument()
  })

  it('shows an unreachable bar as "never", not as a number', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => payload() }),
    )
    render(<NearMissPanel />)
    await waitFor(() => expect(screen.getByText('never')).toBeInTheDocument())
    expect(screen.getByText('implausible')).toBeInTheDocument()
  })

  it('degrades rather than breaking the page when the endpoint is absent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }),
    )
    render(<NearMissPanel />)
    await waitFor(() => expect(screen.getByText(/Not available/i)).toBeInTheDocument())
  })
})
