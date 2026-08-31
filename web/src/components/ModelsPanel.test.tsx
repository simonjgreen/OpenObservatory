// @vitest-environment jsdom

/** ADR-006/ADR-017 promise that a model asset's licence is "surfaced in
 *  `/api/v1/models` and in the UI". The API half has been true since
 *  Milestone 2; the UI half was never built — nothing under `web/src`
 *  fetched that endpoint at all, so every model's licence, BirdNET's
 *  CC-BY-NC-SA-4.0 included, was surfaced nowhere a person would look.
 *
 *  These tests are that half. They care about four things in particular,
 *  because each one is a way this panel could quietly *mis*state a licence:
 *
 *  1. an asset that is not installed must not read as though it is;
 *  2. a file whose checksum does not match is not the asset the manifest
 *     names, so the licence shown beside it may not be its licence;
 *  3. a package entry carries no checksum at all, so the station cannot
 *     verify it and must not borrow the word "verified" from the file case;
 *  4. a station that cannot answer must say so — an empty panel reads as
 *     "no licences apply", which is the one wrong answer.
 */

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ModelsPanel,
  acquisitionCommand,
  assetKind,
  assetName,
  installationStatus,
} from './ModelsPanel'

const BIRDNET = {
  kind: 'file',
  filename: 'birdnet.tflite',
  licence: 'CC-BY-NC-SA-4.0',
  source_url:
    'https://raw.githubusercontent.com/tphakala/birdnet-go/main/internal/classifier/data/BirdNET_GLOBAL_6K_V2.4_Model_FP32.tflite',
  expected_sha256: '55f3e4055b1a13bfa9a2452731d0d34f6a02d6b775a334362665892794165e4c',
  installed: true,
  verified: true,
  size_bytes: 51_720_768,
}

/** The exact shape `models.licence_summary()` emits for a package row: no
 *  `expected_sha256`, and no `verified` key at all, because there is nothing
 *  to verify against. */
const BATDETECT2 = {
  kind: 'package',
  name: 'batdetect2',
  version: '1.3.1',
  licence: 'CC-BY-NC-4.0',
  source_url: 'https://github.com/macaodha/batdetect2',
  install_command: 'pip install batdetect2==1.3.1',
  used_for:
    'Bat-call proposals over stored ultrasonic evidence clips, in the ADR-045 ' +
    'refinement runner only. Never in the live pipeline.',
  installed: false,
}

function payload(assets: Array<Record<string, unknown>> = [BIRDNET, BATDETECT2]) {
  return {
    model_dir: '/opt/open-observatory/models',
    assets,
    note:
      'Model assets are not bundled with this software (ADR-006). Their licences differ ' +
      "from the code's and are listed per asset.",
  }
}

function answering(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  return vi.fn().mockResolvedValue({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  })
}

/** The rendered `<li>` whose text mentions `needle`. */
function entryFor(needle: string): HTMLElement {
  const found = screen
    .getAllByRole('listitem')
    .find((item) => (item.textContent ?? '').includes(needle))
  if (!found) throw new Error(`no model entry mentioning ${needle}`)
  return found
}

describe('assetKind', () => {
  it('takes the station at its word when it reports one', () => {
    expect(assetKind(BIRDNET as never)).toBe('file')
    expect(assetKind(BATDETECT2 as never)).toBe('package')
  })

  it('infers a checksummed entry is a file, for a station too old to say', () => {
    const { kind, ...older } = BIRDNET
    expect(kind).toBe('file') // the field this test is about removing
    expect(assetKind(older as never)).toBe('file')
  })

  it('does not guess when there is nothing to guess from', () => {
    expect(
      assetKind({ licence: 'MIT', source_url: 'https://example.invalid', installed: false, verified: false } as never),
    ).toBe('unknown')
  })
})

describe('assetName', () => {
  it('uses the filename for a file and the package name for a package', () => {
    expect(assetName(BIRDNET as never)).toBe('birdnet.tflite')
    expect(assetName(BATDETECT2 as never)).toBe('batdetect2')
  })
})

describe('acquisitionCommand', () => {
  it('is the command for a package and nothing for a file', () => {
    expect(acquisitionCommand(BATDETECT2 as never)).toBe('pip install batdetect2==1.3.1')
    expect(acquisitionCommand(BIRDNET as never)).toBeNull()
  })
})

describe('installationStatus', () => {
  it('never says "verified" of a package, which has no checksum to verify', () => {
    const status = installationStatus({ ...BATDETECT2, installed: true, verified: true } as never)
    expect(status.text).not.toMatch(/verified/i)
    expect(status.caveat).toMatch(/cannot verify/i)
  })

  it('calls a checksum mismatch what it is', () => {
    const status = installationStatus({ ...BIRDNET, verified: false } as never)
    expect(status.tone).toBe('danger')
    expect(status.caveat).toMatch(/licence/i)
  })
})

