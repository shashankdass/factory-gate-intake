import { useEffect, useRef, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../context/AuthContext.jsx'
import { useExpiryCheck } from './IntakeWorkbench.jsx'

// One screen, one submission: all five validation pillars plus the resume.
// Everything is uploaded to the private bucket concurrently by the backend, so
// six documents cost about as much wall-clock as the slowest single file.
const SLOTS = [
  { key: 'aadhaar_file', label: 'Aadhaar card', hint: 'Identity — required for gate entry' },
  { key: 'pan_file', label: 'PAN card', hint: 'Identity' },
  { key: 'safety_file', label: 'Safety Training certificate', hint: 'Expires — set the date' },
  { key: 'medical_file', label: 'Medical fitness report', hint: 'Valid 1 year from exam date' },
  { key: 'pvc_file', label: 'Police verification (PVC)', hint: 'Valid 1 year from issue date' },
  { key: 'resume_file', label: 'Resume / CV', hint: 'Parsed into a searchable profile' },
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
}

export default function UnifiedIntakeOverlay({ open, onClose, onCreated }) {
  const { token } = useAuth()
  const [form, setForm] = useState(EMPTY)
  const [files, setFiles] = useState({})
  const [busy, setBusy] = useState(false)
  const [scanning, setScanning] = useState(false)
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
      setResumePreview(null)
      setError(null)
      setDone(null)
    }
  }, [open])

  // Live 365-day checks, so an expired document is caught before submitting
  // rather than bounced back by the server.
  const medical = useExpiryCheck(form.exam_date)
  const police = useExpiryCheck(form.issue_date)

  if (!open) return null

  function pick(key, file) {
    setFiles((f) => ({ ...f, [key]: file || undefined }))
    setError(null)
  }

  // Read the resume before committing, so the contractor can sanity-check what
  // was extracted (and fix the worker's name from it) before anything is saved.
  async function scanResume() {
    if (!files.resume_file) return
    setScanning(true)
    setError(null)
    try {
      const fd = new FormData()
      fd.append('resume', files.resume_file)
      const res = await api.parseResume(token, fd)
      setResumePreview(res)
      if (res.name && !form.name) set('name', res.name)
    } catch (e) {
      setError(e.message)
    } finally {
      setScanning(false)
    }
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

  const identityReady =
    form.name.trim() && form.skill_type.trim() && form.aadhar_number.length === 12
  const attached = Object.values(files).filter(Boolean).length

  return (
    <div className="ui-overlay" role="dialog" aria-modal="true" aria-label="Unified worker intake">
      <div className="ui-panel" ref={dialogRef} tabIndex={-1}>
        <header className="ui-head">
          <div>
            <h2>Unified Worker Intake</h2>
            <p className="muted">
              Onboard a worker and all six documents in a single pass — Aadhaar, PAN,
              Safety certificate, Medical, Police verification and Resume.
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
            {/* --- Worker identity --- */}
            <section className="ui-section">
              <h3>1 · Worker</h3>
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

            {/* --- Documents --- */}
            <section className="ui-section">
              <h3>2 · Documents</h3>
              <div className="ui-slots">
                {SLOTS.map((slot) => (
                  <FileSlot
                    key={slot.key}
                    slot={slot}
                    file={files[slot.key]}
                    onPick={(f) => pick(slot.key, f)}
                  />
                ))}
              </div>
            </section>

            {/* --- Document details --- */}
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
            </section>

            {/* --- Resume --- */}
            <section className="ui-section">
              <h3>4 · Resume</h3>
              <div className="row gap">
                <button
                  className="btn small"
                  disabled={!files.resume_file || scanning}
                  onClick={scanResume}
                >
                  {scanning ? 'Reading…' : '🔍 Scan resume before saving'}
                </button>
                <span className="muted">
                  Name, phone and email are encrypted at rest; skills and experience stay
                  searchable.
                </span>
              </div>
              {resumePreview && <ResumePreview data={resumePreview} />}
            </section>

            {error && <div className="alert error">⚠ {error}</div>}
          </div>
        )}

        {!done && (
          <footer className="ui-foot">
            <span className="muted">
              {attached} of {SLOTS.length} documents attached
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

function FileSlot({ slot, file, onPick }) {
  const inputRef = useRef(null)
  return (
    <div className={`ui-slot ${file ? 'filled' : ''}`}>
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf"
        hidden
        onChange={(e) => onPick(e.target.files?.[0])}
      />
      <div className="ui-slot-main">
        <div className="ui-slot-label">{slot.label}</div>
        <div className="muted">{file ? file.name : slot.hint}</div>
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
      <div className="muted">Read via {data.provider}. Nothing is saved until you submit.</div>
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
            Finish these in Verification & Testing — the worker is already in your pool.
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
