import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'
import IntakeWorkbench from '../components/contractor/IntakeWorkbench.jsx'
import UnifiedIntakeOverlay from '../components/contractor/UnifiedIntakeOverlay.jsx'
import VerificationStatusTable from '../components/contractor/VerificationStatusTable.jsx'
import WorkforceDemand from '../components/contractor/WorkforceDemand.jsx'

const REASON_LABEL = {
  MISSING: 'Missing',
  EXPIRED: 'Expired',
  REJECTED: 'Rejected',
  PENDING: 'Pending review',
  FAILED: 'Failed',
  INCOMPLETE: 'Incomplete',
  NOT_PASSED: 'Not passed',
}

const TABS = [
  { key: 'demand', label: '🔎 Workforce Demand' },
  { key: 'pool', label: '👷 Worker Pool' },
  // Not an add-worker screen — that is the "New Worker Intake" overlay. This tab
  // is for an existing worker: administer the trade test, play the safety video,
  // and re-verify a single document.
  { key: 'workbench', label: '🧾 Verification & Testing' },
  { key: 'status', label: '✅ Verification Status' },
]

// The Contractor Suite. Everything operational lives here now: the worker pool
// they own, the split-pane intake workbench, trade tests, safety-video tracking,
// resume-backed skill search, and the unified onboarding overlay.
export default function WorkerSelection() {
  const { token } = useAuth()
  const [tab, setTab] = useState('demand')
  const [overlayOpen, setOverlayOpen] = useState(false)

  const [projects, setProjects] = useState([])
  const [projectId, setProjectId] = useState(null)
  const [workers, setWorkers] = useState([])
  const [data, setData] = useState(null)
  const [poolTab, setPoolTab] = useState('ready') // 'ready' | 'fix'
  const [query, setQuery] = useState('')
  const [masterReqs, setMasterReqs] = useState([])
  const [reqFilter, setReqFilter] = useState(() => new Set())
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [submitMsg, setSubmitMsg] = useState(null)
  const [myLists, setMyLists] = useState([])
  const [selectedIds, setSelectedIds] = useState(() => new Set())

  const toggleSelected = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // --- loaders -------------------------------------------------------------
  const loadWorkers = () =>
    api.workers(token).then(setWorkers).catch((e) => setError(e.message))

  const loadEligible = async (pid) => {
    if (!token || !pid) return
    setLoading(true)
    setError(null)
    try {
      const result = await api.eligibleWorkers(token, pid)
      setData(result)
      // Default: pre-select every compliant worker.
      setSelectedIds(new Set(result.ready_to_deploy.map((w) => w.worker.id)))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const loadMyLists = async () => {
    if (!token) return
    try {
      setMyLists(await api.intakeLists(token))
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    if (!token) return
    api
      .projects(token)
      .then((p) => {
        setProjects(p)
        if (p.length && !projectId) setProjectId(p[0].id)
      })
      .catch((e) => setError(e.message))
    api.requirements(token).then(setMasterReqs).catch((e) => setError(e.message))
    loadWorkers()
    loadMyLists()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  useEffect(() => {
    if (projectId) loadEligible(projectId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, token])

  // Anything that changes a worker refreshes both the registry and the split.
  const refreshAll = () => {
    loadWorkers()
    if (projectId) loadEligible(projectId)
  }

  const toggleReq = (id) => {
    setReqFilter((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // A worker "fulfils" a requirement if they hold a Verified, non-expired
  // document for it — the same rule the compliance engine uses, but checked here
  // against ALL master requirements (not just the project's mandatory set).
  const workerFulfills = (worker, requirementId) => {
    const today = new Date().toISOString().slice(0, 10)
    return (worker.documents || []).some(
      (d) =>
        d.requirement === requirementId &&
        d.verification_status === 'Verified' &&
        (!d.expiry_date || d.expiry_date >= today)
    )
  }

  // ---- Instant multi-criteria filter -------------------------------------
  // Three independent, AND-combined filters:
  //   1. free-text  -> name, skill type, any requirement name, or a resume skill
  //   2. checkboxes -> worker must FULFIL every ticked master requirement
  const filterList = (list) => {
    const q = query.trim().toLowerCase()
    const checked = [...reqFilter]

    return list.filter(({ worker, compliance }) => {
      if (checked.length && !checked.every((id) => workerFulfills(worker, id))) {
        return false
      }
      if (!q) return true
      if (worker.name.toLowerCase().includes(q)) return true
      if (worker.skill_type.toLowerCase().includes(q)) return true
      const resumeSkills = worker.candidate_profile?.skills || []
      if (resumeSkills.some((s) => s.toLowerCase().includes(q))) return true
      const satisfiedNames = (compliance.satisfied || []).map((s) =>
        s.requirement_name.toLowerCase()
      )
      const gapNames = (compliance.gaps || []).map((g) => g.requirement_name.toLowerCase())
      return [...satisfiedNames, ...gapNames].some((n) => n.includes(q))
    })
  }

  const readyList = useMemo(
    () => filterList(data?.ready_to_deploy || []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, query, reqFilter]
  )
  const fixList = useMemo(
    () => filterList(data?.needs_fixes || []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, query, reqFilter]
  )

  // Workers that are BOTH visible under the current filter AND ticked. This is
  // exactly what gets submitted, so searching narrows the outgoing list too.
  const submittableIds = useMemo(
    () => readyList.filter((w) => selectedIds.has(w.worker.id)).map((w) => w.worker.id),
    [readyList, selectedIds]
  )
  const selectedCount = submittableIds.length

  async function submitList() {
    if (!token || !projectId) return
    const ids = submittableIds
    if (!ids.length) {
      setSubmitMsg({
        tone: 'error',
        text: 'Select at least one visible compliant worker to submit.',
      })
      return
    }
    try {
      const res = await api.submitList(token, {
        project: projectId,
        worker_ids: ids,
        submit: true,
      })
      setSubmitMsg({
        tone: 'success',
        text: `Submitted list #${res.id} with ${ids.length} worker(s) to the Employer.`,
      })
      loadMyLists()
    } catch (e) {
      setSubmitMsg({ tone: 'error', text: e.message })
    }
  }

  // Jump from a workforce-demand result straight into a ready-to-submit selection.
  const pickWorkers = (ids) => {
    setSelectedIds(new Set(ids))
    setPoolTab('ready')
    setTab('pool')
  }

  const project = projects.find((p) => p.id === projectId)

  return (
    <div className="page">
      <div className="page-head">
        <h1>Contractor · Workforce Suite</h1>
        <button className="btn primary lg" onClick={() => setOverlayOpen(true)}>
          ＋ New Worker Intake
        </button>
      </div>

      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? 'active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && <div className="alert error">⚠ {error}</div>}

      {tab === 'demand' && (
        <WorkforceDemand projects={projects} onPickWorkers={pickWorkers} />
      )}

      {tab === 'workbench' && (
        <IntakeWorkbench workers={workers} onChanged={refreshAll} />
      )}

      {tab === 'status' && <VerificationStatusTable onChanged={refreshAll} />}

      {tab === 'pool' && (
        <>
          <div className="toolbar">
            <label>
              Project:&nbsp;
              <select
                value={projectId || ''}
                onChange={(e) => setProjectId(Number(e.target.value))}
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>

            <input
              className="search"
              placeholder="Filter by name, skill, resume skill or document (e.g. PAN)…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div className="req-filter">
            <span className="req-filter-label">
              Must have fulfilled{reqFilter.size ? ` (${reqFilter.size})` : ''}:
            </span>
            {masterReqs.map((r) => (
              <label key={r.id} className={`req-check ${reqFilter.has(r.id) ? 'on' : ''}`}>
                <input
                  type="checkbox"
                  checked={reqFilter.has(r.id)}
                  onChange={() => toggleReq(r.id)}
                />
                {r.name}
                {r.is_expirable ? ' ⏳' : ''}
              </label>
            ))}
            {reqFilter.size > 0 && (
              <button className="btn small ghost" onClick={() => setReqFilter(new Set())}>
                Clear
              </button>
            )}
          </div>

          {project && (
            <div className="requirements-strip">
              <strong>Required documents:</strong>{' '}
              {project.requirements.map((r) => (
                <span key={r.id} className="pill">
                  {r.requirement.name}
                  {r.requirement.is_expirable ? ' ⏳' : ''}
                </span>
              ))}
            </div>
          )}

          <div className="tabs sub">
            <button
              className={`tab ${poolTab === 'ready' ? 'active' : ''}`}
              onClick={() => setPoolTab('ready')}
            >
              ✅ Ready to Deploy ({readyList.length})
            </button>
            <button
              className={`tab ${poolTab === 'fix' ? 'active' : ''}`}
              onClick={() => setPoolTab('fix')}
            >
              🛠 Fix Requirements ({fixList.length})
            </button>
          </div>

          {loading && <div className="banner">Evaluating compliance…</div>}

          {poolTab === 'ready' && (
            <div className="worker-grid">
              {readyList.map(({ worker }) => (
                <label
                  key={worker.id}
                  className={`worker-card ready selectable ${
                    selectedIds.has(worker.id) ? 'selected' : ''
                  }`}
                >
                  <div className="worker-head">
                    <div className="select-name">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(worker.id)}
                        onChange={() => toggleSelected(worker.id)}
                      />
                      <strong>{worker.name}</strong>
                    </div>
                    <span className="badge green">Compliant</span>
                  </div>
                  <div className="muted">{worker.skill_type}</div>
                  <div className="aadhar">Aadhar {worker.aadhar_number}</div>
                  {worker.candidate_profile?.skills?.length > 0 && (
                    <div className="wc-skills">
                      {worker.candidate_profile.skills.slice(0, 4).map((s) => (
                        <span key={s} className="pill">
                          {s}
                        </span>
                      ))}
                    </div>
                  )}
                </label>
              ))}
              {!loading && readyList.length === 0 && (
                <div className="empty">No workers match — try clearing the filter.</div>
              )}
            </div>
          )}

          {poolTab === 'fix' && (
            <div className="worker-grid">
              {fixList.map(({ worker, compliance }) => (
                <FixCard
                  key={worker.id}
                  worker={worker}
                  compliance={compliance}
                  token={token}
                  onSaved={refreshAll}
                />
              ))}
              {!loading && fixList.length === 0 && (
                <div className="empty">Nothing to fix here. 🎉</div>
              )}
            </div>
          )}

          <div className="submit-bar">
            <button
              className="btn primary lg"
              onClick={submitList}
              disabled={selectedCount === 0}
            >
              Submit {selectedCount} Selected to Employer →
            </button>
            {submitMsg && (
              <span className={`inline-msg ${submitMsg.tone}`}>{submitMsg.text}</span>
            )}
          </div>

          <SubmittedLists
            lists={myLists}
            token={token}
            onChanged={() => {
              loadMyLists()
              refreshAll()
            }}
          />
        </>
      )}

      <UnifiedIntakeOverlay
        open={overlayOpen}
        onClose={() => setOverlayOpen(false)}
        onCreated={refreshAll}
      />
    </div>
  )
}

const LIST_STATUS = {
  Draft: { tone: 'grey', label: 'Draft' },
  Submitted: { tone: 'blue', label: 'Submitted — awaiting review' },
  Revision_Requested: { tone: 'amber', label: 'Modifications requested' },
  Approved: { tone: 'green', label: 'Approved' },
  Rejected: { tone: 'red', label: 'Rejected' },
}

// Contractor-facing view of their submitted lists and the PE's decision. This is
// where "Request Modifications" feedback surfaces — and where the same list can
// be revised and resubmitted in place.
function SubmittedLists({ lists, token, onChanged }) {
  if (!lists.length) return null

  // Surface lists needing action first.
  const sorted = [...lists].sort((a, b) => {
    const rank = (s) => (s === 'Revision_Requested' ? 0 : s === 'Submitted' ? 1 : 2)
    return rank(a.status) - rank(b.status)
  })

  return (
    <div className="submitted-lists">
      <h2>My Submitted Lists</h2>
      {sorted.map((list) => (
        <SubmittedCard key={list.id} list={list} token={token} onChanged={onChanged} />
      ))}
    </div>
  )
}

function SubmittedCard({ list, token, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const meta = LIST_STATUS[list.status] || { tone: 'grey', label: list.status }
  const needsAction = list.status === 'Revision_Requested'

  async function resubmit() {
    setBusy(true)
    setErr(null)
    try {
      // Keep the same roster (docs were fixed above) and flip this SAME list
      // back to 'Submitted' for a fresh PE review.
      await api.updateList(token, list.id, { submit: true })
      onChanged()
    } catch (e) {
      // Backend returns the names of any worker still non-compliant.
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`submitted-card ${needsAction ? 'attention' : ''}`}>
      <div className="submitted-head">
        <div>
          <strong>List #{list.id}</strong> · {list.project_name}
          <div className="muted">
            {list.workers.length} worker(s)
            {list.submitted_at
              ? ` · sent ${new Date(list.submitted_at).toLocaleString()}`
              : ''}
          </div>
        </div>
        <span className={`badge ${meta.tone}`}>{meta.label}</span>
      </div>

      {needsAction && (
        <div className="revision-callout">
          <strong>⚠ The Employer asked for changes.</strong>
          <div className="pe-feedback">
            {list.pe_comments
              ? `“${list.pe_comments}”`
              : 'No specific comment was left — review the workers and resubmit.'}
          </div>
          <div className="revision-workers">
            On this list: {list.workers.map((w) => w.worker.name).join(', ') || '(none)'}
          </div>
          <div className="muted">
            Fix the flagged documents in “Fix Requirements” above, then resubmit this
            same list.
          </div>
          <div className="row gap" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={busy} onClick={resubmit}>
              {busy ? 'Resubmitting…' : '↻ Revise & Resubmit'}
            </button>
          </div>
          {err && <div className="inline-msg error">{err}</div>}
        </div>
      )}

      {list.status === 'Rejected' && list.pe_comments && (
        <div className="pe-feedback">Employer note: “{list.pe_comments}”</div>
      )}
      {list.status === 'Approved' && (
        <div className="pe-feedback ok">
          ✅ Approved — these workers can be admitted at the gate, provided their
          documents are still valid when they scan in.
        </div>
      )}
    </div>
  )
}

// A single worker needing fixes: shows each gap and an inline upload control.
function FixCard({ worker, compliance, token, onSaved }) {
  return (
    <div className="worker-card fix">
      <div className="worker-head">
        <strong>{worker.name}</strong>
        <span className="badge amber">{compliance.gaps.length} issue(s)</span>
      </div>
      <div className="muted">
        {worker.skill_type} · Aadhar {worker.aadhar_number}
      </div>
      <ul className="gap-list">
        {compliance.gaps.map((g) => (
          <GapRow
            key={g.kind === 'intake' ? `intake-${g.pillar}` : `doc-${g.requirement_id}`}
            workerId={worker.id}
            gap={g}
            token={token}
            onSaved={onSaved}
          />
        ))}
      </ul>
    </div>
  )
}

function GapRow({ workerId, gap, token, onSaved }) {
  const [file, setFile] = useState(null)
  const [docNumber, setDocNumber] = useState('')
  const [expiry, setExpiry] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  // Intake pillars (medical / police / trade test / video / resume) have no
  // uploadable slot here — they are resolved in the Verification & Testing tab.
  if (gap.kind === 'intake') {
    return (
      <li className="gap-row intake">
        <div className="gap-head">
          <span className="gap-name">🩺 {gap.requirement_name}</span>
          <span className={`chip ${gap.reason.toLowerCase()}`}>
            {REASON_LABEL[gap.reason] || gap.reason}
          </span>
        </div>
        <div className="reject-reason">{gap.detail}</div>
        <div className="muted intake-hint">Resolve this in the Verification & Testing tab.</div>
      </li>
    )
  }

  async function save() {
    if (!file && !docNumber) {
      setErr('Attach a file or enter a document number.')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      // Route through the verify endpoint so the contractor's own upload lands
      // Verified on the spot rather than queueing for someone else to approve.
      const fd = new FormData()
      fd.append('worker', workerId)
      fd.append('doc_type', 'IDENTITY')
      fd.append('requirement_name', gap.requirement_name)
      if (docNumber) fd.append('document_number', docNumber)
      if (expiry) fd.append('expiry_date', expiry)
      if (file) fd.append('file', file)
      await api.verifyDocumentForm(token, fd)
      onSaved()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="gap-row">
      <div className="gap-head">
        <span className="gap-name">{gap.requirement_name}</span>
        <span className={`chip ${gap.reason.toLowerCase()}`}>
          {REASON_LABEL[gap.reason] || gap.reason}
        </span>
      </div>
      {gap.reason === 'REJECTED' && gap.rejection_reason && (
        <div className="reject-reason">Reason: {gap.rejection_reason}</div>
      )}
      {gap.reason === 'EXPIRED' && gap.expiry_date && (
        <div className="reject-reason">Expired on {gap.expiry_date}</div>
      )}
      <div className="upload-row">
        <input
          type="text"
          placeholder="Doc #"
          value={docNumber}
          onChange={(e) => setDocNumber(e.target.value)}
        />
        {gap.is_expirable && (
          <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        )}
        <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <button className="btn small" disabled={busy} onClick={save}>
          {busy ? '…' : 'Upload & verify'}
        </button>
      </div>
      {err && <div className="inline-msg error">{err}</div>}
    </li>
  )
}
