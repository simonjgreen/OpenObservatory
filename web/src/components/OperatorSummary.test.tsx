// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { OperatorSummary } from './OperatorSummary'

describe('OperatorSummary', () => {
  it('renders a waiting card with no status', () => {
    render(<OperatorSummary status={null} />)
    expect(screen.getByText('not connected')).toBeInTheDocument()
  })
})
