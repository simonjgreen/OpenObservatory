// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FirmwarePanel } from './FirmwarePanel'

const published = {
  version: '0.2.1',
  sha256: 'a'.repeat(64),
  size_bytes: 1_127_649,
  published_utc: '2026-08-09T16:00:00+00:00',
  notes: 'adds OTA',
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    published,
    image_path: '/api/v1/firmware/image',
    offer_on_connect: true,
    app_slot_bytes: 0x1f0000,
    displays: [],
    ...overrides,
  }
}

function stubFetch(response: unknown, ok = true) {
  const fetch = vi.fn().mockResolvedValue({ ok, status: ok ? 200 : 404, json: async () => response })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('FirmwarePanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('degrades to "not available" rather than breaking the page', async () => {
    stubFetch({}, false)
    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByText(/not available on this station/i)).toBeInTheDocument())
  })

  it('says plainly when nothing is published', async () => {
    stubFetch(payload({ published: null }))
    render(<FirmwarePanel />)
    await waitFor(() =>
      expect(screen.getByText(/displays keep running whatever they have/i)).toBeInTheDocument(),
    )
  })

  it('shows the published image and how much of the slot it uses', async () => {
    stubFetch(payload())
    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByText('0.2.1')).toBeInTheDocument())
    expect(screen.getByText(/1\.08 MB of 1\.94 MB slot/)).toBeInTheDocument()
  })

  it('reports a display that does not name a version as unknown, not as behind', async () => {
    // Different claims. A build older than ADR-050 has no update path and says
    // nothing; calling that "out of date" would be a statement the station
    // cannot support.
    stubFetch(
      payload({
        displays: [{ firmware_version: null, up_to_date: null, frames_sent: 3 }],
      }),
    )
    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByText('unknown')).toBeInTheDocument())
    expect(screen.queryByText('behind')).not.toBeInTheDocument()
  })

  it('distinguishes a display that is behind from one that is current', async () => {
    stubFetch(
      payload({
        displays: [
          { firmware_version: '0.2.0', up_to_date: false, frames_sent: 3 },
          { firmware_version: '0.2.1', up_to_date: true, frames_sent: 9 },
        ],
      }),
    )
    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByText('behind')).toBeInTheDocument())
    expect(screen.getByText('up to date')).toBeInTheDocument()
  })

  it('refuses a version the display could not order, before the upload', async () => {
    stubFetch(payload({ published: null }))
    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByLabelText('version')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('version'), '0.2.1-rc1')
    expect(screen.getByText(/numbers and dots only/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'publish' })).toBeDisabled()
  })

  it('reports a rollout as "offered", never as installed', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload() })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ...payload(), offered: 1, connected: 2 }),
      })
    vi.stubGlobal('fetch', fetch)

    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'roll out now' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'roll out now' }))

    await waitFor(() => expect(screen.getByText(/offered to 1 of 2 connected/)).toBeInTheDocument())
    expect(fetch).toHaveBeenLastCalledWith(
      '/api/v1/firmware/rollout',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('says so when no display needed the rollout', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload() })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ...payload(), offered: 0, connected: 1 }),
      })
    vi.stubGlobal('fetch', fetch)

    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'roll out now' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'roll out now' }))
    await waitFor(() => expect(screen.getByText(/no display needed it/)).toBeInTheDocument())
  })

  it('surfaces the station’s own words when an image is refused', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => payload({ published: null }) })
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({
          detail: { errors: { image: 'This is not an ESP32 application image: it does not start with 0xE9.' } },
        }),
      })
    vi.stubGlobal('fetch', fetch)

    render(<FirmwarePanel />)
    await waitFor(() => expect(screen.getByLabelText('version')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('version'), '0.2.1')

    // Named .bin because the `accept` attribute is a hint, not the check --
    // the station reads the first byte, which is the only thing that settles
    // whether a file will boot.
    const file = new File([new Uint8Array(16)], 'firmware.bin', {
      type: 'application/octet-stream',
    })
    await userEvent.upload(screen.getByLabelText('image'), file)
    await waitFor(() => expect(screen.getByRole('button', { name: 'publish' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'publish' }))

    await waitFor(() => expect(screen.getByText(/does not start with 0xE9/)).toBeInTheDocument())
  })
})
