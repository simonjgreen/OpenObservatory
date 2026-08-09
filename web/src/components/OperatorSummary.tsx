/** Station health, surfaced *by exception* (ADR-028, revised 2026-08-09).
 *
 *  This used to render four cards unconditionally — "yes, on the microphone",
 *  "plenty of room", "all detectors running" — which spent the most valuable
 *  strip of the page telling the operator that nothing needed them. Their
 *  words: "you don't need to be told things are fine and you don't need to do
 *  anything."
 *
 *  So: when everything is nominal this renders nothing at all. The header
 *  already carries a live status dot and the device line, which is a
 *  sufficient "all well" signal, and the underlying detail already has homes
 *  one click away under Diagnostics — `Pipeline`'s "Capture & derivation" and
 *  "Detectors" panels, and "Storage & retention". Nothing is lost by hiding
 *  it here; it was duplicated.
 *
 *  When something *does* need attention, only the cards that are not ok are
 *  shown. A row that appears solely because something is wrong is far louder
 *  than a row that is always present and merely changes colour — which is the
 *  failure mode of every dashboard that trains its operator to ignore it.
 */

import { operatorCards } from '../state/operatorHealth'
import type { StationStatus } from '../types'

export function OperatorSummary({ status }: { status: StationStatus | null }) {
  // A null status is "we do not know yet", not "everything is fine" — but it is
  // also not something the operator can act on, and the header already shows a
  // disconnected state. Stay quiet.
  if (!status) return null

  const attention = operatorCards(status).filter((card) => card.tone !== 'ok')
  if (attention.length === 0) return null

  return (
    <section className="operator-summary needs-attention" aria-label="Station health">
      {attention.map((card) => (
        <div key={card.key} className={`operator-card tone-${card.tone}`}>
          <span className="operator-card-title">{card.title}</span>
          <span className="operator-card-headline">{card.headline}</span>
          <p className="operator-card-detail">{card.detail}</p>
        </div>
      ))}
    </section>
  )
}
