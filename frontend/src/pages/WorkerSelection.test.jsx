import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithAuth } from '../test/renderWithAuth.jsx'
import WorkerSelection from './WorkerSelection.jsx'
import { api } from '../api'

vi.mock('../api', () => ({
  api: {
    projects: vi.fn(),
    requirements: vi.fn(),
    workers: vi.fn(),
    intakeLists: vi.fn(),
    eligibleWorkers: vi.fn(),
    verificationStatus: vi.fn(),
    workforceDemand: vi.fn(),
    submitList: vi.fn(),
    updateList: vi.fn(),
    verifyDocumentForm: vi.fn(),
    deleteWorker: vi.fn(),
    ocrExtract: vi.fn(),
    tradeTestStart: vi.fn(),
    tradeTestSubmit: vi.fn(),
    safetyVideoHeartbeat: vi.fn(),
    onboardWorker: vi.fn(),
    parseResume: vi.fn(),
  },
}))

const PROJECT = {
  id: 1,
  name: 'Plant-A Turnaround',
  requirements: [
    { id: 1, requirement: { id: 1, name: 'Aadhar', is_expirable: false } },
    { id: 2, requirement: { id: 3, name: 'Safety Training', is_expirable: true } },
  ],
}

const READY_WORKER = {
  id: 1,
  name: 'Ravi Kumar',
  skill_type: 'Carpenter',
  aadhar_number: '100000000001',
  documents: [],
  candidate_profile: { skills: ['Welder', 'Fitter'] },
}

const FIX_WORKER = {
  id: 2,
  name: 'Deepak Singh',
  skill_type: 'Fitter',
  aadhar_number: '100000000005',
  documents: [],
  candidate_profile: null,
}

const ELIGIBLE = {
  project: PROJECT,
  required_documents: [],
  summary: { total: 2, ready: 1, needs_fixes: 1 },
  ready_to_deploy: [
    { worker: READY_WORKER, compliance: { is_compliant: true, satisfied: [], gaps: [] } },
  ],
  needs_fixes: [
    {
      worker: FIX_WORKER,
      compliance: {
        is_compliant: false,
        satisfied: [],
        gaps: [
          { kind: 'document', requirement_id: 2, requirement_name: 'PAN', reason: 'MISSING' },
          {
            kind: 'intake',
            pillar: 'MEDICAL',
            requirement_name: 'Medical Exam',
            reason: 'MISSING',
            detail: 'No medical record on file.',
          },
        ],
      },
    },
  ],
}

describe('WorkerSelection (Contractor Suite)', () => {
  beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset?.())
    api.projects.mockImplementation(async () => [PROJECT])
    api.requirements.mockImplementation(async () => [
      { id: 1, name: 'Aadhar', is_expirable: false },
      { id: 2, name: 'PAN', is_expirable: false },
    ])
    api.workers.mockImplementation(async () => [READY_WORKER, FIX_WORKER])
    api.intakeLists.mockImplementation(async () => [])
    api.eligibleWorkers.mockImplementation(async () => ELIGIBLE)
    api.verificationStatus.mockImplementation(async () => [])
    api.workforceDemand.mockImplementation(async () => ({
      project: null,
      summary: { total_required: 0, total_ready: 0, total_shortfall: 0, pool_size: 2 },
      lines: [],
    }))
    api.submitList.mockImplementation(async () => ({ id: 42 }))
  })

  it('opens on the workforce-demand tab', async () => {
    renderWithAuth(<WorkerSelection />)

    expect(await screen.findByText(/enter your immediate workforce needs/i)).toBeInTheDocument()
  })

  it('exposes every contractor capability as a tab', () => {
    renderWithAuth(<WorkerSelection />)

    for (const label of [
      /workforce demand/i,
      /worker pool/i,
      /verification & testing/i,
      /verification status/i,
    ]) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('splits the pool into ready and needs-fixes', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkerSelection />)

    await user.click(screen.getByRole('button', { name: /worker pool/i }))

    expect(await screen.findByRole('button', { name: /ready to deploy \(1\)/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /fix requirements \(1\)/i })).toBeInTheDocument()
    expect(screen.getByText('Ravi Kumar')).toBeInTheDocument()
  })

  it('filters the pool by a resume skill, not just the registered trade', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkerSelection />)
    await user.click(screen.getByRole('button', { name: /worker pool/i }))
    await screen.findByText('Ravi Kumar')

    // "Welder" appears only in Ravi's parsed resume skills.
    await user.type(screen.getByPlaceholderText(/filter by name, skill/i), 'welder')

    expect(screen.getByText('Ravi Kumar')).toBeInTheDocument()
  })

  it('submits the selected compliant workers to the employer', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkerSelection />)
    await user.click(screen.getByRole('button', { name: /worker pool/i }))
    await screen.findByText('Ravi Kumar')

    await user.click(screen.getByRole('button', { name: /submit 1 selected/i }))

    await waitFor(() =>
      expect(api.submitList).toHaveBeenCalledWith('test-token', {
        project: 1,
        worker_ids: [1],
        submit: true,
      })
    )
    expect(await screen.findByText(/submitted list #42/i)).toBeInTheDocument()
  })

  it('routes intake pillars to the workbench rather than an inline upload', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkerSelection />)
    await user.click(screen.getByRole('button', { name: /worker pool/i }))
    await screen.findByText('Ravi Kumar')

    await user.click(screen.getByRole('button', { name: /fix requirements/i }))

    // Document gap: inline upload control. Scoped to the gap label — "PAN" also
    // appears as a requirement-filter checkbox above the grid.
    expect(await screen.findByText('PAN', { selector: '.gap-name' })).toBeInTheDocument()
    // Pillar gap: pointer to the workbench, no upload control.
    expect(screen.getByText(/resolve this in the verification & testing tab/i)).toBeInTheDocument()
  })

  it('opens the unified intake overlay', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkerSelection />)

    await user.click(screen.getByRole('button', { name: /new worker intake/i }))

    expect(await screen.findByText(/attach the documents first/i)).toBeInTheDocument()
  })

  it('renders the intake workbench with the contractor pool loaded', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkerSelection />)

    await user.click(screen.getByRole('button', { name: /verification & testing/i }))

    expect(await screen.findByText(/document previewer/i)).toBeInTheDocument()
    expect(screen.getByText(/confirm & verify values/i)).toBeInTheDocument()
  })
})
