import { useMemo, useState } from 'react'

import type { Detection } from './types'
import { Header } from './components/Header'
import { Spectrogram } from './components/Spectrogram'
import { Suggestions } from './components/Suggestions'
import { LevelMeter, ListenControl } from './components/Meters'
import { CapturePanel, DetectorPanel, EventLog, StoragePanel } from './components/Pipeline'
import { DetectionDrawer } from './components/DetectionDrawer'
import { FirmwarePanel } from './components/FirmwarePanel'
import { FirstRun } from './components/FirstRun'
import { History } from './components/History'
import { NearMissPanel } from './components/NearMissPanel'
import { ChangePasswordGate, Login } from './components/Login'
import { OperatorSummary } from './components/OperatorSummary'
import { PauseBanner, PauseControl } from './components/PauseControl'
import { RetentionPanel } from './components/RetentionPanel'
import { SettingsPanel } from './components/SettingsPanel'
import { estimatePlayhead } from './components/playhead'

import { useAuth } from './hooks/useAuth'
import { useClock } from './hooks/useClock'
import { useFirstRun } from './hooks/useFirstRun'
import { useHistoryBrowser } from './hooks/useHistoryBrowser'
import { useLiveAudio } from './hooks/useLiveAudio'
import { useLiveConnection } from './hooks/useLiveConnection'
import { usePause } from './hooks/usePause'
import { useSpectrogramControls } from './hooks/useSpectrogramControls'
import { useViewMode } from './hooks/useViewMode'

/** Top level of the station UI.
 *
 *  ADR-016/024: one application, two depths. `useViewMode` decides which —
 *  "operate" is the calm default (`OperatorSummary` plus the spectrogram and
 *  species list), "diagnose" additionally reveals the pipeline internals
 *  (`CapturePanel`/`DetectorPanel`/`StoragePanel`/`EventLog`, and the
 *  header's raw stat row) that used to be the whole page. Nothing is
 *  deleted — see ADR-011's retained constraint — it is reorganised behind an
 *  explicit toggle instead of shown unconditionally.
 *
 *  Everything that used to be direct `useState` here (about 25 hooks, per
 *  ADR-016/024's prerequisite note) now lives in `hooks/*`, grouped by
 *  concern: the live socket, spectrogram display controls, history
 *  browsing, and the audio monitor. This component is left holding only
 *  what genuinely crosses those concerns — the selected detection for the
 *  drawer, and view/diagnostics depth.
 */
