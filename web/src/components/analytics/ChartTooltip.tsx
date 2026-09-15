/** One tooltip for every analytics chart: positioned inside the chart's own
 *  wrapper from pointer coordinates, flipped to the left when it would
 *  otherwise leave the wrapper. Text only, in text tokens; the coloured mark
 *  beside a line carries identity, the words carry the number. */

import type { ReactNode } from 'react'

export interface HoverState {
  x: number
  y: number
  lines: ReactNode[]
  title?: string
}

export function ChartTooltip({ hover, width }: { hover: HoverState | null; width: number }) {
  if (!hover) return null
  const flip = width > 0 && hover.x > width * 0.6
  return (
    <div
      className={`chart-tooltip mono ${flip ? 'flip' : ''}`}
      style={{ left: hover.x, top: hover.y }}
      role="status"
    >
      {hover.title && <div className="chart-tooltip-title">{hover.title}</div>}
      {hover.lines.map((line, index) => (
        <div key={index}>{line}</div>
      ))}
    </div>
  )
}

/** Pointer position relative to an element, for the tooltip. */
export function relativePoint(event: { clientX: number; clientY: number; currentTarget: Element }): {
  x: number
  y: number
  fx: number
  fy: number
  width: number
} {
  const rect = event.currentTarget.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  return {
    x,
    y,
    fx: rect.width > 0 ? Math.min(0.999999, Math.max(0, x / rect.width)) : 0,
    fy: rect.height > 0 ? Math.min(0.999999, Math.max(0, y / rect.height)) : 0,
    width: rect.width,
  }
}
