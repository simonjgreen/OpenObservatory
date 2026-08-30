// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SettingsPanel } from './SettingsPanel'

const basePayload = {
  fields: [
    {
      name: 'station_name',
      category: 'station',
      tier: 'live' as const,
      kind: 'text' as const,
      label: 'station name',
      help: null,
      unit: null,
      minimum: null,
      maximum: null,
      choices: [],
      danger: null,
      secret: false,
      restart_required: false,
      note: null,
      default: 'Garden Observatory',
      value: 'Garden Observatory',
    },
    {
      name: 'latitude',
      category: 'station',
      tier: 'restart' as const,
      kind: 'float' as const,
      label: 'latitude',
      help: null,
      unit: '°',
      minimum: -90,
      maximum: 90,
      choices: [],
      danger: null,
      secret: false,
      restart_required: true,
      note: 'Bound at detector start.',
      default: null,
      value: null,
    },
    {
      name: 'ultrasonic_min_snr_db',
      category: 'detect-ultrasonic',
      tier: 'live' as const,
      kind: 'float' as const,
      label: 'pulse SNR threshold',
      help: 'The first knob to raise when a noisy mount is producing false passes.',
      unit: 'dB',
      minimum: 0,
      maximum: 90,
      choices: [],
      danger: null,
      secret: false,
      restart_required: false,
      note: null,
      default: 12,
      value: 18,
    },
    {
      name: 'clip_plugins',
      category: 'clips',
      tier: 'live' as const,
      kind: 'csv' as const,
      label: 'detectors that produce clips',
      help: null,
      unit: null,
      minimum: null,
      maximum: null,
      choices: [],
      danger: "Adding 'activity-v1' here will fill any disk.",
      secret: false,
      restart_required: false,
      note: null,
      default: 'birdnet-v2.4,ultrasonic-pass-v1',
      value: 'birdnet-v2.4,ultrasonic-pass-v1',
    },
    {
      name: 'evidence_common_species',
      category: 'retention',
      tier: 'live' as const,
      kind: 'csv' as const,
      label: 'common species',
      help: null,
      unit: null,
      minimum: null,
      maximum: null,
      choices: [],
      danger: null,
      secret: false,
      restart_required: false,
      note: null,
      default: 'Common Woodpigeon',
      value: 'Common Woodpigeon',
    },
    {
      name: 'evidence_suggestion_dismissed',
      category: 'retention',
      tier: 'live' as const,
      kind: 'csv' as const,
      label: 'never suggest again',
      help: null,
      unit: null,
      minimum: null,
      maximum: null,
      choices: [],
      danger: null,
      secret: false,
      restart_required: false,
      note: null,
      default: '',
      value: '',
    },
    {
      name: 'mqtt_password',
      category: 'mqtt',
      tier: 'live' as const,
      kind: 'text' as const,
      label: 'password',
      help: null,
      unit: null,
      minimum: null,
      maximum: null,
      choices: [],
      danger: null,
      secret: true,
      restart_required: false,
      note: null,
      default: null,
      value: null,
      is_set: true,
    },
  ],
  categories: [
    { id: 'station', title: 'Station', description: 'Who and where this station is.' },
    { id: 'detect-ultrasonic', title: 'Ultrasonic detection', description: 'The bat-pass detector.' },
    { id: 'clips', title: 'Evidence clips', description: '' },
    { id: 'retention', title: 'Retention', description: '' },
    { id: 'mqtt', title: 'MQTT / Home Assistant', description: '' },
    { id: 'setup', title: 'Setup', description: '', hidden: true },
  ],
  non_editable: [{ name: 'bind_port', reason: 'the browser cannot follow the station to a new port.' }],
  pending_restart: [],
  location_configured: false,
}

const expand = async (title: string) => {
  fireEvent.click(await screen.findByRole('button', { name: new RegExp(title, 'i') }))
}

const suggestionsResponse = (suggestions: unknown[] = []) => ({
  ok: true,
  json: async () => ({ suggestions }),
})

/** `SettingsPanel` fetches `/api/v1/retention/suggestions` on mount alongside
 *  `/api/v1/settings`. Every test below routes that request to a fixed,
 *  always-empty response so it never disturbs a sequence of `/settings`
 *  GET/PUT responses a test has queued up -- and `settingsCalls` strips it
 *  back out before a test indexes into `fetchMock.mock.calls`, so the rest of
 *  this file can keep asserting call order the way it already did. */
