/** Level meters and the GO LIVE listen control.
 *
 *  The dBFS labelling is deliberate and repeated in the tooltip: these are digital
 *  full-scale measurements, not calibrated sound pressure. Without a calibration
 *  procedure, a number labelled "dB SPL" would be a fabrication.
 */

import type { AudioHelloInfo, AudioStatus, AudioTelemetry, LiveAudioChannel } from '../audio'
import { ULTRASONIC_TUNE_MAX_HZ, ULTRASONIC_TUNE_MIN_HZ } from '../audio'
import type { LevelSample } from '../types'

interface MeterProps {
  label: string
  sample: LevelSample | null
}

/** Map dBFS onto a 0..1 bar. -72 dBFS is a sensible floor for a quiet garden. */
function meterFraction(dbfs: number): number {
  const floor = -72
  return Math.max(0, Math.min(1, (dbfs - floor) / -floor))
}

export function LevelMeter({ label, sample }: MeterProps) {
  const rms = sample?.rms_dbfs ?? -120
  const peak = sample?.peak_dbfs ?? -120
  const clipping = (sample?.clipping_ratio ?? 0) > 0
  const hot = peak > -3

  return (
    <div className="meter" title="dBFS relative to digital full scale — not calibrated SPL">
      <div className="meter-label">
        {label}
        {clipping && <span className="chip danger">clipping</span>}
        {!clipping && hot && <span className="chip warn">hot</span>}
        {sample?.silent && <span className="chip danger">silent</span>}
      </div>
      <div className="meter-track">
        <span className="meter-rms" style={{ width: `${meterFraction(rms) * 100}%` }} />
        <span className="meter-peak" style={{ left: `${meterFraction(peak) * 100}%` }} />
        <span className="meter-redline" />
      </div>
      <div className="meter-numbers mono">
        <span>rms {rms.toFixed(1)}</span>
        <span>peak {peak.toFixed(1)}</span>
        <span className="dim">crest {(sample?.crest_factor_db ?? 0).toFixed(1)}</span>
      </div>
    </div>
  )
}

interface ListenProps {
  status: AudioStatus
  telemetry: AudioTelemetry | null
  volume: number
  monitorGainDb: number
  onToggle: () => void
  onVolume: (value: number) => void
  onMonitorGain: (value: number) => void
  detail?: string
  channel: LiveAudioChannel
  onChannel: (channel: LiveAudioChannel) => void
  tuneHz: number
  onTuneHz: (hz: number) => void
  hello: AudioHelloInfo | null
}

