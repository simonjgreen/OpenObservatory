/** When the day's activity started and stopped, against the light. One thin
 *  bar per day from the 5th to the 95th percentile detection, a hairline to
 *  the first and last, and sunrise, sunset and civil twilight as lines. A
 *  second panel plots the rate per captured hour of daylight (birds) or of
 *  night (bats) against how long that daylight or night was. */

import { useState } from 'react'
import { longDate, monthStarts } from '../../analytics/calendar'
import { clock, groupColour, niceMax, ticks } from '../../analytics/scale'
import type { SpanDay, SpanPayload } from '../../analytics/types'
import { ChartTooltip, relativePoint, type HoverState } from './ChartTooltip'

const W = 1000
const H = 240

interface Props {
  payload: SpanPayload
}

export function SpanChart({ payload }: Props) {
  const [hover, setHover] = useState<HoverState | null>(null)
  const [width, setWidth] = useState(0)
  const { days } = payload
  const colour = groupColour(payload.group)
  const slot = days.length ? W / days.length : W
  const y = (hours: number) => (hours / 24) * H

  function line(key: 'sunrise' | 'sunset' | 'civil_dawn' | 'civil_dusk'): string {
    const points: string[] = []
    days.forEach((d, index) => {
      const value = d[key]
      if (value === null || value === undefined) return
      points.push(`${(index + 0.5) * slot},${y(value)}`)
    })
    return points.length > 1 ? `M${points.join(' L')}` : ''
  }

  const months = monthStarts(days.map((d) => d.date))
  const nocturnal = payload.group === 'bat'

  return (
    <div className="chart span-chart">
      <div className="chart-y mono dim hours-y">
        {[0, 6, 12, 18, 24].map((h) => (
          <span key={h} style={{ top: `${(h / 24) * 100}%` }}>
            {clock(h)}
          </span>
        ))}
      </div>
      <div className="chart-plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label="Daily active span against sunrise and sunset"
          onMouseMove={(event) => {
            const p = relativePoint(event)
            setWidth(p.width)
            const d = days[Math.floor(p.fx * days.length)]
            if (!d) return setHover(null)
            setHover({ x: p.x, y: p.y, title: longDate(d.date), lines: describe(d, nocturnal) })
          }}
          onMouseLeave={() => setHover(null)}
        >
          {payload.coordinates_set && (
            <>
              <path d={line('civil_dawn')} className="solar twilight" vectorEffect="non-scaling-stroke" />
              <path d={line('civil_dusk')} className="solar twilight" vectorEffect="non-scaling-stroke" />
              <path d={line('sunrise')} className="solar sun" vectorEffect="non-scaling-stroke" />
              <path d={line('sunset')} className="solar sun" vectorEffect="non-scaling-stroke" />
            </>
          )}
          {days.map((d, index) => {
            const x = (index + 0.5) * slot
            if (!d.built) {
              return <rect key={d.date} x={index * slot} y={0} width={slot} height={H} className="unbuilt" />
            }
            if (d.first === null || d.first === undefined || d.last === null || d.last === undefined) return null
            const barW = Math.max(1, slot * 0.6)
            return (
              <g key={d.date} opacity={d.complete ? 1 : 0.55}>
                <line x1={x} x2={x} y1={y(d.first)} y2={y(d.last)} stroke={colour} strokeOpacity={0.35} vectorEffect="non-scaling-stroke" />
                {d.p05 !== null && d.p05 !== undefined && d.p95 !== null && d.p95 !== undefined && (
                  <rect
                    x={x - barW / 2}
                    y={y(d.p05)}
                    width={barW}
                    height={Math.max(1, y(d.p95) - y(d.p05))}
                    fill={colour}
                    rx={1}
                  />
                )}
              </g>
            )
          })}
        </svg>
        <ChartTooltip hover={hover} width={width} />
        <div className="chart-x mono dim">
          {months.map((m) => (
            <span key={m.index} style={{ left: `${(m.index / days.length) * 100}%` }} className="month">
              {m.label}
            </span>
          ))}
        </div>
      </div>
      <div className="chart-legend">
        <span>
          <i style={{ background: colour }} /> central 90% of the day's detections
        </span>
        <span>
          <i className="key-line sun" /> sunrise · sunset
        </span>
        <span>
          <i className="key-line twilight" /> civil dawn · dusk
        </span>
      </div>
      <RateVersusLength payload={payload} nocturnal={nocturnal} />
    </div>
  )
}

