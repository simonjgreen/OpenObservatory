/** The long view: what lived in this garden, and when (ADR-079).
 *
 *  Distinct from HISTORY on purpose. History answers "what happened" -- a
 *  timeline of events with the evidence behind each one. This answers "what
 *  is the pattern": aggregates over the station's roll-up of every local day
 *  it has recorded, drawn against the light. Nothing here shows a detection
 *  or plays a clip; everything here is a count, and the coverage that count
 *  sits on travels with it.
 *
 *  The questions on the left are demonstrators. Each says what a working
 *  station should show, so a chart that does not look like that is a reason
 *  to suspect the detector, the clock or the coordinates before the garden.
 */

import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { apiFetch } from '../api'
import { groupLabel } from '../analytics/scale'
import type {
  AnalyticsParams,
  AnalyticsStatus,
  AnalyticsView,
  ColumnsGrain,
  HoursPayload,
  Question,
  SavedReport,
  SeriesGrain,
  SeriesPayload,
  SpanPayload,
  TaxaGrain,
  TaxaPayload,
  TaxaRow,
} from '../analytics/types'
import type { AnalyticsRouteState } from '../hooks/useAnalyticsRoute'
import { useAnalyticsData } from '../hooks/useAnalyticsData'
import { HourProfile, HoursHeatmap } from './analytics/HoursHeatmap'
import { SeriesChart } from './analytics/SeriesChart'
import { SpanChart } from './analytics/SpanChart'
import { TaxaGrid } from './analytics/TaxaGrid'

const RANGES: Array<{ name: string; label: string }> = [
  { name: 'last-30d', label: '30 days' },
  { name: 'last-90d', label: '90 days' },
  { name: 'this-year', label: 'this year' },
  { name: 'last-year', label: 'last year' },
  { name: 'all', label: 'everything' },
]

const VIEW_LABEL: Record<AnalyticsView, string> = {
  series: 'over time',
  hours: 'time of day',
  taxa: 'who, when',
  span: 'against the light',
}

const GROUPS = ['bird', 'bat', 'acoustic_event']

interface Props {
  route: AnalyticsRouteState
  timeZone: string
}

