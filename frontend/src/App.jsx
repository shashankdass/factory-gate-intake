import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useEffect } from 'react'
import Navbar from './components/Navbar.jsx'
import { useAuth } from './context/AuthContext.jsx'
import FieldOfficerUpload from './pages/FieldOfficerUpload.jsx'
import WorkerSelection from './pages/WorkerSelection.jsx'
import PeReview from './pages/PeReview.jsx'
import GateCheck from './pages/GateCheck.jsx'

export default function App() {
  const { activePersona, loading } = useAuth()
  const navigate = useNavigate()

  // Whenever the active persona changes, route to that persona's home dashboard.
  useEffect(() => {
    if (activePersona) navigate(activePersona.home)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePersona?.key])

  return (
    <div className="app">
      <Navbar />
      <main className="main">
        {loading && <div className="banner">Switching persona…</div>}
        <Routes>
          <Route path="/" element={<Navigate to="/employer" replace />} />
          <Route path="/employer" element={<PeReview />} />
          <Route path="/contractor" element={<WorkerSelection />} />
          <Route path="/field-officer" element={<FieldOfficerUpload />} />
          <Route path="/gate" element={<GateCheck />} />
          <Route path="*" element={<Navigate to="/employer" replace />} />
        </Routes>
      </main>
    </div>
  )
}
