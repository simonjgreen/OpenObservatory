/** Site settings, editable on the device.
 *
 *  The principle this page exists for: the repository describes a *system*;
 *  a deployment describes a *site*. Anything true of exactly one installation
 *  — where the station is, what it is called, which MQTT broker it talks to —
 *  is runtime state managed here, persisted server-side to the gitignored
 *  `config/runtime.env`, and never committed to version control.
 *
 *  Honesty rules the layout:
 *  - Coordinates are saved immediately but bind into the BirdNET range filter
 *    and the night schedule only at detector start; the server names them in
 *    `pending_restart` and this panel repeats that verbatim rather than
 *    letting "saved" read as "in force".
 *  - The MQTT password is write-only: the server reports `is_set`, never the
 *    value, and an empty input here means "leave unchanged", with an explicit
 *    clear affordance instead of empty-string ambiguity.
 */

import { useEffect, useState } from 'react'

import { apiFetch } from '../api'

interface SettingsField {
  name: string
  category: string
  secret: boolean
  restart_required: boolean
  note: string | null
  value?: string | number | boolean | null
  is_set?: boolean
  pending_restart?: boolean
}

interface SettingsPayload {
  fields: SettingsField[]
  pending_restart: string[]
  location_configured: boolean
  saved?: string[]
}

interface Props {
  onClose: () => void
  /** Called after a successful save so the app can refresh station status. */
  onSaved?: () => void
}

type Draft = Record<string, string | boolean>

function draftFrom(payload: SettingsPayload): Draft {
  const draft: Draft = {}
  for (const field of payload.fields) {
    if (field.secret) {
      draft[field.name] = '' // write-only; empty means "unchanged"
    } else if (typeof field.value === 'boolean') {
      draft[field.name] = field.value
    } else {
      draft[field.name] = field.value == null ? '' : String(field.value)
    }
  }
  return draft
}

const LABELS: Record<string, string> = {
  station_name: 'station name',
  timezone: 'timezone (IANA, e.g. Europe/London)',
  latitude: 'latitude (decimal degrees)',
  longitude: 'longitude (decimal degrees)',
  mqtt_enabled: 'publish to MQTT',
  mqtt_host: 'broker host',
  mqtt_port: 'broker port',
  mqtt_tls: 'TLS',
  mqtt_tls_insecure: 'skip TLS certificate verification',
  mqtt_username: 'username',
  mqtt_password: 'password',
  mqtt_client_id: 'client id',
  mqtt_topic_prefix: 'topic prefix',
  mqtt_discovery_enabled: 'Home Assistant discovery',
  mqtt_discovery_prefix: 'discovery prefix',
}

export function SettingsPanel({ onClose, onSaved }: Props) {
  const [payload, setPayload] = useState<SettingsPayload | null>(null)
  const [draft, setDraft] = useState<Draft>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [state, setState] = useState<'loading' | 'ready' | 'saving' | 'unavailable'>('loading')
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/v1/settings')
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const body = (await response.json()) as SettingsPayload
        if (cancelled) return
        setPayload(body)
        setDraft(draftFrom(body))
        setState('ready')
      })
      .catch(() => {
        if (!cancelled) setState('unavailable')
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (state === 'loading') {
    return (
      <section className="panel settings-panel">
        <h2>settings</h2>
        <p>loading…</p>
      </section>
    )
  }
  if (state === 'unavailable' || payload === null) {
    return (
      <section className="panel settings-panel">
        <h2>settings</h2>
        <p>The settings endpoint is not reachable.</p>
        <button onClick={onClose}>close</button>
      </section>
    )
  }

  const byCategory = (category: string) =>
    payload.fields.filter((field) => field.category === category)

  const save = async () => {
    setState('saving')
    setErrors({})
    setMessage(null)
    // Send only what differs from the loaded payload; secrets only when typed.
    const body: Record<string, unknown> = {}
    for (const field of payload.fields) {
      const value = draft[field.name]
      if (field.secret) {
        if (typeof value === 'string' && value !== '') body[field.name] = value
        continue
      }
      const original =
        typeof field.value === 'boolean'
          ? field.value
          : field.value == null
            ? ''
            : String(field.value)
      if (value !== original) body[field.name] = value
    }
    try {
      const response = await apiFetch('/api/v1/settings', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
      const result = await response.json()
      if (!response.ok) {
        setErrors((result?.detail?.errors ?? {}) as Record<string, string>)
        setMessage('not saved — fix the fields marked below')
        setState('ready')
        return
      }
      const saved = result as SettingsPayload
      setPayload(saved)
      setDraft(draftFrom(saved))
      const savedNames = saved.saved ?? []
      const pending = saved.pending_restart ?? []
      setMessage(
        savedNames.length === 0
          ? 'nothing changed'
          : pending.length > 0
            ? `saved. In force after the next restart: ${pending.join(', ')}`
            : 'saved and applied',
      )
      setState('ready')
      onSaved?.()
    } catch {
      setMessage('save failed — station unreachable')
      setState('ready')
    }
  }

  const input = (field: SettingsField) => {
    const value = draft[field.name]
    if (typeof value === 'boolean') {
      return (
        <label className="checkbox" key={field.name}>
          <input
            type="checkbox"
            checked={value}
            onChange={(event) =>
              setDraft((d) => ({ ...d, [field.name]: event.target.checked }))
            }
          />
          {LABELS[field.name] ?? field.name}
        </label>
      )
    }
    return (
      <label className="settings-field" key={field.name}>
        <span>
          {LABELS[field.name] ?? field.name}
          {field.restart_required && <em className="restart-tag"> restart to apply</em>}
          {field.pending_restart && <em className="restart-tag pending"> saved, awaiting restart</em>}
        </span>
        <input
          type={field.secret ? 'password' : 'text'}
          value={typeof value === 'string' ? value : ''}
          placeholder={field.secret ? (field.is_set ? '(set — leave blank to keep)' : '(not set)') : ''}
          onChange={(event) => setDraft((d) => ({ ...d, [field.name]: event.target.value }))}
        />
        {errors[field.name] && <em className="field-error">{errors[field.name]}</em>}
      </label>
    )
  }

  return (
    <section className="panel settings-panel">
      <h2>settings</h2>
      <p className="settings-note">
        Site configuration lives on this device (`config/runtime.env`), never in the
        repository. Values saved here survive restarts and upgrades.
      </p>

      {!payload.location_configured && (
        <p className="settings-warning">
          No location set. BirdNET runs without range-based plausibility filtering and
          the ultrasonic night schedule stays always-on until coordinates are configured.
        </p>
      )}

      <h3>station</h3>
      {byCategory('station').map(input)}

      <h3>MQTT / Home Assistant</h3>
      {byCategory('mqtt').map(input)}

      <div className="settings-actions">
        <button onClick={save} disabled={state === 'saving'}>
          {state === 'saving' ? 'saving…' : 'save'}
        </button>
        <button onClick={onClose}>close</button>
        {message && <span className="settings-message">{message}</span>}
      </div>
    </section>
  )
}
