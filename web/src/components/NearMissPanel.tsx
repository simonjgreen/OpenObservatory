/** What the detector proposed and then refused (ADR-052).
 *
 *  The problem this exists for: an operator can hear a bird, sees no
 *  detection, and the only thing the station will tell them is that it
 *  suppressed 152 candidates. Which species, at what score, against which
 *  bar, was recorded nowhere. This panel is that record, and it is laid out
 *  in the order a person actually tunes in:
 *
 *  1. **the histogram**, per band — where the rejected scores sit relative to
 *     the bar. This is what decides whether moving a threshold buys anything:
 *     "380 below 0.2 and 20 between 0.45 and 0.55" answers the question, a
 *     total of 400 does not.
 *  2. **the species table** — which birds were refused, how close the best
 *     one came, and what the range model thought of them.
 *  3. **the recent log** — the last few minutes, individually, with times.
 *
 *  Rendered only in the diagnose depth, and it polls only while mounted, so
 *  it costs the station nothing in its normal state of nobody at a browser
 *  (charter item 8).
 *
 *  Scores are model outputs, not probabilities, and this panel says so. A
 *  band whose bar is unreachable (`implausible`, ADR-032) shows no shortfall
 *  at all rather than a large number — nothing clears an infinite bar, and a
 *  distance would imply it was merely a long way off.
 */

import { useEffect, useState } from 'react'

const POLL_MS = 10_000

interface BandRow {
  band: string
  threshold: number | null
  threshold_unreachable: boolean
  rejected: number
  admitted: number
  histogram: { bin_width: number; counts: number[] }
}

interface SpeciesRow {
  label_index: number
  common_name: string
  scientific_name: string | null
  band: string
  occurrence_probability: number | null
  rejected: number
  admitted: number
  best_score: number
  shortfall: number | null
  last_at_ns: number
}

interface RecentRow {
  at_ns: number
  common_name: string
  score: number
  occurrence_probability: number | null
  band: string
  threshold: number | null
  shortfall: number | null
}

export interface NearMissDetector {
  plugin_id: string
  capacity: number
  held: number
  rejected_total: number
  admitted_total: number
  species_tracked: number
  species_omitted: number
  windows_analysed: number
  min_confidence: number
  range_model_loaded: boolean
  week: number
  note: string
  bands: BandRow[]
  species: SpeciesRow[]
  recent: RecentRow[]
}

export function bandLabel(band: string): string {
  if (band === 'no_prior') return 'no prior for species'
  if (band === 'non_biological') return 'sound category'
  return band.replace(/_/g, ' ')
}

/** Which histogram bin the bar sits in, so the chart can mark it. `null` when
 *  the band has no reachable bar. */
export function thresholdBin(band: BandRow): number | null {
  if (band.threshold === null || band.threshold_unreachable) return null
  return Math.min(
    band.histogram.counts.length - 1,
    Math.floor(band.threshold / band.histogram.bin_width),
  )
}

function timeOf(atNs: number, timeZone: string): string {
  return new Date(atNs / 1e6).toLocaleTimeString('en-GB', { timeZone })
}

function Histogram({ band }: { band: BandRow }) {
  const counts = band.histogram.counts
  const peak = Math.max(1, ...counts)
  const bar = thresholdBin(band)
  return (
    <div className="near-miss-histogram" role="img"
      aria-label={`Rejected score distribution for ${bandLabel(band.band)}`}>
      {counts.map((count, index) => {
        const low = (index * band.histogram.bin_width).toFixed(2)
        const high = ((index + 1) * band.histogram.bin_width).toFixed(2)
        return (
          <span
            key={index}
            className={`near-miss-bin${bar !== null && index === bar ? ' at-bar' : ''}${
              bar !== null && index >= bar ? ' above-bar' : ''
            }`}
            title={`${count} rejected with a score of ${low}–${high}`}
          >
            <span
              className="near-miss-bin-fill"
              style={{ height: `${(100 * count) / peak}%` }}
            />
          </span>
        )
      })}
    </div>
  )
}

