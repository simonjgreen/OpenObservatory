/** Detail view for one detection: the evidence behind a claim.
 *
 *  The raw detector output is shown in full, unedited. For BirdNET that includes
 *  the logit, the plausibility band and the threshold that was applied, so a
 *  surprising identification can be argued with rather than just believed.
 */

import type { Detection } from '../types'
import { formatHz } from './Spectrogram'
import { glyphFor } from './Suggestions'

interface Props {
  detection: Detection | null
  localTimeZone: string
  onClose: () => void
}

export function DetectionDrawer({ detection, localTimeZone, onClose }: Props) {
  if (!detection) return null
  const start = new Date(detection.event_start_utc)
  const format = new Intl.DateTimeFormat('en-GB', {
    dateStyle: 'medium',
    timeStyle: 'medium',
    timeZone: localTimeZone,
  })
  const native = detection.native_result

  return (
    <aside className="drawer" role="dialog" aria-label="Detection detail">
      <header className="drawer-head">
        <div className={`avatar group-${detection.taxonomic_group}`} aria-hidden>
          {glyphFor(detection.taxonomic_group)}
        </div>
        <div className="grow">
          <h2>{detection.display_name}</h2>
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

      {detection.media.length > 0 ? (
        <div className="subsection">
          <h3>Evidence</h3>
          {detection.media.map((asset) => (
            <div className="media" key={asset.id}>
              <div className="media-meta mono dim">
                {asset.kind} · {(asset.sample_rate / 1000).toFixed(0)} kHz ·{' '}
                {(asset.byte_length / 1024).toFixed(0)} kB
              </div>
              {asset.sample_rate <= 96000 ? (
                <audio controls preload="none" src={asset.url} />
              ) : (
                <p className="dim">
                  {(asset.sample_rate / 1000).toFixed(0)} kHz source — browsers cannot
                  decode this directly. A 48 kHz playback derivative is written alongside.{' '}
                  <a href={asset.url} download>
                    download
                  </a>
                </p>
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

      <div className="subsection">
        <h3>Raw detector output</h3>
        <pre className="json">{JSON.stringify(native, null, 2)}</pre>
      </div>
    </aside>
  )
}
