# ADR-002: Highest practical native capture rate with derived audible stream
**Decision:** Capture at the highest target-supported rate needed by ultrasonic analysis, then downsample for audible models.

**Reason:** Upsampling a 48 kHz stream cannot recover bat ultrasound. Downsampling a high-rate authoritative stream preserves both use cases.

**Caveat:** Actual AudioMoth/Linux mode and Pi stability must be measured. Audible-only fallback is required.
