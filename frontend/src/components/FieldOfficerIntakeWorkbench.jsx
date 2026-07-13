import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'

// What the Field Officer can process. Identity docs map to a WorkerDocument
// requirement; Medical/Police map to their structured 1-year-validity records.
const DOC_TYPES = [
  { value: 'IDENTITY:Aadhar', label: 'Aadhar Card', formType: 'IDENTITY', requirement: 'Aadhar' },
  { value: 'IDENTITY:PAN', label: 'PAN Card', formType: 'IDENTITY', requirement: 'PAN' },
  {
    value: 'IDENTITY:Safety Training',
    label: 'Safety Training Certificate',
    formType: 'IDENTITY',
    requirement: 'Safety Training',
    expirable: true,
  },
  { value: 'MEDICAL:', label: 'Medical Exam', formType: 'MEDICAL' },
  { value: 'POLICE:', label: 'Police Verification (PVC)', formType: 'POLICE' },
]

// Testing menu — autofills the form with simulated OCR for the matching type.
const SAMPLES = [
  { key: 'aadhar_clean', label: 'Sample Clean Aadhar Card', doc: 'IDENTITY:Aadhar' },
  { key: 'medical_expired', label: 'Sample Expired Medical Form', doc: 'MEDICAL:' },
  { key: 'pvc_valid', label: 'Sample Valid PVC', doc: 'POLICE:' },
]

const VIDEO_SRC = 'https://www.w3schools.com/html/mov_bbb.mp4'

// --- Reusable hook: is an ISO date string more than 365 days old? ----------
function useExpiryCheck(isoDate) {
  return useMemo(() => {
    if (!isoDate) return { expired: false, daysOld: null }
    const then = new Date(isoDate)
    if (Number.isNaN(then.getTime())) return { expired: false, daysOld: null }
    const daysOld = Math.floor((Date.now() - then.getTime()) / 86_400_000)
    return { expired: daysOld > 365, daysOld }
  }, [isoDate])
}

