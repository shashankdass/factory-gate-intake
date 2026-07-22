import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'

// Column order for the verification matrix.
const COLUMNS = [
  { key: 'Aadhar', label: 'Aadhaar' },
  { key: 'PAN', label: 'PAN' },
  { key: 'Safety Training', label: 'Safety Cert' },
  { key: 'MEDICAL', label: 'Medical' },
  { key: 'POLICE', label: 'Police' },
  { key: 'TRADE_TEST', label: 'Trade Test' },
  { key: 'SAFETY_VIDEO', label: 'Safety Video' },
]

// Map a status to a badge tone + short glyph.
function badge(status) {
  const s = (status || '').toUpperCase()
  if (s === 'VERIFIED' || s === 'PASSED') return { tone: 'green', text: '✅ Verified' }
  if (s === 'PENDING') return { tone: 'amber', text: '⏳ Pending' }
  if (s === 'INCOMPLETE' || s === 'NOT_PASSED') return { tone: 'amber', text: '⏳ Not done' }
  if (s === 'REJECTED') return { tone: 'red', text: '✖ Rejected' }
  if (s === 'EXPIRED') return { tone: 'red', text: '✖ Expired' }
  if (s === 'FAILED') return { tone: 'red', text: '✖ Failed' }
  return { tone: 'grey', text: '— Missing' } // MISSING
}

export default function VerificationStatusTable() {
  const { token } = useAuth()
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [query, setQuery] = useState('')
  const [onlyIncomplete, setOnlyIncomplete] = useState(false)

  const load = () => {
    if (!token) return
    setLoading(true)
    api
      .verificationStatus(token)
      .then((r) => setRows(r))
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return rows.filter((r) => {
      if (onlyIncomplete && r.all_verified) return false
      if (!q) return true
      return (
        r.name.toLowerCase().includes(q) ||
        r.aadhar_number.includes(q) ||
        r.skill_type.toLowerCase().includes(q)
      )
    })
  }, [rows, query, onlyIncomplete])

  const cellFor = (row, colKey) => row.items.find((it) => it.key === colKey)

  return (
    <div>
      <p className="muted">
        Every worker in the registry and the status of each verification type.
        Click 📎 to open the uploaded document.
      </p>

      <div className="vs-toolbar">
        <input
          className="search"
          placeholder="Search by name, skill or Aadhaar…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="vs-check">
          <input
            type="checkbox"
            checked={onlyIncomplete}
            onChange={(e) => setOnlyIncomplete(e.target.checked)}
          />
          Show only incomplete
        </label>
        <button className="btn small" onClick={load}>
          ↻ Refresh
        </button>
      </div>

      {error && <div className="alert error">⚠ {error}</div>}
      {loading && <div className="banner">Loading verification status…</div>}

      {!loading && (
        <div className="vs-scroll">
          <table className="vs-table">
            <thead>
              <tr>
                <th className="vs-worker">Worker</th>
                {COLUMNS.map((c) => (
                  <th key={c.key}>{c.label}</th>
                ))}
                <th>Overall</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.id}>
                  <td className="vs-worker">
                    <strong>{row.name}</strong>
                    <div className="muted">
                      {row.skill_type} · {row.aadhar_number}
                      {row.contractor_email ? ` · ${row.contractor_email}` : ''}
                    </div>
                  </td>
                  {COLUMNS.map((c) => {
                    const it = cellFor(row, c.key)
                    const b = badge(it?.status)
                    return (
                      <td key={c.key}>
                        <span className={`badge ${b.tone}`}>{b.text}</span>
                        {it?.doc_url && (
                          <a
                            className="vs-doc"
                            href={it.doc_url}
                            target="_blank"
                            rel="noreferrer"
                            title="Open uploaded document"
                          >
                            📎
                          </a>
                        )}
                      </td>
                    )
                  })}
                  <td>
                    {row.all_verified ? (
                      <span className="badge green">✅ All verified</span>
                    ) : (
                      <span className="badge amber">{row.remaining} remaining</span>
                    )}
                  </td>
                </tr>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={COLUMNS.length + 2} className="muted">
                    No workers match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
