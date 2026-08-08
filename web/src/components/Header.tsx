/** Status header: what the station is doing, at a glance.
 *
 *  The loudest thing here is whether audio is coming from the real microphone. A
 *  synthetic fallback stream looks completely normal in a spectrogram, so if it is
 *  ever mistaken for live capture every conclusion drawn from the page is wrong.
 */

import type { ConnectionState } from '../live'
import type { StationStatus } from '../types'

interface Props {
  status: StationStatus | null
  connection: ConnectionState
  clock: Date
  localTimeZone: string
  /** ADR-016/024: diagnostic numbers (continuity, gaps, hot-path CPU, the
   *  socket link state) stay behind this — a product claim is never backed
   *  by a raw pipeline figure the operator has no context to read. */
  showDiagnostics?: boolean
  children?: React.ReactNode
}

function uptime(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days}d ${hours}h`
  if (hours) return `${hours}h ${minutes}m`
  return `${minutes}m ${Math.floor(seconds % 60)}s`
}

export function Header({
  status,
  connection,
  clock,
  localTimeZone,
  showDiagnostics = true,
  children,
}: Props) {
  const capture = status?.capture
  const synthetic = capture ? !capture.is_live_hardware : false
  const timeFormat = new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: localTimeZone,
  })
  const zoneName = new Intl.DateTimeFormat('en-GB', {
    timeZoneName: 'short',
    timeZone: localTimeZone,
  })
    .formatToParts(clock)
    .find((part) => part.type === 'timeZoneName')?.value

  return (
    <header className="topbar">
      <div className="brand">
        <span className={`pulse ${capture?.state === 'capturing' && !synthetic ? 'live' : 'dead'}`} />
        <div>
          <h1>{status?.station.name ?? 'Open Observatory'}</h1>
          <p className="dim mono">
            {capture?.device_label ?? 'no device'}
            {capture?.sample_rate ? ` · ${(capture.sample_rate / 1000).toFixed(0)} kHz` : ''}
            {capture?.sample_format ? ` ${capture.sample_format}` : ''}
          </p>
        </div>
      </div>

      {synthetic && (
        <div className="synthetic-warning" role="alert">
          <strong>NOT LIVE AUDIO</strong>
          <span>
            capturing from {capture?.source_kind} — the microphone is unavailable, so
            everything below is generated
          </span>
        </div>
      )}

      {showDiagnostics && (
      <div className="stat-row">
        <Stat
          label="continuity"
          value={
            capture?.continuity_ratio != null
              ? `${(capture.continuity_ratio * 100).toFixed(3)}%`
              : '—'
          }
          tone={
            capture?.continuity_ratio != null && capture.continuity_ratio > 0.9995
              ? 'ok'
              : 'warn'
          }
          hint="Frames captured versus frames elapsed time implies, from frame zero"
        />
        <Stat
          label="gaps"
          value={String(capture?.discontinuities ?? '—')}
          tone={(capture?.discontinuities ?? 0) > 0 ? 'warn' : 'ok'}
          hint="Capture discontinuities on the current stream"
        />
        <Stat
          label="block age"
          value={capture?.block_age_s != null ? `${capture.block_age_s.toFixed(2)}s` : '—'}
          tone={(capture?.block_age_s ?? 0) > 1 ? 'warn' : 'ok'}
          hint="Time since the last block of audio arrived"
        />
        <Stat
          label="hot path"
          value={
            capture?.hot_path_cpu_ratio != null
              ? `${(capture.hot_path_cpu_ratio * 100).toFixed(1)}%`
              : '—'
          }
          tone={(capture?.hot_path_cpu_ratio ?? 0) < 0.35 ? 'ok' : 'warn'}
          hint="CPU in the per-block capture path, per second of audio"
        />
        <Stat
          label="uptime"
          value={status ? uptime(status.station.uptime_s) : '—'}
          tone="dim"
          hint="Station process uptime"
        />
        <Stat
          label="link"
          value={connection}
          tone={connection === 'open' ? 'ok' : 'warn'}
          hint="Browser to station WebSocket"
        />
      </div>
      )}

      <div className="topbar-right">
        {children}
        <div className="clock mono">
          <span className="clock-time">{timeFormat.format(clock)}</span>
          <span className="dim">{zoneName ?? localTimeZone}</span>
        </div>
      </div>
    </header>
  )
}

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: string
  tone: string
  hint: string
}) {
  return (
    <div className={`stat tone-${tone}`} title={hint}>
      <span className="stat-label">{label}</span>
      <span className="stat-value mono">{value}</span>
    </div>
  )
}
