/** The operator privacy pause (ADR-055): state, menu and the two actions.
 *
 *  Two sources of truth, deliberately, and they are not equal. `GET
 *  /api/v1/pause` is fetched once on mount and after every action, because it
 *  carries the *menu* as well as the state. The live status frame (every 2 s)
 *  then keeps the state current without polling — which is what makes a pause
 *  set from a phone in the garden show up on the laptop in the kitchen within
 *  a tick, rather than whenever someone reloads.
 *
 *  The countdown is computed from `ends_utc` against the caller's clock, never
 *  from `remaining_s`. A page left open for an hour has a `remaining_s` from an
 *  hour ago and a still-correct deadline, and a privacy control that shows a
 *  confidently wrong "12 minutes left" is worse than one that shows nothing.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { apiFetch } from '../api'
import type { PausePayload, PausePreset, PauseState } from '../types'

/** Shipped fallback for the menu, used only while the first fetch is in
 *  flight or if it failed. The station is still the authority — pressing any
 *  of these posts a key it validates — but the control must never render
 *  empty, because an empty privacy control reads as "you cannot do this". */
const FALLBACK_PRESETS: PausePreset[] = [
  { key: '15m', label: '15 minutes', seconds: 900 },
  { key: '1h', label: '1 hour', seconds: 3600 },
  { key: '3h', label: '3 hours', seconds: 10800 },
  { key: '6h', label: '6 hours', seconds: 21600 },
  { key: 'until-midnight', label: 'until midnight', seconds: null },
]

const LAST_CHOICE_KEY = 'oo.pause.preset'

function readLastChoice(): string | null {
  try {
    return window.localStorage.getItem(LAST_CHOICE_KEY)
  } catch {
    // Private windows and disabled storage both throw here. Remembering the
    // last choice is a convenience; losing it must not lose the control.
    return null
  }
}

function writeLastChoice(key: string): void {
  try {
    window.localStorage.setItem(LAST_CHOICE_KEY, key)
  } catch {
    /* see readLastChoice */
  }
}

export interface PauseControlState {
  active: boolean
  /** Milliseconds until the pause ends, or 0. Ticks. */
  remainingMs: number
  endsUtc: string | null
  presets: PausePreset[]
  /** The preset the main button will use if pressed. */
  selected: string
  select: (key: string) => void
  busy: boolean
  error: string | null
  pause: () => void
  resume: () => void
}

export function usePause(livePause: PauseState | null | undefined, now: Date): PauseControlState {
  const [payload, setPayload] = useState<PausePayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [chosen, setChosen] = useState<string | null>(() => readLastChoice())
  //: When this browser last changed the pause itself. The live status frame is
  //: only every 2 s, so for a moment after pressing the button it still says
  //: the old thing -- and letting it win would make the control visibly flip
  //: back to "pause" a heartbeat after the operator paused. For that window the
  //: response to our own request is the newer fact.
  const [mutatedAt, setMutatedAt] = useState(0)

  const refresh = useCallback(() => {
    apiFetch('/api/v1/pause')
      .then(async (response) => {
        if (!response.ok) return
        setPayload((await response.json()) as PausePayload)
      })
      .catch(() => {
        /* the control falls back to its shipped menu rather than vanishing */
      })
  }, [])

  useEffect(refresh, [refresh])

  const presets = payload?.presets?.length ? payload.presets : FALLBACK_PRESETS

  // Precedence: what this browser last chose, then the station's configured
  // default, then the first thing on the menu. Anything the current menu no
  // longer offers is discarded rather than shown as a selected option the
  // drop-down does not contain.
  const selected = useMemo(() => {
    const keys = presets.map((preset) => preset.key)
    for (const candidate of [chosen, payload?.default_preset]) {
      if (candidate && keys.includes(candidate)) return candidate
    }
    return keys[0] ?? '1h'
  }, [chosen, payload?.default_preset, presets])

  const select = useCallback((key: string) => {
    setChosen(key)
    writeLastChoice(key)
  }, [])

  // The live frame normally wins for `active`/`ends_utc`: it is at most two
  // seconds old and it reflects a pause set from any device. The exception is
  // the couple of seconds after this browser itself changed things, when our
  // own response is newer than the last frame -- see `mutatedAt`.
  const ownAnswerIsNewer = mutatedAt > 0 && now.getTime() - mutatedAt < 3000
  const state: PauseState | null =
    (ownAnswerIsNewer ? payload : livePause ?? payload) ?? null
  const active = Boolean(state?.active)
  const endsUtc = active ? state?.ends_utc ?? null : null
  const remainingMs = endsUtc ? Math.max(0, Date.parse(endsUtc) - now.getTime()) : 0

  const act = useCallback(
    (method: 'POST' | 'DELETE', body?: unknown) => {
      setBusy(true)
      setError(null)
      apiFetch('/api/v1/pause', {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      })
        .then(async (response) => {
          if (!response.ok) {
            const detail = await response.json().catch(() => null)
            setError(
              typeof detail?.detail === 'string' ? detail.detail : `HTTP ${response.status}`,
            )
            return
          }
          setPayload((await response.json()) as PausePayload)
          setMutatedAt(Date.now())
        })
        .catch((cause) => setError(String(cause)))
        .finally(() => setBusy(false))
    },
    [],
  )

  const pause = useCallback(() => act('POST', { preset: selected }), [act, selected])
  const resume = useCallback(() => act('DELETE'), [act])

  return {
    active,
    remainingMs,
    endsUtc,
    presets,
    selected,
    select,
    busy,
    error,
    pause,
    resume,
  }
}

/** "1h 04m", "12m", "48s" — the countdown on the button.
 *
 *  Seconds only appear under a minute. A control that ticks every second all
 *  afternoon is an animation, not information, and this one sits in a header
 *  the operator is meant to be able to ignore. */
export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes) return `${minutes}m`
  return `${seconds}s`
}