function describe(d: SpanDay, nocturnal: boolean): string[] {
  if (!d.built) return ['not yet rolled up']
  const lines = [
    `${d.detections ?? 0} detections`,
    `first ${clock(d.first)} · last ${clock(d.last)}`,
    `central 90%: ${clock(d.p05)}–${clock(d.p95)}`,
  ]
  if (d.sunrise !== null && d.sunrise !== undefined) {
    lines.push(`sunrise ${clock(d.sunrise)} · sunset ${clock(d.sunset)} (${d.daylight_hours?.toFixed(1)} h)`)
  }
  if (nocturnal && d.night_known && d.night_hours) {
    lines.push(
      `night: ${d.night_detections} passes in ${d.night_hours.toFixed(1)} h` +
        (d.night_per_captured_hour !== null && d.night_per_captured_hour !== undefined
          ? ` · ${d.night_per_captured_hour.toFixed(2)} per captured hour`
          : ''),
    )
  } else if (!nocturnal && d.daylight_per_captured_hour !== null && d.daylight_per_captured_hour !== undefined) {
    lines.push(`${d.daylight_per_captured_hour.toFixed(2)} per captured daylight hour`)
  }
  if (d.complete === false) lines.push('in progress')
  return lines
}

/** Rate per captured hour against the length of the light or the night:
 *  the question "does density change as the hours change", as a scatter.
 *  No fitted line: the charter forbids a number that claims more than the
 *  points do, and a person can see a slope. */
function RateVersusLength({ payload, nocturnal }: { payload: SpanPayload; nocturnal: boolean }) {
  const [hover, setHover] = useState<HoverState | null>(null)
  const [width, setWidth] = useState(0)
  const colour = groupColour(payload.group)
  const points = payload.days
    .filter((d) => d.built)
    .map((d) => ({
      day: d,
      x: nocturnal ? d.night_hours : d.daylight_hours,
      y: nocturnal ? d.night_per_captured_hour : d.daylight_per_captured_hour,
    }))
    .filter((p): p is { day: SpanDay; x: number; y: number } => typeof p.x === 'number' && typeof p.y === 'number')
  if (points.length < 2) {
    return (
      <p className="dim chart-caption">
        {payload.coordinates_set
          ? 'Not enough days yet to plot rate against length.'
          : 'Set the station’s coordinates in settings to relate activity to daylight.'}
      </p>
    )
  }
  const xs = points.map((p) => p.x)
  const xMin = Math.floor(Math.min(...xs))
  const xMax = Math.ceil(Math.max(...xs))
  const xSpan = Math.max(1, xMax - xMin)
  const yMax = niceMax(Math.max(...points.map((p) => p.y)))
  return (
    <div className="chart rate-chart">
      <p className="chart-caption dim">
        {nocturnal ? 'Passes per captured night hour, against night length' : 'Detections per captured daylight hour, against day length'}
      </p>
      <div className="chart-y mono dim">
        {ticks(yMax, 4).map((t) => (
          <span key={t} style={{ bottom: `${(t / yMax) * 100}%` }}>
            {t.toFixed(t < 10 ? 1 : 0)}
          </span>
        ))}
      </div>
      <div className="chart-plot">
        <svg
          viewBox={`0 0 ${W} 120`}
          preserveAspectRatio="none"
          role="img"
          aria-label="Rate per captured hour against hours of light or night"
          onMouseMove={(event) => {
            const p = relativePoint(event)
            setWidth(p.width)
            let best: (typeof points)[number] | null = null
            let bestDistance = Infinity
            for (const point of points) {
              const dx = (point.x - xMin) / xSpan - p.fx
              const dy = 1 - point.y / yMax - p.fy
              const distance = dx * dx + dy * dy
              if (distance < bestDistance) {
                bestDistance = distance
                best = point
              }
            }
            if (!best || bestDistance > 0.01) return setHover(null)
            setHover({ x: p.x, y: p.y, title: longDate(best.day.date), lines: describe(best.day, nocturnal) })
          }}
          onMouseLeave={() => setHover(null)}
        >
          {points.map((p) => (
            <circle
              key={p.day.date}
              cx={((p.x - xMin) / xSpan) * W}
              cy={120 - (p.y / yMax) * 120}
              r={4}
              fill={colour}
              fillOpacity={0.75}
              className="dot-mark"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        <ChartTooltip hover={hover} width={width} />
        <div className="chart-x mono dim">
          {[xMin, xMin + xSpan / 2, xMax].map((h, index) => (
            <span key={h} style={{ left: `${(index / 2) * 100}%` }} className={index === 2 ? 'last' : index === 0 ? 'first' : ''}>
              {h.toFixed(1)} h
            </span>
          ))}
        </div>
      </div>
    </div>
  )
}
