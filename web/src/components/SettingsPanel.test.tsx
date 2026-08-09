// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SettingsPanel } from './SettingsPanel'

const basePayload = {
  fields: [
    {
      name: 'station_name',
      category: 'station',
      secret: false,
      restart_required: false,
      note: null,
      value: 'Garden Observatory',
    },
    {
      name: 'latitude',
      category: 'station',
      secret: false,
      restart_required: true,
      note: 'Bound at detector start.',
      value: null,
    },
    {
      name: 'mqtt_password',
      category: 'mqtt',
      secret: true,
      restart_required: false,
      note: null,
      value: null,
      is_set: true,
    },
  ],
  pending_restart: [],
  location_configured: false,
}

describe('SettingsPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('warns loudly when no location is configured', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => basePayload }),
    )
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/No location set/)).toBeInTheDocument())
    expect(screen.getByText(/plausibility filtering/)).toBeInTheDocument()
  })

  it('never renders a secret value and marks it as set via placeholder', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => basePayload }),
    )
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/leave blank to keep/)).toBeInTheDocument(),
    )
  })

  it('reports pending-restart fields verbatim after a save', async () => {
    const afterSave = {
      ...basePayload,
      fields: basePayload.fields.map((field) =>
        field.name === 'latitude'
          ? { ...field, value: 51.4769, pending_restart: true }
          : field,
      ),
      pending_restart: ['latitude'],
      saved: ['latitude'],
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => basePayload })
      .mockResolvedValueOnce({ ok: true, json: async () => afterSave })
    vi.stubGlobal('fetch', fetchMock)
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('save')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/latitude/), { target: { value: '51.4769' } })
    fireEvent.click(screen.getByText('save'))

    await waitFor(() =>
      expect(screen.getByText(/In force after the next restart: latitude/)).toBeInTheDocument(),
    )
    // The PUT body carried only the changed field.
    const putCall = fetchMock.mock.calls[1]
    expect(JSON.parse(putCall[1].body)).toEqual({ latitude: '51.4769' })
  })

  it('surfaces per-field validation errors instead of a generic failure', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => basePayload })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({ detail: { errors: { latitude: 'set both coordinates, or clear both' } } }),
      })
    vi.stubGlobal('fetch', fetchMock)
    render(<SettingsPanel onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('save')).toBeInTheDocument())

    fireEvent.change(screen.getByLabelText(/latitude/), { target: { value: '51.4769' } })
    fireEvent.click(screen.getByText('save'))

    await waitFor(() =>
      expect(screen.getByText(/set both coordinates, or clear both/)).toBeInTheDocument(),
    )
  })
})
