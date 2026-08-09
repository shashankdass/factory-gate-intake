import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithAuth } from '../../test/renderWithAuth.jsx'
import WorkforceDemand from './WorkforceDemand.jsx'
import { api } from '../../api'

vi.mock('../../api', () => ({
  api: { workforceDemand: vi.fn() },
}))

const RESULT = {
  project: null,
  summary: { total_required: 7, total_ready: 3, total_shortfall: 4, pool_size: 5 },
  lines: [
    {
      skill: 'Carpenter',
      required: 3,
      available: 2,
      shortfall: 1,
      fixable: 1,
      ready_workers: [
        { worker: { id: 1, name: 'Ravi Kumar' }, compliance: { gaps: [] } },
        { worker: { id: 2, name: 'Mahesh Patil' }, compliance: { gaps: [] } },
      ],
      needs_fixes: [
        {
          worker: { id: 3, name: 'Deepak Singh' },
          compliance: { gaps: [{ requirement_name: 'PAN' }] },
        },
      ],
    },
    {
      skill: 'Mason',
      required: 4,
      available: 1,
      shortfall: 3,
      fixable: 0,
      ready_workers: [{ worker: { id: 4, name: 'Suresh Yadav' }, compliance: { gaps: [] } }],
      needs_fixes: [],
    },
  ],
}

describe('WorkforceDemand', () => {
  // mockReset + mockImplementation rather than mockResolvedValue: the latter
  // registers a phantom zero-arg call, which breaks `not.toHaveBeenCalled()`,
  // and it builds the rejected promise eagerly, which trips the unhandled-
  // rejection guard.
  beforeEach(() => {
    api.workforceDemand.mockReset()
    api.workforceDemand.mockImplementation(async () => RESULT)
  })

  it('sends the typed skill counts to the API', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkforceDemand projects={[]} />)

    const counts = screen.getAllByLabelText('How many')
    const skills = screen.getAllByLabelText('Skill')
    await user.clear(counts[0])
    await user.type(counts[0], '3')
    await user.type(skills[0], 'Carpenter')
    await user.clear(counts[1])
    await user.type(counts[1], '4')
    await user.type(skills[1], 'Mason')
    await user.click(screen.getByRole('button', { name: /search my pool/i }))

    await waitFor(() =>
      expect(api.workforceDemand).toHaveBeenCalledWith('test-token', {
        demands: [
          { skill: 'Carpenter', count: 3 },
          { skill: 'Mason', count: 4 },
        ],
        project: null,
      })
    )
  })

  it('reports availability and shortfall per demand line', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkforceDemand projects={[]} />)

    await user.type(screen.getAllByLabelText('Skill')[0], 'Carpenter')
    await user.click(screen.getByRole('button', { name: /search my pool/i }))

    expect(await screen.findByText('3 × Carpenter')).toBeInTheDocument()
    expect(screen.getByText('4 × Mason')).toBeInTheDocument()
    expect(screen.getByText(/2 deployable · 1 need paperwork/)).toBeInTheDocument()
    expect(screen.getByText('Short 1')).toBeInTheDocument()
    expect(screen.getByText('Short 3')).toBeInTheDocument()
  })

  it('lists the workers who can be deployed right now', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkforceDemand projects={[]} />)

    await user.type(screen.getAllByLabelText('Skill')[0], 'Carpenter')
    await user.click(screen.getByRole('button', { name: /search my pool/i }))

    expect(await screen.findByText('Ravi Kumar')).toBeInTheDocument()
    expect(screen.getByText('Mahesh Patil')).toBeInTheDocument()
  })

  it('hands the ready workers back to the pool tab when asked', async () => {
    const user = userEvent.setup()
    const onPickWorkers = vi.fn()
    renderWithAuth(<WorkforceDemand projects={[]} onPickWorkers={onPickWorkers} />)

    await user.type(screen.getAllByLabelText('Skill')[0], 'Carpenter')
    await user.click(screen.getByRole('button', { name: /search my pool/i }))
    await user.click((await screen.findAllByRole('button', { name: /select these/i }))[0])

    expect(onPickWorkers).toHaveBeenCalledWith([1, 2])
  })

  it('can add and remove demand lines', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkforceDemand projects={[]} />)

    expect(screen.getAllByLabelText('Skill')).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: /add a skill/i }))
    expect(screen.getAllByLabelText('Skill')).toHaveLength(3)

    await user.click(screen.getAllByTitle('Remove this line')[0])
    expect(screen.getAllByLabelText('Skill')).toHaveLength(2)
  })

  it('refuses to search with no skill entered', async () => {
    const user = userEvent.setup()
    renderWithAuth(<WorkforceDemand projects={[]} />)

    await user.click(screen.getByRole('button', { name: /search my pool/i }))

    expect(await screen.findByText(/enter at least one skill/i)).toBeInTheDocument()
    expect(api.workforceDemand).not.toHaveBeenCalled()
  })

  it('scopes the search to a project when one is chosen', async () => {
    const user = userEvent.setup()
    renderWithAuth(
      <WorkforceDemand projects={[{ id: 12, name: 'Plant-A Turnaround' }]} />
    )

    await user.type(screen.getAllByLabelText('Skill')[0], 'Mason')
    await user.selectOptions(screen.getByRole('combobox'), '12')
    await user.click(screen.getByRole('button', { name: /search my pool/i }))

    await waitFor(() =>
      expect(api.workforceDemand).toHaveBeenCalledWith(
        'test-token',
        expect.objectContaining({ project: 12 })
      )
    )
  })

  it('surfaces an API failure instead of silently showing nothing', async () => {
    const user = userEvent.setup()
    api.workforceDemand.mockImplementation(() =>
      Promise.reject(new Error('pool unavailable'))
    )
    renderWithAuth(<WorkforceDemand projects={[]} />)

    await user.type(screen.getAllByLabelText('Skill')[0], 'Carpenter')
    await user.click(screen.getByRole('button', { name: /search my pool/i }))

    expect(await screen.findByText(/pool unavailable/)).toBeInTheDocument()
  })
})