export function ListenControl({
  status,
  telemetry,
  volume,
  monitorGainDb,
  onToggle,
  onVolume,
  onMonitorGain,
  detail,
  channel,
  onChannel,
  tuneHz,
  onTuneHz,
  hello,
}: ListenProps) {
  const playing = status === 'playing'
  const busy = status === 'starting'
  const ultrasonic = channel === 'ultrasonic'
  const unavailable = hello !== null && hello.available === false

  return (
    <div className={`listen ${playing ? 'on' : ''}`}>
      <div className="segmented channel-switch" title="Audible listens to the derived 48 kHz mix. Ultrasonic heterodynes the native stream down to an audible band around a tuned frequency, like a handheld bat detector.">
        <button
          className={!ultrasonic ? 'on' : ''}
          disabled={playing}
          onClick={() => onChannel('audible')}
        >
          audible
        </button>
        <button
          className={ultrasonic ? 'on' : ''}
          disabled={playing}
          onClick={() => onChannel('ultrasonic')}
        >
          ultrasonic
        </button>
      </div>

      {ultrasonic && (
        <label
          className="tune"
          title="Heterodyne tuning frequency. Everything outside a band around this is discarded — this is a listening aid, not a measurement, and its levels are not comparable with the native recording."
        >
          <span aria-hidden>🦇</span>
          <input
            type="range"
            min={ULTRASONIC_TUNE_MIN_HZ}
            max={ULTRASONIC_TUNE_MAX_HZ}
            step={500}
            value={tuneHz}
            onChange={(event) => onTuneHz(Number(event.target.value))}
          />
          <span className="mono">{(tuneHz / 1000).toFixed(1)} kHz</span>
        </label>
      )}

      <button
        className={`go-live ${playing ? 'on' : ''}`}
        onClick={onToggle}
        disabled={busy}
        title={
          ultrasonic
            ? 'Stream a live heterodyne rendering of the native ultrasonic stream, tuned to the frequency above — a listening aid, not a measurement'
            : 'Stream the live 48 kHz audible mix to this browser with a small jitter buffer'
        }
      >
        <span className="go-live-dot" />
        {busy ? 'connecting…' : playing ? 'STOP' : 'GO LIVE'}
      </button>

      {playing && unavailable && (
        <span className="warn-text" title={hello?.reason}>
          ultrasonic monitoring unavailable{hello?.reason ? `: ${hello.reason}` : ''}
        </span>
      )}

      {playing && ultrasonic && !unavailable && (
        <span className="dim" title="Band-limited around the tuning frequency; everything outside is discarded. Real-time heterodyne rendering, not a calibrated measurement.">
          heterodyne ±{hello?.bandwidthHz ? (hello.bandwidthHz / 1000).toFixed(1) : '—'} kHz
        </span>
      )}

      {playing && (
        <>
          <label className="volume" title="Playback volume in this browser only">
            <span aria-hidden>🔈</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.02}
              value={volume}
              onChange={(event) => onVolume(Number(event.target.value))}
            />
          </label>
          <label
            className="volume"
            title="Monitor make-up gain. A quiet garden sits near -45 dBFS, which is inaudible on laptop speakers at unity, so the live feed needs lifting to be useful. A limiter after this stops loud events becoming painful."
          >
            <span aria-hidden>+dB</span>
            <input
              type="range"
              min={0}
              max={48}
              step={1}
              value={monitorGainDb}
              onChange={(event) => onMonitorGain(Number(event.target.value))}
            />
            <span className="mono">{monitorGainDb}</span>
          </label>
          {/* Proof that audio is reaching the speakers, not just the browser. */}
          <div
            className="output-meter"
            title="RMS of what is actually reaching the speakers, after gain and limiting"
          >
            <span
              className="output-fill"
              style={{
                width: `${Math.max(0, Math.min(100, ((telemetry?.outputRmsDbfs ?? -70) + 70) * (100 / 70)))}%`,
              }}
            />
          </div>
          <div className="listen-telemetry mono">
            {telemetry && telemetry.contextState !== 'running' && (
              <span className="warn-text" title="The browser has not allowed playback to start">
                context {telemetry.contextState}
              </span>
            )}
            <span title="Level reaching the speakers. If this moves and you hear nothing, check the system volume and output device.">
              out {telemetry ? telemetry.outputRmsDbfs.toFixed(0) : '—'} dBFS
            </span>
            <span title="Audio waiting in the jitter buffer before playback">
              buffer {telemetry ? telemetry.bufferedMs.toFixed(0) : '—'} ms
            </span>
            <span title="Latency the browser's audio device adds on top of the jitter buffer">
              device {telemetry ? telemetry.contextLatencyMs.toFixed(0) : '—'} ms
            </span>
            <span
              className={telemetry && telemetry.underruns > 0 ? 'warn-text' : 'dim'}
              title="Times the buffer ran dry — increase the target latency if this climbs"
            >
              under {telemetry?.underruns ?? 0}
            </span>
            {telemetry && telemetry.overflows > 0 && (
              <span className="dim" title="Chunks dropped to converge back to the target latency — expected, and how the feed stays live">
                trim {telemetry.overflows}
              </span>
            )}
            {telemetry && telemetry.resyncs > 0 && (
              <span
                className="warn-text"
                title="Cursor re-seated after a backlog; stale audio was skipped"
              >
                resync {telemetry.resyncs}
              </span>
            )}
          </div>
        </>
      )}
      {status === 'error' && detail && <span className="warn-text">{detail}</span>}
    </div>
  )
}
