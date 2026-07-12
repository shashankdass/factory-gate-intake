import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { api } from '../api'

// The four hardcoded dummy personas the role-switcher toggles between. Passwords
// live here purely for the seamless-testing masquerade requirement — this is a
// demo affordance, not a production auth pattern.
export const PERSONAS = [
  {
    key: 'PE',
    label: 'Principal Employer',
    email: 'pe.admin@factory.com',
    password: 'pe_test_123',
    home: '/employer',
    color: '#2563eb',
  },
  {
    key: 'CONTRACTOR',
    label: 'Contractor',
    email: 'contractor.one@vendor.com',
    password: 'contractor_test_123',
    home: '/contractor',
    color: '#059669',
  },
  {
    key: 'FIELD_OFFICER',
    label: 'Field Officer',
    email: 'field.officer@vendor.com',
    password: 'field_test_123',
    home: '/field-officer',
    color: '#d97706',
  },
  {
    key: 'GATE_SECURITY',
    label: 'Gate Security',
    email: 'gate.security@factory.com',
    password: 'gate_test_123',
    home: '/gate',
    color: '#dc2626',
  },
]

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  // Cache one token per persona so switching is instant after the first login.
  const [tokens, setTokens] = useState({})
  const [activeKey, setActiveKey] = useState('PE')
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const activePersona = useMemo(
    () => PERSONAS.find((p) => p.key === activeKey) || PERSONAS[0],
    [activeKey]
  )

  async function switchTo(key) {
    const persona = PERSONAS.find((p) => p.key === key)
    if (!persona) return
    setActiveKey(key)
    setError(null)

    // Reuse cached token if we already logged in as this persona.
    if (tokens[key]) {
      setUser(tokens[key].user)
      return
    }

    setLoading(true)
    try {
      const data = await api.login(persona.email, persona.password)
      setTokens((prev) => ({ ...prev, [key]: data }))
      setUser(data.user)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  // Auto-login as the default persona on first mount.
  useEffect(() => {
    switchTo('PE')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const value = {
    personas: PERSONAS,
    activePersona,
    activeKey,
    user,
    token: tokens[activeKey]?.token || null,
    loading,
    error,
    switchTo,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
