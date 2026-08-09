/** The counter-top display's firmware, from the browser (ADR-050).
 *
 *  The point of this panel is that the ESP32 on the kitchen counter never goes
 *  back on a USB cable. The station holds one image; a display fetches it over
 *  the WebSocket connection it already has.
 *
 *  Two honesty rules shape what it says:
 *
 *  - **"Offered" is not "installed."** The station knows only what it told a
 *    display. The display refuses anything not strictly newer, verifies a
 *    SHA-256 before committing, and waits until nobody is looking at it and
 *    nothing is happening in the garden. So the rollout button reports how many
 *    displays were *told*, and the version each display reports is the only
 *    evidence of what actually landed.
 *  - **"Unknown" is a real answer.** A display running a build older than
 *    ADR-050 does not report a version at all. That is shown as unknown, not as
 *    out of date — they are different claims and only one of them is true.
 *
 *  Like `RetentionPanel`, this degrades to "not available" rather than breaking
 *  the page if the endpoint is not there.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { apiFetch } from '../api'

interface PublishedFirmware {
  version: string
  sha256: string
  size_bytes: number
  published_utc: string
  notes: string
}

interface ConnectedDisplay {
  firmware_version: string | null
  up_to_date: boolean | null
  frames_sent: number
}

interface FirmwarePayload {
  published: PublishedFirmware | null
  image_path: string | null
  offer_on_connect: boolean
  app_slot_bytes: number
  displays: ConnectedDisplay[]
}

function bytes(value: number): string {
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(2)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(0)} kB`
  return `${value} B`
}

/** The same rule the station and the firmware both enforce: dot-separated
 *  numbers only. Checked here too so the operator is told before the upload
 *  rather than after it. */
function isPlausibleVersion(value: string): boolean {
  return /^\d{1,5}(\.\d{1,5}){0,3}$/.test(value)
}

