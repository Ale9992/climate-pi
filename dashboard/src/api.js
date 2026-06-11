// api.js — piccolo wrapper sulle chiamate REST al backend FastAPI.

const BASE = '/api'

async function req(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText} ${text}`)
  }
  // Alcuni endpoint (DELETE) possono non avere corpo JSON.
  const ct = res.headers.get('content-type') || ''
  return ct.includes('application/json') ? res.json() : null
}

export const api = {
  getRooms: () => req('/rooms'),
  getStatus: () => req('/status'),
  getConfig: () => req('/config'),
  getLogs: (limit = 100) => req(`/logs?limit=${limit}`),
  getHistory: (room, hours = 24) =>
    req(`/rooms/${encodeURIComponent(room)}/history?hours=${hours}`),

  // Luci IKEA
  getLights: () => req('/lights'),
  setLight: (id, body) =>
    req(`/lights/${encodeURIComponent(id)}`, { method: 'POST', body: JSON.stringify(body) }),
  setRoomLights: (room, body) =>
    req(`/lights/room/${encodeURIComponent(room)}`, { method: 'POST', body: JSON.stringify(body) }),

  setOverride: (room, body) =>
    req(`/rooms/${encodeURIComponent(room)}/ac/override`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  control: (room, body) =>
    req(`/rooms/${encodeURIComponent(room)}/ac/control`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  clearOverride: (room) =>
    req(`/rooms/${encodeURIComponent(room)}/ac/override`, { method: 'DELETE' }),

  updateRules: (room, rules) =>
    req(`/config/rooms/${encodeURIComponent(room)}/rules`, {
      method: 'PUT',
      body: JSON.stringify({ rules }),
    }),
  updateSchedule: (force_off_time) =>
    req('/config/schedule', {
      method: 'PUT',
      body: JSON.stringify({ force_off_time }),
    }),
}
