/** Whether this station still has first-run questions outstanding.
 *
 *  Asks the station, not the browser. `setup_completed` is a setting like any
 *  other, so "I have been through this" survives a different laptop, a private
 *  window and a cleared cache — and, equally, a station that genuinely has not
 *  been configured keeps offering to help rather than being silenced by
 *  whoever happened to open it first (ADR-048).
 *
 *  Deliberately quiet on failure: an unreachable `/api/v1/setup` means no
 *  first-run panel, never a blocked or half-rendered app. The flow is
 *  assistance, and assistance that can break the page is not assistance.
 */

import { useCallback, useEffect, useState } from 'react'

import { apiFetch } from '../api'
import type { SetupPayload } from '../components/FirstRun'

export interface FirstRunState {
  /** True while the station has unanswered required questions and the
   *  operator has not dismissed the flow. */
  offer: boolean
  setup: SetupPayload | null
  dismissed: boolean
  dismiss: () => void
  refresh: () => void
}

export function useFirstRun(): FirstRunState {
  const [setup, setSetup] = useState<SetupPayload | null>(null)
  const [dismissed, setDismissed] = useState(false)

  const refresh = useCallback(() => {
    apiFetch('/api/v1/setup')
      .then(async (response) => {
        if (!response.ok) return
        setSetup((await response.json()) as SetupPayload)
      })
      .catch(() => {
        /* assistance never breaks the page */
      })
  }, [])

  useEffect(refresh, [refresh])

  return {
    offer: Boolean(setup && !setup.completed && setup.required_outstanding.length > 0 && !dismissed),
    setup,
    dismissed,
    dismiss: () => setDismissed(true),
    refresh,
  }
}
