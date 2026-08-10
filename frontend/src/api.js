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

const form = (path, formData, token) =>
  request(path, { method: 'POST', body: formData, token, isForm: true })

export const api = {
  base: BASE_URL,

  login: (email, password) =>
    request('/auth/login/', { method: 'POST', body: { email, password } }),

  me: (token) => request('/me/', { token }),

  // Master requirement catalogue
  requirements: (token) => request('/requirements/', { token }),

  // Projects (read-only — configuration was removed from the PE dashboard)
  projects: (token) => request('/projects/', { token }),
  project: (token, id) => request(`/projects/${id}/`, { token }),
  eligibleWorkers: (token, projectId, contractorId) =>
    request(
      `/projects/${projectId}/eligible-workers/${
        contractorId ? `?contractor_id=${contractorId}` : ''
      }`,
      { token }
    ),

  // Contractor workforce planning — "3 Carpenters, 4 Masons"
  workforceDemand: (token, body) =>
    request('/workforce-demand/', { method: 'POST', body, token }),

  // Workers (the contractor's own pool)
  workers: (token) => request('/workers/', { token }),
  createWorker: (token, body) =>
    request('/workers/', { method: 'POST', body, token }),
  deleteWorker: (token, id) =>
    request(`/workers/${id}/`, { method: 'DELETE', token }),
  verificationStatus: (token) => request('/verification-status/', { token }),

  // Documents (inline gap-fix upload)
  uploadDocument: (token, formData) => form('/documents/upload/', formData, token),
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

  // Gate security — real-time compliance check
  gateCheck: (token, aadhar) =>
    request(`/gate-check/?aadhar=${encodeURIComponent(aadhar)}`, { token }),

  // Verification & Testing (single-document re-verify, 5 pillars)
  verifyDocument: (token, body) =>
    request('/intake/verify-document/', { method: 'POST', body, token }),
  // Multipart variant — carries the uploaded scan alongside the form fields.
  verifyDocumentForm: (token, formData) =>
    form('/intake/verify-document/', formData, token),
  // Real OCR on an uploaded scan → prefill fields for the given doc_type.
  ocrExtract: (token, formData) => form('/intake/ocr-extract/', formData, token),

  // Unified single-pass onboarding (5 pillars + resume, one submission)
  onboardWorker: (token, formData) => form('/intake/onboard-worker/', formData, token),

  // Resume scanning + candidate search
  parseResume: (token, formData) => form('/resume/parse/', formData, token),
  searchCandidates: (token, params) => {
    const query = new URLSearchParams()
    Object.entries(params || {}).forEach(([key, value]) => {
      if (Array.isArray(value)) value.forEach((v) => v && query.append(key, v))
      else if (value !== '' && value != null) query.append(key, value)
    })
    return request(`/candidates/search/?${query.toString()}`, { token })
  },

  // Fresh expiring download links for private documents
  signedUrls: (token, keys) =>
    request('/storage/signed-url/', { method: 'POST', body: { keys }, token }),

  // Trade test (Contractor-administered practical MCQ exam)
  tradeTestStart: (token, workerId) =>
    request(`/trade-test/start/?worker_id=${workerId}`, { token }),
  tradeTestSubmit: (token, body) =>
    request('/trade-test/submit-attempt/', { method: 'POST', body, token }),

  // Safety induction video watch progress
  safetyVideoHeartbeat: (token, body) =>
    request('/safety-video/heartbeat/', { method: 'POST', body, token }),
}
