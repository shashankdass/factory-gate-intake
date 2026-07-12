import { useAuth } from '../context/AuthContext.jsx'

// Prominent global header with the role-switching toggle. Clicking a persona
// logs in as that dummy account (cached after first use), swaps the active token
// context and routes to the matching dashboard.
export default function Navbar() {
  const { personas, activeKey, activePersona, user, switchTo, error } = useAuth()

  return (
    <header className="navbar" style={{ borderTopColor: activePersona.color }}>
      <div className="navbar-brand">
        <span className="logo-dot" style={{ background: activePersona.color }} />
        <div>
          <div className="brand-title">Factory Gate Intake</div>
          <div className="brand-sub">Onboarding verification, in under 24h</div>
        </div>
      </div>

      <div className="role-switcher">
        {personas.map((p) => (
          <button
            key={p.key}
            className={`role-btn ${activeKey === p.key ? 'active' : ''}`}
            style={activeKey === p.key ? { background: p.color } : undefined}
            onClick={() => switchTo(p.key)}
            title={p.email}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="navbar-user">
        {error ? (
          <span className="user-error">⚠ {error}</span>
        ) : (
          <>
            <span className="user-role">{activePersona.label}</span>
            <span className="user-email">{user?.email || activePersona.email}</span>
          </>
        )}
      </div>
    </header>
  )
}
