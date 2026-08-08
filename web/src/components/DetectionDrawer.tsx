/** Detail view for one detection: the evidence behind a claim.
 *
 *  The raw detector output is shown in full, unedited. For BirdNET that includes
 *  the logit, the plausibility band and the threshold that was applied, so a
 *  surprising identification can be argued with rather than just believed.
 */

import { useEffect, useState } from 'react'
import type { Detection, MediaRef } from '../types'
import { formatDetectionTitle } from './detectionTitle'
import { formatHz } from './Spectrogram'
import { glyphFor } from './Suggestions'

interface ReviewState {
  status: 'confirmed' | 'rejected'
  note: string
  created_at: string | null
}

/** Minimal review workflow (Milestone 4, lower priority than the foundation
 *  work): confirm or reject a detection, writing to the `review` table via
 *  `POST /api/v1/detections/{id}/review`. Fetches the latest review on open
 *  so a previously-reviewed detection shows its status rather than looking
 *  untouched. */
function useReview(detectionId: string | null) {
  const [review, setReview] = useState<ReviewState | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setReview(null)
    if (!detectionId) return
    let cancelled = false
    fetch(`/api/v1/detections/${detectionId}/review`)
      .then((response) => (response.ok ? response.json() : { review: null }))
      .then((data) => !cancelled && setReview(data.review ?? null))
      .catch(() => !cancelled && setReview(null))
    return () => {
      cancelled = true
    }
  }, [detectionId])

  const submit = (status: 'confirmed' | 'rejected') => {
    if (!detectionId) return
    setSaving(true)
    fetch(`/api/v1/detections/${detectionId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    })
      .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
      .then((data) => setReview(data))
      .catch(() => {
        // Left as-is: the button state itself communicates failure (still
        // shows "confirm"/"reject" rather than a checked state).
      })
      .finally(() => setSaving(false))
  }

  return { review, saving, submit }
}

const MEDIA_LABELS: Record<string, string> = {
  evidence_native: 'Authoritative recording',
  playback: 'Playback (48 kHz)',
  audible_ultrasonic: 'Ultrasound made audible',
}

/** Say plainly what was done to the audio and what it costs.
 *
 *  Both renderings alter the signal, and a reviewer drawing conclusions from one
 *  needs to know which properties survived. Time expansion keeps everything but
 *  changes the timebase; heterodyning keeps the timebase but throws away most of
 *  the spectrum.
 */
function explainUltrasonic(asset: MediaRef): string {
  const detail = asset.detail ?? {}
  if (detail.method === 'time-expansion') {
    const factor = Number(detail.factor ?? 0)
    const source = Number(detail.source_peak_hz ?? 0)
    const audible = Number(detail.audible_peak_hz ?? 0)
    return (
      `Played ${factor}x slower, so every frequency is divided by ${factor}: ` +
      `the ${(source / 1000).toFixed(1)} kHz call is heard at ${(audible / 1000).toFixed(1)} kHz. ` +
      `Harmonics, sweep shape and pulse timing are all preserved — the clip simply ` +
      `lasts ${factor}x longer than the event did.`
    )
  }
  if (detail.method === 'heterodyne') {
    const tuned = Number(detail.tuned_hz ?? 0)
    const bandwidth = Number(detail.bandwidth_hz ?? 0)
    return (
      `Mixed down from ${(tuned / 1000).toFixed(1)} kHz, keeping ±${(bandwidth / 1000).toFixed(1)} kHz, ` +
      `as a handheld bat detector does. Real time is preserved, so the rhythm of the ` +
      `pass is intact, but everything outside that band is discarded — good for ` +
      `listening, not for measurement.`
    )
  }
  return ''
}

interface Props {
  detection: Detection | null
  localTimeZone: string
  onClose: () => void
}

export function DetectionDrawer({ detection, localTimeZone, onClose }: Props) {
  const { review, saving, submit } = useReview(detection?.id ?? null)
  if (!detection) return null
  const start = new Date(detection.event_start_utc)
  const format = new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'medium',
    timeZone: localTimeZone,
  })
  const native = detection.native_result
  const title = formatDetectionTitle(detection)
  const frequencyGroup =
    typeof native?.frequency_group_hint === 'string' ? native.frequency_group_hint : null

  return (
    <aside className="drawer" role="dialog" aria-label="Detection detail">
      <header className="drawer-head">
        <div className={`avatar group-${detection.taxonomic_group}`} aria-hidden>
          {glyphFor(detection.taxonomic_group)}
        </div>
        <div className="grow">
          <h2>
            {title.label}
            {title.hint && <span className="title-hint">{title.hint}</span>}
            {title.feedingBuzz && <span className="feeding-buzz-marker">feeding buzz</span>}
          </h2>
          {detection.scientific_name && <p className="sci">{detection.scientific_name}</p>}
        </div>
        <button className="close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </header>

      <dl className="kv compact">
        <div>
          <dt>when</dt>
          <dd>{format.format(start)}</dd>
        </div>
        <div>
          <dt>duration</dt>
          <dd className="mono">{detection.duration_s.toFixed(2)} s</dd>
        </div>
        <div>
          <dt>{detection.calibrated_probability !== null ? 'probability' : 'model score'}</dt>
          <dd className="mono">
            {(detection.score * 100).toFixed(1)}
            {detection.calibrated_probability === null && (
              <span className="dim"> (uncalibrated)</span>
            )}
          </dd>
        </div>
        <div>
          <dt>detector</dt>
          <dd className="mono">
            {detection.detector.plugin_id} · {detection.detector.model_id}{' '}
            {detection.detector.model_version}
          </dd>
        </div>
        {detection.peak_frequency_hz && (
          <div>
            <dt>peak frequency</dt>
            <dd className="mono">{formatHz(detection.peak_frequency_hz)}</dd>
          </div>
        )}
        <div>
          <dt>rank / group</dt>
          <dd>
            {detection.rank ?? 'none'} · {detection.taxonomic_group}
          </dd>
        </div>
        <div>
          <dt title="Frame bounds in the authoritative native stream, so this event can be re-extracted exactly">
            native frames
          </dt>
          <dd className="mono">
            {detection.source_start_frame.toLocaleString()}–
            {detection.source_end_frame.toLocaleString()}
          </dd>
        </div>
      </dl>

      <div className="subsection review-controls">
        <h3>Review</h3>
        <div className="review-buttons">
          <button
            className={`review-btn confirm ${review?.status === 'confirmed' ? 'on' : ''}`}
            disabled={saving}
            onClick={() => submit('confirmed')}
          >
            ✓ confirm
          </button>
          <button
            className={`review-btn reject ${review?.status === 'rejected' ? 'on' : ''}`}
            disabled={saving}
            onClick={() => submit('rejected')}
          >
            ✕ reject
          </button>
          {review && (
            <span className="dim review-status">
              last reviewed: {review.status}
              {review.created_at ? ` at ${new Date(review.created_at).toLocaleString()}` : ''}
            </span>
          )}
        </div>
      </div>

      {detection.media.length > 0 ? (
        <div className="subsection">
          <h3>Evidence</h3>
          {detection.media.map((asset) => (
            <div
              className={`media ${asset.kind === 'audible_ultrasonic' ? 'audible-ultrasonic' : ''}`}
              key={asset.id}
            >
              <div className="media-head">
                <span className="media-title">{MEDIA_LABELS[asset.kind] ?? asset.kind}</span>
                {asset.description && <span className="media-badge">{asset.description}</span>}
                {asset.detail?.authoritative === false && (
                  <span
                    className="chip warn"
                    title="Filtered and normalised for listening. Its levels are not comparable with the authoritative recording."
                  >
                    processed
                  </span>
                )}
              </div>
              <div className="media-meta mono dim">
                {(asset.sample_rate / 1000).toFixed(asset.sample_rate < 10000 ? 1 : 0)} kHz ·{' '}
                {(asset.byte_length / 1024).toFixed(0)} kB
                {typeof asset.duration_s === 'number' && ` · ${asset.duration_s.toFixed(1)} s`}
              </div>

              {asset.sample_rate <= 96000 ? (
                <audio controls preload="none" src={asset.url} />
              ) : (
                <p className="dim">
                  {(asset.sample_rate / 1000).toFixed(0)} kHz — no browser will decode this
                  directly. It is the authoritative recording; use the derivatives below to
                  listen, or{' '}
                  <a href={asset.url} download>
                    download it
                  </a>{' '}
                  for analysis.
                </p>
              )}

              {asset.kind === 'audible_ultrasonic' && (
                <p className="media-explainer dim">{explainUltrasonic(asset)}</p>
              )}

              <div className="mono dim hash" title="SHA-256 of the written file">
                {asset.sha256.slice(0, 32)}…
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="dim">
          No evidence clip. Clips are written only for the detectors named in
          <code> clip_plugins</code>, above the score threshold, and within the rate and
          disk budgets.
        </p>
      )}

      {detection.taxonomic_group === 'bat' && (
        <div className="subsection">
          <h3>Bat pass candidate</h3>
          <p className="dim">
            The candidate name above is inferred from peak frequency alone, using a coarse
            band table. It is <strong>not a species identification</strong> — several UK
            species share the same band, and peak frequency is only a weak signal even
            within a band. Treat it as a hint for where to look, not a result.
          </p>
          {frequencyGroup && (
            <dl className="kv compact">
              <div>
                <dt>frequency group</dt>
                <dd>{frequencyGroup}</dd>
              </div>
            </dl>
          )}
          {typeof native?.min_interval_ms === 'number' && (
            <dl className="kv compact">
              <div>
                <dt>shortest interval in train</dt>
                <dd className="mono">{Number(native.min_interval_ms).toFixed(1)} ms</dd>
              </div>
              {title.feedingBuzz && (
                <>
                  {typeof native?.buzz_offset_s === 'number' && (
                    <div>
                      <dt>buzz starts at</dt>
                      <dd className="mono">{Number(native.buzz_offset_s).toFixed(2)} s</dd>
                    </div>
                  )}
                  {typeof native?.buzz_min_interval_ms === 'number' && (
                    <div>
                      <dt>buzz shortest interval</dt>
                      <dd className="mono">
                        {Number(native.buzz_min_interval_ms).toFixed(1)} ms
                      </dd>
                    </div>
                  )}
                  {typeof native?.buzz_pulse_count === 'number' && (
                    <div>
                      <dt>buzz pulse count</dt>
                      <dd className="mono">{native.buzz_pulse_count}</dd>
                    </div>
                  )}
                </>
              )}
            </dl>
          )}
        </div>
      )}

      <div className="subsection">
        <h3>Raw detector output</h3>
        <pre className="json">{JSON.stringify(native, null, 2)}</pre>
      </div>
    </aside>
  )
}
