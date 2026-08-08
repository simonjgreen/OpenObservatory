/** Which of the two depths ADR-016/024 calls for is showing: the calm
 *  operator summary, or the full diagnostic surface underneath it. Synced to
 *  `?view=` in the URL (`replaceState`, so it never grows history entries)
 *  so a refresh — or a link sent to someone else — lands on the same depth.
 *  This is deliberately small rather than a router: one query parameter,
 *  one default, read once on mount and written on every change. */

import { useEffect, useState } from 'react'

export type ViewDepth = 'operate' | 'diagnose'

const PARAM = 'view'

function readInitial(search: string): ViewDepth {
  const params = new URLSearchParams(search)
  return params.get(PARAM) === 'diagnose' ? 'diagnose' : 'operate'
}

export interface ViewModeState {
  depth: ViewDepth
  setDepth: (depth: ViewDepth) => void
  toggle: () => void
}

export function useViewMode(location: Pick<Location, 'search'> = window.location): ViewModeState {
  const [depth, setDepthState] = useState<ViewDepth>(() => readInitial(location.search))

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (depth === 'operate') params.delete(PARAM)
    else params.set(PARAM, depth)
    const query = params.toString()
    const url = `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`
    window.history.replaceState(window.history.state, '', url)
  }, [depth])

  return {
    depth,
    setDepth: setDepthState,
    toggle: () => setDepthState((current) => (current === 'operate' ? 'diagnose' : 'operate')),
  }
}
