/** The Merlin-inspired "best suggestions" list.
 *
 *  Merlin shows one row per candidate species, photo left, name right, and
 *  highlights whichever is calling right now. This keeps that reading order and
 *  the highlight, and adds what a debug surface has to add: which detector said
 *  so, the score as a number rather than only a bar, and an audio clip to check it
 *  against.
 *
 *  Two honesty rules are enforced here, not just documented:
 *   - "score" is never called a probability unless the detector declares itself
 *     calibrated, because BirdNET's sigmoid output is not one;
 *   - the acoustic-activity and ultrasonic detectors are shown as events, styled
 *     differently from species, so a bat "pass" is never read as an identification.
 */

import { useMemo, useState } from 'react'
import type { Detection } from '../types'
import { formatHz } from './Spectrogram'

interface Props {
  detections: Detection[]
  localTimeZone: string
  onSelect: (detection: Detection) => void
  selectedId: string | null
}

type Grouping = 'species' | 'recent'

interface SpeciesRow {
  key: string
  displayName: string
  scientificName: string | null
  group: string
  bestScore: number
  latestScore: number
  count: number
  lastSeen: number
  latest: Detection
  calibrated: boolean
  pluginId: string
  /** Every detection in this group, newest first, so grouping hides nothing. */
  members: Detection[]
}

function groupKey(detection: Detection): string {
  return (
    detection.canonical_taxon_id ??
    detection.scientific_name ??
    `${detection.detector.plugin_id}:${detection.label ?? detection.display_name}`
  )
}

