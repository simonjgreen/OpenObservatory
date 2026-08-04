#!/usr/bin/env bash
set -euo pipefail

cat <<'MSG'
Open this repository in Claude Code and use the following initial instruction:

Read CLAUDE.md and every Markdown file under docs/. Do not write implementation code yet. Produce:
1. a contradictions, gaps and unresolved-hardware-assumptions report;
2. ADRs for any recommended changes;
3. a detailed Milestone 0 implementation checklist;
4. the exact target-device commands needed to probe the attached AudioMoth safely.
Then begin Milestone 0 only, keeping the repository runnable and committing tests and documentation with the implementation.
MSG
