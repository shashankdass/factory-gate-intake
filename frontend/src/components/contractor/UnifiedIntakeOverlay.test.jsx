import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithAuth } from '../../test/renderWithAuth.jsx'
import UnifiedIntakeOverlay, { toFormPatch } from './UnifiedIntakeOverlay.jsx'
import { api } from '../../api'

vi.mock('../../api', () => ({
  api: { onboardWorker: vi.fn(), parseResume: vi.fn(), ocrExtract: vi.fn() },
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

// What /intake/ocr-extract/ returns for each doc_type.
const OCR = {
  IDENTITY_Aadhar: { name: 'Suresh Yadav', aadhar_number: '100000000002' },
  IDENTITY_PAN: { document_number: 'ABCDE1234F' },
  MEDICAL: {
    exam_date: '2026-07-15',
    vision: '6/6',
    blood_type: 'O+',
    color_blindness: false,
    vertigo: false,
  },
  POLICE: { certificate_number: 'PVC-2026-8842', issue_date: '2026-07-01' },
}

const pdf = (name = 'scan.pdf') =>
  new File(['%PDF-1.4'], name, { type: 'application/pdf' })
const png = (name = 'scan.png') => new File(['\x89PNG'], name, { type: 'image/png' })

/** Attach a file to the slot with the given label. */
async function attach(user, label, file) {
  const slot = screen.getByText(label, { selector: '.ui-slot-label' }).closest('.ui-slot')
  await user.upload(slot.querySelector('input[type=file]'), file)
  return slot
}

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
    api.ocrExtract.mockReset()
    api.ocrExtract.mockImplementation(async (_token, fd) => {
      const key = [fd.get('doc_type'), fd.get('requirement_name')].filter(Boolean).join('_')
      return { form_type: fd.get('doc_type'), fields: OCR[key] || {}, provider: 'mock' }
    })
  })

  it('renders nothing when closed', () => {
    const { container } = renderWithAuth(<UnifiedIntakeOverlay open={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('puts documents first, before the fields they fill', () => {
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(headings[0]).toMatch(/documents/i)
    expect(headings[1]).toMatch(/worker/i)
    expect(headings[2]).toMatch(/details/i)
  })

  it('offers a slot for all six documents in one pass', () => {
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

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

  // --- OCR autofill --------------------------------------------------------
  it('reads the Aadhaar on attach and fills name + number', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await attach(user, /aadhaar card/i, pdf('aadhaar.pdf'))

    await waitFor(() =>
      expect(screen.getByLabelText(/full name/i)).toHaveValue('Suresh Yadav')
    )
    expect(screen.getByLabelText(/aadhaar number/i)).toHaveValue('100000000002')
  })

  it('reads the medical on attach and fills its details', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await attach(user, /medical fitness report/i, pdf('medical.pdf'))

    await waitFor(() => expect(screen.getByLabelText(/exam date/i)).toHaveValue('2026-07-15'))
    expect(screen.getByLabelText(/vision/i)).toHaveValue('6/6')
    expect(screen.getByLabelText(/blood type/i)).toHaveValue('O+')
  })

  it('reads the PVC on attach', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await attach(user, /police verification/i, pdf('pvc.pdf'))

    await waitFor(() =>
      expect(screen.getByLabelText(/certificate number/i)).toHaveValue('PVC-2026-8842')
    )
    expect(screen.getByLabelText(/issue date/i)).toHaveValue('2026-07-01')
  })

  it('never overwrites something already typed', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await user.type(screen.getByLabelText(/full name/i), 'Typed By Hand')
    await attach(user, /aadhaar card/i, pdf('aadhaar.pdf'))

    // Blank field gets filled, typed field is left alone.
    await waitFor(() =>
      expect(screen.getByLabelText(/aadhaar number/i)).toHaveValue('100000000002')
    )
    expect(screen.getByLabelText(/full name/i)).toHaveValue('Typed By Hand')
  })

  it('reports per-document read status', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const slot = await attach(user, /aadhaar card/i, pdf('aadhaar.pdf'))
    await waitFor(() => expect(slot.querySelector('.ui-slot-status')).toHaveTextContent('read'))
  })

  it('says so when a document yields nothing readable', async () => {
    const user = userEvent.setup()
    api.ocrExtract.mockImplementation(async () => ({ fields: {}, provider: 'mock' }))
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const slot = await attach(user, /pan card/i, pdf('pan.pdf'))

    await waitFor(() =>
      expect(slot.querySelector('.ui-slot-status')).toHaveTextContent(/nothing readable/i)
    )
  })

  it('survives an OCR failure without blocking the form', async () => {
    const user = userEvent.setup()
    api.ocrExtract.mockImplementation(() => Promise.reject(new Error('ocr down')))
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const slot = await attach(user, /aadhaar card/i, pdf('aadhaar.pdf'))

    await waitFor(() =>
      expect(slot.querySelector('.ui-slot-status')).toHaveTextContent(/could not read/i)
    )
    // Still fully usable by hand.
    await fillIdentity(user)
    expect(submitButton()).toBeEnabled()
  })

  it('does not OCR the safety certificate (no parseable expiry)', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await attach(user, /safety training certificate/i, pdf('safety.pdf'))

    expect(api.ocrExtract).not.toHaveBeenCalled()
  })

  // --- Thumbnails ----------------------------------------------------------
  it('shows a thumbnail so the OCR can be checked against the document', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const slot = await attach(user, /aadhaar card/i, png('aadhaar.png'))

    const thumb = slot.querySelector('.ui-thumb')
    expect(thumb).toBeInTheDocument()
    expect(thumb.querySelector('img')).toHaveAttribute('alt', expect.stringMatching(/aadhaar/i))
    expect(screen.getAllByText(/click to enlarge/i).length).toBeGreaterThan(0)
  })

  it('renders a PDF thumbnail in a frame', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const slot = await attach(user, /pan card/i, pdf('pan.pdf'))

    expect(slot.querySelector('.ui-thumb iframe')).toBeInTheDocument()
  })

  it('drops the thumbnail when the file is removed', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    const slot = await attach(user, /aadhaar card/i, png('aadhaar.png'))
    expect(slot.querySelector('.ui-thumb')).toBeInTheDocument()

    await user.click(slot.querySelector('button.ghost'))
    expect(slot.querySelector('.ui-thumb')).not.toBeInTheDocument()
  })

  // --- Resume --------------------------------------------------------------
  it('parses the resume on attach and previews it without saving', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await attach(user, /resume \/ cv/i, pdf('resume.pdf'))

    expect(await screen.findByText('6 yrs')).toBeInTheDocument()
    expect(screen.getByText('Welder')).toBeInTheDocument()
    expect(screen.getByText(/nothing is stored until you submit/i)).toBeInTheDocument()
    expect(api.onboardWorker).not.toHaveBeenCalled()
  })

  it('guesses the trade from the first resume skill', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    await attach(user, /resume \/ cv/i, pdf('resume.pdf'))

    await waitFor(() => expect(screen.getByLabelText(/skill \/ trade/i)).toHaveValue('Welder'))
  })

  // --- Validation + submit -------------------------------------------------
  it('keeps submit disabled until the identity fields are complete', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    expect(submitButton()).toBeDisabled()
    await fillIdentity(user)
    expect(submitButton()).toBeEnabled()
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
    await attach(user, /aadhaar card/i, pdf('aadhaar.pdf'))
    await user.click(submitButton())

    await waitFor(() => expect(api.onboardWorker).toHaveBeenCalled())
    const [, formData] = api.onboardWorker.mock.calls[0]
    expect(formData.get('name')).toBe('Mahesh Patil')
    expect(formData.get('aadhar_number')).toBe('100000000042')
    expect(formData.get('skill_type')).toBe('Mason')
    expect(formData.get('aadhaar_file')).toBeInstanceOf(File)
    expect(formData.get('color_blindness')).toBe('false')
  })

  it('counts the attached documents', async () => {
    const user = userEvent.setup()
    renderWithAuth(<UnifiedIntakeOverlay open onClose={vi.fn()} />)

    expect(screen.getByText(/0 of 6 documents attached/)).toBeInTheDocument()
    await attach(user, /pan card/i, pdf('pan.pdf'))
    expect(screen.getByText(/1 of 6 documents attached/)).toBeInTheDocument()
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

describe('toFormPatch', () => {
  it('maps each document to the fields it should fill', () => {
    expect(toFormPatch('aadhaar_file', { name: 'A B', aadhar_number: '1234 5678 9012' }))
      .toEqual({ name: 'A B', aadhar_number: '123456789012' })
    expect(toFormPatch('pan_file', { document_number: 'abcde1234f' }))
      .toEqual({ pan_number: 'ABCDE1234F' })
    expect(toFormPatch('pvc_file', { certificate_number: 'X-1', issue_date: '2026-01-01' }))
      .toEqual({ certificate_number: 'X-1', issue_date: '2026-01-01' })
    expect(toFormPatch('resume_file', { name: 'R K', skills: ['Welder', 'Fitter'] }))
      .toEqual({ name: 'R K', skill_type: 'Welder' })
  })

  it('tolerates an empty extraction', () => {
    expect(toFormPatch('aadhaar_file', {})).toEqual({ name: '', aadhar_number: '' })
    expect(toFormPatch('unknown_slot', { x: 1 })).toEqual({})
  })
})
