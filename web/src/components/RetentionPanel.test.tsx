// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

  // ADR-057. The tier counts are rows, not files. 8,067 of this station's rows
  // claimed clips that had been deleted from under them, so this panel showed
  // 20.59 GB that did not exist and said so nowhere.
  const withMissing = (missing: Record<string, unknown> | undefined) => ({
    tiers: [{ name: 'native + audible', age_days_max: 7, clips: 12, bytes: 1024 }],
    eligible_for_deletion: {
      clips: 4,
      bytes: 8 * 1024 ** 3,
      bytes_verified_present: 2 * 1024 ** 3,
    },
    ...(missing ? { missing_files: missing } : {}),
    disk_reclaim_threshold: 0.85,
    last_run_utc: null,
    dry_run: false,
  })

  it('says when rows claim clips that are not on disk', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          withMissing({
            clips: 8067,
            bytes: 20_588_388_416,
            exact: true,
            passes_completed: 1,
            last_pass_scanned: 48941,
          }),
      }),
    )
    render(<RetentionPanel />)
    await waitFor(() => expect(screen.getByText('missing from disk')).toBeInTheDocument())
    expect(screen.getByText(/8,067 of 48,941 stored evidence rows/)).toBeInTheDocument()
    expect(screen.getByText(/oo clips reconcile-missing/)).toBeInTheDocument()
  })

  it('shows what deleting the eligible clips would actually free', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () =>
          withMissing({
            clips: 3,
            bytes: 3000,
            exact: true,
            passes_completed: 2,
            last_pass_scanned: 15,
          }),
      }),
    )
    const { container } = render(<RetentionPanel />)
    // 2 GB verified present, not the 8 GB the rows claim: the retention
    // budget must not promise space that reclaiming cannot recover.
    await waitFor(() => expect(container.textContent).toContain('4 clips · 2.00 GB'))
    expect(container.textContent).not.toContain('4 clips · 8.00 GB')
  })

  it('does not invent a zero when the station does not report the audit', async () => {
    // An older station omits the field. "Not reported" and "checked, none
    // missing" are different answers and must not render the same.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => withMissing(undefined) }),
    )
    render(<RetentionPanel />)
    await waitFor(() => expect(screen.getByText('native + audible')).toBeInTheDocument())
    expect(screen.queryByText('missing from disk')).not.toBeInTheDocument()
  })
})
