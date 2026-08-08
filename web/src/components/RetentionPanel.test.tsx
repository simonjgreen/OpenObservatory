// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RetentionPanel } from './RetentionPanel'

describe('RetentionPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('degrades to "not available yet" when the backend has not shipped', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }),
    )
    render(<RetentionPanel />)
    await waitFor(() => expect(screen.getByText(/not available yet/i)).toBeInTheDocument())
  })

  it('renders tiers once the endpoint answers', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          tiers: [{ name: 'native + audible', age_days_max: 7, clips: 12, bytes: 1024 }],
          eligible_for_deletion: { clips: 0, bytes: 0 },
          disk_reclaim_threshold: 0.85,
          last_run_utc: null,
          dry_run: true,
        }),
      }),
    )
    render(<RetentionPanel />)
    await waitFor(() => expect(screen.getByText('native + audible')).toBeInTheDocument())
    expect(screen.getByText(/Dry-run mode/)).toBeInTheDocument()
  })
})
