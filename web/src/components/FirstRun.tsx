/** The guided first run: a station with no configuration guides rather than
 *  fails (ADR-048).
 *
 *  Four questions, in the order a person actually has them on day one — where
 *  am I, what should this station be called and what time is it here, is my
 *  microphone working, do I want Home Assistant. Each step edits real settings
 *  through the same `PUT /api/v1/settings` the settings page uses, with the
 *  same field renderer, so there is no second path that could save something
 *  subtly different.
 *
 *  What this is not: the commissioning wizard of Milestone 7. It does not
 *  probe hardware, calibrate anything, or claim the station is "ready". It
 *  reports what the station knows, including when the answer is unwelcome —
 *  the microphone step reads live capture state, so a station running on the
 *  synthetic fallback says so instead of ticking a box. A first-run flow that
 *  said "all set" over a fallback source would be the most expensive lie this
 *  surface could tell.
 *
 *  It is dismissible, and the dismissal is stored on the station
 *  (`setup_completed`), not in one browser's localStorage: whether a station
 *  has been commissioned is a fact about the station, and the second device to
 *  open it should not be told to configure something already configured.
 */

import { useEffect, useState } from 'react'

import { apiFetch } from '../api'
import {
  type SettingsPayload,
  type Draft,
  SettingField,
  changedBody,
  draftFrom,
} from './settingsForm'

export interface SetupStep {
  id: string
  title: string
  detail: string
  done: boolean
  optional: boolean
  fields: string[]
}

export interface SetupPayload {
  completed: boolean
  required_outstanding: string[]
  steps: SetupStep[]
}

interface Props {
  onClose: () => void
  /** Called after any successful save, so the app can refresh station status. */
  onSaved?: () => void
  /** Escape hatch to the full settings page. */
  onOpenSettings?: () => void
}

export function FirstRun({ onClose, onSaved, onOpenSettings }: Props) {
  const [setup, setSetup] = useState<SetupPayload | null>(null)
  const [settings, setSettings] = useState<SettingsPayload | null>(null)
  const [draft, setDraft] = useState<Draft>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [index, setIndex] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  const reload = async () => {
    const [setupResponse, settingsResponse] = await Promise.all([
      apiFetch('/api/v1/setup'),
      apiFetch('/api/v1/settings'),
    ])
    if (!setupResponse.ok || !settingsResponse.ok) throw new Error('unavailable')
    const setupBody = (await setupResponse.json()) as SetupPayload
    const settingsBody = (await settingsResponse.json()) as SettingsPayload
    setSetup(setupBody)
    setSettings(settingsBody)
    setDraft(draftFrom(settingsBody))
    return setupBody
  }

  useEffect(() => {
    let cancelled = false
    reload().catch(() => {
      if (!cancelled) setFailed(true)
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (failed) {
    return (
      <section className="panel settings-panel first-run">
        <h2>welcome</h2>
        <p>The station is not answering yet. Try again in a moment.</p>
        <button onClick={onClose}>dismiss</button>
      </section>
    )
  }
  if (setup === null || settings === null) {
    return (
      <section className="panel settings-panel first-run">
        <h2>welcome</h2>
        <p>loading…</p>
      </section>
    )
  }

  const step = setup.steps[index]
  const stepFields = settings.fields.filter((field) => step.fields.includes(field.name))

  const saveStep = async () => {
    setBusy(true)
    setErrors({})
    setMessage(null)
    const body = changedBody(settings, draft, step.fields)
    if (Object.keys(body).length === 0) {
      setBusy(false)
      advance()
      return
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
        setBusy(false)
        return
      }
      await reload()
      onSaved?.()
      setBusy(false)
      advance()
    } catch {
      setMessage('save failed — station unreachable')
      setBusy(false)
    }
  }

  const advance = () => {
    if (index + 1 < setup.steps.length) setIndex(index + 1)
    else void finish()
  }

  const finish = async () => {
    setBusy(true)
    try {
      await apiFetch('/api/v1/settings', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ setup_completed: true }),
      })
      onSaved?.()
    } catch {
      // Dismissal is a convenience; a station that cannot record it is still
      // usable, and nagging is a smaller failure than blocking the UI.
    }
    setBusy(false)
    onClose()
  }

  return (
    <section className="panel settings-panel first-run">
      <h2>welcome — let’s set this station up</h2>
      <p className="settings-note">
        Four questions. Nothing here is required to record: the station is already
        capturing and detecting. Answering them makes what it reports more accurate,
        and everything is changeable later under settings.
      </p>

      <ol className="first-run-steps">
        {setup.steps.map((entry, position) => (
          <li
            key={entry.id}
            className={`${position === index ? 'current' : ''} ${entry.done ? 'done' : ''}`}
          >
            <button type="button" className="linklike" onClick={() => setIndex(position)}>
              {entry.done ? '✓' : '•'} {entry.title}
              {entry.optional && <span className="dim"> (optional)</span>}
            </button>
          </li>
        ))}
      </ol>

      <div className="first-run-step">
        <h3>{step.title}</h3>
        <p className="settings-help">{step.detail}</p>
        {stepFields.map((field) => (
          <SettingField
            key={field.name}
            field={field}
            value={draft[field.name]}
            error={errors[field.name]}
            acknowledged
            onChange={(value) => setDraft((d) => ({ ...d, [field.name]: value }))}
            onAcknowledge={() => {}}
          />
        ))}
      </div>

      <div className="settings-actions">
        <button onClick={saveStep} disabled={busy}>
          {index + 1 < setup.steps.length ? 'save and continue' : 'save and finish'}
        </button>
        <button onClick={advance} disabled={busy}>
          skip
        </button>
        <button onClick={finish} disabled={busy}>
          don’t show this again
        </button>
        {onOpenSettings && (
          <button
            className="linklike"
            onClick={() => {
              onOpenSettings()
              onClose()
            }}
          >
            open full settings
          </button>
        )}
        {message && <span className="settings-message">{message}</span>}
      </div>
    </section>
  )
}
