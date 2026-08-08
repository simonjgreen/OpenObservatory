/** Reduces a `StationStatus` snapshot to the four plain-language questions
 *  an operator actually has (Milestone 4 / ADR-024): is it listening, is the
 *  microphone real, is storage healthy, is anything degraded. Pure and
 *  tested apart from the component that renders it, so the wording and
 *  thresholds can be checked without mounting React.
 *
 *  Deliberately conservative about what counts as "healthy" — see
 *  `docs/architecture/ADRS.md` ADR-024: a diagnostic number is not a
 *  product claim, and this file exists precisely so the product claim
 *  ("storage is fine") is derived from the same measured fields the
 *  diagnostics panel shows, not a separate invented one. */

import type { StationStatus } from '../types'

export type Tone = 'ok' | 'warn' | 'danger'

export interface OperatorCard {
  key: string
  title: string
  tone: Tone
  headline: string
  detail: string
}

const DISK_WARN_RATIO = 0.85
const DISK_DANGER_RATIO = 0.97

export function operatorCards(status: StationStatus | null): OperatorCard[] {
  if (!status) {
    return [
      {
        key: 'listening',
        title: 'Listening',
        tone: 'warn',
        headline: 'not connected',
        detail: 'Waiting for the station to answer.',
      },
    ]
  }

  const capture = status.capture
  const synthetic = !capture.is_live_hardware
  const listening = capture.state === 'capturing'

  const cards: OperatorCard[] = []

  if (synthetic) {
    cards.push({
      key: 'listening',
      title: 'Listening',
      tone: 'danger',
      headline: 'NOT LIVE AUDIO',
      detail: `Capturing from ${capture.source_kind ?? 'a synthetic source'}, not the microphone. Nothing below this is a real observation.`,
    })
  } else if (!listening) {
    cards.push({
      key: 'listening',
      title: 'Listening',
      tone: 'danger',
      headline: 'not capturing',
      detail: capture.detail || `capture state is ${capture.state}`,
    })
  } else {
    const blockAge = capture.block_age_s ?? 0
    const stale = blockAge > 2
    cards.push({
      key: 'listening',
      title: 'Listening',
      tone: stale ? 'warn' : 'ok',
      headline: stale ? 'audio has gone quiet' : 'yes, on the microphone',
      detail: stale
        ? `No new audio for ${blockAge.toFixed(0)}s. This can be a genuinely silent night, or a stalled capture — check diagnostics.`
        : `${capture.device_label ?? 'microphone'} at ${
            capture.sample_rate ? `${(capture.sample_rate / 1000).toFixed(0)} kHz` : 'an unknown rate'
          }.`,
    })
  }

  const usedRatio = status.storage.disk_used_ratio
  const storageTone: Tone =
    usedRatio >= DISK_DANGER_RATIO ? 'danger' : usedRatio >= DISK_WARN_RATIO ? 'warn' : 'ok'
  cards.push({
    key: 'storage',
    title: 'Storage',
    tone: storageTone,
    headline:
      storageTone === 'ok'
        ? 'plenty of room'
        : storageTone === 'warn'
          ? 'getting full'
          : 'nearly full',
    detail: `${(usedRatio * 100).toFixed(0)}% used · ${status.storage.clip_count.toLocaleString()} clips on disk.${
      status.clips.disk_guard_active ? ` Evidence writing is currently paused: ${status.clips.disk_guard_active}.` : ''
    }`,
  })

  const degraded = status.detectors.filter((d) => d.state === 'degraded' || d.state === 'error')
  const unavailable = status.detectors.filter((d) => d.state === 'unavailable')
  cards.push({
    key: 'detectors',
    title: 'Detection',
    tone: degraded.length > 0 ? 'danger' : unavailable.length > 0 ? 'warn' : 'ok',
    headline:
      degraded.length > 0
        ? `${degraded.length} detector${degraded.length > 1 ? 's' : ''} having trouble`
        : unavailable.length > 0
          ? `${unavailable.length} detector${unavailable.length > 1 ? 's' : ''} unavailable`
          : 'all detectors running',
    detail:
      degraded.length > 0
        ? degraded.map((d) => `${d.plugin_id}: ${d.detail || d.state}`).join('; ')
        : unavailable.length > 0
          ? unavailable.map((d) => d.plugin_id).join(', ')
          : `${status.detectors.length} running normally.`,
  })

  return cards
}
