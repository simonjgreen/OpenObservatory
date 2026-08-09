/** Every setting this station has, editable on the device.
 *
 *  The principle this page exists for: the repository describes a *system*;
 *  a deployment describes a *site*. Anything true of exactly one installation
 *  is runtime state managed here, persisted server-side to the gitignored
 *  `config/runtime.env`, and never committed to version control (ADR-047).
 *
 *  ADR-048 widened that from site identity to the whole of `Settings`. The
 *  goal is a new operator getting from a freshly imaged Pi to a tuned station
 *  without opening a terminal: capture, spectrogram contrast, every detector
 *  threshold, clips, retention and the overnight refiner are all here. The
 *  handful of settings that are deliberately *not* here are listed at the
 *  bottom of the page with the reason, because an operator hunting for a knob
 *  deserves to be told where it went rather than left to conclude the page is
 *  incomplete.
 *
 *  Honesty rules the layout:
 *  - The page renders what the server says: categories, labels, units,
 *    bounds, defaults and tier all come from `GET /api/v1/settings`. There is
 *    no second copy of the catalogue here to drift out of date.
 *  - A field the station has saved but is not yet using is marked "saved, not
 *    yet in force" — for live-tier fields too, because a live setting whose
 *    target object is not running is exactly as not-in-force as a pinned one.
 *  - Every field carries its shipped default as a one-click way back to a
 *    known state. Measured defaults that an operator cannot return to are not
 *    much of a reference point.
 *  - Dangerous-but-legitimate settings are editable behind an explicit
 *    acknowledgement, not hidden. Hiding them does not make them safe; it
 *    makes them an SSH session.
 *  - Secrets are write-only: the server reports `is_set`, never the value.
 */

import { useEffect, useMemo, useState } from 'react'

import { apiFetch } from '../api'
import {
  type SettingsCategory,
  type SettingsField,
  type SettingsPayload,
  type Draft,
  SettingField,
  changedBody,
  draftFrom,
  fieldLabel,
  unacknowledgedDangers,
} from './settingsForm'

interface Props {
  onClose: () => void
  /** Called after a successful save so the app can refresh station status. */
  onSaved?: () => void
  /** Open with this category expanded (the first-run flow deep-links here). */
  initialCategory?: string
}

/** Categories the server did not describe still have to render somewhere,
 *  rather than silently swallowing their fields. */
function categoriesFor(payload: SettingsPayload): SettingsCategory[] {
  const described = payload.categories ?? []
  const known = new Set(described.map((category) => category.id))
  const extra: SettingsCategory[] = []
  for (const field of payload.fields) {
    if (!known.has(field.category)) {
      known.add(field.category)
      extra.push({ id: field.category, title: field.category, description: '' })
    }
  }
  return [...described, ...extra]
}

export function SettingsPanel({ onClose, onSaved, initialCategory }: Props) {
  const [payload, setPayload] = useState<SettingsPayload | null>(null)
  const [draft, setDraft] = useState<Draft>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [state, setState] = useState<'loading' | 'ready' | 'saving' | 'unavailable'>('loading')
  const [message, setMessage] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set())
  const [open, setOpen] = useState<string | null>(initialCategory ?? 'station')

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

  const searching = query.trim().length > 0
  const matches = useMemo(() => {
    if (!payload || !searching) return null
    const needle = query.trim().toLowerCase()
    return new Set(
      payload.fields
        .filter(
          (field) =>
            field.name.toLowerCase().includes(needle) ||
            fieldLabel(field).toLowerCase().includes(needle) ||
            (field.help ?? '').toLowerCase().includes(needle),
        )
        .map((field) => field.name),
    )
  }, [payload, query, searching])

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

  const visible = (field: SettingsField) => (matches ? matches.has(field.name) : true)
  const categories = categoriesFor(payload).filter((category) => !category.hidden)

  const body = changedBody(payload, draft)
  const blocking = unacknowledgedDangers(payload, body, acknowledged)

  const save = async () => {
    if (blocking.length > 0) {
      setMessage(
        `not saved — acknowledge the warning on: ${blocking.map(fieldLabel).join(', ')}`,
      )
      return
    }
    setState('saving')
    setErrors({})
    setMessage(null)
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
      setAcknowledged(new Set())
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

  const renderField = (field: SettingsField) => (
    <SettingField
      key={field.name}
      field={field}
      value={draft[field.name]}
      error={errors[field.name]}
      acknowledged={acknowledged.has(field.name)}
      onChange={(value) => setDraft((d) => ({ ...d, [field.name]: value }))}
      onAcknowledge={(on) =>
        setAcknowledged((current) => {
          const next = new Set(current)
          if (on) next.add(field.name)
          else next.delete(field.name)
          return next
        })
      }
    />
  )

  const changedCount = Object.keys(body).length

  return (
    <section className="panel settings-panel">
      <h2>settings</h2>
      <p className="settings-note">
        Everything this station can be configured with, stored on the device in
        `config/runtime.env` and never in the repository. Values saved here survive
        restarts and upgrades, and a hand-edited file and this page are one
        configuration, not two.
      </p>

      {!payload.location_configured && (
        <p className="settings-warning">
          No location set. BirdNET runs without range-based plausibility filtering and
          the ultrasonic night schedule stays always-on until coordinates are configured.
        </p>
      )}
      {payload.pending_restart.length > 0 && (
        <p className="settings-warning">
          Saved but not yet in force — restart the station to apply:{' '}
          {payload.pending_restart.join(', ')}
        </p>
      )}

      <label className="settings-search">
        find a setting
        <input
          type="search"
          value={query}
          placeholder="snr, floor, retention…"
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>

      {categories.map((category) => {
        const fields = payload.fields.filter(
          (field) => field.category === category.id && visible(field),
        )
        if (fields.length === 0) return null
        const expanded = searching || open === category.id
        return (
          <div className="settings-category" key={category.id}>
            <button
              type="button"
              className="settings-category-header"
              aria-expanded={expanded}
              onClick={() => setOpen(expanded && !searching ? null : category.id)}
            >
              <h3>{category.title}</h3>
              <span className="dim">{fields.length}</span>
            </button>
            {expanded && (
              <div className="settings-category-body">
                {category.description && (
                  <p className="settings-help">{category.description}</p>
                )}
                {fields.map(renderField)}
              </div>
            )}
          </div>
        )
      })}

      {searching && (matches?.size ?? 0) === 0 && (
        <p className="settings-help">Nothing matches “{query}”.</p>
      )}

      {(payload.non_editable?.length ?? 0) > 0 && (
        <details className="settings-category settings-non-editable">
          <summary>
            not editable from a browser ({payload.non_editable!.length}) — and why
          </summary>
          <ul>
            {payload.non_editable!.map((entry) => (
              <li key={entry.name}>
                <code>{entry.name}</code> — {entry.reason}
              </li>
            ))}
          </ul>
          <p className="settings-help">
            These are set by editing <code>config/runtime.env</code> on the station and
            restarting. Each one is excluded for a hazard that cannot be undone from the
            browser, not for tidiness.
          </p>
        </details>
      )}

      <div className="settings-actions">
        <button onClick={save} disabled={state === 'saving' || changedCount === 0}>
          {state === 'saving' ? 'saving…' : changedCount === 0 ? 'save' : `save ${changedCount}`}
        </button>
        <button onClick={onClose}>close</button>
        {message && <span className="settings-message">{message}</span>}
      </div>
    </section>
  )
}
