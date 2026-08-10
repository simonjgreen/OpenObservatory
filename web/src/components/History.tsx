/** Browsing what happened, rather than what is happening.
 *
 *  The live channel only knows about the session it is connected for, so a page
 *  opened at breakfast has no idea a bat passed at 03:40. This reads the persisted
 *  history instead: a timeline showing the shape of the window, what was identified
 *  in it, and — importantly — how much of it the station was actually listening for.
 *
 *  That last figure is why coverage is displayed as prominently as the detections. An
 *  empty night means something completely different depending on whether nothing
 *  called or nothing was recording, and a display that cannot tell you which invites
 *  the wrong conclusion.
 */

import { useEffect, useMemo, useState } from 'react'
import { formatDetectionTitle } from './detectionTitle'
import { ExportLinks } from './ExportLinks'

export interface HistoryRange {
  start_utc: string
  end_utc: string
  label: string
  seconds: number
}

interface TimelineBucket {
  start_utc: string
  groups: Record<string, { detections: number; best_score: number }>
}

interface SpeciesRow {
  taxonomic_group: string
  common_name: string | null
  scientific_name: string | null
  label: string | null
  display_name: string
  title_hint?: string | null
  plugin_id: string
  detections: number
  best_score: number
  first_seen_utc: string
  last_seen_utc: string
}

interface HistoryPayload {
  range: HistoryRange
  timezone: string
  timeline: { bucket_seconds: number; buckets: TimelineBucket[]; note: string }
  species: SpeciesRow[]
  coverage: {
    seconds_in_range: number
    seconds_captured: number
    seconds_from_microphone: number
    fraction_captured: number | null
    gaps: number
    estimated_missing_frames: number
    /** ADR-055. Time inside this window that the operator deliberately paused
     *  recording for. Reported separately from `seconds_captured` and never
     *  subtracted from it: the station really was capturing throughout, so
     *  deducting it would make a pause indistinguishable from the dead
     *  microphone charter item 2 exists to tell it apart from. */
    seconds_paused?: number
    pauses?: Array<{
      pause_id: string
      start_utc: string
      end_utc: string
      seconds: number
      label: string
      end_reason: string | null
      running: boolean
    }>
    streams: Array<{
      stream_id: string
      source_kind: string
      start_utc: string
      end_utc: string
      seconds: number
      sample_rate: number
    }>
  }
}

const WINDOWS: Array<{ name: string; label: string }> = [
  { name: 'last-hour', label: 'last hour' },
  { name: 'dawn-chorus', label: 'dawn chorus' },
  { name: 'last-night', label: 'last night' },
  { name: 'today', label: 'today' },
  { name: 'yesterday', label: 'yesterday' },
  { name: 'last-24h', label: '24 hours' },
]

const GROUP_COLOUR: Record<string, string> = {
  bird: '#5ce08a',
  bat: '#c39bff',
  acoustic_event: '#3f6ea8',
  noise: '#6b7280',
}

