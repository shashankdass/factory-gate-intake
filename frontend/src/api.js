// Thin fetch wrapper around the Django REST API.
// Base URL is env-driven so the same build works across environments.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

async function request(path, { method = 'GET', body, token, isForm = false } = {}) {
  const headers = {}
  if (token) headers['Authorization'] = `Token ${token}`

  let payload = body
  if (body && !isForm) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }

  const res = await fetch(`${BASE_URL}${path}`, { method, headers, body: payload })

  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { detail: text }
  }

  if (!res.ok) {
    const message = (data && (data.detail || JSON.stringify(data))) || res.statusText
    throw new Error(message)
  }
  return data
}

export const api = {
  base: BASE_URL,

  login: (email, password) =>
    request('/auth/login/', { method: 'POST', body: { email, password } }),

  me: (token) => request('/me/', { token }),

  // Projects
  projects: (token) => request('/projects/', { token }),
  project: (token, id) => request(`/projects/${id}/`, { token }),
  createProject: (token, body) =>
    request('/projects/', { method: 'POST', body, token }),
  eligibleWorkers: (token, projectId, contractorId) =>
    request(
      `/projects/${projectId}/eligible-workers/${
        contractorId ? `?contractor_id=${contractorId}` : ''
      }`,
      { token }
    ),

  // Workers / Field officer
  workers: (token) => request('/workers/', { token }),
  bulkUpload: (token, formData) =>
    request('/workers/bulk-upload/', {
      method: 'POST',
      body: formData,
      token,
      isForm: true,
    }),

  // Documents (contractor inline upload)
  uploadDocument: (token, formData) =>
    request('/documents/upload/', {
      method: 'POST',
      body: formData,
      token,
      isForm: true,
    }),
  reviewDocument: (token, id, body) =>
    request(`/documents/${id}/review/`, { method: 'PATCH', body, token }),

  // Intake lists
  intakeLists: (token) => request('/intake-lists/', { token }),
  submitList: (token, body) =>
    request('/intake-lists/', { method: 'POST', body, token }),
  updateList: (token, id, body) =>
    request(`/intake-lists/${id}/`, { method: 'PATCH', body, token }),
  reviewList: (token, id, body) =>
    request(`/intake-lists/${id}/review/`, { method: 'PATCH', body, token }),

  // Gate security
  gateCheck: (token, aadhar) =>
    request(`/gate-check/?aadhar=${encodeURIComponent(aadhar)}`, { token }),
}
