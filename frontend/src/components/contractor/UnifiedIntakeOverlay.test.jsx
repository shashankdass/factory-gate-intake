import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithAuth } from '../../test/renderWithAuth.jsx'
import UnifiedIntakeOverlay from './UnifiedIntakeOverlay.jsx'
import { api } from '../../api'

vi.mock('../../api', () => ({
  api: { onboardWorker: vi.fn(), parseResume: vi.fn() },
}))

const CREATED = {
  worker: { id: 7, name: 'Mahesh Patil' },
  documents_stored: ['aadhaar', 'pan', 'medical'],
  compliance: { is_compliant: false, gaps: [{ requirement_name: 'PVC', reason: 'MISSING' }] },
  resume: null,
}

const RESUME = {
  name: 'Ravi Kumar',
  phone: '9876543210',
  email: 'ravi.kumar@example.com',
  place: 'Pune',
  stream: 'Mechanical',
  category: 'Technician',
  years_of_experience: 6,
  qualification: 'ITI',
  skills: ['Welder', 'Fitter'],
  provider: 'mock',
  note: null,
}

const file = (name = 'scan.pdf') =>
  new File(['%PDF-1.4'], name, { type: 'application/pdf' })

/** Fill in the three mandatory identity fields. */
async function fillIdentity(user) {
  await user.type(screen.getByLabelText(/full name/i), 'Mahesh Patil')
  await user.type(screen.getByLabelText(/aadhaar number/i), '100000000042')
  await user.type(screen.getByLabelText(/skill \/ trade/i), 'Mason')
}

const submitButton = () =>
  screen.getByRole('button', { name: /create worker & upload all documents/i })

describe('UnifiedIntakeOverlay', () => {
  beforeEach(() => {
    api.onboardWorker.mockReset()
    api.onboardWorker.mockImplementation(async () => CREATED)
    api.parseResume.mockReset()
    api.parseResume.mockImplementation(async () => RESUME)
  })

  it('renders nothing when closed', () => {
    const { container } = renderWithAuth(<UnifiedIntakeOverlay open={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('offers a slot for all six documents in one pass', () => {
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    // Scoped to the slot labels — several of these words also appear in the
    // detail fields below (e.g. "Police verification" / "Certificate number").
    for (const label of [
      /aadhaar card/i,
      /pan card/i,
      /safety training certificate/i,
      /medical fitness report/i,
      /police verification/i,
      /resume \/ cv/i,
    ]) {
      expect(screen.getByText(label, { selector: '.ui-slot-label' })).toBeInTheDocument()
    }
  })

  it('keeps submit disabled until the identity fields are complete', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    expect(submitButton()).toBeDisabled()
    await fillIdentity(user)
    expect(submitButton()).toBeEnabled()
  })

  it('rejects a short Aadhaar number', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await user.type(screen.getByLabelText(/full name/i), 'Mahesh Patil')
    await user.type(screen.getByLabelText(/aadhaar number/i), '12345')
    await user.type(screen.getByLabelText(/skill \/ trade/i), 'Mason')

    expect(submitButton()).toBeDisabled()
  })

  it('blocks submission when the medical is already expired', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)
    await fillIdentity(user)

    const stale = new Date(Date.now() - 400 * 86_400_000).toISOString().slice(0, 10)
    await user.type(screen.getByLabelText(/exam date/i), stale)

    expect(await screen.findByText(/this medical is already expired/i)).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    expect(api.onboardWorker).not.toHaveBeenCalled()
  })

  it('blocks submission when the PVC is already expired', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)
    await fillIdentity(user)

    const stale = new Date(Date.now() - 400 * 86_400_000).toISOString().slice(0, 10)
    await user.type(screen.getByLabelText(/issue date/i), stale)

    expect(await screen.findByText(/this pvc is already expired/i)).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
  })

  it('submits every field and attached file in one request', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} onCreated={vi.fn()} />)
    await fillIdentity(user)

    await user.upload(
      screen.getByText(/aadhaar card/i).closest('.ui-slot').querySelector('input[type=file]'),
      file('aadhaar.pdf')
    )
    await user.click(submitButton())

    await waitFor(() => expect(api.onboardWorker).toHaveBeenCalled())
    const [, formData] = api.onboardWorker.mock.calls[0]
    expect(formData.get('name')).toBe('Mahesh Patil')
    expect(formData.get('aadhar_number')).toBe('100000000042')
    expect(formData.get('skill_type')).toBe('Mason')
    expect(formData.get('aadhaar_file')).toBeInstanceOf(File)
    // Checkboxes travel as explicit booleans the backend can coerce.
    expect(formData.get('color_blindness')).toBe('false')
  })

  it('counts the attached documents', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    expect(screen.getByText('0 of 6 documents attached')).toBeInTheDocument()
    await user.upload(
      screen.getByText(/pan card/i).closest('.ui-slot').querySelector('input[type=file]'),
      file('pan.pdf')
    )
    expect(screen.getByText('1 of 6 documents attached')).toBeInTheDocument()
  })

  it('previews the parsed resume before anything is saved', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await user.upload(
      screen.getByText(/resume \/ cv/i).closest('.ui-slot').querySelector('input[type=file]'),
      file('resume.pdf')
    )
    await user.click(screen.getByRole('button', { name: /scan resume before saving/i }))

    expect(await screen.findByText('Ravi Kumar')).toBeInTheDocument()
    expect(screen.getByText('6 yrs')).toBeInTheDocument()
    expect(screen.getByText('Welder')).toBeInTheDocument()
    expect(screen.getByText(/nothing is saved until you submit/i)).toBeInTheDocument()
    expect(api.onboardWorker).not.toHaveBeenCalled()
  })

  it('reports what is still outstanding after onboarding', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} onCreated={vi.fn()} />)
    await fillIdentity(user)
    await user.click(submitButton())

    expect(await screen.findByText(/Mahesh Patil onboarded/)).toBeInTheDocument()
    expect(screen.getByText(/still outstanding \(1\)/i)).toBeInTheDocument()
    expect(screen.getByText('PVC')).toBeInTheDocument()
  })

  it('surfaces a backend rejection instead of closing silently', async () => {
    const user = userEvent.setup()
    api.onboardWorker.mockImplementation(() =>
      Promise.reject(new Error('Rejected: Medical exam date is more than 365 days old'))
    )
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)
    await fillIdentity(user)
    await user.click(submitButton())

    expect(await screen.findByText(/more than 365 days old/)).toBeInTheDocument()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={onClose} />)

    await user.keyboard('{Escape}')

    expect(onClose).toHaveBeenCalled()
  })
})