function duration(seconds: number): string {
  if (seconds >= 3600) {
    const hours = Math.floor(seconds / 3600)
    const minutes = Math.round((seconds % 3600) / 60)
    return minutes ? `${hours}h ${minutes}m` : `${hours}h`
  }
  if (seconds >= 60) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds)}s`
}

interface Props {
  timeZone: string
  /** Called with the window whenever it changes, so the detection list can follow. */
  onWindowChange: (windowName: string, range: HistoryRange | null) => void
  /** Called when a bucket is clicked, to focus the detection list on that slice. */
  onFocus: (fromUtc: string, toUtc: string) => void
  windowName: string
  focused: { fromUtc: string; toUtc: string } | null
  includeUnidentified: boolean
}

export function History({
  timeZone,
  onWindowChange,
  onFocus,
  windowName,
  focused,
  includeUnidentified,
}: Props) {
  const [payload, setPayload] = useState<HistoryPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetch(
      `/api/v1/history?window=${encodeURIComponent(windowName)}` +
        `&include_unidentified=${includeUnidentified}`,
    )
      .then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
        return response.json()
      })
      .then((data: HistoryPayload) => {
        if (cancelled) return
        setPayload(data)
        onWindowChange(windowName, data.range)
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      })
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // onWindowChange is stable enough for this; re-running on it would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowName, includeUnidentified])

  const timeFormat = useMemo(
    () =>
      new Intl.DateTimeFormat('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone,
      }),
    [timeZone],
  )

  const chart = useMemo(() => {
    if (!payload) return null
    const { buckets, bucket_seconds } = payload.timeline
    const start = Date.parse(payload.range.start_utc)
    const end = Date.parse(payload.range.end_utc)
    const span = Math.max(1, end - start)
    const groups = [...new Set(buckets.flatMap((b) => Object.keys(b.groups)))].sort()
    const peak = Math.max(
      1,
      ...buckets.map((b) => Object.values(b.groups).reduce((t, g) => t + g.detections, 0)),
    )
    return { buckets, bucket_seconds, start, span, groups, peak }
  }, [payload])

  return (
    <section className="history">
      <div className="history-controls">
        {/* Six named windows never fit one phone-width row, and dropping any of
            them would put a question ("what came through at dawn?") out of
            reach from the garden. `segmented-wrap` lets this one group break
            into rows instead — see ADR-054. */}
        <div className="segmented segmented-wrap">
          {WINDOWS.map((entry) => (
            <button
              key={entry.name}
              className={entry.name === windowName ? 'on' : ''}
              onClick={() => onWindowChange(entry.name, null)}
            >
              {entry.label}
            </button>
          ))}
        </div>
        {payload && (
          <span className="dim mono history-span">
            {timeFormat.format(Date.parse(payload.range.start_utc))}–
            {timeFormat.format(Date.parse(payload.range.end_utc))} ·{' '}
            {duration(payload.range.seconds)}
          </span>
        )}
        {loading && <span className="dim">loading…</span>}
        {error && <span className="warn-text">history unavailable: {error}</span>}
        {focused && (
          <button onClick={() => onFocus(payload!.range.start_utc, payload!.range.end_utc)}>
            clear focus
          </button>
        )}
        <span className="grow" />
        <ExportLinks windowName={windowName} focus={focused} />
      </div>

      {payload && (
        <>
          {/* Coverage first. Without it an empty window is ambiguous. */}
          <div className="coverage">
            <CoverageBar payload={payload} timeFormat={timeFormat} />
            <div className="coverage-numbers mono dim">
              <span
                className={
                  (payload.coverage.fraction_captured ?? 0) > 0.98 ? '' : 'warn-text'
                }
                title="Fraction of the window the station was capturing for. Anything below 100% means part of this window has no audio behind it, so absence of detections there means nothing."
              >
                {((payload.coverage.fraction_captured ?? 0) * 100).toFixed(1)}% captured
              </span>
              <span>{duration(payload.coverage.seconds_from_microphone)} from the microphone</span>
              <span className={payload.coverage.gaps > 0 ? 'warn-text' : ''}>
                {payload.coverage.gaps} gaps
              </span>
              <span>{payload.coverage.streams.length} streams</span>
              {/* ADR-055. Only when there was one -- an "0s paused" on every
                  window would be noise on a figure that is normally zero, and
                  the point of showing it at all is that a paused window is not
                  a quiet one. */}
              {(payload.coverage.seconds_paused ?? 0) > 0 && (
                <span
                  className="pause-coverage"
                  title="Time the operator deliberately paused recording for. Audio was still being captured, but nothing was detected, stored or published, so an absence here means nothing about the garden."
                >
                  {duration(payload.coverage.seconds_paused ?? 0)} paused
                </span>
              )}
            </div>
          </div>

          {chart && chart.buckets.length > 0 ? (
            <div className="history-chart">
              <svg
                viewBox={`0 0 1000 150`}
                preserveAspectRatio="none"
                role="img"
                aria-label="Detections over the selected window"
              >
                {chart.buckets.map((bucket) => {
                  const at = Date.parse(bucket.start_utc)
                  const x = ((at - chart.start) / chart.span) * 1000
                  const width = Math.max(
                    1.5,
                    ((chart.bucket_seconds * 1000) / chart.span) * 1000 - 0.5,
                  )
                  let offset = 0
                  const isFocused =
                    focused !== null &&
                    at >= Date.parse(focused.fromUtc) &&
                    at < Date.parse(focused.toUtc)
                  return (
                    <g
                      key={bucket.start_utc}
                      className={`bucket ${isFocused ? 'focused' : ''}`}
                      onClick={() =>
                        onFocus(
                          bucket.start_utc,
                          new Date(at + chart.bucket_seconds * 1000).toISOString(),
                        )
                      }
                    >
                      {/* Full-height hit area, so thin bars are still clickable. */}
                      <rect x={x} y={0} width={width} height={150} fill="transparent" />
                      {chart.groups.map((group) => {
                        const count = bucket.groups[group]?.detections ?? 0
                        if (!count) return null
                        const height = (count / chart.peak) * 140
                        const y = 145 - offset - height
                        offset += height
                        return (
                          <rect
                            key={group}
                            x={x}
                            y={y}
                            width={width}
                            height={height}
                            fill={GROUP_COLOUR[group] ?? '#6fb4ff'}
                          />
                        )
                      })}
                    </g>
                  )
                })}
              </svg>
              <div className="history-axis mono dim">
                {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
                  <span key={fraction} style={{ left: `${fraction * 100}%` }}>
                    {timeFormat.format(
                      Date.parse(payload.range.start_utc) + fraction * payload.range.seconds * 1000,
                    )}
                  </span>
                ))}
              </div>
              <div className="history-legend">
                {chart.groups.map((group) => (
                  <span key={group}>
                    <i style={{ background: GROUP_COLOUR[group] ?? '#6fb4ff' }} />
                    {group.replace('_', ' ')}
                  </span>
                ))}
                <span className="dim">
                  {chart.bucket_seconds >= 60
                    ? `${chart.bucket_seconds / 60} min buckets`
                    : `${chart.bucket_seconds}s buckets`}{' '}
                  · click to focus
                </span>
              </div>
              <p className="dim history-note">{payload.timeline.note}</p>
            </div>
          ) : (
            <p className="empty">
              {(payload.coverage.fraction_captured ?? 0) < 0.01
                ? 'Nothing was recorded in this window, so there is nothing to show — this is not a quiet night, it is no data.'
                : 'Audio was captured across this window but nothing was detected in it.'}
            </p>
          )}

          {payload.species.length > 0 && (
            <div className="history-species">
              <table>
                <thead>
                  <tr>
                    <th>identified</th>
                    <th>n</th>
                    <th>best</th>
                    <th>first</th>
                    <th>last</th>
                  </tr>
                </thead>
                <tbody>
                  {payload.species.map((row) => {
                    const title = formatDetectionTitle(row)
                    return (
                    <tr
                      key={`${row.plugin_id}:${row.display_name}`}
                      onClick={() => onFocus(row.first_seen_utc, row.last_seen_utc)}
                      title={`Focus on ${row.display_name}'s calling period`}
                    >
                      <td>
                        <span
                          className="dot"
                          style={{ background: GROUP_COLOUR[row.taxonomic_group] ?? '#6fb4ff' }}
                        />
                        {title.label}
                        {title.hint && <span className="title-hint">{title.hint}</span>}
                        {row.scientific_name && <i className="sci"> {row.scientific_name}</i>}
                      </td>
                      <td className="mono">{row.detections}</td>
                      <td className="mono">{(row.best_score * 100).toFixed(0)}</td>
                      <td className="mono dim">{timeFormat.format(Date.parse(row.first_seen_utc))}</td>
                      <td className="mono dim">{timeFormat.format(Date.parse(row.last_seen_utc))}</td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  )
}

