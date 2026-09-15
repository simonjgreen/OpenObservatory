#!/usr/bin/env bash
# Move the station's SQLite database from the SD card to the evidence SSD
# (ADR-078), exactly as docs/operations/DEPLOYMENT_AND_OPERATIONS.md,
# "Database on the evidence SSD", describes: stop, copy, check, repoint,
# start, verify. Run from the repository root on the laptop, AFTER
# deploy/deploy.sh has put code that knows the setting on the station:
#
#     HOST=user@host ./deploy/move-database-to-ssd.sh
#
# Capture is stopped for one copy of the database plus one check. Measured
# on the live station at idle priority: 459 s for a 2.47 GB VACUUM INTO and
# 150 s for quick_check; stopped and un-niced it is faster, but budget five
# to ten minutes of visible gap. The remote half runs detached under nohup
# with its own log, so a dropped SSH session cannot leave the station half
# moved; this side polls the log until it ends.
#
# Renames the original rather than deleting it, so rollback is a rename and
# a two-line edit (see the runbook). Refuses to run twice: if runtime.env
# already names a database DSN it stops before doing anything.
set -euo pipefail

: "${HOST:?set HOST=user@host (ADR-047: the repository ships no station address)}"
REMOTE_DIR="${REMOTE_DIR:-open-observatory}"
STAMP="${STAMP:-$(date -u +%Y-%m-%d)}"
SSH=(ssh -o ServerAliveInterval=15 -o ServerAliveCountMax=4 "$HOST")

REMOTE_SCRIPT=$(cat <<'EOS'
set -euo pipefail
cd "$HOME/$REMOTE_DIR"
OO=.venv/bin/oo
NEW_REL="data/clips/database/openobservatory.sqlite"
NEW_ABS="$PWD/$NEW_REL"
STATION_UNITS="open-observatory-refine.timer open-observatory-analytics.timer open-observatory-refine.service open-observatory-analytics.service open-observatory.service"

fail() { echo "!! $*" >&2; exit 2; }

echo "==> pre-flight"
grep -qE '^OO_DATABASE_DSN=.+' config/runtime.env 2>/dev/null && fail "config/runtime.env already names a database DSN; nothing done"
$OO db --help >/dev/null 2>&1 || fail "this station's code has no 'oo db'; run deploy/deploy.sh first"
systemctl cat open-observatory-analytics.timer >/dev/null 2>&1 || fail "the analytics units are not installed; run deploy/deploy.sh first"
findmnt -n data/clips >/dev/null || fail "data/clips is not a mount point; the SSD is not where ADR-021 says"
HEAD=$(.venv/bin/python -m alembic heads 2>/dev/null | awk 'NR==1 {print $1}')
[ -n "$HEAD" ] || fail "could not read the Alembic head"
echo "  alembic head: $HEAD"

echo "==> stopping every writer, timers and the runs they may have started"
# shellcheck disable=SC2086
sudo systemctl stop $STATION_UNITS
# A stopped unit reads "inactive", or "failed" when the process answered the
# stop signal with a non-zero exit (the station does); both mean not running.
for unit in open-observatory-analytics.service open-observatory-refine.service open-observatory.service; do
    state=$(systemctl is-active "$unit" || true)
    case "$state" in inactive|failed) ;; *) fail "$unit is still $state" ;; esac
done
STOPPED_AT=$(date +%s)

echo "==> facts about the original (closing this connection checkpoints its WAL: -wal/-shm vanishing is expected)"
$OO db status --json > /tmp/db-before.json
python3 - "$HEAD" <<'PY'
import json, sys
d = json.load(open("/tmp/db-before.json"))
print("  original:", d["path"], d["bytes"], "bytes; revision", d["alembic_revision"], "; rows", d["row_counts"])
assert d["alembic_revision"] == sys.argv[1], f"original is at {d['alembic_revision']}, head is {sys.argv[1]}: deploy.sh has not migrated it"
PY

echo "==> consistent copy onto the SSD"
mkdir -p "$(dirname "$NEW_REL")"
$OO db copy --force "$NEW_REL"

echo "==> checking the copy"
$OO db check --json "$NEW_REL" > /tmp/db-copy-check.json
python3 - <<'PY'
import json
before = json.load(open("/tmp/db-before.json"))
after = json.load(open("/tmp/db-copy-check.json"))
assert after["ok"], f"quick_check failed: {after['problems']}"
assert after["row_counts"] == before["row_counts"], f"row counts differ: {before['row_counts']} vs {after['row_counts']}"
assert after["alembic_revision"] == before["alembic_revision"], "revision differs between original and copy"
print("  quick_check ok in", after["elapsed_s"], "s; revision", after["alembic_revision"], "; row counts match:", after["row_counts"])
PY
# VACUUM INTO does not fsync what it wrote; the check above read it back
# through the page cache. Make the verdict true of what is on the disk.
sync -f "$NEW_REL"

echo "==> repointing config/runtime.env and retiring the original"
[ -s config/runtime.env ] && [ -n "$(tail -c1 config/runtime.env)" ] && echo >> config/runtime.env
printf '%s\n' \
    "# ADR-078 ($STAMP): the record lives on the evidence SSD; the station refuses a database on the SD card." \
    "OO_DATABASE_DSN=sqlite+pysqlite:///$NEW_ABS" \
    "OO_DATABASE_REQUIRE_MOUNT=true" >> config/runtime.env
mv data/openobservatory.sqlite "data/openobservatory.sqlite.moved-$STAMP"
for f in data/openobservatory.sqlite-wal data/openobservatory.sqlite-shm; do
    [ -e "$f" ] && mv "$f" "$f.moved-$STAMP"
done

echo "==> starting the station"
sudo systemctl start open-observatory.service
healthy=0
for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8080/api/v1/health >/dev/null 2>&1; then healthy=1; break; fi
    sleep 2
done
STARTED_AT=$(date +%s)
if [ "$healthy" != 1 ]; then
    echo "!! not healthy after 120 s; recent journal:" >&2
    journalctl -u open-observatory -n 40 --no-pager >&2 || true
    exit 1
fi
echo "  stop to healthy: $((STARTED_AT - STOPPED_AT)) s"
sudo systemctl start open-observatory-refine.timer open-observatory-analytics.timer

echo "==> verifying from both sides"
$OO db status --json | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  oo db status:", {k: d[k] for k in ("path","mount_point","on_system_disk")}); print("  rows:", d["row_counts"])'
curl -s http://127.0.0.1:8080/api/v1/health | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  health:", d["status"], d["database"])'
journalctl -u open-observatory --since "15 min ago" --no-pager | grep -E "db.engine_ready|db.schema_at_head|Refusing to open" | tail -3 | sed "s/^/  /" || true
echo "==> done"
EOS
)

echo "==> handing the move to the station (log: /tmp/db-move.log there)"
printf '%s\n' "$REMOTE_SCRIPT" | "${SSH[@]}" "cat > /tmp/db-move-remote.sh"
"${SSH[@]}" "REMOTE_DIR='$REMOTE_DIR' STAMP='$STAMP' nohup bash /tmp/db-move-remote.sh > /tmp/db-move.log 2>&1 < /dev/null &"

# Poll rather than hold a session open: the move survives this side dropping.
while ! "${SSH[@]}" "grep -qE '^(==> done|!!)' /tmp/db-move.log 2>/dev/null"; do
    sleep 5
done
"${SSH[@]}" "cat /tmp/db-move.log"
"${SSH[@]}" "grep -q '^==> done' /tmp/db-move.log"
