import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithAuth } from '../../test/renderWithAuth.jsx'
import VerificationStatusTable, { badge } from './VerificationStatusTable.jsx'
import { api } from '../../api'

vi.mock('../../api', () => ({
  api: { verificationStatus: vi.fn(), deleteWorker: vi.fn() },
}))

const item = (key, status, doc_url = null) => ({ key, label: key, status, doc_url })

const ROWS = [
  {
    id: 1,
    name: 'Ravi Kumar',
    skill_type: 'Carpenter',
    aadhar_number: '100000000001',
    photo_url: 'https://signed.example/photo.jpg?token=xyz',
    items: [
      item('Aadhar', 'VERIFIED', 'https://signed.example/aadhaar?token=abc'),
      item('PAN', 'VERIFIED'),
      item('Safety Training', 'VERIFIED'),
      item('MEDICAL', 'VERIFIED'),
      item('POLICE', 'VERIFIED'),
      item('TRADE_TEST', 'PASSED'),
      item('SAFETY_VIDEO', 'VERIFIED'),
      item('RESUME', 'VERIFIED'),
    ],
    remaining: 0,
    all_verified: true,
  },
  {
    id: 2,
    name: 'Deepak Singh',
    skill_type: 'Fitter',
    aadhar_number: '100000000005',
    photo_url: null,
    items: [
      item('Aadhar', 'VERIFIED'),
      item('PAN', 'REJECTED'),
      item('Safety Training', 'EXPIRED'),
      item('MEDICAL', 'MISSING'),
      item('POLICE', 'MISSING'),
      item('TRADE_TEST', 'PENDING'),
      item('SAFETY_VIDEO', 'INCOMPLETE'),
      item('RESUME', 'MISSING'),
    ],
    remaining: 7,
    all_verified: false,
  },
]

describe('VerificationStatusTable', () => {
  beforeEach(() => {
    api.verificationStatus.mockReset()
    api.verificationStatus.mockImplementation(async () => ROWS)
    api.deleteWorker.mockReset()
    api.deleteWorker.mockImplementation(async () => ({ deleted: true }))
  })

  it('renders a column for every pillar including the resume', async () => {
    renderWithAuth(<VerificationStatusTable />)

    await screen.findByText('Ravi Kumar')
    for (const label of ['Aadhaar', 'PAN', 'Safety Cert', 'Medical', 'Police',
                         'Trade Test', 'Safety Video', 'Resume']) {
      expect(screen.getByRole('columnheader', { name: label })).toBeInTheDocument()
    }
  })

  it('summarises each worker as verified or outstanding', async () => {
    renderWithAuth(<VerificationStatusTable />)

    await screen.findByText('Ravi Kumar')
    expect(screen.getByText('✅ All verified')).toBeInTheDocument()
    expect(screen.getByText('7 remaining')).toBeInTheDocument()
  })

  it('links to a stored document only when one exists', async () => {
    renderWithAuth(<VerificationStatusTable />)

    await screen.findByText('Ravi Kumar')
    const links = screen.getAllByTitle(/open document/i)
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', 'https://signed.example/aadhaar?token=abc')
  })

  it('filters to incomplete workers on request', async () => {
    const user = userEvent.setup()
    renderWithAuth(<VerificationStatusTable />)
    await screen.findByText('Ravi Kumar')

    await user.click(screen.getByLabelText(/show only incomplete/i))

    expect(screen.queryByText('Ravi Kumar')).not.toBeInTheDocument()
    expect(screen.getByText('Deepak Singh')).toBeInTheDocument()
  })

  it('searches by name, skill or Aadhaar', async () => {
    const user = userEvent.setup()
    renderWithAuth(<VerificationStatusTable />)
    await screen.findByText('Ravi Kumar')

    await user.type(screen.getByPlaceholderText(/search by name/i), 'fitter')

    expect(screen.queryByText('Ravi Kumar')).not.toBeInTheDocument()
    expect(screen.getByText('Deepak Singh')).toBeInTheDocument()
  })

  it('confirms before deleting a worker', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderWithAuth(<VerificationStatusTable />)
    await screen.findByText('Ravi Kumar')

    await user.click(screen.getAllByTitle('Delete worker')[0])

    expect(api.deleteWorker).not.toHaveBeenCalled()
    expect(screen.getByText('Ravi Kumar')).toBeInTheDocument()
  })

  it('removes the row once deletion is confirmed', async () => {
    const user = userEvent.setup()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChanged = vi.fn()
    renderWithAuth(<VerificationStatusTable onChanged={onChanged} />)
    await screen.findByText('Ravi Kumar')

    await user.click(screen.getAllByTitle('Delete worker')[0])

    await waitFor(() => expect(api.deleteWorker).toHaveBeenCalledWith('test-token', 1))
    expect(screen.queryByText('Ravi Kumar')).not.toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })

  it('maps every status to a distinct badge tone', () => {
    expect(badge('VERIFIED').tone).toBe('green')
    expect(badge('PASSED').tone).toBe('green')
    expect(badge('PENDING').tone).toBe('amber')
    expect(badge('INCOMPLETE').tone).toBe('amber')
    expect(badge('NOT_PASSED').tone).toBe('amber')
    expect(badge('REJECTED').tone).toBe('red')
    expect(badge('EXPIRED').tone).toBe('red')
    expect(badge('FAILED').tone).toBe('red')
    expect(badge('MISSING').tone).toBe('grey')
    expect(badge(undefined).tone).toBe('grey')
  })

  it('shows expiry as a red failure, not a soft warning', async () => {
    renderWithAuth(<VerificationStatusTable />)

    const row = (await screen.findByText('Deepak Singh')).closest('tr')
    expect(within(row).getByText('✖ Expired')).toBeInTheDocument()
  })
})

describe('worker photo', () => {
  beforeEach(() => {
    api.verificationStatus.mockReset()
    api.verificationStatus.mockImplementation(async () => ROWS)
  })

  it('shows the photo when there is one, linked to the full size', async () => {
    renderWithAuth(<VerificationStatusTable />)

    const photo = await screen.findByAltText('Ravi Kumar')
    expect(photo).toHaveAttribute('src', 'https://signed.example/photo.jpg?token=xyz')
    expect(photo.closest('a')).toHaveAttribute(
      'href',
      'https://signed.example/photo.jpg?token=xyz'
    )
  })

  it('falls back to a placeholder rather than a broken image', async () => {
    renderWithAuth(<VerificationStatusTable />)

    await screen.findByText('Deepak Singh')
    // No <img> for the worker who has no photo.
    expect(screen.queryByAltText('Deepak Singh')).not.toBeInTheDocument()
    expect(document.querySelectorAll('.vs-photo.none')).toHaveLength(1)
  })

  it('does not treat a missing photo as an outstanding verification', async () => {
    renderWithAuth(<VerificationStatusTable />)

    const row = (await screen.findByText('Deepak Singh')).closest('tr')
    // Seven remaining, exactly as the payload says — the photo is not counted.
    expect(within(row).getByText('7 remaining')).toBeInTheDocument()
  })
})
