// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FirstRun } from './FirstRun'

const setupPayload = {
  completed: false,
  required_outstanding: ['location', 'microphone'],
  steps: [
    {
      id: 'location',
      title: 'Where is this station?',
      detail: 'Coordinates switch on species plausibility filtering.',
      done: false,
      optional: false,
      fields: ['latitude', 'longitude'],
    },
    {
      id: 'microphone',
      title: 'Is the microphone working?',
      detail:
        'No microphone is being recorded from: the station is running on a synthetic source.',
      done: false,
      optional: false,
      fields: ['audio_device'],
    },
  ],
}

const field = (name: string, overrides: Record<string, unknown> = {}) => ({
  name,
  category: 'station',
  tier: 'restart' as const,
  kind: 'float' as const,
  label: name,
  help: null,
  unit: null,
  minimum: null,
  maximum: null,
  choices: [],
  danger: null,
  secret: false,
  restart_required: true,
  note: null,
  default: null,
  value: null,
  ...overrides,
})

const settingsPayload = {
  fields: [field('latitude'), field('longitude'), field('audio_device', { kind: 'text' })],
  categories: [],
  non_editable: [],
  pending_restart: [],
  location_configured: false,
}

/** `fetch` router: the component asks for /setup and /settings together. */
function stubApi(onPut?: (body: unknown) => { ok: boolean; body: unknown }) {
  const calls: Array<{ url: string; init?: RequestInit }> = []
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init })
    if (init?.method === 'PUT') {
      const parsed = JSON.parse(String(init.body))
      const result = onPut ? onPut(parsed) : { ok: true, body: settingsPayload }
      return { ok: result.ok, status: result.ok ? 200 : 422, json: async () => result.body }
    }
    if (url.includes('/setup')) return { ok: true, json: async () => setupPayload }
    return { ok: true, json: async () => settingsPayload }
  })
  vi.stubGlobal('fetch', mock)
  return calls
}

describe('FirstRun', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('walks the operator through the outstanding questions', async () => {
    stubApi()
    render(<FirstRun onClose={() => {}} />)
    await waitFor(() =>
      expect(screen.getByText('Where is this station?')).toBeInTheDocument(),
    )
    expect(screen.getByLabelText(/latitude/)).toBeInTheDocument()
    expect(screen.getByText(/Is the microphone working\?/)).toBeInTheDocument()
  })

  it('reports a synthetic source honestly rather than ticking the box', async () => {
    stubApi()
    render(<FirstRun onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Where is this station?')).toBeInTheDocument())
    fireEvent.click(screen.getByText(/Is the microphone working/))
    expect(
      screen.getByText(/running on a synthetic source/),
    ).toBeInTheDocument()
  })

  it('saves only the current step’s fields', async () => {
    const calls = stubApi()
    render(<FirstRun onClose={() => {}} />)
    await waitFor(() => expect(screen.getByLabelText(/latitude/)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText(/latitude/), { target: { value: '51.4769' } })
    fireEvent.change(screen.getByLabelText(/longitude/), { target: { value: '-0.0005' } })
    fireEvent.click(screen.getByText('save and continue'))
    await waitFor(() => expect(calls.some((call) => call.init?.method === 'PUT')).toBe(true))
    const put = calls.find((call) => call.init?.method === 'PUT')!
    expect(JSON.parse(String(put.init!.body))).toEqual({
      latitude: '51.4769',
      longitude: '-0.0005',
    })
  })

  it('shows the API’s own message when a value is rejected', async () => {
    stubApi(() => ({
      ok: false,
      body: { detail: { errors: { latitude: 'set both coordinates, or clear both' } } },
    }))
    render(<FirstRun onClose={() => {}} />)
    await waitFor(() => expect(screen.getByLabelText(/latitude/)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText(/latitude/), { target: { value: '51.4769' } })
    fireEvent.click(screen.getByText('save and continue'))
    await waitFor(() =>
      expect(screen.getByText(/set both coordinates, or clear both/)).toBeInTheDocument(),
    )
  })

  it('records the dismissal on the station, not in the browser', async () => {
    const calls = stubApi()
    const onClose = vi.fn()
    render(<FirstRun onClose={onClose} />)
    await waitFor(() => expect(screen.getByLabelText(/latitude/)).toBeInTheDocument())
    fireEvent.click(screen.getByText(/don’t show this again/))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const put = calls.find((call) => call.init?.method === 'PUT')!
    expect(JSON.parse(String(put.init!.body))).toEqual({ setup_completed: true })
  })
})
