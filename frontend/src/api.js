const BASE = '/api'

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {})
  const token = window.localStorage.getItem('video-fission.token')
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (options.body && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
    headers,
  })
  const contentType = response.headers.get('content-type') || ''
  const data = contentType.includes('application/json')
    ? await response.json()
    : await response.text()
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new CustomEvent('video-fission:auth-expired'))
    }
    const detail = typeof data === 'string' ? data : data.detail || data.message
    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || String(item)).join('；')
      : detail
    throw new Error(message || `请求失败 (${response.status})`)
  }
  return data
}

const json = (method, body) => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const api = {
  login: (username, password) => {
    const body = new FormData()
    body.append('username', username)
    body.append('password', password)
    return request('/auth/login', { method: 'POST', body })
  },
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),
  ssoLoginUrl: () => request('/sso/login'),
  users: () => request('/users'),
  createUser: (payload) => request('/users', json('POST', payload)),
  updateUser: (id, payload) =>
    request(`/users/${encodeURIComponent(id)}`, json('PATCH', payload)),
  deleteUser: (id) =>
    request(`/users/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  health: () => request('/health'),
  capabilities: () => request('/capabilities'),
  models: (refresh = false) => request(`/models${refresh ? '?refresh=true' : ''}`),
  conversations: ({
    archived = false,
    cursor = '',
    query = '',
    limit = 30,
  } = {}) => {
    const search = new URLSearchParams({
      archived: String(Boolean(archived)),
      limit: String(limit),
    })
    if (cursor) search.set('cursor', cursor)
    if (query.trim()) search.set('q', query.trim())
    return request(`/conversations?${search.toString()}`)
  },
  conversation: (id) => request(`/conversations/${id}`),
  createConversation: (payload = {}) =>
    request('/conversations', json('POST', payload)),
  updateConversation: (id, payload) =>
    request(`/conversations/${id}`, json('PATCH', payload)),
  deleteConversation: (id) =>
    request(`/conversations/${id}`, { method: 'DELETE' }),
  sendMessage: (id, payload) =>
    request(`/conversations/${id}/messages`, json('POST', payload)),
  steer: (id, payload) =>
    request(`/conversations/${id}/steer`, json('POST', payload)),
  interrupt: (id) =>
    request(`/conversations/${id}/interrupt`, { method: 'POST' }),
  compact: (id) =>
    request(`/conversations/${id}/compact`, { method: 'POST' }),
  fork: (id) =>
    request(`/conversations/${id}/fork`, { method: 'POST' }),
  archive: (id) =>
    request(`/conversations/${id}/archive`, { method: 'POST' }),
  unarchive: (id) =>
    request(`/conversations/${id}/unarchive`, { method: 'POST' }),
  sync: (id) =>
    request(`/conversations/${id}/sync`, { method: 'POST' }),
  decideApproval: (id, decision) =>
    request(`/approvals/${id}/decide`, json('POST', { decision })),
  answerClarification: (id, answers) =>
    request(`/clarifications/${id}/answer`, json('POST', { answers })),
  upload: (file) => {
    const body = new FormData()
    body.append('file', file)
    return request('/uploads', { method: 'POST', body })
  },
  deleteUpload: (id) =>
    request(`/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  skills: () => request('/skills'),
  skill: (slug) => request(`/skills/${encodeURIComponent(slug)}`),
  createSkill: (payload) => request('/skills', json('POST', payload)),
  updateSkill: (slug, payload) =>
    request(`/skills/${encodeURIComponent(slug)}`, json('PUT', payload)),
  toggleSkill: (slug, enabled) =>
    request(`/skills/${encodeURIComponent(slug)}/toggle`, json('POST', { enabled })),
  deleteSkill: (slug) =>
    request(`/skills/${encodeURIComponent(slug)}`, { method: 'DELETE' }),
  tools: () => request('/tools'),
  settings: () => request('/settings'),
  updateSettings: (payload) => request('/settings', json('PUT', payload)),
  testSettings: (target) => request('/settings/test', json('POST', { target })),
  uploadProductionVideo: (file) => {
    const body = new FormData()
    body.append('file', file)
    return request('/productions/uploads', { method: 'POST', body })
  },
  productions: (conversationId) =>
    request(
      conversationId
        ? `/productions?conversation_id=${encodeURIComponent(conversationId)}`
        : '/productions',
    ),
  production: (id) => request(`/productions/${encodeURIComponent(id)}`),
  createProduction: (payload) => request('/productions', json('POST', payload)),
  patchProduction: (id, payload) =>
    request(`/productions/${encodeURIComponent(id)}`, json('PATCH', payload)),
  runProduction: (id, payload) =>
    request(
      `/productions/${encodeURIComponent(id)}/run`,
      payload === undefined ? { method: 'POST' } : json('POST', payload),
    ),
  cancelProduction: (id) =>
    request(`/productions/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  selectScript: (id, scriptId) =>
    request(
      `/productions/${encodeURIComponent(id)}/select-script`,
      json('POST', { script_id: scriptId }),
    ),
  deleteProduction: (id) =>
    request(`/productions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}

export function eventUrl(conversationId, afterId = 0) {
  return `${BASE}/conversations/${conversationId}/events?after_id=${afterId}`
}

export function productionEventUrl(productionId, afterId = 0) {
  return `${BASE}/productions/${encodeURIComponent(productionId)}/events?after_id=${afterId}`
}

export function productionArtifactUrl(productionId, name) {
  return `${BASE}/productions/${encodeURIComponent(productionId)}/artifacts/${encodeURIComponent(name)}`
}


export function productionUploadUrl(uploadId) {
  return `${BASE}/productions/uploads/${encodeURIComponent(uploadId)}`
}
