/** Which analytics question is open, synced to the URL so a chart can be
 *  bookmarked, refreshed, or sent to somebody -- the bookmark *is* the URL.
 *  `?section=analytics` opens the section; `&q=<id>` names a shipped question
 *  or `&r=<id>` a saved one; otherwise `chart`, `range`, `grain`, `columns`,
 *  `group` and `label` describe the view directly. Like `useViewMode`, this
 *  is one hook and `replaceState`, deliberately not a router. `?view=` is
 *  left alone: that key already belongs to the diagnostics depth. */

import { useCallback, useEffect, useState } from 'react'
import type { AnalyticsParams, AnalyticsView, ColumnsGrain, SeriesGrain, TaxaGrain } from '../analytics/types'

export interface AnalyticsRoute {
  active: boolean
  /** A shipped question's id, when one was opened and not since edited. */
  questionId: string | null
  /** A saved report's id, likewise. */
  reportId: string | null
  view: AnalyticsView
  params: AnalyticsParams
}

export const DEFAULT_ROUTE: AnalyticsRoute = {
  active: false,
  questionId: null,
  reportId: null,
  view: 'series',
  params: { range: 'last-90d', grain: 'day' },
}

const VIEWS: AnalyticsView[] = ['series', 'hours', 'taxa', 'span']

export function readRoute(search: string): AnalyticsRoute {
  const params = new URLSearchParams(search)
  if (params.get('section') !== 'analytics') return DEFAULT_ROUTE
  const chart = params.get('chart')
  const view: AnalyticsView = VIEWS.includes(chart as AnalyticsView) ? (chart as AnalyticsView) : 'series'
  const read: AnalyticsParams = { range: params.get('range') ?? DEFAULT_ROUTE.params.range }
  const grain = params.get('grain')
  if (grain) read.grain = grain as SeriesGrain | TaxaGrain
  const columns = params.get('columns')
  if (columns) read.columns = columns as ColumnsGrain
  const group = params.get('group')
  if (group) read.group = group
  const label = params.get('label')
  if (label) read.label = label
  return {
    active: true,
    questionId: params.get('q'),
    reportId: params.get('r'),
    view,
    params: read,
  }
}

export function writeRoute(search: string, route: AnalyticsRoute): string {
  const params = new URLSearchParams(search)
  for (const key of ['section', 'q', 'r', 'chart', 'range', 'grain', 'columns', 'group', 'label']) {
    params.delete(key)
  }
  if (route.active) {
    params.set('section', 'analytics')
    if (route.questionId) params.set('q', route.questionId)
    if (route.reportId) params.set('r', route.reportId)
    params.set('chart', route.view)
    params.set('range', route.params.range)
    if (route.params.grain) params.set('grain', route.params.grain)
    if (route.params.columns) params.set('columns', route.params.columns)
    if (route.params.group) params.set('group', route.params.group)
    if (route.params.label) params.set('label', route.params.label)
  }
  return params.toString()
}

export interface AnalyticsRouteState {
  route: AnalyticsRoute
  open: () => void
  close: () => void
  /** Show a question or saved report exactly as it was defined. */
  show: (view: AnalyticsView, params: AnalyticsParams, ids?: { questionId?: string; reportId?: string }) => void
  /** Change one thing about the current view; the question is then "edited"
   *  and no longer claims a shipped id. */
  adjust: (patch: Partial<AnalyticsParams>, view?: AnalyticsView) => void
}

export function useAnalyticsRoute(
  location: Pick<Location, 'search'> = window.location,
): AnalyticsRouteState {
  const [route, setRoute] = useState<AnalyticsRoute>(() => readRoute(location.search))

  useEffect(() => {
    const query = writeRoute(window.location.search, route)
    const url = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`
    window.history.replaceState(window.history.state, '', url)
  }, [route])

  const open = useCallback(() => setRoute((current) => ({ ...current, active: true })), [])
  const close = useCallback(() => setRoute((current) => ({ ...current, active: false })), [])
  const show = useCallback(
    (view: AnalyticsView, params: AnalyticsParams, ids?: { questionId?: string; reportId?: string }) =>
      setRoute({
        active: true,
        questionId: ids?.questionId ?? null,
        reportId: ids?.reportId ?? null,
        view,
        params: { ...params },
      }),
    [],
  )
  const adjust = useCallback(
    (patch: Partial<AnalyticsParams>, view?: AnalyticsView) =>
      setRoute((current) => ({
        active: true,
        questionId: null,
        reportId: null,
        view: view ?? current.view,
        params: { ...current.params, ...patch },
      })),
    [],
  )

  return { route, open, close, show, adjust }
}
