async function request(path, { method = "GET", token, body } = {}) {
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* empty body */
  }

  if (!res.ok) {
    const message = (data && data.detail) || `${method} ${path} failed (${res.status})`;
    const err = new Error(message);
    err.status = res.status;
    err.body = data;
    throw err;
  }
  return data;
}

export const api = {
  login: (username, password) =>
    request("/api/auth/login", { method: "POST", body: { username, password } }),

  zonesStatus: (token) => request("/api/zones/status", { token }),

  incidents: (token, filters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value);
    }
    const qs = params.toString();
    return request(`/api/incidents${qs ? `?${qs}` : ""}`, { token });
  },

  incidentTimeline: (token, id) => request(`/api/incidents/${id}`, { token }),

  ack: (token, id) => request(`/api/incidents/${id}/ack`, { method: "POST", token }),

  override: (token, payload) =>
    request("/api/admin/override", { method: "POST", token, body: payload }),

  health: (token) => request("/api/admin/health", { token }),

  trend: (token, zoneId) => request(`/api/zones/${zoneId}/trend`, { token }),
};
