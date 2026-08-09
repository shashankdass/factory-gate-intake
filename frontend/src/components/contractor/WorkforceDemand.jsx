import { useState } from 'react'
import { api } from '../../api'
import { useAuth } from '../../context/AuthContext.jsx'

// The contractor types what they need right now — "3 Carpenters, 4 Masons" —
// and the app answers straight from their own labour pool. No SOW upload, no
// peer-sourcing: just their workers, matched on trade or parsed resume skills.
const BLANK = { skill: '', count: 1 }

export default function WorkforceDemand({ projects = [], onPickWorkers }) {
  const { token } = useAuth()
  const [rows, setRows] = useState([{ ...BLANK }, { ...BLANK }])
  const [projectId, setProjectId] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const update = (index, patch) =>
    setRows((rs) => rs.map((r, i) => (i === index ? { ...r, ...patch } : r)))

  const addRow = () => setRows((rs) => [...rs, { ...BLANK }])
  const removeRow = (index) =>
    setRows((rs) => (rs.length > 1 ? rs.filter((_, i) => i !== index) : rs))

  async function search() {
    const demands = rows
      .filter((r) => r.skill.trim())
      .map((r) => ({ skill: r.skill.trim(), count: Number(r.count) || 0 }))
    if (!demands.length) {
      setError('Enter at least one skill.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      setResult(
        await api.workforceDemand(token, {
          demands,
          project: projectId ? Number(projectId) : null,
        })
      )
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="wd">
      <p className="muted">
        Enter your immediate workforce needs. Each line is matched against your own
        pool — by registered trade and by skills parsed from resumes — and scored
        against live compliance.
      </p>

      <div className="wd-form">
        {rows.map((row, index) => (
          <div key={index} className="wd-row">
            <input
              className="wd-count"
              type="number"
              min="0"
              value={row.count}
              onChange={(e) => update(index, { count: e.target.value })}
              aria-label="How many"
            />
            <input
              className="wd-skill"
              placeholder="Skill / trade (e.g. Carpenter)"
              value={row.skill}
              onChange={(e) => update(index, { skill: e.target.value })}
              onKeyDown={(e) => e.key === 'Enter' && search()}
              aria-label="Skill"
            />
            <button
              className="btn small ghost"
              onClick={() => removeRow(index)}
              disabled={rows.length === 1}
              title="Remove this line"
            >
              ✕
            </button>
          </div>
        ))}

        <div className="row gap wd-actions">
          <button className="btn small" onClick={addRow}>
            + Add a skill
          </button>
          <label className="wd-project">
            Check against:&nbsp;
            <select value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              <option value="">Pillars only (no project)</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <button className="btn primary" disabled={busy} onClick={search}>
            {busy ? 'Searching…' : '🔍 Search my pool'}
          </button>
        </div>
      </div>

      {error && <div className="alert error">⚠ {error}</div>}

      {result && (
        <>
          <div className="stat-row wd-summary">
            <Stat label="Required" value={result.summary.total_required} tone="grey" />
            <Stat label="Deployable now" value={result.summary.total_ready} tone="green" />
            <Stat label="Shortfall" value={result.summary.total_shortfall} tone="red" />
            <Stat label="Pool size" value={result.summary.pool_size} tone="amber" />
          </div>

          <div className="wd-lines">
            {result.lines.map((line) => (
              <DemandLine key={line.skill} line={line} onPickWorkers={onPickWorkers} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function DemandLine({ line, onPickWorkers }) {
  const met = line.shortfall === 0
  return (
    <div className={`wd-line ${met ? 'met' : 'short'}`}>
      <div className="wd-line-head">
        <div>
          <strong>
            {line.required} × {line.skill}
          </strong>
          <div className="muted">
            {line.available} deployable
            {line.fixable > 0 && ` · ${line.fixable} need paperwork`}
          </div>
        </div>
        {met ? (
          <span className="badge green">✅ Covered</span>
        ) : (
          <span className="badge red">Short {line.shortfall}</span>
        )}
      </div>

      {line.ready_workers.length > 0 && (
        <div className="wd-workers">
          {line.ready_workers.map(({ worker }) => (
            <span key={worker.id} className="pill green">
              {worker.name}
            </span>
          ))}
          {onPickWorkers && (
            <button
              className="btn small"
              onClick={() => onPickWorkers(line.ready_workers.map((r) => r.worker.id))}
            >
              Select these →
            </button>
          )}
        </div>
      )}

      {line.needs_fixes.length > 0 && (
        <details className="wd-fixable">
          <summary>{line.needs_fixes.length} could be ready with fixes</summary>
          <ul className="gap-list">
            {line.needs_fixes.map(({ worker, compliance }) => (
              <li key={worker.id} className="gap-row">
                <div className="gap-head">
                  <span className="gap-name">{worker.name}</span>
                  <span className="chip pending">
                    {compliance.gaps.length} issue(s)
                  </span>
                </div>
                <div className="reject-reason">
                  {compliance.gaps.map((g) => g.requirement_name).join(', ')}
                </div>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}

function Stat({ label, value, tone }) {
  return (
    <div className={`stat ${tone}`}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}
