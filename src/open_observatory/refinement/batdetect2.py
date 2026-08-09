"""The BatDetect2 cascade, as a refiner that may only ever *propose*.

The speed case is settled and was never the question. `ultrasonic-pass-v1` runs
live at 36-40x realtime and decides *when* something happened; BatDetect2 runs at
0.52x realtime and cannot follow a 384 kHz stream, but it never has to — it only
ever sees the 1.5 s already flagged as a pass. Measured on this station's own
clips (ADR-017's 2026-08-05 update): 2.1 s of inference per pass, so the 1015
passes of the night of 2026-08-05 are about 36 minutes of classifier work for a
whole night.

**The accuracy case is not settled, and this module is built around that.**

Offline classification of this station's own 33-36 kHz cluster
(`HANDOVER.md` §6.3 item 6):

* 6 of 8 clips leaned *Myotis*, at 0.20-0.30 — a lean, not an identification;
* one clip returned *Pipistrellus pygmaeus* at **0.77**, on a call whose measured
  peak was **34 kHz**, when soprano pipistrelle peaks near 55 kHz. That is a
  confident answer contradicted by a physical measurement the station made
  itself, which is the same shape of failure as a 0.96 BirdNET score on a
  species absent from the continent (ADR-032): evidence that the score is
  meaningless for that species, not evidence of the bat.
* The AudioMoth gain is hot and still clips on loud nearby events
  (`HANDOVER.md` §6.3 item 4), which is a plausible confound for all of the
  above and has not been eliminated.

A classifier that is confidently wrong about a thing the station can check is not
calibrated on this station's audio, and the charter's honesty constraint —
"never claim more than the evidence supports" — makes the ceiling obvious:
:attr:`authority` is ``"propose"``. This refiner records what BatDetect2 said,
next to what the pass detector measured, as a question for a person. It cannot
change an identification, and ``store.record_refinement`` raises if it tries.

That is not a placeholder for "apply, once we are braver". Charter item 5 lists
"a human ear" as a basis for refinement in its own right; the review table is the
mechanism, and the honest sequence is proposals first, a human listening to the
audible renderings second, and only then any argument that this model has earned
authority over the record on *this* station's audio.

Two smaller honesty decisions, stated because they are easy to get wrong later:

* **Only ``evidence_native`` clips are classified.** A heterodyne rendering has
  thrown away everything outside its tuned band and a time-expanded one is no
  longer at its original rate; classifying either is classifying the renderer.
* **No species-frequency plausibility filter is applied**, tempting as it is
  after the pygmaeus contradiction. This station has no calibrated, sourced
  reference for UK species peak frequencies, and inventing one from memory is
  exactly the class of plausible fabrication this project has been careful to
  avoid (see the favicon note in `HANDOVER.md` §6.3a for the same reasoning
  applied to an icon). Instead the station's *own* measurement —
  ``peak_frequency_hz`` from `ultrasonic-pass-v1` — is recorded next to the
  proposal, so the human doing the review sees the 34 kHz and the *pygmaeus*
  side by side, which is how the contradiction was found in the first place.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import structlog

from .contracts import (
    EvidenceIdentity,
    RefinementBasis,
    RefinementCandidate,
    RefinementOutcome,
    RefinementProposal,
    RefinerUnavailable,
)

log = structlog.get_logger(__name__)

REFINER_ID = "batdetect2-cascade"
REFINER_VERSION = "1"
MODEL_ID = "batdetect2"

#: BatDetect2's own working rate. `scripts/classify_clips_batdetect2.py` uses
#: the same constant; both resample with the project's soxr path rather than
#: BatDetect2's internal librosa, so the offline tool and this refiner see
#: identical audio.
TARGET_SAMPLE_RATE_HZ = 256_000

#: Proposals at or below this det_prob are not recorded as a claim. Deliberately
#: low: this is a noise floor to stop a pass emitting a dozen near-zero species
#: rows, **not** a truth threshold. The station's own measured leans sit at
#: 0.20-0.30 and must survive it, because a low-confidence lean is exactly the
#: kind of thing a human ear should arbitrate.
DEFAULT_MIN_DET_PROB = 0.05

#: Seconds classified, centred on the loudest sample. Trimming is where the
#: cascade's cost actually goes: an untrimmed 6 s evidence clip is mostly
#: pre-roll silence and costs four times as much (ADR-017, 2026-08-05).
DEFAULT_TRIM_S = 1.5


class BatDetect2Refiner:
    """Deferred species classification of stored bat-pass evidence. Proposes only."""

    #: Charter item 5 and the honesty constraint. See the module docstring; do
    #: not change this without the measured evidence that would justify it.
    authority = "propose"
    handles_groups = frozenset({"bat"})

    def __init__(
        self,
        *,
        trim_s: float = DEFAULT_TRIM_S,
        min_det_prob: float = DEFAULT_MIN_DET_PROB,
        threads: int = 2,
        max_species_recorded: int = 3,
    ) -> None:
        self.trim_s = trim_s
        self.min_det_prob = min_det_prob
        self.threads = threads
        self.max_species_recorded = max_species_recorded
        self._model: Any = None
        self._config: Any = None
        self._device: Any = None
        self._api: Any = None
        self._model_version = "unknown"

    @property
    def identity(self) -> EvidenceIdentity:
        """Includes the configuration, so a re-run with different settings counts as new.

        ``model_version`` is the installed BatDetect2 package version, resolved
        in :meth:`prepare`. Before preparation it reads ``"unknown"``, which is
        fine for the "which groups do you handle" question the runner asks first
        and is never what gets written: a refinement is only recorded after a
        successful ``prepare()``.
        """
        return EvidenceIdentity(
            refiner_id=REFINER_ID,
            refiner_version=REFINER_VERSION,
            model_id=MODEL_ID,
            model_version=self._model_version,
            model_sha256=None,
            config={
                "trim_s": self.trim_s,
                "min_det_prob": self.min_det_prob,
                "target_sample_rate_hz": TARGET_SAMPLE_RATE_HZ,
                "max_species_recorded": self.max_species_recorded,
            },
        )

    def prepare(self) -> None:
        """Load the model, or raise :class:`RefinerUnavailable`.

        BatDetect2's code, weights and example recordings are all CC-BY-NC-4.0
        and are never vendored (ADR-006, ADR-017), so "not installed" is a
        normal state of a working station and must be reported as *skipped*, not
        as a refinement pass that found nothing.
        """
        try:
            import torch
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RefinerUnavailable(
                "torch is not installed; see docs/detectors/BATDETECT2_EVALUATION.md"
            ) from exc
        try:
            from batdetect2 import api
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RefinerUnavailable(
                "batdetect2 is not installed; see docs/detectors/BATDETECT2_EVALUATION.md"
            ) from exc

        try:  # pragma: no cover - environment-dependent
            from importlib.metadata import version

            self._model_version = version("batdetect2")
        except Exception:  # pragma: no cover
            self._model_version = "unknown"

        # Bounded on purpose. The unit is already fenced to cores 2-3
        # (AllowedCPUs), but torch defaults to one thread per core and would
        # otherwise spawn four, two of which the fence then refuses to schedule
        # on the cores capture owns -- wasted context switching, not extra work.
        torch.set_num_threads(self.threads)
        self._api = api
        self._model, _params = api.load_model()
        self._config = api.get_config()
        self._device = torch.device("cpu")
        log.info(
            "refinement.batdetect2_ready",
            model_version=self._model_version,
            threads=self.threads,
            trim_s=self.trim_s,
        )

    def close(self) -> None:
        self._model = None
        self._config = None
        self._api = None

    # -- classification -----------------------------------------------------

    def refine(self, candidate: RefinementCandidate) -> RefinementProposal:
        if self._model is None or self._api is None:
            raise RuntimeError("BatDetect2Refiner.refine called before prepare()")

        clip = candidate.clip_path
        if clip is None or not clip.exists():
            # Never conflated with "found nothing". The charter's retention
            # safeguard exists precisely because "the refiner never saw it" and
            # "the refiner could not improve it" look identical from the outside.
            return RefinementProposal(
                outcome=RefinementOutcome.UNAVAILABLE,
                basis=RefinementBasis.NEW_MODEL,
                reason=(
                    f"no native evidence clip on disk at {clip}"
                    if clip
                    else "no native evidence clip is associated with this detection"
                ),
                evidence={"clip_present": False},
            )

        pcm, native_rate, duration_s = self._load(clip)
        ranked = self._classify(pcm)

        measured = {
            "classified_audio_s": round(duration_s, 4),
            "clip_native_sample_rate_hz": native_rate,
            "trim_s": self.trim_s,
            # The station's own physical measurements, carried alongside the
            # model's opinion so a reviewer can see both at once. This pairing
            # is what exposed the 0.77 P. pygmaeus on a 34 kHz call.
            "our_peak_frequency_hz": candidate.peak_frequency_hz,
            "our_score": round(candidate.score, 4),
            "our_peak_snr_db": candidate.native_result.get("peak_snr_db"),
            "our_pulse_count": candidate.native_result.get("pulse_count"),
            "species_ranked": [
                {"species": name, "det_prob": round(prob, 4)} for name, prob in ranked
            ],
            "distinct_species_named": len(ranked),
        }

        if not ranked:
            return RefinementProposal(
                outcome=RefinementOutcome.NO_CHANGE,
                basis=RefinementBasis.NEW_MODEL,
                reason=(
                    f"BatDetect2 {self._model_version} found no calls above "
                    f"det_prob {self.min_det_prob} in {duration_s:.2f}s of clip audio; "
                    "the original pass record is unchanged"
                ),
                evidence=measured,
            )

        top_species, top_prob = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else None
        caution = self._caution(candidate, top_species, top_prob, runner_up)

        if (
            candidate.scientific_name is not None
            and candidate.scientific_name.lower() == top_species.lower()
        ):
            return RefinementProposal(
                outcome=RefinementOutcome.CONFIRMED,
                basis=RefinementBasis.NEW_MODEL,
                reason=(
                    f"BatDetect2 {self._model_version} independently reaches the stored "
                    f"identification {top_species} at det_prob {top_prob:.2f}. {caution}"
                ),
                proposed_scientific_name=top_species,
                proposed_score=round(top_prob, 4),
                evidence={**measured, "caution": caution},
            )

        return RefinementProposal(
            outcome=RefinementOutcome.PROPOSED,
            basis=RefinementBasis.NEW_MODEL,
            reason=(
                f"BatDetect2 {self._model_version} suggests {top_species} at det_prob "
                f"{top_prob:.2f} for an event the station recorded only as "
                f"{candidate.common_name or candidate.taxonomic_group!r}. "
                "This is a proposal for human review, not a station identification. "
                f"{caution}"
            ),
            proposed_scientific_name=top_species,
            proposed_rank="species",
            proposed_taxonomic_group=candidate.taxonomic_group,
            proposed_score=round(top_prob, 4),
            evidence={**measured, "caution": caution, "needs_human_ear": True},
        )

    # -- helpers ------------------------------------------------------------

    def _load(self, clip: Any) -> tuple[np.ndarray, int, float]:
        """Read, downmix, trim to the pass, and resample to BatDetect2's rate."""
        import soundfile as sf

        from ..audio.resample import AudibleResampler

        pcm, native_rate = sf.read(str(clip), dtype="float32", always_2d=False)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1).astype("float32")
        width = int(self.trim_s * native_rate)
        if self.trim_s > 0.0 and pcm.shape[0] > width > 0:
            centre = int(np.abs(pcm).argmax())
            start = max(0, min(centre - width // 2, pcm.shape[0] - width))
            pcm = pcm[start : start + width]
        duration_s = pcm.shape[0] / native_rate if native_rate else 0.0
        if native_rate != TARGET_SAMPLE_RATE_HZ:
            pcm = AudibleResampler(native_rate, TARGET_SAMPLE_RATE_HZ).process(pcm).pcm
        return np.asarray(pcm, dtype="float32"), int(native_rate), duration_s

    def _classify(self, pcm: np.ndarray) -> list[tuple[str, float]]:
        """Best call per species, ranked, above the noise floor.

        A pass contains many calls and BatDetect2's raw prediction list is
        dominated by repeats of the same species, so the list is collapsed to
        each species' best call — the same collapse
        `scripts/classify_clips_batdetect2.py` does, so the offline tool and this
        refiner cannot disagree about what the model said.
        """
        predictions, _features, _spec = self._api.process_audio(
            pcm,
            samp_rate=TARGET_SAMPLE_RATE_HZ,
            model=self._model,
            config=self._config,
            device=self._device,
        )
        best: dict[str, float] = {}
        for pred in predictions:
            name = str(pred.get("class", "")) or "?"
            prob = float(pred.get("det_prob", 0.0))
            if prob <= self.min_det_prob:
                continue
            best[name] = max(best.get(name, 0.0), prob)
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[: self.max_species_recorded]

    def _caution(
        self,
        candidate: RefinementCandidate,
        species: str,
        prob: float,
        runner_up: float | None,
    ) -> str:
        """The honest health warning that travels with every proposal.

        Assembled from things actually measured, never from a species reference
        table this station does not have.
        """
        parts: list[str] = []
        if prob < 0.5:
            parts.append(
                f"det_prob {prob:.2f} is a lean, not an identification "
                "(BatDetect2's det_prob is not a calibrated probability)"
            )
        if runner_up is not None and prob - runner_up < 0.15:
            parts.append(
                f"the runner-up species is within {prob - runner_up:.2f} of it, so the "
                "model is not separating species on this clip"
            )
        if candidate.peak_frequency_hz:
            parts.append(
                f"this station measured the call's peak at "
                f"{candidate.peak_frequency_hz / 1000.0:.1f} kHz — check the species "
                "against that before accepting; a confident BatDetect2 answer has "
                "already contradicted this station's own peak measurement once "
                "(HANDOVER.md §6.3 item 6)"
            )
        parts.append(
            "the AudioMoth gain is hot and still clips on loud nearby events, which is "
            "an unresolved confound for every ultrasonic classification here"
        )
        return " Caution: " + "; ".join(parts) + "."