describe('ModelsPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('names each asset, its licence, its state and where it came from', async () => {
    vi.stubGlobal('fetch', answering(payload()))
    render(<ModelsPanel />)

    await waitFor(() => expect(screen.getByText('birdnet.tflite')).toBeInTheDocument())
    const birdnet = entryFor('birdnet.tflite')
    expect(birdnet.textContent).toContain('CC-BY-NC-SA-4.0')
    expect(birdnet.textContent).toMatch(/installed/i)
    expect(birdnet.textContent).toMatch(/verified/i)
    const link = birdnet.querySelector('a')
    expect(link).toHaveAttribute('href', BIRDNET.source_url)
  })

  it('shows a package its acquisition command instead of a checksum', async () => {
    vi.stubGlobal('fetch', answering(payload()))
    render(<ModelsPanel />)

    await waitFor(() => expect(screen.getByText('batdetect2')).toBeInTheDocument())
    const batdetect = entryFor('batdetect2')
    expect(batdetect.textContent).toContain('pip install batdetect2==1.3.1')
    expect(batdetect.textContent).toContain('1.3.1')
    // The station's own sentence on what it is for, beside the terms.
    expect(batdetect.textContent).toMatch(/refinement runner only/)
    expect(batdetect.textContent).not.toMatch(/sha256/i)
    // It is not installed, and must not read as though it were.
    expect(batdetect.textContent).toMatch(/not installed/i)
    expect(batdetect.textContent).toContain('CC-BY-NC-4.0')
  })

  it('does not call an installed package "verified" — nothing checksums it', async () => {
    vi.stubGlobal(
      'fetch',
      answering(payload([{ ...BATDETECT2, installed: true, verified: true }])),
    )
    render(<ModelsPanel />)

    await waitFor(() => expect(screen.getByText('batdetect2')).toBeInTheDocument())
    const batdetect = entryFor('batdetect2')
    expect(batdetect.textContent).not.toMatch(/checksum verified/i)
    expect(batdetect.textContent).toMatch(/cannot verify/i)
  })

  it('renders an older station that does not send the kind field', async () => {
    const { kind, ...older } = BIRDNET
    expect(kind).toBe('file')
    vi.stubGlobal('fetch', answering(payload([older])))
    render(<ModelsPanel />)

    await waitFor(() => expect(screen.getByText('birdnet.tflite')).toBeInTheDocument())
    const birdnet = entryFor('birdnet.tflite')
    expect(birdnet.textContent).toContain('CC-BY-NC-SA-4.0')
    expect(birdnet.textContent).toMatch(/sha256/i)
  })

  it('warns that the licence may not describe a file whose checksum is wrong', async () => {
    vi.stubGlobal('fetch', answering(payload([{ ...BIRDNET, verified: false }])))
    render(<ModelsPanel />)

    await waitFor(() => expect(screen.getByText('birdnet.tflite')).toBeInTheDocument())
    const birdnet = entryFor('birdnet.tflite')
    expect(birdnet.textContent).toMatch(/does not match/i)
    expect(birdnet.textContent).toMatch(/may not be the licence/i)
  })

  it('says so plainly when the endpoint is absent on an older station', async () => {
    vi.stubGlobal('fetch', answering({}, { ok: false, status: 404 }))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/not available/i)).toBeInTheDocument())
    expect(screen.getByText(/oo models status/)).toBeInTheDocument()
  })

  it('says so plainly when authentication blocks the endpoint', async () => {
    vi.stubGlobal('fetch', answering({}, { ok: false, status: 401 }))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/sign in/i)).toBeInTheDocument())
  })

  it('says so plainly when the request fails outright', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/could not read/i)).toBeInTheDocument())
  })

  it('does not render an empty box when the station lists no assets', async () => {
    vi.stubGlobal('fetch', answering(payload([])))
    render(<ModelsPanel />)
    await waitFor(() => expect(screen.getByText(/no model assets/i)).toBeInTheDocument())
  })
})

/** ADR-028: one depth toggle, and this panel belongs to the deeper one.
 *
 *  Read off disk rather than rendered, following `responsive.test.tsx`:
 *  mounting `App` here would need a WebSocket, a canvas and the auth probe
 *  stubbed to assert one line of JSX placement. What can silently regress is
 *  the placement itself, and that is text.
 */
describe('depth', () => {
  const APP = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8')
  const MAIN = APP.slice(APP.indexOf('<main'), APP.indexOf('</main>'))

  it('is rendered exactly once', () => {
    expect(APP.match(/<ModelsPanel/g) ?? []).toHaveLength(1)
  })

  it('sits inside the diagnose-only region of the panel columns', () => {
    const gate = MAIN.indexOf('{diagnosing && (')
    const panel = MAIN.indexOf('<ModelsPanel')
    expect(gate).toBeGreaterThan(-1)
    expect(panel).toBeGreaterThan(gate)
  })
})
