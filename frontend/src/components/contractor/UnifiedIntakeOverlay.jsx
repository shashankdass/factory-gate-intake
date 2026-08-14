import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../context/AuthContext.jsx'
import { useExpiryCheck } from './IntakeWorkbench.jsx'

// One screen, one submission: all five validation pillars plus the resume.
//
// Documents come FIRST and each is OCR'd the moment it is attached, so the
// fields below arrive pre-filled and the contractor verifies rather than
// transcribes. Everything stays editable — OCR is a first draft, never the final
// word, which is why nothing is committed until Submit.
//
// `read` says what each document can populate:
//   IDENTITY / MEDICAL / POLICE -> /intake/ocr-extract/
//   resume                      -> /resume/parse/ (preview only, not persisted)
// The safety certificate carries no reliably parseable expiry, so it stays manual.
const SLOTS = [
  {
    key: 'aadhaar_file',
    label: 'Aadhaar card',
    hint: 'Identity — required for gate entry',
    fills: 'name + Aadhaar number',
    // The one mandatory upload: it is the identity the gate scans against.
    required: true,
    read: { kind: 'ocr', docType: 'IDENTITY', requirement: 'Aadhar', slot: 'aadhaar' },
  },
  {
    key: 'pan_file',
    label: 'PAN card',
    hint: 'Identity',
    fills: 'PAN number',
    read: { kind: 'ocr', docType: 'IDENTITY', requirement: 'PAN', slot: 'pan' },
  },
  {
    key: 'safety_file',
    label: 'Safety Training certificate',
    hint: 'Expires — set the date yourself',
    fills: null,
    read: null,
  },
  {
    key: 'medical_file',
    label: 'Medical fitness report',
    hint: 'Valid 1 year from exam date',
    fills: 'exam date, vision, blood type, flags',
    read: { kind: 'ocr', docType: 'MEDICAL', slot: 'medical' },
  },
  {
    key: 'pvc_file',
    label: 'Police verification (PVC)',
    hint: 'Valid 1 year from issue date',
    fills: 'certificate number + issue date',
    read: { kind: 'ocr', docType: 'POLICE', slot: 'pvc' },
  },
  {
    key: 'bank_file',
    label: 'Cancelled cheque or passbook',
    hint: 'Where wages are paid',
    fills: 'account number, IFSC, bank',
    read: { kind: 'ocr', docType: 'BANK', slot: 'bank' },
  },
  {
    key: 'resume_file',
    label: 'Resume / CV',
    hint: 'Parsed into a searchable profile',
    fills: 'name and trade',
    read: { kind: 'resume' },
  },
]

const EMPTY = {
  name: '',
  aadhar_number: '',
  skill_type: '',
  pan_number: '',
  safety_expiry: '',
  exam_date: '',
  vision: '',
  blood_type: '',
  color_blindness: false,
  vertigo: false,
  certificate_number: '',
  issue_date: '',
  bank_account_number: '',
  ifsc: '',
  bank_name: '',
}

/** Map one OCR / resume payload onto the form's field names. */
export function toFormPatch(slotKey, fields) {
  const f = fields || {}
  switch (slotKey) {
    case 'aadhaar_file':
      return {
        name: f.name || '',
        aadhar_number: String(f.aadhar_number || '').replace(/\D/g, '').slice(0, 12),
      }
    case 'pan_file':
      return { pan_number: String(f.document_number || f.aadhar_number || '').toUpperCase() }
    case 'medical_file':
      return {
        exam_date: f.exam_date || '',
        vision: f.vision || '',
        blood_type: f.blood_type || '',
        color_blindness: !!f.color_blindness,
        vertigo: !!f.vertigo,
      }
    case 'pvc_file':
      return {
        certificate_number: f.certificate_number || '',
        issue_date: f.issue_date || '',
      }
    case 'bank_file':
      return {
        bank_account_number: String(f.bank_account_number || '').replace(/\D/g, ''),
        ifsc: String(f.ifsc || '').toUpperCase(),
        bank_name: f.bank_name || '',
      }
    case 'resume_file':
      // The first parsed skill is a reasonable guess at the trade.
      return { name: f.name || '', skill_type: (f.skills && f.skills[0]) || '' }
    default:
      return {}
  }
}

