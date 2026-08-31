/** Which models this station holds, and under what licence (ADR-006).
 *
 *  ADR-006 keeps third-party model assets out of the repository because a
 *  model's licence is not the code's licence — BirdNET's released
 *  checkpoints are CC-BY-NC-SA-4.0 against Apache-2.0 code, and BatDetect2
 *  is CC-BY-NC-4.0 for code, weights and example audio alike. The rule it
 *  set was that acquisition is a deliberate, attributable operator act and
 *  that the terms are "surfaced in `/api/v1/models` and in the UI"
 *  (ADR-017). The API half shipped; nothing in this application ever
 *  fetched that endpoint, so the UI half was a promise, not a feature. This
 *  panel is the UI half.
 *
 *    GET /api/v1/models ->
 *      {
 *        "model_dir": "/opt/open-observatory/models",
 *        "assets": [
 *          { "kind": "file", "filename": "birdnet.tflite",
 *            "licence": "CC-BY-NC-SA-4.0", "source_url": "https://...",
 *            "expected_sha256": "...", "installed": true, "verified": true,
 *            "size_bytes": 51720768 },
 *          { "kind": "package", "name": "batdetect2", "version": "1.3.1",
 *            "licence": "CC-BY-NC-4.0", "source_url": "https://...",
 *            "install_command": "pip install batdetect2==1.3.1",
 *            "used_for": "...", "installed": false }
 *        ],
 *        "note": "..."
 *      }
 *
 *  **`kind` is read optionally.** A station on an older build sends neither
 *  it nor any package entry — every asset there is a checksummed file — so
 *  its absence is inferred from the checksum rather than rendered as a gap.
 *
 *  **What this panel must never do is overstate a licence claim**, which is
 *  a subtler failure than showing nothing at all:
 *
 *  - an asset that is not installed is labelled as such, and its licence is
 *    shown as the terms that *would* apply, because that is the disclosure
 *    ADR-017 wants made *before* download;
 *  - a file whose checksum does not match is not the asset the manifest
 *    describes, so the licence beside it may not be its licence, and it says
 *    so rather than showing a confident licence name over unknown bytes;
 *  - a package entry has no checksum, so the word "verified" — which for a
 *    file means a digest matched — is never borrowed for it;
 *  - a station that cannot answer says why. An empty panel would read as
 *    "no licences apply", which is the one answer that is never true.
 *
 *  Rendered in the diagnose depth only (ADR-028) and fetched once on mount,
 *  so it costs the station nothing in its normal state of nobody at a
 *  browser.
 */

import { useEffect, useState } from 'react'

import { apiFetch } from '../api'

export type AssetKind = 'file' | 'package' | 'unknown'

export interface ModelAsset {
  /** `"file"` or `"package"`. Absent on a station predating the
   *  distinction, where every asset is a checksummed file. */
  kind?: string
  /** File assets. */
  filename?: string
  /** Package assets: the import/distribution name. `package`/`package_name`
   *  are accepted as aliases so a rename on the station side degrades to a
   *  name that is merely stale rather than to "(unnamed asset)". */
  name?: string
  package?: string
  package_name?: string
  /** Package assets: the pinned version, which is the only integrity claim
   *  a pip install supports. */
  version?: string
  /** Package assets: what the station uses it for, so a licence is attached
   *  to a purpose rather than floating on its own. */
  used_for?: string
  licence?: string
  source_url?: string
  expected_sha256?: string | null
  /** How a package is acquired, since no checksummed download exists for it. */
  install_command?: string | null
  command?: string | null
  installed?: boolean
  /** File assets only. A package carries no digest, so this is absent there
   *  and must not be invented — see `installationStatus`. */
  verified?: boolean
  size_bytes?: number | null
}

export interface ModelsResponse {
  model_dir?: string
  assets: ModelAsset[]
  note?: string
}

/** What sort of asset this is, believing the station first and inferring
 *  only where it is silent. A checksum means a file: that is what a digest
 *  is for. Nothing to infer from is reported as `unknown` rather than
 *  guessed, because the two kinds make different claims about verification. */
export function assetKind(asset: ModelAsset): AssetKind {
  if (asset.kind === 'file' || asset.kind === 'package') return asset.kind
  if (asset.expected_sha256) return 'file'
  if (asset.package || asset.package_name || acquisitionCommand(asset)) return 'package'
  return 'unknown'
}

export function assetName(asset: ModelAsset): string {
  return (
    asset.filename ?? asset.name ?? asset.package ?? asset.package_name ?? '(unnamed asset)'
  )
}

/** The command that acquires a package asset, or `null` for a file (which
 *  is fetched and checksummed by `oo models fetch` instead). */
export function acquisitionCommand(asset: ModelAsset): string | null {
  return asset.install_command ?? asset.command ?? null
}

export interface InstallationStatus {
  text: string
  tone: 'ok' | 'warn' | 'danger' | 'dim'
  /** The sentence that stops the line above being read as more than it is.
   *  `null` only where the short label is already the whole truth. */
  caveat: string | null
}

