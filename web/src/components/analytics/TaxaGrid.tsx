/** Every label by bucket: the phenology grid. A table, because it is one --
 *  rows a person scans by name, columns they scan by date -- with each cell
 *  carrying its count as well as its shade, per ADR-056: a first-heard date
 *  resting on one detection must look different from one resting on forty. */

import { useState } from 'react'
import { bucketLabel } from '../../analytics/calendar'
import { compact, groupColour, ramp } from '../../analytics/scale'
import type { TaxaPayload, TaxaRow } from '../../analytics/types'

interface Props {
  payload: TaxaPayload
  onPick?: (row: TaxaRow) => void
}

export function TaxaGrid({ payload, onPick }: Props) {
  const [sort, setSort] = useState<'total' | 'first' | 'name'>('total')
  const rows = [...payload.rows].sort((a, b) => {
    if (sort === 'first') return a.first_date.localeCompare(b.first_date) || b.detections - a.detections
    if (sort === 'name') return a.label.localeCompare(b.label)
    return b.detections - a.detections || a.label.localeCompare(b.label)
  })
  const showCounts = payload.columns.length <= 60
  return (
    <div className="taxa-grid">
      <div className="taxa-sort dim">
        sort by{' '}
        <div className="segmented">
          {(['total', 'first', 'name'] as const).map((key) => (
            <button key={key} className={sort === key ? 'on' : ''} onClick={() => setSort(key)}>
              {key === 'first' ? 'first heard' : key}
            </button>
          ))}
        </div>
      </div>
      <div className="taxa-scroll">
        <table>
          <thead>
            <tr>
              <th className="taxa-name">label</th>
              <th className="mono">total</th>
              {payload.columns.map((c) => (
                <th key={c.key} className={`mono taxa-col ${c.days_built === 0 ? 'unbuilt' : ''}`}>
                  <span>{bucketLabel(c.start_date, payload.grain)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const rowMax = Math.max(1, ...row.cells)
              const colour = groupColour(row.taxonomic_group)
              return (
                <tr key={`${row.taxonomic_group}:${row.label}`} onClick={() => onPick?.(row)}>
                  <td className="taxa-name">
                    <i className="dot" style={{ background: colour }} />
                    {row.label}
                    {row.scientific_name && <span className="sci"> {row.scientific_name}</span>}
                  </td>
                  <td className="mono">{compact(row.detections)}</td>
                  {row.cells.map((n, index) => (
                    <td
                      key={payload.columns[index].key}
                      className="mono taxa-cell"
                      style={{ background: ramp(n, rowMax, colour) }}
                      title={`${row.label} · ${bucketLabel(payload.columns[index].start_date, payload.grain)}: ${n} detections`}
                    >
                      {showCounts && n > 0 ? compact(n) : ''}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {payload.total_rows > payload.rows.length && (
        <p className="dim chart-caption">
          {payload.rows.length} of {payload.total_rows} labels shown, most detections first.
        </p>
      )}
    </div>
  )
}