export function FirmwarePanel() {
  const [payload, setPayload] = useState<FirmwarePayload | null>(null)
  const [available, setAvailable] = useState<boolean | null>(null)
  const [version, setVersion] = useState('')
  const [notes, setNotes] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    try {
      const response = await apiFetch('/api/v1/firmware')
      if (!response.ok) throw new Error(String(response.status))
      setPayload((await response.json()) as FirmwarePayload)
      setAvailable(true)
    } catch {
      setAvailable(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  if (available === null) {
    return (
      <section className="panel">
        <header className="panel-head">
          <h2>Display firmware</h2>
        </header>
        <p className="empty">checking…</p>
      </section>
    )
  }
  if (available === false || payload === null) {
    return (
      <section className="panel">
        <header className="panel-head">
          <h2>Display firmware</h2>
        </header>
        <p className="empty">
          Not available on this station — over-the-air display updates need a station
          running ADR-050 or later.
        </p>
      </section>
    )
  }

  const tooLarge = file !== null && file.size > payload.app_slot_bytes
  const versionOk = isPlausibleVersion(version)
  const canPublish = file !== null && versionOk && !tooLarge && !busy

  const publish = async () => {
    if (file === null) return
    setBusy(true)
    setMessage(null)
    try {
      const query = `version=${encodeURIComponent(version)}&notes=${encodeURIComponent(notes)}`
      const response = await apiFetch(`/api/v1/firmware?${query}`, {
        method: 'POST',
        headers: { 'content-type': 'application/octet-stream' },
        body: await file.arrayBuffer(),
      })
      const body = await response.json()
      if (!response.ok) {
        setMessage(body?.detail?.errors?.image ?? 'the station refused that image')
        return
      }
      setPayload(body as FirmwarePayload)
      setFile(null)
      if (fileInput.current) fileInput.current.value = ''
      setMessage(`published ${version} — offered to displays as they connect`)
    } catch {
      setMessage('upload failed — station unreachable')
    } finally {
      setBusy(false)
    }
  }

  const rollout = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const response = await apiFetch('/api/v1/firmware/rollout', { method: 'POST' })
      const body = await response.json()
      if (!response.ok) {
        setMessage('nothing is published to roll out')
        return
      }
      setPayload(body as FirmwarePayload)
      setMessage(
        body.offered === 0
          ? `no display needed it (${body.connected} connected)`
          : `offered to ${body.offered} of ${body.connected} connected — a display installs it once nobody is looking at it`,
      )
    } catch {
      setMessage('rollout failed — station unreachable')
    } finally {
      setBusy(false)
    }
  }

  const withdraw = async () => {
    setBusy(true)
    setMessage(null)
    try {
      const response = await apiFetch('/api/v1/firmware', { method: 'DELETE' })
      setPayload((await response.json()) as FirmwarePayload)
      setMessage('withdrawn — displays that already installed it are unaffected')
    } catch {
      setMessage('withdraw failed — station unreachable')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Display firmware</h2>
      </header>

      <p className="dim panel-caption">
        The counter-top display fetches this over the connection it already has, so it
        never needs a cable. Upload{' '}
        <code>firmware/inside-observer/.pio/build/cyd/firmware.bin</code> and give it the
        version from <code>platformio.ini</code>.
      </p>

      {payload.published ? (
        <dl className="kv compact">
          <div>
            <dt>published</dt>
            <dd className="mono">{payload.published.version}</dd>
          </div>
          <div>
            <dt>size</dt>
            <dd className="mono">
              {bytes(payload.published.size_bytes)} of {bytes(payload.app_slot_bytes)} slot
            </dd>
          </div>
          <div>
            <dt title="Verified on the device against every byte it received, before anything is committed to a boot slot">
              sha-256
            </dt>
            <dd className="mono">{payload.published.sha256.slice(0, 16)}…</dd>
          </div>
          <div>
            <dt>uploaded</dt>
            <dd className="mono">{payload.published.published_utc}</dd>
          </div>
          {payload.published.notes && (
            <div>
              <dt>notes</dt>
              <dd>{payload.published.notes}</dd>
            </div>
          )}
        </dl>
      ) : (
        <p className="empty">Nothing published. Displays keep running whatever they have.</p>
      )}

      <h3>connected displays</h3>
      {payload.displays.length === 0 ? (
        <p className="empty">No display is connected.</p>
      ) : (
        <ul className="firmware-displays">
          {payload.displays.map((display, index) => (
            <li key={index}>
              <span className="mono">{display.firmware_version ?? 'version not reported'}</span>{' '}
              {display.up_to_date === true && <span className="dim">up to date</span>}
              {display.up_to_date === false && <span className="dim">behind</span>}
              {display.up_to_date === null && (
                <span
                  className="dim"
                  title="This build predates over-the-air updates and does not report a version. Not the same as being out of date."
                >
                  unknown
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="firmware-upload">
        <label>
          image
          <input
            ref={fileInput}
            type="file"
            accept=".bin,application/octet-stream"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <label>
          version
          <input
            type="text"
            value={version}
            placeholder="0.2.1"
            onChange={(event) => setVersion(event.target.value)}
          />
        </label>
        <label>
          notes
          <input
            type="text"
            value={notes}
            placeholder="what changed"
            onChange={(event) => setNotes(event.target.value)}
          />
        </label>
      </div>

      {version !== '' && !versionOk && (
        <p className="settings-warning">
          Numbers and dots only, e.g. 0.2.1. The display refuses to guess where a suffix
          like <code>-rc1</code> sorts, so a version it cannot order would never install.
        </p>
      )}
      {tooLarge && (
        <p className="settings-warning">
          {bytes(file!.size)} will not fit the {bytes(payload.app_slot_bytes)} app slot.
        </p>
      )}

      <div className="settings-actions">
        <button onClick={publish} disabled={!canPublish}>
          {busy ? 'working…' : 'publish'}
        </button>
        <button onClick={rollout} disabled={busy || payload.published === null}>
          roll out now
        </button>
        <button onClick={withdraw} disabled={busy || payload.published === null}>
          withdraw
        </button>
        {message && <span className="settings-message">{message}</span>}
      </div>

      <p className="dim panel-caption">
        Rolling out tells every connected display that is behind. It does not install
        anything: the display verifies the checksum, waits until nobody is touching it and
        nothing has been detected in the last minute, and puts the previous build back by
        itself if the new one cannot reach this station.
      </p>
    </section>
  )
}
