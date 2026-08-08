#!/usr/bin/env bash
# Deploy the station to the Pi.
#
# Builds the web UI locally (the Pi has no Node toolchain and does not need one),
# syncs the source, installs Python dependencies if they changed, and restarts the
# systemd unit. Idempotent: safe to run repeatedly.
#
#   ./deploy/deploy.sh                 full deploy
#   ./deploy/deploy.sh --no-web        skip the UI build
#   ./deploy/deploy.sh --no-deps       skip pip install
#   HOST=user@host ./deploy/deploy.sh  target a different machine
set -euo pipefail

HOST="${HOST:-station.example}"
REMOTE_DIR="${REMOTE_DIR:-open-observatory}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BUILD_WEB=1
INSTALL_DEPS=1
for arg in "$@"; do
    case "$arg" in
        --no-web)  BUILD_WEB=0 ;;
        --no-deps) INSTALL_DEPS=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"

if [[ "$BUILD_WEB" == 1 ]]; then
    echo "==> building web UI"
    if ! command -v npm >/dev/null; then
        echo "npm not found; re-run with --no-web or install Node" >&2
        exit 1
    fi
    ( cd web && npm install --silent && npm run build )
fi

echo "==> syncing source to $HOST:$REMOTE_DIR"
# With --no-web we do not rebuild `web/dist`, and it is gitignored -- so in a
# fresh clone or a git worktree it does not exist locally at all, and
# `--delete` would remove the *working* UI from the station. A code-only
# redeploy must leave whatever UI is already there alone.
WEB_EXCLUDE=()
if [[ "$BUILD_WEB" != 1 ]]; then
    WEB_EXCLUDE=(--exclude 'web/dist')
fi

rsync -a --delete \
    --exclude '.git' \
    `# Agent worktrees live here and are full checkouts with their own venvs.` \
    `# Syncing them would push gigabytes of duplicate source to the Pi.` \
    --exclude '.claude' \
    "${WEB_EXCLUDE[@]}" \
    --exclude 'data' \
    `# Per-station operator configuration: station name, coordinates, device key.` \
    `# It is gitignored, so it does not exist in the source tree and --delete would` \
    `# remove it from the target on every deploy. It did, once.` \
    --exclude 'config/runtime.env' \
    --exclude '.env' \
    --exclude '.venv' \
    --exclude 'node_modules' \
    --exclude 'web/node_modules' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    --exclude '.mypy_cache' \
    --exclude '.ruff_cache' \
    --exclude 'models/*.tflite' \
    --exclude 'models/*.txt' \
    ./ "$HOST:$REMOTE_DIR/"

if [[ "$INSTALL_DEPS" == 1 ]]; then
    echo "==> installing Python dependencies"
    ssh "$HOST" "cd $REMOTE_DIR && \
        { [ -d .venv ] || python3 -m venv .venv; } && \
        .venv/bin/pip install -q --upgrade pip >/dev/null && \
        .venv/bin/pip install -q -e '.[alsa,resample,birdnet,dev]'"
fi

echo "==> installing systemd unit"
ssh "$HOST" "sudo install -m 644 $REMOTE_DIR/deploy/open-observatory.service \
        /etc/systemd/system/open-observatory.service && \
    sudo install -m 644 $REMOTE_DIR/deploy/99-audiomoth.rules \
        /etc/udev/rules.d/99-audiomoth.rules && \
    sudo udevadm control --reload-rules && \
    sudo systemctl daemon-reload && \
    sudo systemctl enable --now open-observatory.service && \
    sudo systemctl restart open-observatory.service"

echo "==> waiting for health"
for _ in $(seq 1 30); do
    if ssh "$HOST" "curl -fsS http://127.0.0.1:8080/api/v1/health >/dev/null 2>&1"; then
        echo "==> up: http://$HOST:8080"
        ssh "$HOST" "curl -fsS http://127.0.0.1:8080/api/v1/health" | head -c 400
        echo
        exit 0
    fi
    sleep 2
done

echo "!! service did not become healthy; recent logs:" >&2
ssh "$HOST" "sudo journalctl -u open-observatory -n 40 --no-pager" >&2
exit 1