/** When the station was actually recording, drawn against the window. */
function CoverageBar({
  payload,
  timeFormat,
}: {
  payload: HistoryPayload
  timeFormat: Intl.DateTimeFormat
}) {
  const start = Date.parse(payload.range.start_utc)
  const span = Math.max(1, Date.parse(payload.range.end_utc) - start)
  return (
    <div className="coverage-bar" title="Green is audio; dark is a window with no recording behind it">
      {payload.coverage.streams.map((stream) => {
        const from = Date.parse(stream.start_utc)
        const to = Date.parse(stream.end_utc)
        return (
          <span
            key={`${stream.stream_id}-${stream.start_utc}`}
            className={stream.source_kind === 'alsa' ? 'live' : 'synthetic'}
            style={{
              left: `${((from - start) / span) * 100}%`,
              width: `${Math.max(0.2, ((to - from) / span) * 100)}%`,
            }}
            title={`${stream.source_kind} · ${timeFormat.format(from)}–${timeFormat.format(
              to,
            )} · ${(stream.sample_rate / 1000).toFixed(0)} kHz`}
          />
        )
      })}
      {/* ADR-055, drawn over the stream spans rather than in place of them.
          The audio underneath a pause really was captured; what stopped was
          everything downstream. An operator looking at a silent afternoon has
          to be able to see that it was deliberate, or the only two readings
          available are "quiet garden" and "broken station" -- and both are
          wrong. */}
      {(payload.coverage.pauses ?? []).map((pause) => {
        const from = Date.parse(pause.start_utc)
        const to = Date.parse(pause.end_utc)
        return (
          <span
            key={pause.pause_id}
            className="paused"
            style={{
              left: `${((from - start) / span) * 100}%`,
              width: `${Math.max(0.3, ((to - from) / span) * 100)}%`,
            }}
            title={`paused by the operator · ${timeFormat.format(from)}–${timeFormat.format(to)}${
              pause.running ? ' (still paused)' : ''
            } · nothing was detected, stored or published in this window`}
          />
        )
      })}
    </div>
  )
}