export default function FieldOfficerIntakeWorkbench() {
  const { token } = useAuth()
  const [workers, setWorkers] = useState([])
  const [workerId, setWorkerId] = useState(null)
  const [docSel, setDocSel] = useState('') // one of DOC_TYPES[].value
  const [form, setForm] = useState({})
  const [sampleTemplate, setSampleTemplate] = useState(null) // mock left-pane preview
  const [upload, setUpload] = useState(null) // { file, url, kind }
  const [busy, setBusy] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [msg, setMsg] = useState(null)
  // Add-worker-from-scratch panel.
  const [contractors, setContractors] = useState([])
  const [showAdd, setShowAdd] = useState(false)
  const [newWorker, setNewWorker] = useState({ name: '', aadhar_number: '', skill_type: '', contractor: '' })
  const [addBusy, setAddBusy] = useState(false)
  const [addMsg, setAddMsg] = useState(null)
  const fileInputRef = useRef(null)

  const current = DOC_TYPES.find((d) => d.value === docSel) || null
  const formType = current?.formType || null

  const loadWorkers = (selectId) =>
    api.workers(token).then((w) => {
      setWorkers(w)
      setWorkerId((id) => selectId ?? id ?? (w[0]?.id ?? null))
      return w
    })

  useEffect(() => {
    if (!token) return
    loadWorkers()
    api.contractors(token).then(setContractors).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  async function addWorker() {
    const { name, aadhar_number, skill_type } = newWorker
    if (!name.trim() || !skill_type.trim() || aadhar_number.trim().length !== 12) {
      setAddMsg({ tone: 'error', text: 'Name, 12-digit Aadhar and skill are required.' })
      return
    }
    setAddBusy(true)
    setAddMsg(null)
    try {
      const created = await api.createWorker(token, {
        name: name.trim(),
        aadhar_number: aadhar_number.trim(),
        skill_type: skill_type.trim(),
        contractor: newWorker.contractor || null,
      })
      await loadWorkers(created.id) // add + auto-select the new worker
      setNewWorker({ name: '', aadhar_number: '', skill_type: '', contractor: '' })
      setShowAdd(false)
      setMsg({ tone: 'success', text: `Added ${created.name}. Now upload and verify their documents.` })
    } catch (e) {
      setAddMsg({ tone: 'error', text: e.message })
    } finally {
      setAddBusy(false)
    }
  }

  // Revoke the object URL when the upload changes / unmounts.
  useEffect(() => () => upload?.url && URL.revokeObjectURL(upload.url), [upload])

  function selectDocType(value) {
    setDocSel(value)
    setForm({})
    setSampleTemplate(null)
    setMsg(null)
  }

  // Load simulated OCR into the form for on-screen testing (no physical doc).
  async function loadSample(key) {
    setMsg(null)
    if (!key) return
    const s = SAMPLES.find((x) => x.key === key)
    try {
      const data = await api.mockOcr(token, key)
      setDocSel(s.doc)
      setForm({ ...data.fields })
      setSampleTemplate({ form_type: data.form_type, fields: data.fields })
    } catch (e) {
      setMsg({ tone: 'error', text: e.message })
    }
  }

  function onFile(f) {
    if (!f) return
    if (upload?.url) URL.revokeObjectURL(upload.url)
    const kind = f.type.startsWith('image/')
      ? 'image'
      : f.type === 'application/pdf'
      ? 'pdf'
      : 'other'
    setUpload({ file: f, url: URL.createObjectURL(f), kind })
    setMsg(null)
  }

  const setField = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  // Run real OCR on the uploaded scan and prefill the form for review.
  async function extractFromScan() {
    if (!upload?.file || !formType) return
    setExtracting(true)
    setMsg(null)
    try {
      const fd = new FormData()
      fd.append('file', upload.file)
      fd.append('doc_type', formType)
      if (formType === 'IDENTITY') fd.append('requirement_name', current.requirement)
      const res = await api.ocrExtract(token, fd)
      setForm((f) => ({ ...f, ...res.fields }))
      setSampleTemplate(null)
      setMsg(
        res.note
          ? { tone: 'error', text: res.note }
          : { tone: 'success', text: `Read from scan via ${res.provider} — verify the values below.` }
      )
    } catch (e) {
      setMsg({ tone: 'error', text: e.message })
    } finally {
      setExtracting(false)
    }
  }

  const criticalDate =
    formType === 'MEDICAL' ? form.exam_date : formType === 'POLICE' ? form.issue_date : null
  const { expired, daysOld } = useExpiryCheck(criticalDate)

  async function commit() {
    if (!workerId || !formType || expired) return
    setBusy(true)
    setMsg(null)
    try {
      const fd = new FormData()
      fd.append('worker', workerId)
      fd.append('doc_type', formType)
      if (formType === 'IDENTITY') {
        fd.append('requirement_name', current.requirement)
        fd.append('document_number', form.aadhar_number || form.document_number || '')
        if (form.expiry_date) fd.append('expiry_date', form.expiry_date)
      } else if (formType === 'MEDICAL') {
        fd.append('exam_date', form.exam_date || '')
        fd.append('color_blindness', form.color_blindness ? 'true' : 'false')
        fd.append('vertigo', form.vertigo ? 'true' : 'false')
        fd.append('vision', form.vision || '')
        fd.append('blood_type', form.blood_type || '')
      } else if (formType === 'POLICE') {
        fd.append('certificate_number', form.certificate_number || '')
        fd.append('issue_date', form.issue_date || '')
        fd.append('verification_status', form.verification_status || 'Verified')
      }
      if (upload?.file) fd.append('file', upload.file)

      await api.verifyDocumentForm(token, fd)
      setMsg({
        tone: 'success',
        text: `✔ Verified & committed ${current.label} for ${
          workers.find((w) => w.id === workerId)?.name || 'worker'
        }.`,
      })
    } catch (e) {
      setMsg({ tone: 'error', text: e.message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="workbench">
      {/* Top controls: worker + document type + test autofill */}
      <div className="wb-controls">
        <label>
          Worker:&nbsp;
          <select value={workerId || ''} onChange={(e) => setWorkerId(Number(e.target.value))}>
            {workers.length === 0 && <option value="">— no workers yet —</option>}
            {workers.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name} · {w.skill_type}
              </option>
            ))}
          </select>
        </label>

        <label>
          Document type:&nbsp;
          <select value={docSel} onChange={(e) => selectDocType(e.target.value)}>
            <option value="">— select —</option>
            {DOC_TYPES.map((d) => (
              <option key={d.value} value={d.value}>
                {d.label}
              </option>
            ))}
          </select>
        </label>

        <label className="wb-sample">
          🧪 Test autofill:&nbsp;
          <select value="" onChange={(e) => loadSample(e.target.value)}>
            <option value="">— mock sample —</option>
            {SAMPLES.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>

        <button
          className={`btn small wb-add-toggle ${showAdd ? '' : 'primary'}`}
          onClick={() => {
            setShowAdd((v) => !v)
            setAddMsg(null)
          }}
        >
          {showAdd ? '✕ Cancel' : '➕ New worker'}
        </button>
      </div>

      {showAdd && (
        <div className="wb-addworker">
          <div className="wb-pane-title">Add a new worker to the registry</div>
          <div className="wb-add-grid">
            <label className="wb-field">
              <span>Full name</span>
              <input
                value={newWorker.name}
                onChange={(e) => setNewWorker({ ...newWorker, name: e.target.value })}
              />
            </label>
            <label className="wb-field">
              <span>Aadhar number (12 digits)</span>
              <input
                inputMode="numeric"
                maxLength={12}
                value={newWorker.aadhar_number}
                onChange={(e) =>
                  setNewWorker({
                    ...newWorker,
                    aadhar_number: e.target.value.replace(/\D/g, '').slice(0, 12),
                  })
                }
              />
            </label>
            <label className="wb-field">
              <span>Skill type</span>
              <input
                placeholder="e.g. Carpenter"
                value={newWorker.skill_type}
                onChange={(e) => setNewWorker({ ...newWorker, skill_type: e.target.value })}
              />
            </label>
            <label className="wb-field">
              <span>Assign to contractor</span>
              <select
                value={newWorker.contractor}
                onChange={(e) => setNewWorker({ ...newWorker, contractor: e.target.value })}
              >
                <option value="">— unassigned —</option>
                {contractors.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.email}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="wb-commit-row">
            <button className="btn primary" disabled={addBusy} onClick={addWorker}>
              {addBusy ? 'Adding…' : 'Create worker'}
            </button>
            {addMsg && <span className={`inline-msg ${addMsg.tone}`}>{addMsg.text}</span>}
          </div>
          <div className="muted">
            After creating, the worker is selected below — upload and verify their
            documents, or assign to a contractor so they appear in that contractor's list.
          </div>
        </div>
      )}

      <div className="wb-split">
        {/* LEFT — document previewer (real upload or mock template) */}
        <div className="wb-pane wb-preview">
          <div className="wb-pane-title">Document Previewer</div>

          <div
            className="wb-upload"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault()
              onFile(e.dataTransfer.files?.[0])
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,application/pdf"
              hidden
              onChange={(e) => onFile(e.target.files?.[0])}
            />
            <button className="btn small" onClick={() => fileInputRef.current?.click()}>
              ⬆ Upload scan / photo
            </button>
            {upload && (
              <button
                className="btn small ghost"
                onClick={() => {
                  URL.revokeObjectURL(upload.url)
                  setUpload(null)
                }}
              >
                Remove
              </button>
            )}
            {upload && formType && (
              <button
                className="btn small primary"
                disabled={extracting}
                onClick={extractFromScan}
              >
                {extracting ? 'Reading…' : '🔍 Read values from scan'}
              </button>
            )}
            {upload && !formType && (
              <span className="muted">pick a document type to read fields</span>
            )}
            {!upload && <span className="muted">or drag a file here</span>}
          </div>

          {upload ? (
            <UploadedPreview upload={upload} />
          ) : sampleTemplate ? (
            <DocumentPreview template={sampleTemplate} />
          ) : (
            <div className="wb-empty">
              Upload the worker's physical document, or load a mock sample to test.
            </div>
          )}
        </div>

        {/* RIGHT — confirmation form */}
        <div className="wb-pane wb-form">
          <div className="wb-pane-title">Confirm &amp; Verify Values</div>

          {!formType && (
            <div className="wb-empty">Pick a document type to enter its fields.</div>
          )}

          {formType && expired && (
            <div className="wb-error-banner">
              Error: This document is already expired! ({daysOld} days old — the
              1-year window is 365 days.)
            </div>
          )}

          {formType === 'IDENTITY' && (
            <IdentityForm form={form} setField={setField} expirable={current.expirable} />
          )}
          {formType === 'MEDICAL' && <MedicalForm form={form} setField={setField} />}
          {formType === 'POLICE' && <PoliceForm form={form} setField={setField} />}

          {formType && (
            <div className="wb-commit-row">
              <button
                className="btn primary"
                disabled={busy || expired || !workerId}
                onClick={commit}
              >
                {busy ? 'Committing…' : 'Verify & Commit'}
              </button>
              {msg && <span className={`inline-msg ${msg.tone}`}>{msg.text}</span>}
            </div>
          )}
        </div>
      </div>

      <VideoStatusTracker token={token} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Left-pane previews
// ---------------------------------------------------------------------------
function UploadedPreview({ upload }) {
  if (upload.kind === 'image') {
    return (
      <div className="wb-file-preview">
        <img src={upload.url} alt={upload.file.name} />
        <div className="muted">{upload.file.name}</div>
      </div>
    )
  }
  if (upload.kind === 'pdf') {
    return (
      <div className="wb-file-preview">
        <iframe title="document" src={upload.url} />
        <div className="muted">{upload.file.name}</div>
      </div>
    )
  }
  return <div className="wb-empty">Attached: {upload.file.name}</div>
}

function DocumentPreview({ template }) {
  const f = template.fields
  if (template.form_type === 'IDENTITY') {
    return (
      <div className="doc-card doc-aadhar">
        <div className="doc-aadhar-head">Government of India · Aadhaar</div>
        <div className="doc-aadhar-body">
          <div className="doc-photo">🧑</div>
          <div>
            <div className="doc-name">{f.name}</div>
            <div>DOB: {f.dob}</div>
            <div>Gender: {f.gender}</div>
            <div className="doc-aadhar-num">{f.aadhar_number}</div>
            <div className="doc-addr">{f.address}</div>
          </div>
        </div>
      </div>
    )
  }
  if (template.form_type === 'MEDICAL') {
    return (
      <div className="doc-card doc-medical">
        <div className="doc-medical-head">MEDICAL FITNESS CERTIFICATE</div>
        <Line k="Exam date" v={f.exam_date} />
        <Line k="Vision" v={f.vision} />
        <Line k="Colour blindness" v={f.color_blindness ? 'DETECTED' : 'None'} />
        <Line k="Vertigo" v={f.vertigo ? 'DETECTED' : 'None'} />
        <Line k="Blood type" v={f.blood_type} />
        <div className="doc-stamp">SCANNED</div>
      </div>
    )
  }
  return (
    <div className="doc-card doc-police">
      <div className="doc-police-head">POLICE VERIFICATION CERTIFICATE</div>
      <Line k="Certificate #" v={f.certificate_number} />
      <Line k="Issue date" v={f.issue_date} />
      <Line k="Status" v={f.verification_status} />
      <div className="doc-stamp police">VERIFIED</div>
    </div>
  )
}

const Line = ({ k, v }) => (
  <div className="doc-line">
    <span className="doc-k">{k}</span>
    <span className="doc-v">{String(v)}</span>
  </div>
)

// ---------------------------------------------------------------------------
// Right-pane editable forms
// ---------------------------------------------------------------------------
function Field({ label, children }) {
  return (
    <label className="wb-field">
      <span>{label}</span>
      {children}
    </label>
  )
}

function IdentityForm({ form, setField, expirable }) {
  return (
    <div className="wb-fields">
      <Field label="Name (as printed)">
        <input value={form.name || ''} onChange={(e) => setField('name', e.target.value)} />
      </Field>
      <Field label="Document / ID number">
        <input
          value={form.aadhar_number || form.document_number || ''}
          onChange={(e) => setField('aadhar_number', e.target.value)}
        />
      </Field>
      {expirable && (
        <Field label="Expiry date">
          <input
            type="date"
            value={form.expiry_date || ''}
            onChange={(e) => setField('expiry_date', e.target.value)}
          />
        </Field>
      )}
    </div>
  )
}

function MedicalForm({ form, setField }) {
  return (
    <div className="wb-fields">
      <Field label="Exam date">
        <input
          type="date"
          value={form.exam_date || ''}
          onChange={(e) => setField('exam_date', e.target.value)}
        />
      </Field>
      <Field label="Vision">
        <input value={form.vision || ''} onChange={(e) => setField('vision', e.target.value)} />
      </Field>
      <Field label="Blood type">
        <input
          value={form.blood_type || ''}
          onChange={(e) => setField('blood_type', e.target.value)}
        />
      </Field>
      <label className="wb-check">
        <input
          type="checkbox"
          checked={!!form.color_blindness}
          onChange={(e) => setField('color_blindness', e.target.checked)}
        />
        Colour blindness
      </label>
      <label className="wb-check">
        <input
          type="checkbox"
          checked={!!form.vertigo}
          onChange={(e) => setField('vertigo', e.target.checked)}
        />
        Vertigo
      </label>
    </div>
  )
}

function PoliceForm({ form, setField }) {
  return (
    <div className="wb-fields">
      <Field label="Certificate number">
        <input
          value={form.certificate_number || ''}
          onChange={(e) => setField('certificate_number', e.target.value)}
        />
      </Field>
      <Field label="Issue date">
        <input
          type="date"
          value={form.issue_date || ''}
          onChange={(e) => setField('issue_date', e.target.value)}
        />
      </Field>
      <Field label="Verification status">
        <select
          value={form.verification_status || 'Verified'}
          onChange={(e) => setField('verification_status', e.target.value)}
        >
          <option>Verified</option>
          <option>Pending</option>
          <option>Rejected</option>
        </select>
      </Field>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Read-only completion tracker. Videos are ASSIGNED to each worker; the worker
// watches (kiosk launcher below), the Field Officer only monitors status.
// ---------------------------------------------------------------------------
const VIDEO_TYPES = [
  { type: 'TRADE_TEST', label: 'Trade Test' },
  { type: 'SAFETY_TRAINING', label: 'Safety Training' },
]

function VideoStatusTracker({ token }) {
  const [workers, setWorkers] = useState([])
  const [modal, setModal] = useState(null) // { worker, videoType, label, initialPct }

  const load = () => token && api.workers(token).then(setWorkers).catch(() => {})
  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const statusFor = (w, vtype) =>
    (w.video_progress || []).find((v) => v.video_type === vtype)

  const closeModal = () => {
    setModal(null)
    load() // refresh statuses after a watch session
  }

  return (
    <div className="wb-videos">
      <div className="wb-pane-title">Video Completion Tracker (assigned per worker)</div>
      <div className="wb-track-scroll">
        <table className="video-table">
          <thead>
            <tr>
              <th>Worker</th>
              {VIDEO_TYPES.map((v) => (
                <th key={v.type}>{v.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.id}>
                <td>
                  <strong>{w.name}</strong>
                  <div className="muted">{w.skill_type}</div>
                </td>
                {VIDEO_TYPES.map((v) => {
                  const s = statusFor(w, v.type)
                  const done = !!s?.is_completed
                  const pct = s?.progress_percentage ?? 0
                  return (
                    <td key={v.type}>
                      <span className={`badge ${done ? 'green' : 'amber'}`}>
                        {done ? '✅ Completed' : `⏳ ${pct}%`}
                      </span>
                      {!done && (
                        <button
                          className="btn small"
                          onClick={() =>
                            setModal({
                              worker: w,
                              videoType: v.type,
                              label: v.label,
                              initialPct: pct,
                            })
                          }
                        >
                          ▶ Play for worker
                        </button>
                      )}
                    </td>
                  )
                })}
              </tr>
            ))}
            {workers.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  No workers yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {modal && (
        <div className="wb-modal-backdrop" onClick={closeModal}>
          <div className="wb-modal" onClick={(e) => e.stopPropagation()}>
            <div className="wb-modal-head">
              <strong>
                {modal.label} — {modal.worker.name}
              </strong>
              <button className="btn small ghost" onClick={closeModal}>
                Close
              </button>
            </div>
            <p className="muted">
              Kiosk mode — hand the screen to the worker. Forward-seeking is blocked.
            </p>
            <VideoPlayer
              token={token}
              workerId={modal.worker.id}
              videoType={modal.videoType}
              initialPct={modal.initialPct}
              onDone={load}
            />
          </div>
        </div>
      )}
    </div>
  )
}

// The seek-locked player the worker actually watches. Resumes from prior progress
// and pushes heartbeats. (Later: this same player moves to a public /watch link.)
function VideoPlayer({ token, workerId, videoType, initialPct = 0, onDone }) {
  const videoRef = useRef(null)
  const maxWatched = useRef(0)
  const lastSent = useRef(initialPct)
  const [progress, setProgress] = useState(initialPct)
  const [completed, setCompleted] = useState(initialPct >= 100)

  const push = async (pct) => {
    if (!workerId) return
    try {
      const r = await api.videoHeartbeat(token, {
        worker: workerId,
        video_type: videoType,
        progress_percentage: pct,
      })
      setCompleted(r.is_completed)
      onDone?.()
    } catch {
      /* heartbeat is best-effort */
    }
  }

  // Resume: place the playhead at the previously-watched watermark.
  const onLoadedMetadata = (e) => {
    const v = e.target
    if (initialPct > 0 && v.duration) {
      const t = (initialPct / 100) * v.duration
      maxWatched.current = t
      v.currentTime = t
    }
  }

  const blockForwardSeek = (v) => {
    if (v.currentTime > maxWatched.current + 1.5) {
      v.currentTime = maxWatched.current
      return true
    }
    return false
  }

  const onTimeUpdate = (e) => {
    const v = e.target
    if (!v.duration) return
    if (blockForwardSeek(v)) return
    maxWatched.current = Math.max(maxWatched.current, v.currentTime)
    const pct = Math.min(100, Math.floor((maxWatched.current / v.duration) * 100))
    setProgress(pct)
    if (pct >= lastSent.current + 5 && pct < 100) {
      lastSent.current = pct
      push(pct)
    }
  }

  const onEnded = () => {
    const v = videoRef.current
    if (v) maxWatched.current = v.duration
    setProgress(100)
    lastSent.current = 100
    push(100)
  }

  return (
    <div className={`wb-video ${completed ? 'done' : ''}`}>
      <video
        ref={videoRef}
        src={VIDEO_SRC}
        controls
        playsInline
        preload="metadata"
        onLoadedMetadata={onLoadedMetadata}
        onTimeUpdate={onTimeUpdate}
        onSeeking={(e) => blockForwardSeek(e.target)}
        onEnded={onEnded}
      />
      <div className="wb-progress">
        <div className="wb-progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="muted wb-hint">
        {completed ? 'Completed ✅' : `${progress}% watched — no skipping ahead.`}
      </div>
    </div>
  )
}