function stubSequenced(responses: unknown[]) {
  let next = 0
  const fetchMock = vi.fn((url: string) => {
    if (String(url).includes('/retention/suggestions')) {
      return Promise.resolve(suggestionsResponse())
    }
    const response = responses[Math.min(next, responses.length - 1)]
    next += 1
    return Promise.resolve(response)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function settingsCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    (call: any[]) => !String(call[0]).includes('/retention/suggestions'),
  )
}

const stubGet = () => stubSequenced([{ ok: true, json: async () => basePayload }])

describe('SettingsPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('warns loudly when no location is configured', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No location set/)).toBeInTheDocument())
    expect(screen.getByText(/plausibility filtering/)).toBeInTheDocument()
  })

  it('never renders a secret value and marks it as set via placeholder', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await expand('MQTT / Home Assistant')
    expect(screen.getByPlaceholderText(/leave blank to keep/)).toBeInTheDocument()
  })

  it('shows the measured default as a one-click way back to a known state', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await expand('Ultrasonic detection')
    const input = screen.getByLabelText(/pulse SNR threshold/) as HTMLInputElement
    expect(input.value).toBe('18')
    fireEvent.click(screen.getByRole('button', { name: 'default: 12' }))
    expect((screen.getByLabelText(/pulse SNR threshold/) as HTMLInputElement).value).toBe('12')
  })

  it('will not save a dangerous change until it is acknowledged', async () => {
    const fetchMock = stubSequenced([{ ok: true, json: async () => basePayload }])
    render(<SettingsPanel onClose={() => {}} />)
    await expand('Evidence clips')
    fireEvent.change(screen.getByLabelText(/detectors that produce clips/), {
      target: { value: 'birdnet-v2.4,activity-v1' },
    })
    expect(screen.getByText(/will fill any disk/)).toBeInTheDocument()
    fireEvent.click(screen.getByText(/^save/))
    await waitFor(() =>
      expect(screen.getByText(/acknowledge the warning on/)).toBeInTheDocument(),
    )
    // Nothing was sent: only the initial GET happened.
    expect(settingsCalls(fetchMock)).toHaveLength(1)

    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByText(/^save/))
    await waitFor(() => expect(settingsCalls(fetchMock)).toHaveLength(2))
    expect(JSON.parse(settingsCalls(fetchMock)[1][1].body)).toEqual({
      clip_plugins: 'birdnet-v2.4,activity-v1',
    })
  })

  it('reports pending-restart fields verbatim after a save', async () => {
    const afterSave = {
      ...basePayload,
      fields: basePayload.fields.map((field) =>
        field.name === 'latitude' ? { ...field, value: 51.4769, pending_restart: true } : field,
      ),
      pending_restart: ['latitude'],
      saved: ['latitude'],
    }
    const fetchMock = stubSequenced([
      { ok: true, json: async () => basePayload },
      { ok: true, json: async () => afterSave },
    ])
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByLabelText(/latitude/)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/latitude/), { target: { value: '51.4769' } })
    fireEvent.click(screen.getByText(/^save/))

    await waitFor(() =>
      expect(screen.getByText(/In force after the next restart: latitude/)).toBeInTheDocument(),
    )
    // The PUT body carried only the changed field.
    expect(JSON.parse(settingsCalls(fetchMock)[1][1].body)).toEqual({ latitude: '51.4769' })
  })

  it('surfaces per-field validation errors instead of a generic failure', async () => {
    stubSequenced([
      { ok: true, json: async () => basePayload },
      {
        ok: false,
        status: 422,
        json: async () => ({
          detail: { errors: { latitude: 'set both coordinates, or clear both' } },
        }),
      },
    ])
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByLabelText(/latitude/)).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/latitude/), { target: { value: '51.4769' } })
    fireEvent.click(screen.getByText(/^save/))

    await waitFor(() =>
      expect(screen.getByText(/set both coordinates, or clear both/)).toBeInTheDocument(),
    )
  })

  it('says which settings are not browser-editable, and why', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() =>
      expect(screen.getByText(/not editable from a browser \(1\)/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/cannot follow the station to a new port/)).toBeInTheDocument()
  })

  it('search finds a field in a collapsed category', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByLabelText(/find a setting/)).toBeInTheDocument())
    // Collapsed to start with: the ultrasonic category is not the open one.
    expect(screen.queryByLabelText(/pulse SNR threshold/)).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/find a setting/), { target: { value: 'snr' } })
    expect(screen.getByLabelText(/pulse SNR threshold/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/station name/)).not.toBeInTheDocument()
  })

  it('does not render the hidden setup category', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('Station')).toBeInTheDocument())
    expect(screen.queryByText('Setup')).not.toBeInTheDocument()
  })
})