export function installationStatus(asset: ModelAsset): InstallationStatus {
  const kind = assetKind(asset)
  if (!asset.installed) {
    return {
      text: 'not installed',
      tone: 'dim',
      caveat: 'Not on this station. These are the terms that would apply if it were fetched.',
    }
  }
  if (kind === 'package') {
    return {
      text: 'installed',
      tone: 'ok',
      caveat:
        'Installed as a package, so this station holds no checksum for it and cannot verify ' +
        'that what is installed is what the licence above describes. It reports only that ' +
        'the module can be imported — not that the installed version is the pinned one.',
    }
  }
  if (kind === 'unknown') {
    return {
      text: asset.verified ? 'installed, reported verified' : 'installed, not verified',
      tone: asset.verified ? 'ok' : 'warn',
      caveat:
        'This station does not report whether this asset is a file or a package, so what ' +
        '"verified" covers here is not known.',
    }
  }
  if (asset.verified) {
    return { text: 'installed, checksum verified', tone: 'ok', caveat: null }
  }
  return {
    text: 'installed, checksum does not match',
    tone: 'danger',
    caveat:
      'The bytes on disk are not the asset this manifest describes, so the licence named ' +
      'here may not be the licence of what is installed.',
  }
}

function kindLabel(kind: AssetKind): string {
  if (kind === 'file') return 'downloaded file'
  if (kind === 'package') return 'installed package'
  return 'not reported'
}

function shortDigest(digest: string): string {
  return digest.length > 16 ? `${digest.slice(0, 16)}…` : digest
}

type PanelState = 'checking' | 'ready' | 'absent' | 'unauthorised' | 'error'

export function ModelsPanel() {
  const [models, setModels] = useState<ModelsResponse | null>(null)
  const [state, setState] = useState<PanelState>('checking')
  const [httpStatus, setHttpStatus] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/v1/models')
      .then(async (response) => {
        if (cancelled) return
        if (response.ok) {
          const data = (await response.json()) as ModelsResponse
          if (cancelled) return
          setModels({ ...data, assets: Array.isArray(data.assets) ? data.assets : [] })
          setState('ready')
          return
        }
        setHttpStatus(response.status)
        if (response.status === 404) setState('absent')
        else if (response.status === 401 || response.status === 403) setState('unauthorised')
        else setState('error')
      })
      .catch(() => {
        if (!cancelled) setState('error')
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Models &amp; licences</h2>
      </header>

      {state === 'checking' && <p className="empty">checking…</p>}

      {state === 'absent' && (
        <p className="empty">
          Not available — this station's API does not serve
          <span className="mono"> /api/v1/models</span>, so no model licence can be shown
          here. Run <span className="mono">oo models status</span> on the station for the
          same information.
        </p>
      )}

      {state === 'unauthorised' && (
        <p className="empty">
          Sign in to see which models are installed and under what licence — this station
          requires authentication for <span className="mono">/api/v1/models</span>.
        </p>
      )}

      {state === 'error' && (
        <p className="empty">
          Could not read model licences from this station
          {httpStatus === null ? '' : ` (HTTP ${httpStatus})`}. This is a failure to ask,
          not a statement that no licence applies.
        </p>
      )}

      {state === 'ready' && models !== null && models.assets.length === 0 && (
        <p className="empty">
          No model assets are listed by this station. Its detectors are running on nothing
          this manifest knows about, so no licence can be attributed here.
        </p>
      )}

      {state === 'ready' && models !== null && models.assets.length > 0 && (
        <ul className="models">
          {models.assets.map((asset, index) => {
            const name = assetName(asset)
            const kind = assetKind(asset)
            const status = installationStatus(asset)
            const command = acquisitionCommand(asset)
            return (
              <li key={`${name}-${index}`} className="model-asset">
                <div className="model-asset-head">
                  <span className="mono model-asset-name">{name}</span>
                  {asset.version && (
                    // "pinned", not a bare version number: the station reports
                    // the version its registry pins, and checks only that the
                    // module imports — never which version is actually there.
                    <span
                      className="mono dim model-asset-version"
                      title="The version this station pins. It does not report which version is installed."
                    >
                      pinned {asset.version}
                    </span>
                  )}
                  <span className={`model-asset-state tone-${status.tone}`}>{status.text}</span>
                </div>

                <p className="model-asset-line">
                  licence{' '}
                  <strong className="model-asset-licence">{asset.licence || 'not stated'}</strong>{' '}
                  · {kindLabel(kind)}
                </p>

                {/* The station's own sentence on what it uses this for. A
                    licence with no purpose beside it is the disclosure ADR-006
                    asks for without the context that makes it actionable —
                    non-commercial terms matter differently for a model in the
                    live path than for one that only annotates stored clips. */}
                {asset.used_for && <p className="model-asset-line">{asset.used_for}</p>}

                {kind === 'package' ? (
                  <p className="model-asset-line">
                    acquire with <code className="mono">{command ?? 'not reported'}</code>
                  </p>
                ) : (
                  <p className="model-asset-line">
                    sha256{' '}
                    <code className="mono">
                      {asset.expected_sha256
                        ? shortDigest(asset.expected_sha256)
                        : 'not reported'}
                    </code>
                  </p>
                )}

                <p className="model-asset-line">
                  {asset.source_url ? (
                    <a href={asset.source_url} target="_blank" rel="noreferrer">
                      {asset.source_url}
                    </a>
                  ) : (
                    'no source recorded'
                  )}
                </p>

                {status.caveat && (
                  <p className={`model-asset-line ${status.tone === 'danger' ? 'warn-text' : ''}`}>
                    {status.caveat}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {state === 'ready' && models?.note && (
        <p className="dim panel-caption models-note">
          {models.note}
          {models.model_dir ? ' ' : ''}
          {models.model_dir && <span className="mono">{models.model_dir}</span>}
        </p>
      )}
    </section>
  )
}
