/** One fetch per (view, params): the roll-up endpoints under
 *  `/api/v1/analytics/`. The canonical three-state shape `History` uses,
 *  through `apiFetch` so a 401 reaches `useAuth`. A non-OK response is an
 *  error the page shows, never an empty chart pretending to be a quiet year. */

import { useEffect, useState } from 'react'
import { apiFetch } from '../api'
import type { AnalyticsParams, AnalyticsView } from '../analytics/types'

export interface AnalyticsData<T> {
  payload: T | null
  loading: boolean
  error: string | null
}

export function endpointFor(view: AnalyticsView, params: AnalyticsParams): string {
  const query = new URLSearchParams({ range: params.range })
  if (view === 'series' && params.grain) query.set('grain', params.grain)
  if (view === 'taxa') query.set('grain', params.grain && params.grain !== 'night' ? params.grain : 'week')
  if (view === 'hours' && params.columns) query.set('columns', params.columns)
  if (params.group) query.set('group', params.group)
  if (params.label && view !== 'taxa' && view !== 'span') query.set('label', params.label)
  return `/api/v1/analytics/${view}?${query}`
}

export function useAnalyticsData<T>(view: AnalyticsView, params: AnalyticsParams): AnalyticsData<T> {
  const url = endpointFor(view, params)
  const [state, setState] = useState<AnalyticsData<T>>({ payload: null, loading: true, error: null })

  useEffect(() => {
    let cancelled = false
    setState((current) => ({ ...current, loading: true, error: null }))
    apiFetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error(`analytics ${response.status}`)
        return (await response.json()) as T
      })
      .then((payload) => {
        if (!cancelled) setState({ payload, loading: false, error: null })
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({ payload: null, loading: false, error: error instanceof Error ? error.message : String(error) })
        }
      })
    return () => {
      cancelled = true
    }
  }, [url])

  return state
}
