import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../context/AuthContext.jsx'

// Verification & Testing — everything that happens to a worker who already
// exists. Onboarding a *new* worker with all six documents at once is the
// Unified Intake overlay's job; this surface covers the three things the overlay
// cannot do:
//
//   1. re-verify or replace a single document (the split pane below),
//   2. administer the Trade Test,
//   3. play the Safety Training video and record watch progress.
//
// (2) and (3) are compliance pillars in their own right and have no other UI, so
// without this tab no worker could ever become deployable.
//
// Identity docs map to a WorkerDocument requirement; Medical/Police map to their
// structured 1-year-validity records.
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

const OPTION_KEYS = ['A', 'B', 'C', 'D']

// Sample safety induction clip. Swap for a self-hosted asset in production;
// the seek-lock + heartbeat logic works with any source.
const SAFETY_VIDEO_SRC = 'https://www.w3schools.com/html/mov_bbb.mp4'

// --- Reusable hook: is an ISO date string more than 365 days old? ----------
export function useExpiryCheck(isoDate) {
  return useMemo(() => {
    if (!isoDate) return { expired: false, daysOld: null }
    const then = new Date(isoDate)
    if (Number.isNaN(then.getTime())) return { expired: false, daysOld: null }
    const daysOld = Math.floor((Date.now() - then.getTime()) / 86_400_000)
    return { expired: daysOld > 365, daysOld }
  }, [isoDate])
}

