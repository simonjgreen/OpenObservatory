/** Storage & retention, operator-facing.
 *
 *  The retention BACKEND (tiered age-out, dry-run mode, `oo clips retention
 *  --dry-run`) is being built by another agent this session and does not
 *  exist yet — this component is UI against an assumed shape, deliberately
 *  read-only and defensive so it degrades to "not available yet" rather than
 *  breaking the page when the endpoint 404s.
 *
 *  Assumed contract (state this loudly; confirm/adjust once the backend
 *  lands):
 *
 *    GET /api/v1/retention/status ->
 *      {
 *        "tiers": [
 *          { "name": "native + audible", "age_days_max": 7, "clips": 123, "bytes": 456 },
 *          { "name": "audible only",      "age_days_max": 30, "clips": ..., "bytes": ... },
 *          { "name": "first/best per species", "age_days_max": 90, "clips": ..., "bytes": ... }
 *        ],
 *        "eligible_for_deletion": { "clips": N, "bytes": N },
 *        "disk_reclaim_threshold": 0.85,
 *        "last_run_utc": "...",
 *        "dry_run": true | false
 *      }
 *
 *  This matches the operator decision already recorded for this session
 *  (0–7d native+audible, 7–30d audible-only, 30–90d first/best-per-species,
 *  90d+ deleted; continuous oldest-first reclaim above 85% disk) and keeps
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
  eligible_for_deletion: { clips: number; bytes: number }
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
                {bytes(status.eligible_for_deletion.bytes)}
              </dd>
            </div>
          </dl>
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