export function NearMissPanel({ localTimeZone = 'UTC' }: { localTimeZone?: string }) {
  const [detectors, setDetectors] = useState<NearMissDetector[] | null>(null)
  const [available, setAvailable] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      fetch('/api/v1/detectors/near-misses')
        .then((response) => {
          if (!response.ok) throw new Error(String(response.status))
          return response.json()
        })
        .then((data: { detectors: NearMissDetector[] }) => {
          if (cancelled) return
          setDetectors(data.detectors)
          setAvailable(true)
        })
        .catch(() => {
          if (!cancelled) setAvailable(false)
        })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Rejected candidates</h2>
      </header>
      {available === false && (
        <p className="empty">
          Not available — this station's API does not serve
          <span className="mono"> /api/v1/detectors/near-misses</span>.
        </p>
      )}
      {available === null && <p className="empty">checking…</p>}
      {detectors?.length === 0 && (
        <p className="empty">
          No detector on this station records rejected candidates. Only detectors with
          plausibility bands do.
        </p>
      )}
      {detectors?.map((detector) => (
        <div key={detector.plugin_id} className="near-miss">
          <p className="dim panel-caption">
            <span className="mono">{detector.plugin_id}</span> ·{' '}
            {detector.rejected_total.toLocaleString()} rejected,{' '}
            {detector.admitted_total.toLocaleString()} admitted over{' '}
            {detector.windows_analysed.toLocaleString()} windows
            {detector.capacity === 0 && ' · individual records off (ring size 0)'}
          </p>

          <table className="near-miss-bands">
            <thead>
              <tr>
                <th>band</th>
                <th>bar</th>
                <th>rejected</th>
                <th>kept</th>
                <th>rejected scores, 0 → 1</th>
              </tr>
            </thead>
            <tbody>
              {detector.bands
                .filter((band) => band.rejected + band.admitted > 0)
                .map((band) => (
                  <tr key={band.band}>
                    <td>{bandLabel(band.band)}</td>
                    <td className="mono">
                      {band.threshold_unreachable ? (
                        <span title="ADR-032: no score admits this band">never</span>
                      ) : (
                        band.threshold?.toFixed(2) ?? '—'
                      )}
                    </td>
                    <td className="mono">{band.rejected.toLocaleString()}</td>
                    <td className="mono dim">{band.admitted.toLocaleString()}</td>
                    <td>
                      <Histogram band={band} />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          {detector.species.length > 0 && (
            <table className="near-miss-species">
              <thead>
                <tr>
                  <th>species</th>
                  <th>band</th>
                  <th title="Occurrence probability from the range model">prior</th>
                  <th>rejected</th>
                  <th title="Highest score this species reached and still failed">best</th>
                  <th title="How far the best score fell short of its bar">short by</th>
                </tr>
              </thead>
              <tbody>
                {detector.species.map((row) => (
                  <tr key={row.label_index}>
                    <td title={row.scientific_name ?? 'not a species claim'}>
                      {row.common_name}
                    </td>
                    <td className="dim">{bandLabel(row.band)}</td>
                    <td className="mono dim">
                      {row.occurrence_probability === null
                        ? '—'
                        : row.occurrence_probability.toExponential(1)}
                    </td>
                    <td className="mono">{row.rejected.toLocaleString()}</td>
                    <td className="mono">{row.best_score.toFixed(3)}</td>
                    <td className="mono">
                      {row.shortfall === null ? '—' : row.shortfall.toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {detector.species_omitted > 0 && (
            <p className="dim panel-caption">
              {detector.species_omitted.toLocaleString()} rejections were of species beyond
              the tracked table's bound and are counted in the histograms only.
            </p>
          )}

          {detector.recent.length > 0 && (
            <details className="near-miss-recent">
              <summary>
                most recent {detector.recent.length} of {detector.held} held
              </summary>
              <ul className="mono">
                {detector.recent.map((row, index) => (
                  <li key={`${row.at_ns}-${index}`}>
                    {timeOf(row.at_ns, localTimeZone)} {row.common_name} {row.score.toFixed(3)}{' '}
                    <span className="dim">
                      {bandLabel(row.band)}
                      {row.threshold === null ? '' : ` bar ${row.threshold.toFixed(2)}`}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          <p className="dim panel-caption">{detector.note}</p>
        </div>
      ))}
    </section>
  )
}
