/** Time of day against date: one column per day or week, 24 cells tall,
 *  with sunrise, sunset and civil twilight drawn over it as lines. The bend
 *  in those lines through a season is the point of the chart: activity that
 *  tracks them is light-driven, activity that ignores them is not birds. */

import { useMemo, useState } from 'react'
import { bucketLabel, monthStarts } from '../../analytics/calendar'
import { clock, compact, duration, groupColour, ramp } from '../../analytics/scale'
import type { HoursPayload } from '../../analytics/types'
import { ChartTooltip, relativePoint, type HoverState } from './ChartTooltip'

const W = 1000
const H = 240

interface Props {
  payload: HoursPayload
  /** Divide each cell by the captured seconds behind it. */
  rate: boolean
}

export function HoursHeatmap({ payload, rate }: Props) {
  const [hover, setHover] = useState<HoverState | null>(null)
  const [width, setWidth] = useState(0)
  const { columns } = payload
  const colour = groupColour(payload.group ?? 'bird')

  const cells = useMemo(
    () =>
      columns.map((c) =>
        c.hours.map((n, hour) => {
          if (!rate) return n
          const seconds = c.captured[hour]
          return seconds > 0 ? (n / seconds) * 3600 : 0
        }),
      ),
    [columns, rate],
  )
  const max = Math.max(0, ...cells.flat())
  const slot = columns.length ? W / columns.length : W
  const rowH = H / 24

  function solarPath(key: 'sunrise' | 'sunset' | 'civil_dawn' | 'civil_dusk'): string {
    const points: string[] = []
    columns.forEach((c, index) => {
      const value = c.solar?.[key]
      if (value === null || value === undefined) return
      points.push(`${(index + 0.5) * slot},${(value / 24) * H}`)
    })
    return points.length > 1 ? `M${points.join(' L')}` : ''
  }

  const months = monthStarts(columns.map((c) => c.start_date))
  const hasSolar = columns.some((c) => c.solar !== null)

  return (
    <div className="chart hours-heatmap">
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
          aria-label="Detections by hour of day"
          onMouseMove={(event) => {
            const p = relativePoint(event)
            setWidth(p.width)
            const index = Math.floor(p.fx * columns.length)
            const hour = Math.floor(p.fy * 24)
            const c = columns[index]
            if (!c) return setHover(null)
            const n = c.hours[hour]
            const seconds = c.captured[hour]
            setHover({
              x: p.x,
              y: p.y,
              title: `${bucketLabel(c.start_date, payload.columns_grain)} · ${clock(hour)}–${clock(hour + 1)}`,
              lines: [
                `${compact(n)} detections`,
                seconds > 0
                  ? `${(n / (seconds / 3600)).toFixed(1)} per captured hour (${duration(seconds)} captured)`
                  : 'nothing captured in this hour',
                ...(c.solar
                  ? [`sunrise ${clock(c.solar.sunrise)} · sunset ${clock(c.solar.sunset)}`]
                  : []),
                ...(c.partial ? ['in progress'] : []),
              ],
            })
          }}
          onMouseLeave={() => setHover(null)}
        >
          {columns.map((c, index) => (
            <g key={c.key}>
              {cells[index].map((value, hour) =>
                value > 0 ? (
                  <rect
                    key={hour}
                    x={index * slot}
                    y={hour * rowH}
                    width={slot + 0.5}
                    height={rowH + 0.5}
                    fill={ramp(value, max, colour)}
                  />
                ) : null,
              )}
            </g>
          ))}
          {hasSolar && (
            <>
              <path d={solarPath('civil_dawn')} className="solar twilight" vectorEffect="non-scaling-stroke" />
              <path d={solarPath('civil_dusk')} className="solar twilight" vectorEffect="non-scaling-stroke" />
              <path d={solarPath('sunrise')} className="solar sun" vectorEffect="non-scaling-stroke" />
              <path d={solarPath('sunset')} className="solar sun" vectorEffect="non-scaling-stroke" />
            </>
          )}
          {columns.map((c, index) =>
            c.days_built === 0 ? (
              <rect key={c.key} x={index * slot} y={0} width={slot} height={H} className="unbuilt" />
            ) : null,
          )}
        </svg>
        <ChartTooltip hover={hover} width={width} />
        <div className="chart-x mono dim">
          {months.map((m) => (
            <span key={m.index} style={{ left: `${(m.index / columns.length) * 100}%` }} className="month">
              {m.label}
            </span>
          ))}
        </div>
      </div>
      <div className="chart-legend">
        <span>
          <i style={{ background: ramp(max, max, colour) }} /> more
        </span>
        <span>
          <i style={{ background: ramp(max * 0.1, max, colour) }} /> fewer
        </span>
        {hasSolar ? (
          <>
            <span>
              <i className="key-line sun" /> sunrise · sunset
            </span>
            <span>
              <i className="key-line twilight" /> civil dawn · dusk
            </span>
          </>
        ) : (
          <span className="dim">no station coordinates, so no sunrise or sunset lines</span>
        )}
      </div>
    </div>
  )
}

/** The column sums of the heat-map: the plain answer to "what time of day". */
export function HourProfile({ payload, rate }: Props) {
  const [hover, setHover] = useState<HoverState | null>(null)
  const [width, setWidth] = useState(0)
  const colour = groupColour(payload.group ?? 'bird')
  const values = payload.profile.map((n, hour) => {
    if (!rate) return n
    const seconds = payload.profile_captured[hour]
    return seconds > 0 ? (n / seconds) * 3600 : 0
  })
  const max = Math.max(1, ...values)
  const peak = values.indexOf(Math.max(...values))
  const slot = W / 24
  return (
    <div className="chart hour-profile">
      <div className="chart-plot">
        <svg
          viewBox={`0 0 ${W} 100`}
          preserveAspectRatio="none"
          role="img"
          aria-label="Detections by hour, whole range"
          onMouseMove={(event) => {
            const p = relativePoint(event)
            setWidth(p.width)
            const hour = Math.floor(p.fx * 24)
            setHover({
              x: p.x,
              y: p.y,
              title: `${clock(hour)}–${clock(hour + 1)}`,
              lines: [
                `${compact(payload.profile[hour])} detections`,
                `${duration(payload.profile_captured[hour])} captured across the range`,
              ],
            })
          }}
          onMouseLeave={() => setHover(null)}
        >
          {values.map((v, hour) => (
            <rect
              key={hour}
              x={hour * slot + 1}
              y={100 - (v / max) * 100}
              width={slot - 2}
              height={(v / max) * 100}
              fill={colour}
              opacity={hour === peak ? 1 : 0.7}
            />
          ))}
        </svg>
        <ChartTooltip hover={hover} width={width} />
        <div className="chart-x mono dim">
          {[0, 6, 12, 18].map((h) => (
            <span key={h} style={{ left: `${(h / 24) * 100}%` }} className="month">
              {clock(h)}
            </span>
          ))}
        </div>
      </div>
      <p className="dim chart-caption">
        {payload.profile.some((n) => n > 0)
          ? `Most often ${clock(peak)}–${clock(peak + 1)}${rate ? ', per captured hour' : ''}.`
          : 'Nothing in the range.'}
      </p>
    </div>
  )
}
