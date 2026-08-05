import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { LiveAudioPlayer, type AudioStatus, type AudioTelemetry } from './audio'
import { LiveConnection, type ConnectionState, type HelloPayload } from './live'
import type { ColumnBatch, Detection, Envelope, SpectrogramSpec, StationStatus } from './types'
import { Header } from './components/Header'
import { Spectrogram, type Palette } from './components/Spectrogram'
import { orderPanels, type Orientation } from './components/geometry'
import { Suggestions } from './components/Suggestions'
import { LevelMeter, ListenControl } from './components/Meters'
import { CapturePanel, DetectorPanel, EventLog, StoragePanel } from './components/Pipeline'
import { DetectionDrawer } from './components/DetectionDrawer'

const MAX_DETECTIONS = 600
const MAX_EVENTS = 400

export default function App() {
  const [status, setStatus] = useState<StationStatus | null>(null)
  const [specs, setSpecs] = useState<SpectrogramSpec[]>([])
  const [detections, setDetections] = useState<Detection[]>([])
  const [events, setEvents] = useState<Envelope[]>([])
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [selected, setSelected] = useState<Detection | null>(null)
  const [clock, setClock] = useState(() => new Date())

  const [palette, setPalette] = useState<Palette>('observatory')
  const [windowSeconds, setWindowSeconds] = useState(30)
  // Defaults chosen from a measured distribution of real garden audio on the
  // target: p1 sat at -84 dBFS and the loudest peaks at -41, so with the server's
  // -95..-15 dB encoding the useful signal occupies roughly 0.14 to 0.68.
  const [blackPoint, setBlackPoint] = useState(0.13)
  const [whitePoint, setWhitePoint] = useState(0.72)
  const [showDetections, setShowDetections] = useState(true)
  const [orientation, setOrientation] = useState<Orientation>('scroll')
  const [activeChannel, setActiveChannel] = useState<number | 'both'>('both')

  const [audioStatus, setAudioStatus] = useState<AudioStatus>('idle')
  const [audioTelemetry, setAudioTelemetry] = useState<AudioTelemetry | null>(null)
  const [audioDetail, setAudioDetail] = useState<string | undefined>()
  const [volume, setVolume] = useState(0.9)
  // The live mic runs near -45 dBFS in a quiet garden, so unity monitoring is
  // effectively silent on laptop speakers. Default to a useful listening level.
  const [monitorGainDb, setMonitorGainDb] = useState(24)

  // Spectrogram sinks are held in a ref: batches arrive dozens of times a second
  // and must reach the canvases without a React render.
  const sinksRef = useRef(new Map<number, Set<(batch: ColumnBatch) => void>>())
  const skewRef = useRef(0)
  const connectionRef = useRef<LiveConnection | null>(null)

  const register = useCallback((channel: number, sink: (batch: ColumnBatch) => void) => {
    const sinks = sinksRef.current
    if (!sinks.has(channel)) sinks.set(channel, new Set())
    sinks.get(channel)!.add(sink)
    return () => {
      sinks.get(channel)?.delete(sink)
    }
  }, [])

  useEffect(() => {
    const live = new LiveConnection({
      onColumns: (batch) => {
        const sinks = sinksRef.current.get(batch.channel)
        if (!sinks) return
        for (const sink of sinks) sink(batch)
      },
      onStatus: (next) => {
        setStatus(next)
        setSpecs((current) =>
          // Only replace when the channel set actually changes, so re-anchoring
          // the canvases does not throw away scroll history on every status tick.
          current.length === next.spectrograms.length &&
          current.every(
            (spec, index) =>
              spec.channel === next.spectrograms[index].channel &&
              spec.bins === next.spectrograms[index].bins &&
              spec.sample_rate === next.spectrograms[index].sample_rate,
          )
            ? current
            : next.spectrograms,
        )
      },
      onDetection: (detection) =>
        setDetections((current) => {
          if (current.some((existing) => existing.id === detection.id)) return current
          const next = [...current, detection]
          return next.length > MAX_DETECTIONS ? next.slice(-MAX_DETECTIONS) : next
        }),
      onEvent: (event) =>
        setEvents((current) => {
          // Per-second level telemetry would swamp the log; it drives the meters.
          if (event.event_type === 'capture.levels' || event.event_type === 'station.status') {
            return current
          }
          const next = [event, ...current]
          return next.length > MAX_EVENTS ? next.slice(0, MAX_EVENTS) : next
        }),
      onConnectionChange: (state) => setConnection(state),
      onHello: (hello: HelloPayload) => {
        skewRef.current = live.clockSkewS
        setSpecs(hello.spectrograms)
      },
    })
    connectionRef.current = live
    live.connect()
    return () => live.close()
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(new Date()), 500)
    return () => window.clearInterval(timer)
  }, [])

  const player = useMemo(
    () =>
      new LiveAudioPlayer(
        (state, detail) => {
          setAudioStatus(state)
          setAudioDetail(detail)
        },
        (telemetry) => setAudioTelemetry(telemetry),
        0.9,
      ),
    [],
  )

  useEffect(() => () => void player.stop(), [player])

  const toggleAudio = useCallback(() => {
    if (player.playing) void player.stop()
    else void player.start(volume)
  }, [player, volume])

  const changeVolume = useCallback(
    (value: number) => {
      setVolume(value)
      player.setVolume(value)
    },
    [player],
  )

  const changeMonitorGain = useCallback(
    (value: number) => {
      setMonitorGainDb(value)
      player.setMonitorGainDb(value)
    },
    [player],
  )

  const timeZone = status?.station.timezone ?? 'UTC'

  // Ordered so the panels' frequency axes form one continuous run across the page.
  // Which way round that is depends on the orientation, so the rule lives in
  // geometry.ts where both directions are tested.
  const orderedSpecs = useMemo(() => orderPanels(specs, orientation), [specs, orientation])
  const visibleSpecs = orderedSpecs.filter(
    (spec) => activeChannel === 'both' || spec.channel === activeChannel,
  )
  // A waterfall needs vertical room, because time runs down it rather than across.
  const heroHeight =
    orientation === 'waterfall'
      ? visibleSpecs.length > 1 ? 420 : 620
      : visibleSpecs.length > 1 ? 250 : 420

  return (
    <div className="app">
      <Header
        status={status}
        connection={connection}
        clock={clock}
        localTimeZone={timeZone}
      >
        <ListenControl
          status={audioStatus}
          telemetry={audioTelemetry}
          volume={volume}
          monitorGainDb={monitorGainDb}
          onToggle={toggleAudio}
          onVolume={changeVolume}
          onMonitorGain={changeMonitorGain}
          detail={audioDetail}
        />
      </Header>

      <div className={`hero ${orientation === 'waterfall' ? 'hero-waterfall' : ''}`}>
        <div className="hero-controls">
          <div className="segmented">
            <button
              className={activeChannel === 'both' ? 'on' : ''}
              onClick={() => setActiveChannel('both')}
            >
              both
            </button>
            {/* Same order as the stack, so the picker cannot contradict it. */}
            {orderedSpecs.map((spec) => (
              <button
                key={spec.channel}
                className={activeChannel === spec.channel ? 'on' : ''}
                onClick={() => setActiveChannel(spec.channel)}
              >
                {spec.name}
              </button>
            ))}
          </div>

          <label>
            history
            <select
              value={windowSeconds}
              onChange={(event) => setWindowSeconds(Number(event.target.value))}
            >
              {[10, 20, 30, 60, 120, 300].map((value) => (
                <option key={value} value={value}>
                  {value < 60 ? `${value}s` : `${value / 60}m`}
                </option>
              ))}
            </select>
          </label>

          <label title="Scroll puts time across the page with now at the right, which reads rhythm well. Waterfall puts frequency across and time down the page with now at the top, which reads the shape of the band well.">
            view
            <select
              value={orientation}
              onChange={(event) => setOrientation(event.target.value as Orientation)}
            >
              <option value="scroll">scroll</option>
              <option value="waterfall">waterfall</option>
            </select>
          </label>

          <label>
            palette
            <select value={palette} onChange={(event) => setPalette(event.target.value as Palette)}>
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
              value={blackPoint}
              onChange={(event) => setBlackPoint(Number(event.target.value))}
            />
            <span className="mono">{blackPoint.toFixed(2)}</span>
          </label>

          <label title="Level mapped to the brightest colour. Lower it to bring out quiet detail.">
            ceiling
            <input
              type="range"
              min={0.1}
              max={1}
              step={0.01}
              value={whitePoint}
              onChange={(event) => setWhitePoint(Number(event.target.value))}
            />
            <span className="mono">{whitePoint.toFixed(2)}</span>
          </label>

          <label className="checkbox">
            <input
              type="checkbox"
              checked={showDetections}
              onChange={(event) => setShowDetections(event.target.checked)}
            />
            overlay detections
          </label>

          <span className="grow" />
          <div className="meters">
            <LevelMeter label="native" sample={status?.levels.native ?? null} />
            <LevelMeter label="audible" sample={status?.levels.audible ?? null} />
          </div>
        </div>

        {visibleSpecs.length === 0 && (
          <div className="spectrogram placeholder" style={{ height: heroHeight }}>
            waiting for the first audio columns…
          </div>
        )}
        <div className={orientation === 'waterfall' ? 'spectrogram-row' : undefined}>
        {visibleSpecs.map((spec) => (
          <Spectrogram
            key={`${spec.channel}-${spec.bins}-${spec.sample_rate}`}
            spec={spec}
            register={register}
            detections={detections}
            palette={palette}
            windowSeconds={windowSeconds}
            blackPoint={blackPoint}
            whitePoint={whitePoint}
            showDetections={showDetections}
            orientation={orientation}
            height={heroHeight}
          />
        ))}
        </div>
      </div>

      <main className="columns">
        <div className="column left">
          <Suggestions
            detections={detections}
            localTimeZone={timeZone}
            onSelect={setSelected}
            selectedId={selected?.id ?? null}
          />
        </div>
        <div className="column middle">
          {status && <CapturePanel status={status} />}
          {status && <DetectorPanel detectors={status.detectors} />}
          {status && <StoragePanel status={status} />}
        </div>
        <div className="column right">
          <EventLog events={events} localTimeZone={timeZone} />
        </div>
      </main>

      <DetectionDrawer
        detection={selected}
        localTimeZone={timeZone}
        onClose={() => setSelected(null)}
      />

      <footer className="footer dim">
        <span>
          Open Observatory {status?.station.software_version ?? ''} — debug surface for
          Milestones 0–3
        </span>
        <span>
          Levels are dBFS relative to digital full scale, not calibrated SPL. Scores are
          model outputs, not probabilities, unless a detector declares calibration.
        </span>
      </footer>
    </div>
  )
}