export default function App() {
  const auth = useAuth()
  const clock = useClock()
  const live = useLiveConnection()
  const spectrogramControls = useSpectrogramControls(live.specs)
  const history = useHistoryBrowser()
  const audio = useLiveAudio()
  const view = useViewMode()
  const firstRun = useFirstRun()
  // ADR-055. Fed the live status frame so a pause set from another device
  // appears here within a tick, and `clock` so the countdown ticks without a
  // second timer.
  const pause = usePause(live.status?.pause, clock)

  const [selected, setSelected] = useState<Detection | null>(null)
  const [showSettings, setShowSettings] = useState(false)

  // Where the sound now leaving the speakers sits on the spectrogram's own
  // timeline (ADR-051). Computed here because it is the one place that holds
  // both halves: the media element's telemetry (`useLiveAudio`) and the
  // browser-to-station clock skew measured on the live socket
  // (`useLiveConnection`). `null` whenever nobody is listening, so the
  // spectrogram draws no marker and starts no timer.
  const playhead = useMemo(
    () =>
      audio.status === 'playing' && audio.telemetry
        ? estimatePlayhead(audio.telemetry, live.clockSkewS)
        : null,
    [audio.status, audio.telemetry, live.clockSkewS],
  )

  const timeZone = live.status?.station.timezone ?? 'UTC'
  const diagnosing = view.depth === 'diagnose'

  // ADR-034: while auth is probing (`'checking'`), render nothing rather
  // than flashing the full app or the login form -- both are wrong for
  // roughly one request's worth of time. `'anonymous'` (auth_enabled=false,
  // the shipped default) falls through to the app exactly as before this
  // feature existed.
  if (auth.status === 'checking') {
    return <div className="app-loading" />
  }
  if (auth.status === 'login-required') {
    return <Login auth={auth} />
  }
  if (auth.status === 'authenticated' && auth.mustChangePassword) {
    return <ChangePasswordGate auth={auth} />
  }

  return (
    <div className="app">
      <Header
        status={live.status}
        connection={live.connection}
        clock={clock}
        localTimeZone={timeZone}
        showDiagnostics={diagnosing}
      >
        {/* First in the header's controls, before the view switches: it is a
            privacy control, not a preference, and the moment it is wanted is
            not a moment to go looking for it. */}
        <PauseControl pause={pause} timeZone={timeZone} />
        <div className="segmented mode-switch">
          <button
            className={history.mode === 'live' ? 'on' : ''}
            onClick={() => history.setMode('live')}
          >
            LIVE
          </button>
          <button
            className={history.mode === 'history' ? 'on' : ''}
            onClick={() => history.setMode('history')}
            title="Browse persisted detections and evidence from earlier, including overnight"
          >
            HISTORY
          </button>
        </div>
        <button
          className={`diagnostics-toggle ${showSettings ? 'on' : ''}`}
          onClick={() => setShowSettings((open) => !open)}
          title="Everything this station can be configured with: identity, location, capture, spectrogram contrast, detector thresholds, clips, retention, MQTT. Stored on the device, never in the repository."
        >
          settings
        </button>
        <button
          className={`diagnostics-toggle ${diagnosing ? 'on' : ''}`}
          onClick={() => view.toggle()}
          title="Pipeline internals: frame counts, queue depth, drop counters, detector lag. Never a substitute for the measurements above it."
        >
          {diagnosing ? 'diagnostics: on' : 'diagnostics'}
        </button>
        <ListenControl
          status={audio.status}
          telemetry={audio.telemetry}
          volume={audio.volume}
          onToggle={audio.toggle}
          onVolume={audio.changeVolume}
          detail={audio.detail}
          channel={audio.channel}
          onChannel={audio.changeChannel}
          tuneHz={audio.tuneHz}
          onTuneHz={audio.changeTuneHz}
          hello={audio.hello}
        />
        {auth.status === 'authenticated' && (
          <button
            className="logout-button"
            title={`Signed in as ${auth.username ?? 'operator'}`}
            onClick={() => auth.logout()}
          >
            sign out
          </button>
        )}
      </Header>

      {/* Above everything, including the first-run flow: while this is on, it
          is the most important true thing about the station. */}
      <PauseBanner pause={pause} timeZone={timeZone} />

      {/* Day one: guide rather than fail. The panel appears only while the
          station itself reports required questions outstanding, and its
          dismissal is recorded on the station, not in this browser. */}
      {firstRun.offer && !showSettings && (
        <FirstRun
          onClose={() => {
            firstRun.dismiss()
            firstRun.refresh()
          }}
          onSaved={() => firstRun.refresh()}
          onOpenSettings={() => setShowSettings(true)}
        />
      )}

      {live.status && live.status.station.location_configured === false && !showSettings && (
        <div className="site-banner" role="note">
          No station location is configured — species plausibility filtering and night
          scheduling are inactive.{' '}
          <button className="linklike" onClick={() => setShowSettings(true)}>
            set it in settings
          </button>
        </div>
      )}
      {live.status && (live.status.station.site_pending_restart?.length ?? 0) > 0 && (
        <div className="site-banner" role="note">
          Saved settings awaiting a restart:{' '}
          {live.status.station.site_pending_restart.join(', ')}
        </div>
      )}

      {showSettings && (
        <>
          <SettingsPanel
            onClose={() => setShowSettings(false)}
            onSaved={() => firstRun.refresh()}
          />
          {/* ADR-050. Alongside settings rather than in them: this is not a
              value the station reads, it is a binary the station serves, and
              the mistake to prevent is an operator hunting for it in a form
              that lists every other thing the display can be told. */}
          <FirmwarePanel />
        </>
      )}

      <OperatorSummary status={live.status} />

      {history.mode === 'history' ? (
        <History
          timeZone={timeZone}
          windowName={history.historyWindow}
          focused={history.focus}
          includeUnidentified={!history.hideUnidentifiedHistory}
          onWindowChange={history.onWindowChange}
          onFocus={history.onFocus}
        />
      ) : (
        <div className={`hero ${spectrogramControls.orientation === 'waterfall' ? 'hero-waterfall' : ''}`}>
          <div className="hero-controls">
            <div className="segmented">
              <button
                className={spectrogramControls.activeChannel === 'both' ? 'on' : ''}
                onClick={() => spectrogramControls.setActiveChannel('both')}
              >
                both
              </button>
              {/* Same order as the stack, so the picker cannot contradict it. */}
              {spectrogramControls.orderedSpecs.map((spec) => (
                <button
                  key={spec.channel}
                  className={spectrogramControls.activeChannel === spec.channel ? 'on' : ''}
                  onClick={() => spectrogramControls.setActiveChannel(spec.channel)}
                >
                  {spec.name}
                </button>
              ))}
            </div>

            <label>
              history
              <select
                value={spectrogramControls.windowSeconds}
                onChange={(event) => spectrogramControls.setWindowSeconds(Number(event.target.value))}
              >
                {[10, 20, 30, 60, 120, 300].map((value) => (
                  <option key={value} value={value}>
                    {value < 60 ? `${value}s` : `${value / 60}m`}
                  </option>
                ))}
              </select>
            </label>

            {diagnosing && (
              <>
                <label title="Scroll puts time across the page with now at the right, which reads rhythm well. Waterfall puts frequency across and time down the page with now at the top, which reads the shape of the band well.">
                  view
                  <select
                    value={spectrogramControls.orientation}
                    onChange={(event) =>
                      spectrogramControls.setOrientation(event.target.value as typeof spectrogramControls.orientation)
                    }
                  >
                    <option value="scroll">scroll</option>
                    <option value="waterfall">waterfall</option>
                  </select>
                </label>

                <label>
                  palette
                  <select
                    value={spectrogramControls.palette}
                    onChange={(event) =>
                      spectrogramControls.setPalette(event.target.value as typeof spectrogramControls.palette)
                    }
                  >
                    <option value="observatory">observatory</option>
                    <option value="merlin">Merlin grey</option>
                    <option value="ice">ice</option>
                  </select>
                </label>

                <label title="Level mapped to the darkest colour. Raise it to push the noise floor to black.">
                  floor
                  <input
                    type="range"
                    min={0}
                    max={0.9}
                    step={0.01}
                    value={spectrogramControls.blackPoint}
                    onChange={(event) => spectrogramControls.setBlackPoint(Number(event.target.value))}
                  />
                  <span className="mono">{spectrogramControls.blackPoint.toFixed(2)}</span>
                </label>

                <label title="Level mapped to the brightest colour. Lower it to bring out quiet detail.">
                  ceiling
                  <input
                    type="range"
                    min={0.1}
                    max={1}
                    step={0.01}
                    value={spectrogramControls.whitePoint}
                    onChange={(event) => spectrogramControls.setWhitePoint(Number(event.target.value))}
                  />
                  <span className="mono">{spectrogramControls.whitePoint.toFixed(2)}</span>
                </label>
              </>
            )}

            <label className="checkbox">
              <input
                type="checkbox"
                checked={spectrogramControls.showDetections}
                onChange={(event) => spectrogramControls.setShowDetections(event.target.checked)}
              />
              overlay detections
            </label>

            <span className="grow" />
            {diagnosing && (
              <div className="meters">
                <LevelMeter label="native" sample={live.status?.levels.native ?? null} />
                <LevelMeter label="audible" sample={live.status?.levels.audible ?? null} />
              </div>
            )}
          </div>

          {spectrogramControls.visibleSpecs.length === 0 && (
            <div className="spectrogram placeholder" style={{ height: spectrogramControls.heroHeight }}>
              waiting for the first audio columns…
            </div>
          )}
          <div className={spectrogramControls.orientation === 'waterfall' ? 'spectrogram-row' : undefined}>
            {spectrogramControls.visibleSpecs.map((spec) => (
              <Spectrogram
                key={`${spec.channel}-${spec.bins}-${spec.sample_rate}`}
                spec={spec}
                register={live.register}
                detections={live.detections}
                palette={spectrogramControls.palette}
                windowSeconds={spectrogramControls.windowSeconds}
                blackPoint={spectrogramControls.blackPoint}
                whitePoint={spectrogramControls.whitePoint}
                showDetections={spectrogramControls.showDetections}
                orientation={spectrogramControls.orientation}
                height={spectrogramControls.heroHeight}
                playhead={playhead}
              />
            ))}
          </div>
        </div>
      )}

      <main className={`columns ${diagnosing ? '' : 'columns-operate'}`}>
        <div className="column left">
          <Suggestions
            detections={history.mode === 'history' ? history.historyDetections : live.detections}
            caption={
              history.mode === 'history'
                ? history.historyLoading
                  ? 'loading…'
                  : `${history.historyDetections.length}${history.historyTruncated ? '+' : ''} in ${
                      history.focus ? 'focused slice' : history.historyRange?.label ?? 'window'
                    }`
                : null
            }
            localTimeZone={timeZone}
            onSelect={setSelected}
            selectedId={selected?.id ?? null}
          />
          {!diagnosing && <RetentionPanel />}
        </div>
        {diagnosing && (
          <>
            <div className="column middle">
              {live.status && <CapturePanel status={live.status} />}
              {live.status && <DetectorPanel detectors={live.status.detectors} />}
              {/* ADR-052. Immediately under the detector panel, because that
                  panel is where an operator reads "998 windows, 0 dropped"
                  and concludes the detector is fine while hearing birds it
                  never reported. The answer to that is here. Mounted only in
                  the diagnose depth, so it polls for nobody. */}
              <NearMissPanel localTimeZone={timeZone} />
              {live.status && <StoragePanel status={live.status} />}
              <RetentionPanel />
            </div>
            <div className="column right">
              <EventLog events={live.events} localTimeZone={timeZone} />
            </div>
          </>
        )}
      </main>

      <DetectionDrawer
        detection={selected}
        localTimeZone={timeZone}
        onClose={() => setSelected(null)}
      />

      <footer className="footer dim">
        <span>
          Open Observatory {live.status?.station.software_version ?? ''} — operator and
          diagnostic surface (ADR-016/024)
        </span>
        <span>
          Levels are dBFS relative to digital full scale, not calibrated SPL. Scores are
          model outputs, not probabilities, unless a detector declares calibration.
        </span>
      </footer>
    </div>
  )
}
