// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { StationStatus } from '../types'
import { OperatorSummary } from './OperatorSummary'

/** A station where everything is nominal. Cast rather than fully populated:
 *  `operatorCards` reads only these fields, and a fixture that mirrored the
 *  whole `StationStatus` would rot every time the API grows a key. */
function healthyStatus(overrides: Record<string, unknown> = {}): StationStatus {
  return {
    capture: {
      state: 'capturing',
      is_live_hardware: true,
      source_kind: 'alsa',
      device_label: '384kHz AudioMoth USB Microphone',
      sample_rate: 384000,
      block_age_s: 0.1,
      detail: '',
    },
    storage: { disk_used_ratio: 0.1, clip_count: 19214 },
    clips: { disk_guard_active: null },
    detectors: [
      { plugin_id: 'birdnet-v2.4', state: 'running', detail: '' },
      { plugin_id: 'activity-v1', state: 'running', detail: '' },
      { plugin_id: 'ultrasonic-pass-v1', state: 'running', detail: '' },
    ],
    ...overrides,
  } as unknown as StationStatus
}

describe('OperatorSummary', () => {
  it('renders nothing at all when the station is healthy', () => {
    // The point of the component. The header already carries a status dot, and
    // the detail lives under Diagnostics -- a permanent row of green boxes
    // saying "fine" is screen space spent on nothing.
    const { container } = render(<OperatorSummary status={healthyStatus()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing before the station has answered', () => {
    // "Not known yet" is not something the operator can act on, and the header
    // already shows a disconnected state.
    const { container } = render(<OperatorSummary status={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shouts when the audio is not live, because nothing below it is real', () => {
    render(
      <OperatorSummary
        status={healthyStatus({
          capture: {
            state: 'capturing',
            is_live_hardware: false,
            source_kind: 'synthetic',
            device_label: null,
            sample_rate: 48000,
            block_age_s: 0.1,
            detail: '',
          },
        })}
      />,
    )
    expect(screen.getByText('NOT LIVE AUDIO')).toBeInTheDocument()
  })

  it('shows only what needs attention, not the things that are fine', () => {
    // A row that appears *because* something is wrong is far louder than one
    // that is always there and merely changes colour.
    render(
      <OperatorSummary
        status={healthyStatus({
          detectors: [
            { plugin_id: 'birdnet-v2.4', state: 'error', detail: 'model missing' },
            { plugin_id: 'activity-v1', state: 'running', detail: '' },
          ],
        })}
      />,
    )
    expect(screen.getByText('Detection')).toBeInTheDocument()
    // Storage and Listening are both healthy here and must stay hidden.
    expect(screen.queryByText('Storage')).not.toBeInTheDocument()
    expect(screen.queryByText('Listening')).not.toBeInTheDocument()
  })

  it('surfaces a full disk', () => {
    render(<OperatorSummary status={healthyStatus({ storage: { disk_used_ratio: 0.97, clip_count: 19214 } })} />)
    expect(screen.getByText('Storage')).toBeInTheDocument()
  })
})
