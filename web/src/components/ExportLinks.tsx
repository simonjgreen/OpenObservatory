/** CSV/JSON export for whatever the history view currently shows — the
 *  acceptance-criteria requirement that had no UI before Milestone 4. Reuses
 *  `GET /api/v1/detections/export`, which mirrors the same `window`/
 *  `since`/`until` filters the page's own fetch uses, so "export what I'm
 *  looking at" is literally true rather than approximate.
 *
 *  Plain anchor tags rather than a fetch+blob dance: the endpoint already
 *  sets `Content-Disposition: attachment`, so the browser's native download
 *  handling is simpler and works the same on a phone as a laptop. */

export function buildExportUrl(
  format: 'csv' | 'json',
  filter: { windowName: string; focus: { fromUtc: string; toUtc: string } | null },
): string {
  const params = new URLSearchParams({ format })
  if (filter.focus) {
    params.set('since', filter.focus.fromUtc)
    params.set('until', filter.focus.toUtc)
  } else {
    params.set('window', filter.windowName)
  }
  return `/api/v1/detections/export?${params.toString()}`
}

export function ExportLinks({
  windowName,
  focus,
}: {
  windowName: string
  focus: { fromUtc: string; toUtc: string } | null
}) {
  const filter = { windowName, focus }
  return (
    <div className="export-links" title="Detections in the current window or focused slice, identical filters to what's shown">
      <span className="dim">export</span>
      <a href={buildExportUrl('csv', filter)} download>
        CSV
      </a>
      <a href={buildExportUrl('json', filter)} download>
        JSON
      </a>
    </div>
  )
}
