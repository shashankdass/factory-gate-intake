import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'

const REASON_LABEL = {
  MISSING: 'Missing',
  EXPIRED: 'Expired',
  REJECTED: 'Rejected',
  PENDING: 'Pending review',
}

// Contractor dashboard: pick a project, see pre-assigned workers split into
// "Ready to Deploy" vs "Fix Requirements", filter instantly, fix docs inline,
// then submit the finalized list to the PE.
export default function WorkerSelection() {
  const { token } = useAuth()
  const [projects, setProjects] = useState([])
  const [projectId, setProjectId] = useState(null)
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('ready') // 'ready' | 'fix'
  const [query, setQuery] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [submitMsg, setSubmitMsg] = useState(null)
  // The contractor's own submitted lists + PE review outcomes.
  const [myLists, setMyLists] = useState([])
  // Explicit per-worker selection. Defaults to all compliant workers checked.
  const [selectedIds, setSelectedIds] = useState(() => new Set())

  const toggleSelected = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  // Load contractor's assigned projects.
  useEffect(() => {
    if (!token) return
    api
      .projects(token)
      .then((p) => {
        setProjects(p)
        if (p.length && !projectId) setProjectId(p[0].id)
      })
      .catch((e) => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

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

  useEffect(() => {
    if (projectId) loadEligible(projectId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, token])

  // Load the contractor's submitted lists so PE feedback is visible.
  const loadMyLists = async () => {
    if (!token) return
    try {
      setMyLists(await api.intakeLists(token))
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    loadMyLists()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  // ---- Instant multi-criteria filter -------------------------------------
  // Matches the typed string against worker name, skill type, OR the names of
  // any required/missing document on that worker.
  const filterList = (list) => {
    const q = query.trim().toLowerCase()
    if (!q) return list
    return list.filter(({ worker, compliance }) => {
      if (worker.name.toLowerCase().includes(q)) return true
      if (worker.skill_type.toLowerCase().includes(q)) return true
      const satisfiedNames = (compliance.satisfied || []).map((s) =>
        s.requirement_name.toLowerCase()
      )
      const gapNames = (compliance.gaps || []).map((g) =>
        g.requirement_name.toLowerCase()
      )
      return [...satisfiedNames, ...gapNames].some((n) => n.includes(q))
    })
  }

  const readyList = useMemo(
    () => filterList(data?.ready_to_deploy || []),
    [data, query]
  )
  const fixList = useMemo(() => filterList(data?.needs_fixes || []), [data, query])

  // Workers that are BOTH visible under the current filter AND ticked. This is
  // exactly what gets submitted, so searching narrows the outgoing list too.
  const submittableIds = useMemo(
    () => readyList.filter((w) => selectedIds.has(w.worker.id)).map((w) => w.worker.id),
    [readyList, selectedIds]
  )
  const selectedCount = submittableIds.length

  async function submitList() {
    if (!token || !projectId) return
    // Submit only workers that are currently visible (matching the filter) AND
    // ticked — so a search narrows what actually goes to the PE.
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

  const project = projects.find((p) => p.id === projectId)

  return (
    <div className="page">
      <h1>Contractor · Worker Selection</h1>

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
          placeholder="Filter by name, skill (e.g. Carpenter) or document (e.g. PAN)…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
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

      {error && <div className="alert error">⚠ {error}</div>}

      <div className="tabs">
        <button
          className={`tab ${tab === 'ready' ? 'active' : ''}`}
          onClick={() => setTab('ready')}
        >
          ✅ Ready to Deploy ({readyList.length})
        </button>
        <button
          className={`tab ${tab === 'fix' ? 'active' : ''}`}
          onClick={() => setTab('fix')}
        >
          🛠 Fix Requirements ({fixList.length})
        </button>
      </div>

      {loading && <div className="banner">Evaluating compliance…</div>}

      {tab === 'ready' && (
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
            </label>
          ))}
          {!loading && readyList.length === 0 && (
            <div className="empty">No workers match — try clearing the filter.</div>
          )}
        </div>
      )}

      {tab === 'fix' && (
        <div className="worker-grid">
          {fixList.map(({ worker, compliance }) => (
            <FixCard
              key={worker.id}
              worker={worker}
              compliance={compliance}
              token={token}
              onSaved={() => loadEligible(projectId)}
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
          if (projectId) loadEligible(projectId)
        }}
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
        <SubmittedCard
          key={list.id}
          list={list}
          token={token}
          onChanged={onChanged}
        />
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
            On this list:{' '}
            {list.workers.map((w) => w.worker.name).join(', ') || '(none)'}
          </div>
          <div className="muted">
            Fix the flagged documents in “Fix Requirements” above, then resubmit
            this same list.
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
          ✅ Approved — these workers can be admitted at the gate.
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
            key={g.requirement_id}
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

  async function save() {
    if (!file && !docNumber) {
      setErr('Attach a file or enter a document number.')
      return
    }
    setBusy(true)
    setErr(null)
    try {
      const fd = new FormData()
      fd.append('worker', workerId)
      fd.append('requirement', gap.requirement_id)
      if (docNumber) fd.append('document_number', docNumber)
      if (expiry) fd.append('expiry_date', expiry)
      if (file) fd.append('file', file)
      await api.uploadDocument(token, fd)
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
          <input
            type="date"
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
          />
        )}
        <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <button className="btn small" disabled={busy} onClick={save}>
          {busy ? '…' : 'Upload'}
        </button>
      </div>
      {err && <div className="inline-msg error">{err}</div>}
    </li>
  )
}
