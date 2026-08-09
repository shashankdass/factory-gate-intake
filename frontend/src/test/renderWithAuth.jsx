import { render } from '@testing-library/react'
import { vi } from 'vitest'
import { AuthContext } from '../context/AuthContext.jsx'

// Components read the active token from AuthContext. Tests provide it directly
// rather than going through the role-switcher's real login round trip.
const DEFAULT_VALUE = {
  personas: [],
  activePersona: { key: 'CONTRACTOR', label: 'Contractor', home: '/contractor', color: '#059669' },
  activeKey: 'CONTRACTOR',
  user: { email: 'contractor.one@vendor.com', role: 'CONTRACTOR' },
  token: 'test-token',
  loading: false,
  error: null,
  switchTo: vi.fn(),
}

export function renderWithAuth(ui, { auth = {}, ...options } = {}) {
  const value = { ...DEFAULT_VALUE, ...auth }
  return render(
    <AuthContext.Provider value={value}>{ui}</AuthContext.Provider>,
    options
  )
}
