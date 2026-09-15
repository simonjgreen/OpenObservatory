/** Detections per bucket, stacked by group or a single named series, with a
 *  coverage strip under it so a low bar over a dark strip reads as "deaf"
 *  and never as "quiet". Marks in SVG stretched to the box; text in HTML. */

import { useMemo, useState } from 'react'
import { bucketLabel, labelledIndices } from '../../analytics/calendar'
import { compact, duration, groupColour, groupLabel, niceMax, orderGroups, ticks } from '../../analytics/scale'
import type { SeriesPayload } from '../../analytics/types'
import { ChartTooltip, relativePoint, type HoverState } from './ChartTooltip'

const H = 160
const W = 1000

interface Props {
  payload: SeriesPayload
  /** Divide each bucket by its captured hours. */
  rate: boolean
}

export function SeriesChart({ payload, rate }: Props) {
  const [hover, setHover] = useState<HoverState | null>(null)
  const [width, setWidth] = useState(0)
  const { buckets, grain } = payload
  const groups = useMemo(
    () => orderGroups(buckets.flatMap((b) => Object.keys(b.groups))),
    [buckets],
  )
  const single = payload.label !== null && payload.label !== undefined && payload.label !== ''

  const values = useMemo(
    () =>
      buckets.map((b) => {
        const hours = b.seconds_from_microphone / 3600
        const scale = rate ? (hours > 0 ? 1 / hours : 0) : 1
        const perGroup: Record<string, number> = {}
        for (const g of groups) perGroup[g] = (b.groups[g] ?? 0) * scale
        return { total: b.detections * scale, perGroup, hours }
      }),
    [buckets, groups, rate],
  )
  const max = niceMax(Math.max(0, ...values.map((v) => v.total)))
  const slot = buckets.length ? W / buckets.length : W
  const gap = Math.min(3, slot * 0.15)
  const barWidth = Math.max(1, slot - gap)
  const expectedSeconds = useMemo(() => {
    // Days the bucket spans, from the bucket's own dates, at 24 h each.
    return buckets.map((b) => {
      const a = Date.parse(b.start_date)
      const z = Date.parse(b.end_date)
      return (Math.round((z - a) / 86400000) + 1) * 86400
    })
  }, [buckets])

  const labelled = new Set(labelledIndices(buckets.length, 8))

  return (
    <div className="chart series-chart">
      <div className="chart-y mono dim">
        {ticks(max, 4).map((t) => (
          <span key={t} style={{ bottom: `${(t / max) * 100}%` }}>
            {rate ? t.toFixed(t < 10 ? 1 : 0) : compact(t)}
          </span>
        ))}
      </div>
      <div className="chart-plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`Detections per ${grain}`}
          onMouseMove={(event) => {
            const p = relativePoint(event)
            setWidth(p.width)
            const index = Math.floor(p.fx * buckets.length)
            const b = buckets[index]
            if (!b) return setHover(null)
            const v = values[index]
            const lines = [
              `${rate ? v.total.toFixed(2) + ' per captured hour' : compact(b.detections) + ' detections'}`,
              ...(single
                ? []
                : groups
                    .filter((g) => (b.groups[g] ?? 0) > 0)
                    .map((g) => `${groupLabel(g)}: ${compact(b.groups[g] ?? 0)}`)),
              `${duration(b.seconds_from_microphone)} from the microphone`,
              ...(b.seconds_paused > 0 ? [`${duration(b.seconds_paused)} paused`] : []),
              ...(b.days_unbuilt > 0 ? [`${b.days_unbuilt} day(s) not yet rolled up`] : []),
              ...(b.partial ? ['in progress'] : []),
            ]
            setHover({ x: p.x, y: p.y, title: bucketLabel(b.start_date, grain), lines })
          }}
          onMouseLeave={() => setHover(null)}
        >
          {ticks(max, 4).map((t) => (
            <line
              key={t}
              x1={0}
              x2={W}
              y1={H - (t / max) * H}
              y2={H - (t / max) * H}
              className="grid-line"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {buckets.map((b, index) => {
            const x = index * slot + gap / 2
            const v = values[index]
            let offset = 0
            const stack = single
              ? [{ key: 'series', value: v.total, colour: groupColour(payload.group ?? 'bird') }]
              : groups.map((g) => ({ key: g, value: v.perGroup[g] ?? 0, colour: groupColour(g) }))
            return (
              <g key={b.key} className={b.partial ? 'partial' : ''}>
                {stack.map((segment) => {
                  if (!(segment.value > 0)) return null
                  const height = (segment.value / max) * H
                  const y = H - offset - height
                  offset += height + 1.5
                  return (
                    <rect
                      key={segment.key}
                      x={x}
                      y={y}
                      width={barWidth}
                      height={Math.max(0.5, height)}
                      fill={segment.colour}
                      opacity={b.partial ? 0.55 : 1}
                    />
                  )
                })}
                {b.days_unbuilt > 0 && (
                  <rect x={x} y={0} width={barWidth} height={H} className="unbuilt" />
                )}
              </g>
            )
          })}
        </svg>
        <ChartTooltip hover={hover} width={width} />
        {/* Coverage under every bucket: full brightness at full capture. */}
        <div className="coverage-strip" title="How much of each bucket the microphone was capturing for">
          {buckets.map((b, index) => {
            const fraction = Math.min(1, b.seconds_from_microphone / expectedSeconds[index])
            return (
              <span
                key={b.key}
                style={{ opacity: 0.15 + 0.85 * fraction }}
                className={fraction < 0.9 ? 'low' : ''}
              />
            )
          })}
        </div>
        <div className="chart-x mono dim">
          {buckets.map((b, index) =>
            labelled.has(index) ? (
              <span key={b.key} style={{ left: `${((index + 0.5) / buckets.length) * 100}%` }}>
                {bucketLabel(b.start_date, grain)}
              </span>
            ) : null,
          )}
        </div>
      </div>
      {!single && groups.length > 1 && (
        <div className="chart-legend">
          {groups.map((g) => (
            <span key={g}>
              <i style={{ background: groupColour(g) }} /> {groupLabel(g)}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