export function Suggestions({ detections, localTimeZone, onSelect, selectedId }: Props) {
  const [grouping, setGrouping] = useState<Grouping>('species')
  const [minScore, setMinScore] = useState(0)
  // Hidden by default. The activity detector fires far more often than anything
  // else, so leaving them in buries the actual identifications on first load. The
  // event stream panel already proves the pipeline is alive.
  const [hideEvents, setHideEvents] = useState(true)
  /** Group keys whose individual detections are expanded. */
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set())

  const filtered = useMemo(
    () =>
      detections.filter(
        (d) =>
          d.score >= minScore &&
          !(hideEvents && d.taxonomic_group === 'acoustic_event'),
      ),
    [detections, minScore, hideEvents],
  )

  const rows = useMemo<SpeciesRow[]>(() => {
    const byKey = new Map<string, SpeciesRow>()
    for (const detection of filtered) {
      const key = groupKey(detection)
      const at = Date.parse(detection.event_start_utc)
      const existing = byKey.get(key)
      if (!existing) {
        byKey.set(key, {
          key,
          displayName: detection.display_name,
          scientificName: detection.scientific_name,
          group: detection.taxonomic_group,
          bestScore: detection.score,
          latestScore: detection.score,
          count: 1,
          lastSeen: at,
          latest: detection,
          calibrated: detection.calibrated_probability !== null,
          pluginId: detection.detector.plugin_id,
          members: [detection],
        })
      } else {
        existing.count += 1
        existing.bestScore = Math.max(existing.bestScore, detection.score)
        existing.members.push(detection)
        if (at >= existing.lastSeen) {
          existing.lastSeen = at
          existing.latestScore = detection.score
          existing.latest = detection
        }
      }
    }
    const list = [...byKey.values()]
    for (const row of list) {
      row.members.sort(
        (a, b) => Date.parse(b.event_start_utc) - Date.parse(a.event_start_utc),
      )
    }
    // "Best suggestions" ranks by how recently it called, then by confidence —
    // the same instinct as Merlin highlighting whatever is singing now.
    list.sort((a, b) => b.lastSeen - a.lastSeen || b.bestScore - a.bestScore)
    return list
  }, [filtered])

  const recent = useMemo(
    () =>
      [...filtered]
        .sort((a, b) => Date.parse(b.event_start_utc) - Date.parse(a.event_start_utc))
        .slice(0, 120),
    [filtered],
  )

  const newestKey = rows[0]?.key
  const timeFormat = useMemo(
    () =>
      new Intl.DateTimeFormat('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        timeZone: localTimeZone,
      }),
    [localTimeZone],
  )

  return (
    <section className="panel suggestions">
      <header className="panel-head">
        <h2>Best suggestions</h2>
        <div className="segmented">
          <button
            className={grouping === 'species' ? 'on' : ''}
            onClick={() => setGrouping('species')}
          >
            Grouped
          </button>
          <button
            className={grouping === 'recent' ? 'on' : ''}
            onClick={() => setGrouping('recent')}
          >
            Timeline
          </button>
        </div>
      </header>

      <div className="suggestions-controls">
        <label>
          min score
          <input
            type="range"
            min={0}
            max={0.95}
            step={0.05}
            value={minScore}
            onChange={(event) => setMinScore(Number(event.target.value))}
          />
          <span className="mono">{minScore.toFixed(2)}</span>
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={hideEvents}
            onChange={(event) => setHideEvents(event.target.checked)}
          />
          hide unidentified events
        </label>
      </div>

      {rows.length === 0 && (
        <p className="empty">
          Nothing yet. The station is listening — unidentified acoustic events are
          {hideEvents ? ' hidden' : ' shown'} by this filter.
        </p>
      )}

      {grouping === 'species' ? (
        <ul className="suggestion-list">
          {rows.map((row) => (
            <li
              key={row.key}
              className={[
                'suggestion',
                row.key === newestKey ? 'newest' : '',
                row.members.some((m) => m.id === selectedId) ? 'selected' : '',
                row.group === 'acoustic_event' ? 'is-event' : '',
                expanded.has(row.key) ? 'expanded' : '',
              ].join(' ')}
              onClick={() => onSelect(row.latest)}
            >
              <div className={`avatar group-${row.group}`} aria-hidden>
                {glyphFor(row.group)}
              </div>
              <div className="suggestion-body">
                <div className="suggestion-title">
                  <span className="name">{row.displayName}</span>
                  {row.count > 1 && (
                    <button
                      className={`count ${expanded.has(row.key) ? 'on' : ''}`}
                      title={`Show all ${row.count} detections in this group`}
                      onClick={(event) => {
                        // Grouping must not make the individual detections
                        // unreachable; expanding beats forcing a view switch.
                        event.stopPropagation()
                        setExpanded((current) => {
                          const next = new Set(current)
                          if (next.has(row.key)) next.delete(row.key)
                          else next.add(row.key)
                          return next
                        })
                      }}
                    >
                      ×{row.count} {expanded.has(row.key) ? '▾' : '▸'}
                    </button>
                  )}
                </div>
                {row.scientificName && <div className="sci">{row.scientificName}</div>}
                <div className="suggestion-meta">
                  <span className="mono">{timeFormat.format(row.lastSeen)}</span>
                  <span className="dot">·</span>
                  <span className="plugin">{row.pluginId}</span>
                  {row.latest.peak_frequency_hz && (
                    <>
                      <span className="dot">·</span>
                      <span className="mono">{formatHz(row.latest.peak_frequency_hz)}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="suggestion-score">
                <div className="score-bar">
                  <span style={{ width: `${Math.round(row.bestScore * 100)}%` }} />
                </div>
                <div className="score-value mono">{(row.bestScore * 100).toFixed(0)}</div>
                <div className="score-caption">
                  {row.calibrated ? 'probability' : 'model score'}
                </div>
              </div>

              {expanded.has(row.key) && (
                <ol className="group-members">
                  {row.members.slice(0, 40).map((member) => (
                    <li
                      key={member.id}
                      className={member.id === selectedId ? 'selected' : ''}
                      onClick={(event) => {
                        event.stopPropagation()
                        onSelect(member)
                      }}
                    >
                      <span className="mono time">
                        {timeFormat.format(Date.parse(member.event_start_utc))}
                      </span>
                      <span className="mono score">{(member.score * 100).toFixed(0)}</span>
                      {member.peak_frequency_hz && (
                        <span className="mono dim">{formatHz(member.peak_frequency_hz)}</span>
                      )}
                      {member.media.length > 0 && (
                        <span className="clip-dot" title="Evidence clip available">
                          ♪
                        </span>
                      )}
                    </li>
                  ))}
                  {row.members.length > 40 && (
                    <li className="dim more">
                      showing the newest 40 of {row.members.length}
                    </li>
                  )}
                </ol>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <ul className="timeline-list">
          {recent.map((detection) => (
            <li
              key={detection.id}
              className={[
                'timeline-row',
                detection.id === selectedId ? 'selected' : '',
                `group-${detection.taxonomic_group}`,
              ].join(' ')}
              onClick={() => onSelect(detection)}
            >
              <span className="mono time">
                {timeFormat.format(Date.parse(detection.event_start_utc))}
              </span>
              <span className="glyph" aria-hidden>
                {glyphFor(detection.taxonomic_group)}
              </span>
              <span className="label">{detection.display_name}</span>
              <span className="mono score">{(detection.score * 100).toFixed(0)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export function glyphFor(group: string): string {
  switch (group) {
    case 'bird':
      return '𝄞'
    case 'bat':
      return '⌇'
    case 'acoustic_event':
      return '◈'
    default:
      return '·'
  }
}
