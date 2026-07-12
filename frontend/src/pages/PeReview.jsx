import { useEffect, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'

const STATUS_TONE = {
  Draft: 'grey',
  Submitted: 'blue',
  Revision_Requested: 'amber',
  Approved: 'green',
  Rejected: 'red',
}

// Principal Employer: review submitted deployment lists, preview worker docs,
// and Approve / Request Modifications / Reject with comments.
export default function PeReview() {
  const { token } = useAuth()
  const [lists, setLists] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = async () => {
    if (!token) return
    setLoading(true)
    try {
      setLists(await api.intakeLists(token))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  return (
    <div className="page">
      <h1>Principal Employer · List Review</h1>
      {error && <div className="alert error">⚠ {error}</div>}
      {loading && <div className="banner">Loading submitted lists…</div>}
      {!loading && lists.length === 0 && (
        <div className="empty">No lists submitted yet.</div>
      )}
      <div className="list-stack">
        {lists.map((list) => (
          <ReviewCard key={list.id} list={list} token={token} onDone={load} />
        ))}
      </div>
    </div>
  )
}

function ReviewCard({ list, token, onDone }) {
  const [comments, setComments] = useState(list.pe_comments || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const act = async (action) => {
    setBusy(true)
    setErr(null)
    try {
      await api.reviewList(token, list.id, { action, comments })
      onDone()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const decided = ['Approved', 'Rejected'].includes(list.status)

  return (
    <div className="review-card">
      <div className="review-head">
        <div>
          <strong>List #{list.id}</strong> · {list.project_name}
          <div className="muted">
            from {list.contractor_email}
            {list.submitted_at
              ? ` · submitted ${new Date(list.submitted_at).toLocaleString()}`
              : ''}
          </div>
        </div>
        <span className={`badge ${STATUS_TONE[list.status] || 'grey'}`}>
          {list.status.replace('_', ' ')}
        </span>
      </div>

      <table className="worker-table">
        <thead>
          <tr>
            <th>Worker</th>
            <th>Skill</th>
            <th>Aadhar</th>
            <th>Documents</th>
          </tr>
        </thead>
        <tbody>
          {list.workers.map(({ worker }) => (
            <tr key={worker.id}>
              <td>{worker.name}</td>
              <td>{worker.skill_type}</td>
              <td>{worker.aadhar_number}</td>
              <td>
                <div className="doc-previews">
                  {worker.documents.map((d) => (
                    <DocPreview key={d.id} doc={d} />
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {!decided && (
        <div className="review-actions">
          <textarea
            placeholder="Feedback / comments for the contractor…"
            value={comments}
            onChange={(e) => setComments(e.target.value)}
          />
          <div className="row gap">
            <button className="btn green" disabled={busy} onClick={() => act('approve')}>
              ✅ Approve List
            </button>
            <button
              className="btn amber"
              disabled={busy}
              onClick={() => act('request_changes')}
            >
              ✏️ Request Modifications
            </button>
            <button className="btn red" disabled={busy} onClick={() => act('reject')}>
              ✖ Reject
            </button>
          </div>
          {err && <div className="inline-msg error">{err}</div>}
        </div>
      )}

      {list.pe_comments && decided && (
        <div className="pe-comment">PE note: “{list.pe_comments}”</div>
      )}
    </div>
  )
}

function DocPreview({ doc }) {
  const href = doc.file_url || doc.document_file
  const tone =
    doc.verification_status === 'Verified'
      ? 'green'
      : doc.verification_status === 'Rejected'
      ? 'red'
      : 'amber'
  return (
    <a
      className={`doc-chip ${tone}`}
      href={href || '#'}
      target="_blank"
      rel="noreferrer"
      title={`${doc.verification_status}${
        doc.expiry_date ? ` · exp ${doc.expiry_date}` : ''
      }`}
    >
      {doc.requirement_name}
    </a>
  )
}