export default function UnifiedIntakeOverlay({ open, onClose, onCreated }) {
  const { token } = useAuth()
  const [form, setForm] = useState(EMPTY)
  const [files, setFiles] = useState({})
  const [reading, setReading] = useState({}) // { slotKey: 'reading'|'done'|'none'|'error' }
  // Object URLs for the attached files, so each slot can show a thumbnail to
  // check the OCR against. Revoked whenever a file is replaced or the overlay
  // closes — leaking these pins the whole file in memory.
  const [previews, setPreviews] = useState({}) // { slotKey: {url, kind} }
  const [busy, setBusy] = useState(false)
  const [slotErrors, setSlotErrors] = useState({}) // { slotKey: 'why it was refused' }
  const [slotWarnings, setSlotWarnings] = useState({}) // { slotKey: [note, …] }
  const [resumePreview, setResumePreview] = useState(null)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)
  const dialogRef = useRef(null)

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))

  // Close on Escape — an overlay that traps the user is worse than no overlay.
  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && !busy && onClose?.()
    window.addEventListener('keydown', onKey)
    dialogRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onClose])

  useEffect(() => {
    if (!open) {
      setForm(EMPTY)
      setFiles({})
      setReading({})
      setSlotErrors({})
      setSlotWarnings({})
      setPreviews((p) => {
        Object.values(p).forEach((v) => v?.url && URL.revokeObjectURL(v.url))
        return {}
      })
      setResumePreview(null)
      setError(null)
      setDone(null)
    }
  }, [open])

  // Last line of defence against leaking object URLs if the overlay unmounts
  // while still open.
  useEffect(
    () => () => Object.values(previews).forEach((v) => v?.url && URL.revokeObjectURL(v.url)),
    [previews]
  )

  // Live 365-day checks, so an expired document is caught before submitting
  // rather than bounced back by the server.
  const medical = useExpiryCheck(form.exam_date)
  const police = useExpiryCheck(form.issue_date)

  if (!open) return null

  function withFile(field, file) {
    const fd = new FormData()
    fd.append(field, file)
    return fd
  }

  /** Only fill blanks — never clobber something the contractor already typed. */
  function mergeBlanks(patch) {
    setForm((prev) => {
      const next = { ...prev }
      for (const [key, value] of Object.entries(patch)) {
        if (value === '' || value == null) continue
        const current = prev[key]
        if (typeof current === 'boolean' ? current === false : !current) {
          next[key] = value
        }
      }
      return next
    })
  }

  /** Drop a file that turned out to be the wrong kind of document. */
  function rejectSlot(slot, message) {
    setFiles((f) => ({ ...f, [slot.key]: undefined }))
    setPreviews((prev) => {
      prev[slot.key]?.url && URL.revokeObjectURL(prev[slot.key].url)
      const next = { ...prev }
      delete next[slot.key]
      return next
    })
    setReading((r) => ({ ...r, [slot.key]: undefined }))
    setSlotErrors((e) => ({ ...e, [slot.key]: message }))
  }

  /** OCR one attached document and merge whatever it yields. */
  async function readSlot(slot, file) {
    if (!slot.read || !file) return
    setReading((r) => ({ ...r, [slot.key]: 'reading' }))
    try {
      let fields
      if (slot.read.kind === 'resume') {
        const res = await api.parseResume(token, withFile('resume', file))
        setResumePreview(res)
        fields = res
      } else {
        const fd = withFile('file', file)
        fd.append('doc_type', slot.read.docType)
        if (slot.read.requirement) fd.append('requirement_name', slot.read.requirement)
        if (slot.read.slot) fd.append('slot', slot.read.slot)
        const res = await api.ocrExtract(token, fd)

        // Wrong document for this slot — refuse it outright rather than
        // storing a resume as someone's identity evidence. Only a positive
        // identification of a *different* type gets here; an unreadable scan
        // is allowed through with a warning.
        if (res.check && res.check.status === 'MISMATCH') {
          rejectSlot(slot, res.check.message)
          return
        }
        if (res.check?.warnings?.length) {
          setSlotWarnings((w) => ({ ...w, [slot.key]: res.check.warnings }))
        }
        fields = res.fields
      }
      const patch = toFormPatch(slot.key, fields)
      const anything = Object.values(patch).some((v) => v !== '' && v !== false && v != null)
      mergeBlanks(patch)
      setReading((r) => ({ ...r, [slot.key]: anything ? 'done' : 'none' }))
    } catch {
      // A failed read is never fatal — those fields are simply typed by hand.
      setReading((r) => ({ ...r, [slot.key]: 'error' }))
    }
  }

  function pick(slot, file) {
    setFiles((f) => ({ ...f, [slot.key]: file || undefined }))
    setError(null)
    setSlotErrors((e) => ({ ...e, [slot.key]: undefined }))
    setSlotWarnings((w) => ({ ...w, [slot.key]: undefined }))

    // Swap the thumbnail, releasing whatever was there before.
    setPreviews((prev) => {
      prev[slot.key]?.url && URL.revokeObjectURL(prev[slot.key].url)
      const next = { ...prev }
      if (!file) {
        delete next[slot.key]
      } else {
        next[slot.key] = {
          url: URL.createObjectURL(file),
          kind: file.type.startsWith('image/')
            ? 'image'
            : file.type === 'application/pdf'
            ? 'pdf'
            : 'other',
          name: file.name,
        }
      }
      return next
    })

    if (!file) {
      setReading((r) => ({ ...r, [slot.key]: undefined }))
      if (slot.key === 'resume_file') setResumePreview(null)
      return
    }
    readSlot(slot, file)
  }

  /** Re-run every attached document (after swapping files, or on demand). */
  async function readAll() {
    await Promise.all(
      SLOTS.filter((s) => s.read && files[s.key]).map((s) => readSlot(s, files[s.key]))
    )
  }

  async function submit() {
    if (medical.expired || police.expired) return
    setBusy(true)
    setError(null)
    try {
      const fd = new FormData()
      Object.entries(form).forEach(([key, value]) => {
        if (typeof value === 'boolean') fd.append(key, value ? 'true' : 'false')
        else if (value !== '') fd.append(key, value)
      })
      Object.entries(files).forEach(([key, file]) => file && fd.append(key, file))

      const res = await api.onboardWorker(token, fd)
      setDone(res)
      onCreated?.(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const aadhaarAttached = !!files.aadhaar_file
  const identityReady =
    form.name.trim() &&
    form.skill_type.trim() &&
    form.aadhar_number.length === 12 &&
    aadhaarAttached
  const attached = Object.values(files).filter(Boolean).length
  const anyReading = Object.values(reading).some((s) => s === 'reading')

  return (
    <div className="ui-overlay" role="dialog" aria-modal="true" aria-label="Unified worker intake">
      <div className="ui-panel" ref={dialogRef} tabIndex={-1}>
        <header className="ui-head">
          <div>
            <h2>Unified Worker Intake</h2>
            <p className="muted">
              Attach the documents first — each is read automatically and fills in the
              fields below. Check what it found, correct anything wrong, then submit once.
            </p>
          </div>
          <button className="btn ghost" onClick={onClose} disabled={busy}>
            ✕ Close
          </button>
        </header>

        {done ? (
          <IntakeResult result={done} onClose={onClose} />
        ) : (
          <div className="ui-body">
            {/* --- 1. Documents, read on attach --- */}
            <section className="ui-section">
              <div className="ui-section-head">
                <h3>1 · Documents</h3>
                {attached > 0 && (
                  <button className="btn small ghost" onClick={readAll} disabled={anyReading}>
                    {anyReading ? 'Reading…' : '↻ Re-read all'}
                  </button>
                )}
              </div>
              <div className="ui-slots">
                {SLOTS.map((slot) => (
                  <FileSlot
                    key={slot.key}
                    slot={slot}
                    file={files[slot.key]}
                    status={reading[slot.key]}
                    preview={previews[slot.key]}
                    error={slotErrors[slot.key]}
                    warnings={slotWarnings[slot.key]}
                    onPick={(f) => pick(slot, f)}
                  />
                ))}
              </div>
            </section>

            {/* --- 2. Worker identity, prefilled from the Aadhaar / resume --- */}
            <section className="ui-section">
              <h3>2 · Worker</h3>
              <div className="ui-grid">
                <label className="wb-field">
                  <span>Full name</span>
                  <input value={form.name} onChange={(e) => set('name', e.target.value)} />
                </label>
                <label className="wb-field">
                  <span>Aadhaar number (12 digits)</span>
                  <input
                    inputMode="numeric"
                    maxLength={12}
                    value={form.aadhar_number}
                    onChange={(e) =>
                      set('aadhar_number', e.target.value.replace(/\D/g, '').slice(0, 12))
                    }
                  />
                </label>
                <label className="wb-field">
                  <span>Skill / trade</span>
                  <input
                    placeholder="e.g. Carpenter"
                    value={form.skill_type}
                    onChange={(e) => set('skill_type', e.target.value)}
                  />
                </label>
              </div>
            </section>

            {/* --- 3. Document details, prefilled per document --- */}
            <section className="ui-section">
              <h3>3 · Details</h3>
              <div className="ui-grid">
                <label className="wb-field">
                  <span>PAN number</span>
                  <input
                    placeholder="ABCDE1234F"
                    value={form.pan_number}
                    onChange={(e) => set('pan_number', e.target.value.toUpperCase())}
                  />
                </label>
                <label className="wb-field">
                  <span>Safety certificate expiry</span>
                  <input
                    type="date"
                    value={form.safety_expiry}
                    onChange={(e) => set('safety_expiry', e.target.value)}
                  />
                </label>
              </div>

              <div className="ui-subhead">Medical</div>
              {medical.expired && (
                <div className="wb-error-banner">
                  This medical is already expired ({medical.daysOld} days old — the
                  window is 365 days). It cannot be accepted.
                </div>
              )}
              <div className="ui-grid">
                <label className="wb-field">
                  <span>Exam date</span>
                  <input
                    type="date"
                    value={form.exam_date}
                    onChange={(e) => set('exam_date', e.target.value)}
                  />
                </label>
                <label className="wb-field">
                  <span>Vision</span>
                  <input
                    placeholder="6/6"
                    value={form.vision}
                    onChange={(e) => set('vision', e.target.value)}
                  />
                </label>
                <label className="wb-field">
                  <span>Blood type</span>
                  <input
                    placeholder="O+"
                    value={form.blood_type}
                    onChange={(e) => set('blood_type', e.target.value)}
                  />
                </label>
              </div>
              <div className="ui-checks">
                <label className="wb-check">
                  <input
                    type="checkbox"
                    checked={form.color_blindness}
                    onChange={(e) => set('color_blindness', e.target.checked)}
                  />
                  Colour blindness detected
                </label>
                <label className="wb-check">
                  <input
                    type="checkbox"
                    checked={form.vertigo}
                    onChange={(e) => set('vertigo', e.target.checked)}
                  />
                  Vertigo detected
                </label>
              </div>

              <div className="ui-subhead">Police verification</div>
              {police.expired && (
                <div className="wb-error-banner">
                  This PVC is already expired ({police.daysOld} days old). It cannot be
                  accepted.
                </div>
              )}
              <div className="ui-grid">
                <label className="wb-field">
                  <span>Certificate number</span>
                  <input
                    value={form.certificate_number}
                    onChange={(e) => set('certificate_number', e.target.value)}
                  />
                </label>
                <label className="wb-field">
                  <span>Issue date</span>
                  <input
                    type="date"
                    value={form.issue_date}
                    onChange={(e) => set('issue_date', e.target.value)}
                  />
                </label>
              </div>

              <div className="ui-subhead">Bank account (for wages)</div>
              <div className="ui-grid">
                <label className="wb-field">
                  <span>Account number</span>
                  <input
                    inputMode="numeric"
                    placeholder="50100123456789"
                    value={form.bank_account_number}
                    onChange={(e) =>
                      set('bank_account_number', e.target.value.replace(/\D/g, '').slice(0, 18))
                    }
                  />
                </label>
                <label className="wb-field">
                  <span>IFSC</span>
                  <input
                    placeholder="HDFC0001234"
                    maxLength={11}
                    value={form.ifsc}
                    onChange={(e) => set('ifsc', e.target.value.toUpperCase())}
                  />
                </label>
                <label className="wb-field">
                  <span>Bank name</span>
                  <input
                    placeholder="HDFC Bank"
                    value={form.bank_name}
                    onChange={(e) => set('bank_name', e.target.value)}
                  />
                </label>
              </div>
              {form.ifsc && !/^[A-Z]{4}0[A-Z0-9]{6}$/.test(form.ifsc) && (
                <div className="inline-msg error">
                  An IFSC is 4 letters, a zero, then 6 characters — e.g. HDFC0001234.
                </div>
              )}
              <div className="muted">
                The account number is encrypted at rest, like the worker's phone and
                email. Only the last four digits are shown back on shared screens.
              </div>
            </section>

            {/* --- 4. Resume, parsed on attach --- */}
            {resumePreview && (
              <section className="ui-section">
                <h3>4 · Resume profile</h3>
                <ResumePreview data={resumePreview} />
              </section>
            )}

            {error && <div className="alert error">⚠ {error}</div>}
          </div>
        )}

        {!done && (
          <footer className="ui-foot">
            <span className="muted">
              {attached} of {SLOTS.length} documents attached
              {anyReading ? ' · reading…' : ''}
              {!aadhaarAttached && (
                <span className="ui-required-note"> · Aadhaar card required</span>
              )}
            </span>
            <div className="row gap">
              <button className="btn ghost" onClick={onClose} disabled={busy}>
                Cancel
              </button>
              <button
                className="btn primary lg"
                disabled={busy || !identityReady || medical.expired || police.expired}
                onClick={submit}
              >
                {busy ? 'Onboarding…' : 'Create worker & upload all documents'}
              </button>
            </div>
          </footer>
        )}
      </div>
    </div>
  )
}

const STATUS_LABEL = {
  reading: { text: '⏳ reading…', tone: 'amber' },
  done: { text: '✅ read', tone: 'green' },
  none: { text: '— nothing readable', tone: 'grey' },
  error: { text: '⚠ could not read', tone: 'amber' },
}

function FileSlot({ slot, file, status, preview, error, warnings, onPick }) {
  const inputRef = useRef(null)
  const badge = status ? STATUS_LABEL[status] : null
  return (
    <div className={`ui-slot ${file ? 'filled' : ''} ${error ? 'rejected' : ''}`}>
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf"
        hidden
        onChange={(e) => onPick(e.target.files?.[0])}
      />

      <div className="ui-slot-row">
        <div className="ui-slot-main">
          <div className="ui-slot-label">
            {slot.label}
            {slot.required && <span className="ui-required">required</span>}
          </div>
          <div className="muted">{file ? file.name : slot.hint}</div>
          {file && badge && (
            <span className={`badge ${badge.tone} ui-slot-status`}>{badge.text}</span>
          )}
          {!file && slot.fills && (
            <div className="muted ui-slot-fills">auto-fills {slot.fills}</div>
          )}
        </div>
        {file ? (
          <button className="btn small ghost" onClick={() => onPick(null)}>
            Remove
          </button>
        ) : (
          <button className="btn small" onClick={() => inputRef.current?.click()}>
            Attach
          </button>
        )}
      </div>

      {/* The file was the wrong kind of document and was not kept. */}
      {error && <div className="ui-slot-error">⚠ {error}</div>}

      {warnings?.map((warning) => (
        <div key={warning} className="ui-slot-warning">{warning}</div>
      ))}

      {/* Thumbnail so the extracted values can be checked against the document
          itself. Click to open it full size in a new tab. */}
      {preview && (
        <button
          type="button"
          className="ui-thumb"
          onClick={() => window.open(preview.url, '_blank', 'noopener')}
          title="Open full size"
        >
          {preview.kind === 'image' ? (
            <img src={preview.url} alt={`${slot.label} preview`} />
          ) : preview.kind === 'pdf' ? (
            <iframe title={`${slot.label} preview`} src={preview.url} />
          ) : (
            <span className="muted">No preview for this file type</span>
          )}
          <span className="ui-thumb-hint">Click to enlarge</span>
        </button>
      )}
    </div>
  )
}

function ResumePreview({ data }) {
  const rows = [
    ['Name', data.name],
    ['Phone', data.phone],
    ['Email', data.email],
    ['Place', data.place],
    ['Stream', data.stream],
    ['Category', data.category],
    ['Experience', data.years_of_experience != null ? `${data.years_of_experience} yrs` : null],
    ['Qualification', data.qualification],
  ]
  return (
    <div className="ui-resume">
      {data.note && <div className="inline-msg error">{data.note}</div>}
      <div className="ui-resume-grid">
        {rows.map(([label, value]) => (
          <div key={label} className="ui-resume-row">
            <span className="doc-k">{label}</span>
            <span className="doc-v">{value || '—'}</span>
          </div>
        ))}
      </div>
      {data.skills?.length > 0 && (
        <div className="ui-skills">
          {data.skills.map((skill) => (
            <span key={skill} className="pill">
              {skill}
            </span>
          ))}
        </div>
      )}
      <div className="muted">
        Read via {data.provider}. Name, phone and email are encrypted when saved;
        nothing is stored until you submit.
      </div>
    </div>
  )
}

function IntakeResult({ result, onClose }) {
  const compliance = result.compliance || {}
  const gaps = compliance.gaps || []
  return (
    <div className="ui-body">
      <div className="ui-done">
        <div className="ui-done-main">✅ {result.worker?.name} onboarded</div>
        <div className="muted">
          {result.documents_stored?.length || 0} document(s) stored in the private bucket:{' '}
          {(result.documents_stored || []).join(', ') || 'none'}
        </div>
      </div>

      {compliance.is_compliant ? (
        <div className="pe-feedback ok">
          ✅ Fully compliant — this worker can be selected for a deployment list now.
        </div>
      ) : (
        <div className="revision-callout">
          <strong>Still outstanding ({gaps.length}):</strong>
          <ul className="gap-list">
            {gaps.map((gap) => (
              <li key={`${gap.pillar || gap.requirement_id}`} className="gap-row">
                <span className="gap-name">{gap.requirement_name}</span>{' '}
                <span className={`chip ${(gap.reason || '').toLowerCase()}`}>{gap.reason}</span>
                {gap.detail && <div className="reject-reason">{gap.detail}</div>}
              </li>
            ))}
          </ul>
          <div className="muted">
            Finish these in Verification &amp; Testing — the worker is already in your pool.
          </div>
        </div>
      )}

      <div className="row gap" style={{ marginTop: 14 }}>
        <button className="btn primary" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  )
}
