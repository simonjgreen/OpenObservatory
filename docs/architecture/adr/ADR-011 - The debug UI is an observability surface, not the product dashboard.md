# ADR-011: The debug UI is an observability surface, not the product dashboard
**Decision:** The real-time debug UI reads only from the API and the live WebSocket, shows
raw pipeline state alongside detections, and is allowed to expose internals (frame counts,
monotonic offsets, queue depth, drop counters, detector lag) that the Milestone 4 product
dashboard would hide.

**Reason:** It exists to prove and diagnose Milestones 1–3 on real hardware. Keeping it
separate stops diagnostic affordances leaking into the product surface and stops product
polish being mistaken for pipeline correctness.

**See also:** ADR-012 for the transport it reads from. **Partly superseded by ADR-016**,
which makes this UI the foundation of the Milestone 4 product dashboard rather than a
surface to be replaced by it. The separation of *concerns* below still holds; the
separation of *codebases* does not.
