/** The calm default view (ADR-028): four plain-language cards answering what
 *  an operator actually wants to know, ahead of anything a dashboard would
 *  usually lead with. No scores, no queue depths, no jargon — those live one
 *  click away behind "Diagnostics" (see `App.tsx`'s `useViewMode`). */

import { operatorCards } from '../state/operatorHealth'
import type { StationStatus } from '../types'

export function OperatorSummary({ status }: { status: StationStatus | null }) {
  const cards = operatorCards(status)
  return (
    <section className="operator-summary" aria-label="Station health">
      {cards.map((card) => (
        <div key={card.key} className={`operator-card tone-${card.tone}`}>
          <span className="operator-card-title">{card.title}</span>
          <span className="operator-card-headline">{card.headline}</span>
          <p className="operator-card-detail">{card.detail}</p>
        </div>
      ))}
    </section>
  )
}
