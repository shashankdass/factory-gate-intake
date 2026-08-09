import { useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'

// Headline shown for each denial reason. The gate re-checks compliance live, so
// "approved but expired" is a real, distinct outcome the guard must be able to
// read at a glance.
const DENIAL_HEADLINE = {
  DOCUMENT_EXPIRED: 'ACCESS DENIED — DOCUMENT EXPIRED',
  COMPLIANCE_REGRESSED: 'ACCESS DENIED — NO LONGER COMPLIANT',
  NOT_APPROVED: 'ACCESS DENIED — NOT ON AN APPROVED LIST',
  UNKNOWN_WORKER: 'ACCESS DENIED — UNKNOWN WORKER',
}

// Gate Security: fast Aadhar lookup with a massive GREEN / RED verdict.
export default function GateCheck() {
  const { token } = useAuth()
  const [aadhar, setAadhar] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function check(e) {
    e?.preventDefault()
    if (!aadhar.trim() || !token) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await api.gateCheck(token, aadhar.trim()))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const granted = result?.access === 'GRANTED'

  return (
    <div className="page gate-page">
      <h1>Gate Security · Entry Check</h1>

      <form className="gate-search" onSubmit={check}>
        <input
          autoFocus
          inputMode="numeric"
          placeholder="Scan / type Aadhar number"
          value={aadhar}
          onChange={(e) => setAadhar(e.target.value)}
        />
        <button className="btn primary lg" disabled={busy}>
          {busy ? 'Checking…' : 'Verify Entry'}
        </button>
      </form>

      {error && <div className="alert error">⚠ {error}</div>}

      {result && (
        <div className={`verdict ${granted ? 'granted' : 'denied'}`}>
          <div className="verdict-main">
            {granted
              ? 'ACCESS GRANTED'
              : DENIAL_HEADLINE[result.reason_code] || 'ACCESS DENIED'}
          </div>
          <div className="verdict-sub">
            {granted ? 'ISSUE GATE PASS' : 'DO NOT ADMIT'}
          </div>
          <div className="verdict-detail">
            {result.worker ? (
              <>
                <div>
                  <strong>{result.worker.name}</strong> — {result.worker.skill_type}
                </div>
                <div>Aadhar {result.worker.aadhar_number}</div>
                {result.project && <div>Project: {result.project}</div>}
              </>
            ) : null}
            <div className="verdict-reason">{result.reason}</div>

            {/* The live re-check is the point of this screen — show exactly what
                lapsed, so the guard can tell the worker what to go and fix. */}
            {result.compliance?.gaps?.length > 0 && (
              <ul className="verdict-gaps">
                {result.compliance.gaps.map((gap, i) => (
                  <li key={i}>
                    <strong>{gap.requirement_name}</strong>: {gap.reason}
                    {gap.expiry_date ? ` (expired ${gap.expiry_date})` : ''}
                    {gap.detail ? ` — ${gap.detail}` : ''}
                  </li>
                ))}
              </ul>
            )}
            {result.checked_at && (
              <div className="verdict-stamp">
                Checked live at {new Date(result.checked_at).toLocaleTimeString()}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
