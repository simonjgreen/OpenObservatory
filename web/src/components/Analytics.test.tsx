// @vitest-environment jsdom
/** The analytics section: the shipped questions appear, choosing one shows
 *  what a working station should look like and draws its chart from the
 *  roll-up, and an absent endpoint degrades to a sentence rather than a
 *  blank page. Fixture values are the ones `tests/test_analytics.py` seeds
 *  and the station emits: ten robins at 06:00, sunrise 05:35. */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Analytics } from './Analytics'
import type { Question } from '../analytics/types'
import type { AnalyticsRoute, AnalyticsRouteState } from '../hooks/useAnalyticsRoute'

const QUESTIONS: { questions: Question[] } = {
  questions: [
    {
      id: 'robin-year',
      title: 'When are there most robins?',
      question: 'Which weeks of the year hold the most European Robin detections?',
      expect: 'Robins sing almost year-round in a British garden.',
      view: 'series',
      params: { range: 'this-year', grain: 'week', group: 'bird', label: 'European Robin' },
    },
    {
      id: 'dawn-chorus',
      title: 'When does the day start?',
      question: 'Bird detections by hour.',
      expect: 'The morning edge should follow the civil-dawn line.',
      view: 'hours',
      params: { range: 'all', columns: 'week', group: 'bird' },
    },
  ],
}

const STATUS = {
  timezone: 'Europe/London',
  builder_version: '1',
  days_built: 3,
  first_date: '2026-08-04',
  last_date: '2026-08-06',
  first_detection_date: '2026-08-04',
  days_missing: 0,
  last_built_at: '2026-08-06T09:00:00Z',
  age_seconds: 120,
  detections_rolled_up: 66,
  today: '2026-08-06',
  coordinates_set: true,
}

const SERIES = {
  range: { name: 'this-year', first_date: '2026-01-01', last_date: '2026-08-06', label: 'this year', days: 218 },
  grain: 'week',
  group: 'bird',
  label: 'European Robin',
  buckets: [
    {
      key: '2026-W32',
      start_date: '2026-08-03',
      end_date: '2026-08-09',
      detections: 10,
      groups: { bird: 10 },
      seconds_from_microphone: 86400,
      seconds_paused: 1800,
      per_captured_hour: 0.417,
      days_built: 1,
      days_unbuilt: 0,
      partial: true,
    },
  ],
  note: 'Counts are of detections, not of animals.',
  excluded_synthetic_count: 1,
  excluded_withdrawn_count: 1,
  excluded_rejected_count: 1,
  days_built: 3,
}

const HOURS = {
  range: { name: 'all', first_date: '2026-08-04', last_date: '2026-08-06', label: 'everything recorded', days: 3 },
  columns_grain: 'week',
  group: 'bird',
  label: null,
  timezone: 'Europe/London',
  columns: [
    {
      key: '2026-W32',
      start_date: '2026-08-03',
      end_date: '2026-08-09',
      hours: [1, 0, 0, 1, 0, 0, 13, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
      captured: Array(24).fill(3600),
      days_built: 3,
      partial: true,
      solar: { sunrise: 5.58, sunset: 20.67, civil_dawn: 4.9, civil_dusk: 21.35 },
    },
  ],
  profile: [1, 0, 0, 1, 0, 0, 13, 5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
  profile_captured: Array(24).fill(3600),
  note: 'Counts are of detections, not of animals.',
  excluded_synthetic_count: 0,
  excluded_withdrawn_count: 0,
  excluded_rejected_count: 0,
  days_built: 3,
}

function fetchStub(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (input: string) => {
    const url = String(input)
    const match = (path: string) => url.includes(path)
    if (match('/analytics/questions')) return { ok: true, json: async () => QUESTIONS }
    if (match('/analytics/status')) return { ok: true, json: async () => STATUS }
    if (match('/analytics/reports')) return { ok: true, json: async () => ({ reports: [] }) }
    if (match('/analytics/taxa')) return { ok: true, json: async () => ({ rows: [] }) }
    if (match('/analytics/series')) {
      return overrides.series ? (overrides.series as object) : { ok: true, json: async () => SERIES }
    }
    if (match('/analytics/hours')) return { ok: true, json: async () => HOURS }
    return { ok: false, status: 404, json: async () => ({}) }
  })
}

function routeState(route: AnalyticsRoute): AnalyticsRouteState {
  return {
    route,
    open: vi.fn<AnalyticsRouteState['open']>(),
    close: vi.fn<AnalyticsRouteState['close']>(),
    show: vi.fn<AnalyticsRouteState['show']>(),
    adjust: vi.fn<AnalyticsRouteState['adjust']>(),
  }
}

describe('Analytics', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lists the shipped questions and, for the open one, what a working station shows', async () => {
    vi.stubGlobal('fetch', fetchStub())
    const state = routeState({
      active: true,
      questionId: 'robin-year',
      reportId: null,
      view: 'series',
      params: QUESTIONS.questions[0].params,
    })
    render(<Analytics route={state} timeZone="Europe/London" />)
    await waitFor(() => expect(screen.getAllByText('When are there most robins?').length).toBeGreaterThan(0))
    expect(screen.getByText('When does the day start?')).toBeInTheDocument()
    expect(screen.getByText(/Robins sing almost year-round/)).toBeInTheDocument()
    // The chart drew, its coverage and exclusions travelled with it.
    await waitFor(() => expect(screen.getByRole('img', { name: /Detections per week/ })).toBeInTheDocument())
    expect(screen.getByText(/3 of 218 days rolled up/)).toBeInTheDocument()
    expect(screen.getByText(/1 withdrawn, 1 rejected/)).toBeInTheDocument()
    expect(screen.getByText(/roll-up: 3 days/)).toBeInTheDocument()
  })

  it('opens a question through the route rather than fetching itself', async () => {
    vi.stubGlobal('fetch', fetchStub())
    const state = routeState({ active: true, questionId: null, reportId: null, view: 'series', params: { range: 'last-90d' } })
    render(<Analytics route={state} timeZone="Europe/London" />)
    const button = await screen.findByText('When does the day start?')
    fireEvent.click(button)
    expect(state.show).toHaveBeenCalledWith('hours', QUESTIONS.questions[1].params, { questionId: 'dawn-chorus' })
  })

  it('draws the hour heat-map with sunrise and sunset lines and the hour profile', async () => {
    vi.stubGlobal('fetch', fetchStub())
    const state = routeState({
      active: true,
      questionId: 'dawn-chorus',
      reportId: null,
      view: 'hours',
      params: QUESTIONS.questions[1].params,
    })
    render(<Analytics route={state} timeZone="Europe/London" />)
    await waitFor(() => expect(screen.getByRole('img', { name: /Detections by hour of day/ })).toBeInTheDocument())
    expect(screen.getByText(/sunrise · sunset/)).toBeInTheDocument()
    expect(screen.getByText(/Most often 06:00–07:00/)).toBeInTheDocument()
  })

  it('degrades to a sentence when the roll-up endpoint is absent', async () => {
    vi.stubGlobal('fetch', fetchStub({ series: { ok: false, status: 404, json: async () => ({}) } }))
    const state = routeState({ active: true, questionId: null, reportId: null, view: 'series', params: { range: 'last-90d' } })
    render(<Analytics route={state} timeZone="Europe/London" />)
    await waitFor(() => expect(screen.getByText(/analytics unavailable: analytics 404/)).toBeInTheDocument())
  })
})
