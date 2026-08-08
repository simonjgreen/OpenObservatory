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
  onToggle: () => void
  onVolume: (value: number) => void
  detail?: string
  channel: LiveAudioChannel
  onChannel: (channel: LiveAudioChannel) => void
  tuneHz: number
  onTuneHz: (hz: number) => void
  hello: AudioHelloInfo | null
}

//: HTMLMediaElement.readyState, spelled out because "readyState 2" means
//: nothing to someone glancing at the control.
const READY_STATE_LABELS = ['no data', 'metadata', 'current frame', 'playable', 'buffered']

export function ListenControl({
  status,
  telemetry,
  volume,
  onToggle,
  onVolume,
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
          {/*
           * Playback runs through a plain <audio> element rather than Web
           * Audio (diagnosed dead — silent output on all Web Audio routes —
           * on at least one real laptop; see audio.ts), so there is no
           * client-side output meter, jitter-buffer depth, or underrun count
           * to show here any more. What follows is exactly what
           * HTMLMediaElement exposes, honestly labelled. Signal level is
           * still visible from the server-side level meters above.
           */}
          <div className="listen-telemetry mono">
            <span title="HTMLMediaElement.readyState — how much of the current playback position is available">
              {telemetry ? READY_STATE_LABELS[telemetry.readyState] ?? telemetry.readyState : '—'}
            </span>
            <span title="Seconds of audio already buffered ahead of the play position">
              buffered {telemetry ? telemetry.bufferedAheadS.toFixed(1) : '—'} s
            </span>
            <span
              className={telemetry && telemetry.waits > 0 ? 'warn-text' : 'dim'}
              title="Times playback paused for lack of data (the 'waiting' event)"
            >
              waits {telemetry?.waits ?? 0}
            </span>
            {telemetry && telemetry.stalls > 0 && (
              <span
                className="warn-text"
                title="Times the browser was fetching data but none arrived (the 'stalled' event)"
              >
                stalls {telemetry.stalls}
              </span>
            )}
            {telemetry?.paused && (
              <span className="warn-text" title="The browser has paused playback">
                paused
              </span>
            )}
          </div>
        </>
      )}
      {status === 'error' && detail && <span className="warn-text">{detail}</span>}
    </div>
  )
}