export function Analytics({ route, timeZone }: Props) {
  const { view, params } = route.route
  const [questions, setQuestions] = useState<Question[]>([])
  const [reports, setReports] = useState<SavedReport[]>([])
  const [status, setStatus] = useState<AnalyticsStatus | null>(null)
  const [rate, setRate] = useState(false)
  const [labels, setLabels] = useState<TaxaRow[]>([])

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/v1/analytics/questions')
      .then((r) => (r.ok ? r.json() : { questions: [] }))
      .then((body) => !cancelled && setQuestions(body.questions ?? []))
      .catch(() => undefined)
    apiFetch('/api/v1/analytics/status')
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => !cancelled && setStatus(body))
      .catch(() => undefined)
    refreshReports()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function refreshReports() {
    apiFetch('/api/v1/analytics/reports')
      .then((r) => (r.ok ? r.json() : { reports: [] }))
      .then((body) => setReports(body.reports ?? []))
      .catch(() => undefined)
  }

  // Labels the picker offers for the current group, from the whole roll-up.
  useEffect(() => {
    let cancelled = false
    const group = params.group ?? 'bird'
    apiFetch(`/api/v1/analytics/taxa?range=all&grain=month&group=${encodeURIComponent(group)}&limit=400`)
      .then((r) => (r.ok ? r.json() : { rows: [] }))
      .then((body: TaxaPayload) => !cancelled && setLabels(body.rows ?? []))
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [params.group])

  const question = useMemo(
    () => questions.find((q) => q.id === route.route.questionId) ?? null,
    [questions, route.route.questionId],
  )
  const report = useMemo(
    () => reports.find((r) => r.id === route.route.reportId) ?? null,
    [reports, route.route.reportId],
  )

  // A URL naming a question we have not resolved yet: show it once the list
  // arrives, without clobbering a view the person has already adjusted.
  useEffect(() => {
    if (question && route.route.questionId === question.id) {
      const same = JSON.stringify(question.params) === JSON.stringify(params) && question.view === view
      if (!same) route.show(question.view, question.params, { questionId: question.id })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [question])
  useEffect(() => {
    if (report && route.route.reportId === report.id) {
      const same = JSON.stringify(report.params) === JSON.stringify(params) && report.view === view
      if (!same) route.show(report.view, report.params, { reportId: report.id })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [report])

  const title = question?.title ?? report?.name ?? describe(view, params)

  return (
    <section className="analytics">
      <aside className="analytics-rail">
        <h2 className="dim">Questions</h2>
        <ul className="question-list">
          {questions.map((q) => (
            <li key={q.id}>
              <button
                className={route.route.questionId === q.id ? 'on' : ''}
                onClick={() => route.show(q.view, q.params, { questionId: q.id })}
                title={q.question}
              >
                {q.title}
              </button>
            </li>
          ))}
        </ul>
        {reports.length > 0 && (
          <>
            <h2 className="dim">Saved</h2>
            <ul className="question-list">
              {reports.map((r) => (
                <li key={r.id}>
                  <button
                    className={route.route.reportId === r.id ? 'on' : ''}
                    onClick={() => route.show(r.view, r.params, { reportId: r.id })}
                    title={r.question || r.name}
                  >
                    {r.name}
                  </button>
                  <button
                    className="linklike remove"
                    title="Delete this saved question"
                    aria-label={`delete ${r.name}`}
                    onClick={() =>
                      apiFetch(`/api/v1/analytics/reports/${r.id}`, { method: 'DELETE' }).then(() =>
                        refreshReports(),
                      )
                    }
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        <h2 className="dim">Build your own</h2>
        <div className="segmented segmented-wrap view-switch">
          {(Object.keys(VIEW_LABEL) as AnalyticsView[]).map((key) => (
            <button key={key} className={view === key ? 'on' : ''} onClick={() => route.adjust({}, key)}>
              {VIEW_LABEL[key]}
            </button>
          ))}
        </div>
        {status && <RollupStatus status={status} timeZone={timeZone} />}
      </aside>

      <div className="analytics-main">
        <header className="analytics-head">
          <h1>{title}</h1>
          {question && <p className="analytics-question">{question.question}</p>}
          {report?.question && <p className="analytics-question">{report.question}</p>}
          {question && (
            <p className="analytics-expect">
              <span className="dim">What a working station shows:</span> {question.expect}
            </p>
          )}
        </header>

        <div className="analytics-controls">
          <div className="segmented segmented-wrap" role="group" aria-label="range">
            {RANGES.map((r) => (
              <button key={r.name} className={params.range === r.name ? 'on' : ''} onClick={() => route.adjust({ range: r.name })}>
                {r.label}
              </button>
            ))}
          </div>
          {view === 'series' && (
            <div className="segmented segmented-wrap" role="group" aria-label="grain">
              {(['day', 'night', 'week', 'month'] as SeriesGrain[]).map((g) => (
                <button key={g} className={(params.grain ?? 'day') === g ? 'on' : ''} onClick={() => route.adjust({ grain: g })}>
                  {g}
                </button>
              ))}
            </div>
          )}
          {view === 'taxa' && (
            <div className="segmented segmented-wrap" role="group" aria-label="grain">
              {(['week', 'month'] as TaxaGrain[]).map((g) => (
                <button key={g} className={(params.grain ?? 'week') === g ? 'on' : ''} onClick={() => route.adjust({ grain: g })}>
                  {g}
                </button>
              ))}
            </div>
          )}
          {view === 'hours' && (
            <div className="segmented segmented-wrap" role="group" aria-label="columns">
              {(['day', 'week'] as ColumnsGrain[]).map((c) => (
                <button key={c} className={(params.columns ?? 'day') === c ? 'on' : ''} onClick={() => route.adjust({ columns: c })}>
                  per {c}
                </button>
              ))}
            </div>
          )}
          <div className="segmented segmented-wrap" role="group" aria-label="group">
            {view === 'series' && (
              <button className={!params.group ? 'on' : ''} onClick={() => route.adjust({ group: null, label: null })}>
                all
              </button>
            )}
            {GROUPS.map((g) => (
              <button key={g} className={params.group === g ? 'on' : ''} onClick={() => route.adjust({ group: g, label: null })}>
                {groupLabel(g)}
              </button>
            ))}
          </div>
          {(view === 'series' || view === 'hours') && (
            <label className="label-picker">
              <span className="dim">label</span>
              <input
                list="analytics-labels"
                value={params.label ?? ''}
                placeholder={params.group === 'bat' ? 'any band' : 'any species'}
                onChange={(event) => route.adjust({ label: event.target.value || null })}
              />
              <datalist id="analytics-labels">
                {labels.map((row) => (
                  <option key={row.label} value={row.label} />
                ))}
              </datalist>
            </label>
          )}
          {view !== 'taxa' && (
            <label className="rate-toggle dim">
              <input type="checkbox" checked={rate} onChange={(event) => setRate(event.target.checked)} /> per
              captured hour
            </label>
          )}
          <span className="grow" />
          <SaveForm view={view} params={params} suggested={title} onSaved={refreshReports} />
        </div>

        <Chart view={view} params={params} rate={rate} onPick={(row) => route.adjust({ group: row.taxonomic_group, label: row.label }, 'series')} />
      </div>
    </section>
  )
}

function describe(view: AnalyticsView, params: AnalyticsParams): string {
  const what = params.label ?? (params.group ? groupLabel(params.group) : 'everything')
  return `${what} ${VIEW_LABEL[view]}`
}

function Chart({
  view,
  params,
  rate,
  onPick,
}: {
  view: AnalyticsView
  params: AnalyticsParams
  rate: boolean
  onPick: (row: TaxaRow) => void
}) {
  if (view === 'series') return <SeriesView params={params} rate={rate} />
  if (view === 'hours') return <HoursView params={params} rate={rate} />
  if (view === 'taxa') return <TaxaView params={params} onPick={onPick} />
  return <SpanView params={params} />
}

function Footer({ payload }: { payload: { note: string; days_built: number; range: { days: number; label: string }; excluded_withdrawn_count: number; excluded_synthetic_count: number; excluded_rejected_count: number } }) {
  const holes = payload.range.days - payload.days_built
  return (
    <div className="analytics-footer dim">
      <span className="mono">
        {payload.range.label} · {payload.days_built} of {payload.range.days} days rolled up
        {holes > 0 && <span className="warn-text"> · {holes} not yet built</span>}
      </span>
      {(payload.excluded_withdrawn_count > 0 || payload.excluded_rejected_count > 0 || payload.excluded_synthetic_count > 0) && (
        <span className="mono">
          {' '}
          · excluded from named counts: {payload.excluded_withdrawn_count} withdrawn, {payload.excluded_rejected_count} rejected
          {payload.excluded_synthetic_count > 0 && `; ${payload.excluded_synthetic_count} synthetic excluded everywhere`}
        </span>
      )}
      <p className="analytics-note">{payload.note}</p>
    </div>
  )
}

function Pending({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) return <p className="warn-text">analytics unavailable: {error}</p>
  if (loading) return <p className="dim">loading…</p>
  return null
}

function SeriesView({ params, rate }: { params: AnalyticsParams; rate: boolean }) {
  const { payload, loading, error } = useAnalyticsData<SeriesPayload>('series', params)
  if (!payload) return <Pending loading={loading} error={error} />
  return (
    <>
      {payload.buckets.length === 0 ? (
        <p className="empty">Nothing rolled up in this range yet.</p>
      ) : (
        <SeriesChart payload={payload} rate={rate} />
      )}
      <Footer payload={payload} />
    </>
  )
}

function HoursView({ params, rate }: { params: AnalyticsParams; rate: boolean }) {
  const { payload, loading, error } = useAnalyticsData<HoursPayload>('hours', params)
  if (!payload) return <Pending loading={loading} error={error} />
  return (
    <>
      {payload.columns.length === 0 ? (
        <p className="empty">Nothing rolled up in this range yet.</p>
      ) : (
        <div className="hours-layout">
          <HoursHeatmap payload={payload} rate={rate} />
          <HourProfile payload={payload} rate={rate} />
        </div>
      )}
      <Footer payload={payload} />
    </>
  )
}

function TaxaView({ params, onPick }: { params: AnalyticsParams; onPick: (row: TaxaRow) => void }) {
  const { payload, loading, error } = useAnalyticsData<TaxaPayload>('taxa', params)
  if (!payload) return <Pending loading={loading} error={error} />
  return (
    <>
      {payload.rows.length === 0 ? (
        <p className="empty">Nothing named in this range yet.</p>
      ) : (
        <TaxaGrid payload={payload} onPick={onPick} />
      )}
      <Footer payload={payload} />
    </>
  )
}

function SpanView({ params }: { params: AnalyticsParams }) {
  const { payload, loading, error } = useAnalyticsData<SpanPayload>('span', params)
  if (!payload) return <Pending loading={loading} error={error} />
  return (
    <>
      {payload.days.length === 0 ? <p className="empty">Nothing rolled up in this range yet.</p> : <SpanChart payload={payload} />}
      <Footer payload={payload} />
    </>
  )
}

function RollupStatus({ status, timeZone }: { status: AnalyticsStatus; timeZone: string }) {
  const built = status.last_built_at
    ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone }).format(Date.parse(status.last_built_at))
    : 'never'
  const stale = status.age_seconds !== null && status.age_seconds > 3 * 3600
  return (
    <p className="rollup-status mono dim" title="The analytics tables are rebuilt from the detection record by an hourly timer on the station. A day that has not been built shows as a hatched hole, never as zero.">
      roll-up: {status.days_built} days, built {built}
      {stale && <span className="warn-text"> (stale)</span>}
      {status.days_missing > 0 && <span className="warn-text"> · {status.days_missing} missing</span>}
      {!status.coordinates_set && <span className="warn-text"> · no coordinates</span>}
    </p>
  )
}

function SaveForm({
  view,
  params,
  suggested,
  onSaved,
}: {
  view: AnalyticsView
  params: AnalyticsParams
  suggested: string
  onSaved: () => void
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  function submit(event: FormEvent) {
    event.preventDefault()
    const body = { name: name || suggested, question: '', view, params }
    apiFetch('/api/v1/analytics/reports', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`save failed: ${r.status}`)
        setOpen(false)
        setName('')
        setError(null)
        onSaved()
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }
  if (!open) {
    return (
      <button className="save-question" onClick={() => setOpen(true)} title="Keep this question on the station, so it is here from any device">
        save this question
      </button>
    )
  }
  return (
    <form className="save-form" onSubmit={submit}>
      <input value={name} placeholder={suggested} onChange={(e) => setName(e.target.value)} aria-label="name for this question" />
      <button type="submit">save</button>
      <button type="button" className="linklike" onClick={() => setOpen(false)}>
        cancel
      </button>
      {error && <span className="warn-text">{error}</span>}
    </form>
  )
}