export default function IntakeWorkbench({ workers = [], onChanged }) {
  const { token } = useAuth()
  const [workerId, setWorkerId] = useState(null)
  const [docSel, setDocSel] = useState('') // one of DOC_TYPES[].value
  const [form, setForm] = useState({})
  const [upload, setUpload] = useState(null) // { file, url, kind }
  const [busy, setBusy] = useState(false)
  const [extracting, setExtracting] = useState(false)
  const [msg, setMsg] = useState(null)
  const fileInputRef = useRef(null)

  const current = DOC_TYPES.find((d) => d.value === docSel) || null
  const formType = current?.formType || null

  // Keep a valid selection as the pool changes underneath us.
  useEffect(() => {
    if (!workers.length) {
      setWorkerId(null)
    } else if (!workers.some((w) => w.id === workerId)) {
      setWorkerId(workers[0].id)
    }
  }, [workers, workerId])

  // Revoke object URLs when the upload changes / unmounts.
  useEffect(() => () => upload?.url && URL.revokeObjectURL(upload.url), [upload])

  function selectDocType(value) {
    setDocSel(value)
    setForm({})
    setMsg(null)
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
      onChanged?.()
    } catch (e) {
      setMsg({ tone: 'error', text: e.message })
    } finally {
      setBusy(false)
    }
  }

  const selectedWorker = workers.find((w) => w.id === workerId) || null

  return (
    <div className="workbench">
      {/* Top controls: which worker, which document */}
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
      </div>

      <div className="wb-split">
        {/* LEFT — document previewer */}
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
          ) : (
            <div className="wb-empty">
              Upload the worker's physical document to preview it here.
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

      <TradeTestPanel token={token} worker={selectedWorker} onChanged={onChanged} />

      <SafetyVideoPanel token={token} worker={selectedWorker} onChanged={onChanged} />
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
// Trade test — the contractor administers a 5-question practical exam on the spot.
// ---------------------------------------------------------------------------
function TradeTestPanel({ token, worker, onChanged }) {
  const [phase, setPhase] = useState('idle') // idle | testing | result
  const [session, setSession] = useState(null) // { questions, attempt_number, ... }
  const [step, setStep] = useState(0)
  const [answers, setAnswers] = useState({}) // { questionId: 'A' }
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  // Reset when the selected worker changes.
  useEffect(() => {
    setPhase('idle')
    setSession(null)
    setStep(0)
    setAnswers({})
    setResult(null)
    setErr(null)
  }, [worker?.id])

  if (!worker) {
    return (
      <div className="wb-videos">
        <div className="wb-pane-title">Trade Test</div>
        <div className="muted">Select a worker above to administer their trade test.</div>
      </div>
    )
  }

  const status = worker.trade_test_status
  const attemptsUsed = worker.trade_test_attempts ?? 0

  async function start() {
    setBusy(true)
    setErr(null)
    try {
      const data = await api.tradeTestStart(token, worker.id)
      setSession(data)
      setAnswers({})
      setStep(0)
      setResult(null)
      setPhase('testing')
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  async function submit(finalAnswers) {
    setBusy(true)
    setErr(null)
    try {
      const payload = {
        worker_id: worker.id,
        answers: Object.entries(finalAnswers).map(([question_id, selected_option]) => ({
          question_id: Number(question_id),
          selected_option,
        })),
      }
      const res = await api.tradeTestSubmit(token, payload)
      setResult(res)
      setPhase('result')
      onChanged?.()
    } catch (e) {
      setErr(e.message)
      setPhase('idle')
    } finally {
      setBusy(false)
    }
  }

  function choose(questionId, opt) {
    const next = { ...answers, [questionId]: opt }
    setAnswers(next)
    if (step < session.questions.length - 1) {
      setStep(step + 1)
    } else {
      submit(next) // last answer → auto-submit
    }
  }

  return (
    <div className="wb-videos">
      <div className="wb-pane-title">Trade Test — {worker.name}</div>
      {err && <div className="alert error">⚠ {err}</div>}

      {phase === 'idle' && (
        <div className="tt-idle">
          {status === 'PASSED' && (
            <div className="tt-status">
              <span className="badge green">✅ Passed</span>
              <span className="muted">This worker has passed the trade test.</span>
            </div>
          )}
          {status === 'FAILED' && (
            <div className="tt-status">
              <span className="badge red">✖ Failed — locked</span>
              <span className="muted">Failed all 3 attempts. Profile is permanently locked.</span>
            </div>
          )}
          {status === 'PENDING' && (
            <div className="tt-status">
              <span className="badge amber">Not yet taken</span>
              <span className="muted">Attempts used: {attemptsUsed} / 3</span>
              <button className="btn primary" disabled={busy} onClick={start}>
                {busy ? 'Loading…' : '▶ Start Trade Test'}
              </button>
            </div>
          )}
        </div>
      )}

      {phase === 'testing' && session && (
        <TradeTestStepper
          session={session}
          step={step}
          answers={answers}
          onChoose={choose}
          busy={busy}
        />
      )}

      {phase === 'result' && result && (
        <TradeTestResult result={result} onRetry={start} busy={busy} />
      )}
    </div>
  )
}

function TradeTestStepper({ session, step, answers, onChoose, busy }) {
  const q = session.questions[step]
  const total = session.questions.length
  return (
    <div className="tt-exam">
      <div className="tt-progress">
        <span className="muted">
          Attempt {session.attempt_number} · Question {step + 1} of {total} ·
          need {session.pass_mark}/{total} to pass
        </span>
        <div className="tt-dots">
          {session.questions.map((qq, i) => (
            <span
              key={qq.id}
              className={`tt-dot ${i === step ? 'on' : ''} ${answers[qq.id] ? 'done' : ''}`}
            />
          ))}
        </div>
      </div>

      <div className="tt-image">
        <img src={q.image_url} alt="question illustration" />
      </div>

      <div className="tt-question">{q.question_text}</div>

      <div className="tt-options">
        {OPTION_KEYS.map((k) => {
          const label = q[`option_${k.toLowerCase()}`]
          if (!label) return null
          return (
            <button
              key={k}
              className={`tt-option ${answers[q.id] === k ? 'chosen' : ''}`}
              disabled={busy}
              onClick={() => onChoose(q.id, k)}
            >
              <span className="tt-key">{k}</span>
              <span>{label}</span>
            </button>
          )
        })}
      </div>
      <div className="muted tt-hint">
        Read the question aloud, then tap the worker's spoken answer.
      </div>
    </div>
  )
}

function TradeTestResult({ result, onRetry, busy }) {
  const passed = result.is_passed
  const canRetry = !passed && !result.locked && result.attempts_remaining > 0
  return (
    <div className={`tt-result ${passed ? 'pass' : 'fail'}`}>
      {passed ? (
        <>
          <div className="tt-result-main">PASSED</div>
          <div className="tt-result-sub">Score: {result.score}/{result.total}</div>
        </>
      ) : (
        <>
          <div className="tt-result-main">FAILED</div>
          <div className="tt-result-sub">
            Score {result.score}/{result.total} · Attempt {result.attempt_number} of 3 used
          </div>
          {result.locked ? (
            <div className="tt-result-note">
              All 3 attempts used — profile permanently locked as Failed.
            </div>
          ) : (
            <div className="tt-result-note">
              {result.attempts_remaining} attempt(s) remaining.
            </div>
          )}
        </>
      )}
      {canRetry && (
        <button className="btn primary" disabled={busy} onClick={onRetry}>
          {busy ? 'Loading…' : '↻ Retry test'}
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Safety Training video — mandatory induction clip. The contractor plays it for
// the selected worker (kiosk); forward-seeking is blocked; progress is pushed to
// the backend heartbeat until it reaches 100%.
// ---------------------------------------------------------------------------
function SafetyVideoPanel({ token, worker, onChanged }) {
  const [playing, setPlaying] = useState(false)

  useEffect(() => {
    setPlaying(false)
  }, [worker?.id])

  if (!worker) {
    return (
      <div className="wb-videos">
        <div className="wb-pane-title">Safety Training Video</div>
        <div className="muted">Select a worker above to play their safety induction video.</div>
      </div>
    )
  }

  const sv = worker.safety_video || { progress_percentage: 0, is_completed: false }

  return (
    <div className="wb-videos">
      <div className="wb-pane-title">Safety Training Video — {worker.name}</div>

      {!playing && (
        <div className="tt-status">
          {sv.is_completed ? (
            <span className="badge green">✅ Watched (100%)</span>
          ) : (
            <span className="badge amber">⏳ {sv.progress_percentage}% watched</span>
          )}
          <span className="muted">
            Every worker must watch the safety induction clip in full.
          </span>
          {!sv.is_completed && (
            <button className="btn primary" onClick={() => setPlaying(true)}>
              ▶ Play safety video for worker
            </button>
          )}
        </div>
      )}

      {playing && (
        <SafetyVideoPlayer
          token={token}
          workerId={worker.id}
          initialPct={sv.progress_percentage}
          onDone={onChanged}
        />
      )}
    </div>
  )
}

function SafetyVideoPlayer({ token, workerId, initialPct = 0, onDone }) {
  const videoRef = useRef(null)
  const maxWatched = useRef(0)
  const lastSent = useRef(initialPct)
  const [progress, setProgress] = useState(initialPct)
  const [completed, setCompleted] = useState(initialPct >= 100)

  const push = async (pct) => {
    if (!workerId) return
    try {
      const r = await api.safetyVideoHeartbeat(token, {
        worker: workerId,
        progress_percentage: pct,
      })
      setCompleted(r.is_completed)
      onDone?.()
    } catch {
      /* heartbeat is best-effort */
    }
  }

  // Resume from prior progress once metadata (duration) is known.
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
      <p className="muted">Kiosk mode — hand the screen to the worker. Forward-seeking is blocked.</p>
      <video
        ref={videoRef}
        src={SAFETY_VIDEO_SRC}
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
        {completed ? 'Completed ✅ — safety induction watched in full.' : `${progress}% watched — no skipping ahead.`}
      </div>
    </div>
  )
}
