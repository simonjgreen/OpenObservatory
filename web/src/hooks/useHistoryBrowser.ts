/** LIVE-vs-HISTORY mode plus the detection fetch that backs the HISTORY
 *  suggestion list. The `History` component owns its own richer payload
 *  (timeline/coverage/species); this hook owns only the flat detection list
 *  shown in the shared `Suggestions` panel and the mode/window/focus state
 *  that both `History` and `Suggestions` read from `App`. */

import { useEffect, useState } from 'react'
import type { Detection } from '../types'
import type { HistoryRange } from '../components/History'

export type ViewMode = 'live' | 'history'

export interface HistoryBrowser {
  mode: ViewMode
  setMode: (mode: ViewMode) => void
  historyWindow: string
  historyRange: HistoryRange | null
  focus: { fromUtc: string; toUtc: string } | null
  historyDetections: Detection[]
  historyTruncated: boolean
  historyLoading: boolean
  /** Mirrors the suggestion list's own default; also drives the history
   *  aggregation query. */
  hideUnidentifiedHistory: boolean
  onWindowChange: (name: string, range: HistoryRange | null) => void
  onFocus: (fromUtc: string, toUtc: string) => void
}

export function useHistoryBrowser(): HistoryBrowser {
  const [mode, setMode] = useState<ViewMode>('live')
  const [historyWindow, setHistoryWindow] = useState('last-night')
  const [historyRange, setHistoryRange] = useState<HistoryRange | null>(null)
  const [focus, setFocus] = useState<{ fromUtc: string; toUtc: string } | null>(null)
  const [historyDetections, setHistoryDetections] = useState<Detection[]>([])
  const [historyTruncated, setHistoryTruncated] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [hideUnidentifiedHistory] = useState(true)

  useEffect(() => {
    if (mode !== 'history') return
    let cancelled = false
    setHistoryLoading(true)
    const params = new URLSearchParams({ limit: '500' })
    if (focus) {
      params.set('since', focus.fromUtc)
      params.set('until', focus.toUtc)
    } else {
      params.set('window', historyWindow)
    }
    fetch(`/api/v1/detections?${params}`)
      .then((response) => response.json())
      .then((data) => {
        if (cancelled) return
        setHistoryDetections(data.detections ?? [])
        setHistoryTruncated(Boolean(data.truncated))
      })
      .catch(() => !cancelled && setHistoryDetections([]))
      .finally(() => !cancelled && setHistoryLoading(false))
    return () => {
      cancelled = true
    }
  }, [mode, historyWindow, focus])

  return {
    mode,
    setMode,
    historyWindow,
    historyRange,
    focus,
    historyDetections,
    historyTruncated,
    historyLoading,
    hideUnidentifiedHistory,
    onWindowChange: (name, range) => {
      if (name !== historyWindow) {
        setHistoryWindow(name)
        setFocus(null)
      }
      if (range) setHistoryRange(range)
    },
    onFocus: (fromUtc, toUtc) => {
      const wholeWindow =
        historyRange !== null && fromUtc === historyRange.start_utc && toUtc === historyRange.end_utc
      setFocus(wholeWindow ? null : { fromUtc, toUtc })
    },
  }
}
