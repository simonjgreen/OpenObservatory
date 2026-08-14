/** Storage & retention, operator-facing.
 *
 *  Built against an assumed shape before the backend existed (ADR-029), and
 *  since reconciled with the real `GET /api/v1/retention/status`. Still
 *  deliberately read-only and defensive: it degrades to "not available yet"
 *  rather than breaking the page when the endpoint 404s, and every field
 *  added since is read optionally so an older station renders "not reported"
 *  instead of a confident zero.
 *
 *    GET /api/v1/retention/status ->
 *      {
 *        "tiers": [
 *          { "name": "native + audible", "age_days_max": 7, "clips": 123, "bytes": 456 },
 *          { "name": "audible only",      "age_days_max": 30, "clips": ..., "bytes": ... }
 *        ],
 *        // ADR-061: past "audible only", only a `kept` detection's clip
 *        // survives (forever, not to some later cutoff), so anything past
 *        // that boundary is reported below as `eligible_for_deletion`
 *        // rather than as a further named tier.
 *        "eligible_for_deletion": { "clips": N, "bytes": N, "bytes_verified_present": N },
 *        "missing_files": { "clips": N, "bytes": N, "exact": bool, ... },
 *        "disk_reclaim_threshold": 0.85,
 *        "last_run_utc": "...",
 *        "dry_run": true | false
 *      }
 *
 *  **`clips` counts rows, not files (ADR-057).** 8,067 of this station's
 *  rows once claimed clips that had been deleted from under them, so this
 *  panel over-reported by 20.59 GB and said so nowhere. `missing_files` is
 *  what the station's rolling audit knows about that, and it is rendered
 *  whenever it is non-zero — the tier numbers are the ones being corrected,
 *  so hiding the correction where they are shown would defeat it.
 *
 *  This matches the operator decision already recorded for this session
 *  (0–7d native+audible, 7–30d audible-only, 30–90d kept-only (ADR-061),
 *  90d+ deleted unless kept; continuous oldest-first reclaim above 85% disk,
 *  also exempting kept clips) and keeps
 *  detection metadata retention — which is unbounded and not shown here —
 *  conceptually separate from clip bytes, which is what this panel is about.
 *
 *  No control to trigger a run is exposed: the operator decision was a
 *  `--dry-run` CLI flag, i.e. an operational/CLI affordance, not a button on
 *  the always-on station UI that could be mis-clicked.
 */

import { useEffect, useState } from 'react'

interface RetentionTier {
  name: string
  age_days_max: number
  clips: number
  bytes: number
}

interface RetentionStatus {
  tiers: RetentionTier[]
  eligible_for_deletion: { clips: number; bytes: number; bytes_verified_present?: number }
  /** ADR-057. Optional: a station on an older build does not send it, and
   *  absent must render as "not reported", never as zero. */
  missing_files?: {
    clips: number
    bytes: number
    /** False while the rolling audit is still on its first pass, when the
     *  figures are a floor rather than a count of the whole table. */
    exact: boolean
    passes_completed: number
    last_pass_scanned: number
  }
  disk_reclaim_threshold: number
  last_run_utc: string | null
  dry_run: boolean
}

function bytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(0)} kB`
  return `${value} B`
}

export function RetentionPanel() {
  const [status, setStatus] = useState<RetentionStatus | null>(null)
  const [available, setAvailable] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/v1/retention/status')
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status))
        return response.json()
      })
      .then((data: RetentionStatus) => {
        if (cancelled) return
        setStatus(data)
        setAvailable(true)
      })
      .catch(() => {
        if (!cancelled) setAvailable(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // ADR-057. `undefined` (an older station that does not report it) and
  // `{clips: 0}` (a station that checked and found nothing) are different
  // answers and must not collapse into one, so this is not defaulted.
  const missing = status?.missing_files

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Storage &amp; retention</h2>
      </header>
      {available === false && (
        <p className="empty">
          Not available yet — the retention service has not shipped on this station. Clips
          currently age out only via the disk guard; see Diagnostics → Evidence, storage
          &amp; buses.
        </p>
      )}
      {available === null && <p className="empty">checking…</p>}
      {status && (
        <>
          <dl className="kv compact">
            {status.tiers.map((tier) => (
              <div key={tier.name}>
                <dt>{tier.name}</dt>
                <dd className="mono">
                  {tier.clips.toLocaleString()} clips · {bytes(tier.bytes)}
                </dd>
              </div>
            ))}
            <div>
              <dt title="Clips old enough to be removed at the next run">eligible now</dt>
              <dd className="mono">
                {status.eligible_for_deletion.clips.toLocaleString()} clips ·{' '}
                {/* ADR-057: what deleting them would actually free, when the
                    station has audited it. `bytes` is only what the rows
                    claim, and 20.59 GB of that claim was once false. */}
                {bytes(
                  status.eligible_for_deletion.bytes_verified_present ??
                    status.eligible_for_deletion.bytes,
                )}
              </dd>
            </div>
            {missing !== undefined && missing.clips > 0 && (
              <div>
                <dt title="Rows recorded as holding evidence whose file is not on disk">
                  missing from disk
                </dt>
                <dd className="mono warn-text">
                  {missing.clips.toLocaleString()} clips · {bytes(missing.bytes)}
                  {missing.exact ? '' : ' (so far)'}
                </dd>
              </div>
            )}
          </dl>
          {missing !== undefined && missing.clips > 0 && (
            <p className="dim panel-caption">
              {missing.exact
                ? `${missing.clips.toLocaleString()} of ${missing.last_pass_scanned.toLocaleString()} stored evidence rows`
                : `${missing.clips.toLocaleString()} evidence rows so far (the audit is still on its first pass)`}{' '}
              record a clip that is not on disk, so the tier figures above count{' '}
              {bytes(missing.bytes)} that are not there and those detections cannot be
              played back. The detections themselves are unaffected. Run{' '}
              <code>oo clips reconcile-missing</code> to list them, and{' '}
              <code>--apply</code> to correct the record; no clip is deleted either way.
            </p>
          )}
          <p className="dim panel-caption">
            {status.dry_run
              ? 'Dry-run mode: nothing is actually being deleted.'
              : `Reclaim continues oldest-first above ${(
                  status.disk_reclaim_threshold * 100
                ).toFixed(0)}% disk used.`}{' '}
            Detection metadata (species, time, score) is kept indefinitely regardless of clip
            age — only the evidence clip bytes are tiered.
          </p>
        </>
      )}
    </section>
  )
}