describe('SettingsPanel evidence suggestions (ADR-074)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders nothing at all when there are no suggestions', async () => {
    stubGet()
    render(<SettingsPanel onClose={() => {}} />)
    await expand('Retention')
    await waitFor(() => expect(screen.getByLabelText(/common species/)).toBeInTheDocument())
    // No empty state, no "0 suggestions" line -- ADR-074's rule is that this
    // must not nag.
    expect(screen.queryByText(/suggest/i, { selector: 'p' })).not.toBeInTheDocument()
    expect(document.querySelector('.evidence-suggestion')).toBeNull()
  })

  it('surfaces a qualifying species as a dismissible row, worded like the ADR', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes('/retention/suggestions')) {
        return Promise.resolve(
          suggestionsResponse([
            {
              common_name: 'European Greenfinch',
              detection_count: 1204,
              byte_total: 3_100_000_000,
              window_days: 30,
            },
          ]),
        )
      }
      return Promise.resolve({ ok: true, json: async () => basePayload })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<SettingsPanel onClose={() => {}} />)
    await expand('Retention')

    expect(
      await screen.findByText(
        /European Greenfinch produced 1,204 clips \(3\.1 GB\) in the last 30 days\. Add to the common list\?/,
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add to common list' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Never suggest this species' })).toBeInTheDocument()
  })

  it('adding a suggestion appends to evidence_common_species through the existing PUT, not a second path', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes('/retention/suggestions')) {
        return Promise.resolve(
          suggestionsResponse([
            {
              common_name: 'European Greenfinch',
              detection_count: 1204,
              byte_total: 3_100_000_000,
              window_days: 30,
            },
          ]),
        )
      }
      return Promise.resolve({ ok: true, json: async () => basePayload })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<SettingsPanel onClose={() => {}} />)
    await expand('Retention')

    fireEvent.click(await screen.findByRole('button', { name: 'Add to common list' }))

    await waitFor(() =>
      expect(settingsCalls(fetchMock).some((call: any[]) => {
        const init = call[1]
        return (
          init?.method === 'PUT' &&
          JSON.parse(String(init.body)).evidence_common_species ===
            'Common Woodpigeon,European Greenfinch'
        )
      })).toBe(true),
    )
    // The row is gone once the write succeeds.
    await waitFor(() =>
      expect(screen.queryByText(/European Greenfinch produced/)).not.toBeInTheDocument(),
    )
  })

  it('dismissing a suggestion appends to evidence_suggestion_dismissed, never to the common list', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url.includes('/retention/suggestions')) {
        return Promise.resolve(
          suggestionsResponse([
            {
              common_name: 'European Greenfinch',
              detection_count: 1204,
              byte_total: 3_100_000_000,
              window_days: 30,
            },
          ]),
        )
      }
      return Promise.resolve({ ok: true, json: async () => basePayload })
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<SettingsPanel onClose={() => {}} />)
    await expand('Retention')

    fireEvent.click(await screen.findByRole('button', { name: 'Never suggest this species' }))

    await waitFor(() =>
      expect(settingsCalls(fetchMock).some((call: any[]) => {
        const init = call[1]
        return (
          init?.method === 'PUT' &&
          JSON.parse(String(init.body)).evidence_suggestion_dismissed === 'European Greenfinch'
        )
      })).toBe(true),
    )
    await waitFor(() =>
      expect(screen.queryByText(/European Greenfinch produced/)).not.toBeInTheDocument(),
    )
  })
})
