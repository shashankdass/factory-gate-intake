import { useRef, useState } from 'react'
import { api } from '../api'
import { useAuth } from '../context/AuthContext.jsx'
import FieldOfficerIntakeWorkbench from '../components/FieldOfficerIntakeWorkbench.jsx'
import VerificationStatusTable from '../components/VerificationStatusTable.jsx'

// Field Officer view: intake workbench, verification-status board, and bulk CSV
// import. Keeps the existing /field-officer route + auth intact.
export default function FieldOfficerUpload() {
  const [tab, setTab] = useState('workbench')
  return (
    <div className="page">
      <h1>Field Officer</h1>
      <div className="tabs">
        <button
          className={`tab ${tab === 'workbench' ? 'active' : ''}`}
          onClick={() => setTab('workbench')}
        >
          🧾 Intake Workbench
        </button>
        <button
          className={`tab ${tab === 'status' ? 'active' : ''}`}
          onClick={() => setTab('status')}
        >
          ✅ Verification Status
        </button>
        <button
          className={`tab ${tab === 'bulk' ? 'active' : ''}`}
          onClick={() => setTab('bulk')}
        >
          📤 Bulk CSV Import
        </button>
      </div>
      {tab === 'workbench' && <FieldOfficerIntakeWorkbench />}
      {tab === 'status' && <VerificationStatusTable />}
      {tab === 'bulk' && <BulkUpload />}
    </div>
  )
}

// Field Officer: mass-import worker master profiles via CSV/Excel drag & drop.
function BulkUpload() {
  const { token } = useAuth()
  const inputRef = useRef(null)
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  function pick(f) {
    if (!f) return
    setFile(f)
    setResult(null)
    setError(null)
  }

  async function upload() {
    if (!file || !token) return
    setBusy(true)
    setError(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const data = await api.bulkUpload(token, fd)
      setResult(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <p className="muted">
        Upload a CSV or .xlsx with columns:{' '}
        <code>name, aadhar_number, skill_type</code> (optional{' '}
        <code>contractor_email</code>). Duplicate Aadhar numbers are skipped.
      </p>

      <div
        className={`dropzone ${dragging ? 'dragging' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          pick(e.dataTransfer.files?.[0])
        }}
        onClick={() => inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx"
          hidden
          onChange={(e) => pick(e.target.files?.[0])}
        />
        {file ? (
          <div>
            <strong>{file.name}</strong>
            <div className="muted">{(file.size / 1024).toFixed(1)} KB — click to change</div>
          </div>
        ) : (
          <div>
            <div className="drop-icon">⬆</div>
            <div>Drag &amp; drop a CSV / Excel file here, or click to browse</div>
          </div>
        )}
      </div>

      <div className="row gap">
        <button className="btn primary" disabled={!file || busy} onClick={upload}>
          {busy ? 'Importing…' : 'Import Workers'}
        </button>
        <a className="btn ghost" href={sampleCsvHref()} download="workers-sample.csv">
          Download sample CSV
        </a>
      </div>

      {error && <div className="alert error">⚠ {error}</div>}

      {result && (
        <div className="result-card">
          <div className="stat-row">
            <Stat label="Created" value={result.created_count} tone="green" />
            <Stat label="Skipped (dupes)" value={result.skipped_count} tone="amber" />
            <Stat label="Errors" value={result.error_count} tone="red" />
          </div>
          {result.errors?.length > 0 && (
            <details open>
              <summary>Row errors</summary>
              <ul>
                {result.errors.map((e, i) => (
                  <li key={i}>
                    Row {e.row}: {e.error}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {result.skipped?.length > 0 && (
            <details>
              <summary>Skipped duplicates</summary>
              <ul>
                {result.skipped.map((s, i) => (
                  <li key={i}>
                    Row {s.row}: Aadhar {s.aadhar} already exists
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
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

function sampleCsvHref() {
  const csv =
    'name,aadhar_number,skill_type,contractor_email\n' +
    'Ramesh Gupta,200000000011,Mason,contractor.one@vendor.com\n' +
    'Vijay Rao,200000000012,Plumber,contractor.one@vendor.com\n'
  return 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv)
}
