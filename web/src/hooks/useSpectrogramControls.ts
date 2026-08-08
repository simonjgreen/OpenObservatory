/** Spectrogram display controls: palette, window, black/white points, the
 *  detection overlay toggle, orientation and the channel picker — plus the
 *  derived ordering/sizing that the hero panel needs. All pure UI state,
 *  extracted verbatim from `App.tsx` so the component only holds the
 *  spectrogram-specific slice of what used to be twenty-five `useState`
 *  calls in one place. */

import { useState } from 'react'

import type { SpectrogramSpec } from '../types'
import type { Palette } from '../components/Spectrogram'
import { orderPanels, type Orientation } from '../components/geometry'

export interface SpectrogramControls {
  palette: Palette
  setPalette: (value: Palette) => void
  windowSeconds: number
  setWindowSeconds: (value: number) => void
  blackPoint: number
  setBlackPoint: (value: number) => void
  whitePoint: number
  setWhitePoint: (value: number) => void
  showDetections: boolean
  setShowDetections: (value: boolean) => void
  orientation: Orientation
  setOrientation: (value: Orientation) => void
  activeChannel: number | 'both'
  setActiveChannel: (value: number | 'both') => void
  orderedSpecs: SpectrogramSpec[]
  visibleSpecs: SpectrogramSpec[]
  heroHeight: number
}

export function useSpectrogramControls(specs: SpectrogramSpec[]): SpectrogramControls {
  const [palette, setPalette] = useState<Palette>('observatory')
  const [windowSeconds, setWindowSeconds] = useState(30)
  // Defaults chosen from a measured distribution of real garden audio on the
  // target: p1 sat at -84 dBFS and the loudest peaks at -41, so with the
  // server's -95..-15 dB encoding the useful signal occupies roughly 0.14 to
  // 0.68.
  const [blackPoint, setBlackPoint] = useState(0.13)
  const [whitePoint, setWhitePoint] = useState(0.72)
  const [showDetections, setShowDetections] = useState(true)
  const [orientation, setOrientation] = useState<Orientation>('scroll')
  const [activeChannel, setActiveChannel] = useState<number | 'both'>('both')

  // Ordered so the panels' frequency axes form one continuous run across the
  // page. Which way round that is depends on orientation, so the rule lives
  // in geometry.ts where both directions are tested.
  const orderedSpecs = orderPanels(specs, orientation)
  const visibleSpecs = orderedSpecs.filter(
    (spec) => activeChannel === 'both' || spec.channel === activeChannel,
  )
  // A waterfall needs vertical room, because time runs down it rather than
  // across.
  const heroHeight =
    orientation === 'waterfall'
      ? visibleSpecs.length > 1
        ? 420
        : 620
      : visibleSpecs.length > 1
        ? 250
        : 420

  return {
    palette,
    setPalette,
    windowSeconds,
    setWindowSeconds,
    blackPoint,
    setBlackPoint,
    whitePoint,
    setWhitePoint,
    showDetections,
    setShowDetections,
    orientation,
    setOrientation,
    activeChannel,
    setActiveChannel,
    orderedSpecs,
    visibleSpecs,
    heroHeight,
  }
}
