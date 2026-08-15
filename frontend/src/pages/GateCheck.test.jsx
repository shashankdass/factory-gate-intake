import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithAuth } from '../test/renderWithAuth.jsx'
import GateCheck from './GateCheck.jsx'
import { api } from '../api'

vi.mock('../api', () => ({ api: { gateCheck: vi.fn() } }))

const WORKER = {
  id: 1,
  name: 'Ravi Kumar',
  skill_type: 'Carpenter',
  aadhar_number: '100000000001',
}

async function scan(user, aadhar = '100000000001') {
  await user.type(screen.getByPlaceholderText(/scan \/ type aadhar/i), aadhar)
  await user.click(screen.getByRole('button', { name: /verify entry/i }))
}

describe('GateCheck', () => {
  // Reset call history, then always leave a default implementation in place.
  // A mock left implementation-less makes vitest treat a later test's rejected
  // promise as unhandled, even though the component catches it.
  beforeEach(() => {
    api.gateCheck.mockReset()
    api.gateCheck.mockImplementation(async () => ({
      access: 'DENIED',
      reason_code: 'UNKNOWN_WORKER',
      reason: 'No worker found for this Aadhar number.',
      worker: null,
      compliance: null,
    }))
  })

  it('shows a GREEN verdict for a currently-compliant worker', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'GRANTED',
      reason_code: 'COMPLIANT',
      reason: 'Approved for deployment and all documents are currently valid.',
      worker: WORKER,
      project: 'Plant-A Turnaround',
      compliance: { is_compliant: true, gaps: [] },
      checked_at: '2026-08-09T10:00:00Z',
    }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText('ACCESS GRANTED')).toBeInTheDocument()
    expect(screen.getByText('ISSUE GATE PASS')).toBeInTheDocument()
    expect(screen.getByText(/Plant-A Turnaround/)).toBeInTheDocument()
  })

  it('flashes RED with DOCUMENT EXPIRED when papers lapsed after approval', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'DENIED',
      reason_code: 'DOCUMENT_EXPIRED',
      reason: 'Document expired since approval: Medical Exam.',
      worker: WORKER,
      project: 'Plant-A Turnaround',
      compliance: {
        is_compliant: false,
        gaps: [
          {
            requirement_name: 'Medical Exam',
            reason: 'EXPIRED',
            detail: 'Medical expired on 2026-07-01.',
          },
        ],
      },
      checked_at: '2026-08-09T10:00:00Z',
    }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText('ACCESS DENIED — DOCUMENT EXPIRED')).toBeInTheDocument()
    expect(screen.getByText('DO NOT ADMIT')).toBeInTheDocument()
    // The guard must be able to say exactly what lapsed.
    expect(screen.getByText(/Medical expired on 2026-07-01/)).toBeInTheDocument()
  })

  it('distinguishes a regressed pillar from an expiry', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'DENIED',
      reason_code: 'COMPLIANCE_REGRESSED',
      reason: 'No longer compliant since approval: Trade Test.',
      worker: WORKER,
      compliance: {
        is_compliant: false,
        gaps: [{ requirement_name: 'Trade Test', reason: 'FAILED' }],
      },
    }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(
      await screen.findByText('ACCESS DENIED — NO LONGER COMPLIANT')
    ).toBeInTheDocument()
  })

  it('denies a worker who is not on an approved list', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'DENIED',
      reason_code: 'NOT_APPROVED',
      reason: 'Worker is not on any approved deployment list.',
      worker: WORKER,
      compliance: null,
    }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(
      await screen.findByText('ACCESS DENIED — NOT ON AN APPROVED LIST')
    ).toBeInTheDocument()
  })

  it('denies an unknown Aadhaar', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'DENIED',
      reason_code: 'UNKNOWN_WORKER',
      reason: 'No worker found for this Aadhar number.',
      worker: null,
      compliance: null,
    }))
    renderWithAuth(<GateCheck />)

    await scan(user, '999999999999')

    expect(await screen.findByText('ACCESS DENIED — UNKNOWN WORKER')).toBeInTheDocument()
  })

  it('shows the live-check timestamp so the verdict is visibly fresh', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'GRANTED',
      reason_code: 'COMPLIANT',
      reason: 'ok',
      worker: WORKER,
      compliance: { is_compliant: true, gaps: [] },
      checked_at: '2026-08-09T10:00:00Z',
    }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText(/checked live at/i)).toBeInTheDocument()
  })

  it('surfaces a lookup failure', async () => {
    const user = userEvent.setup()
    // Return a rejected promise rather than throwing inside an async mock —
    // the latter leaks an unhandled rejection past the component's catch.
    api.gateCheck.mockImplementation(() =>
      Promise.reject(new Error('gate service unreachable'))
    )
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText(/gate service unreachable/)).toBeInTheDocument()
  })
})

describe('worker photo at the gate', () => {
  beforeEach(() => {
    api.gateCheck.mockReset()
  })

  const granted = (worker) => async () => ({
    access: 'GRANTED',
    reason_code: 'COMPLIANT',
    reason: 'Approved and currently compliant.',
    worker,
    project: 'Plant 2 Expansion',
    compliance: { is_compliant: true, gaps: [] },
    checked_at: '2026-08-15T09:00:00Z',
  })

  it('shows the face so the guard can compare it with the person', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(
      granted({ ...WORKER, photo_url: 'https://signed.example/face.jpg?t=1' })
    )
    renderWithAuth(<GateCheck />)

    await scan(user)

    const photo = await screen.findByAltText('Ravi Kumar')
    expect(photo).toHaveAttribute('src', 'https://signed.example/face.jpg?t=1')
    expect(screen.getByText(/compare with the person at the gate/i)).toBeInTheDocument()
  })

  it('says out loud when there is no photo to compare against', async () => {
    // A blank frame beside ACCESS GRANTED would read as though a face had been
    // checked. The guard has to know the system made no such check.
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(granted({ ...WORKER, photo_url: null }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText(/no photo on record/i)).toBeInTheDocument()
    expect(screen.queryByAltText('Ravi Kumar')).not.toBeInTheDocument()
  })

  it('does not hold a worker at the gate for having no photo', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(granted({ ...WORKER, photo_url: null }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText('ACCESS GRANTED')).toBeInTheDocument()
    expect(screen.getByText('ISSUE GATE PASS')).toBeInTheDocument()
  })

  it('shows the face on a denial too, to identify who is being turned away', async () => {
    const user = userEvent.setup()
    api.gateCheck.mockImplementation(async () => ({
      access: 'DENIED',
      reason_code: 'DOCUMENT_EXPIRED',
      reason: 'Medical fitness expired.',
      worker: { ...WORKER, photo_url: 'https://signed.example/face.jpg?t=2' },
      compliance: { is_compliant: false, gaps: [] },
    }))
    renderWithAuth(<GateCheck />)

    await scan(user)

    expect(await screen.findByText(/ACCESS DENIED — DOCUMENT EXPIRED/)).toBeInTheDocument()
    expect(screen.getByAltText('Ravi Kumar')).toBeInTheDocument()
  })
})
