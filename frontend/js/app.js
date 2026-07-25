import { api } from "./api.js";
import {
  renderZoneGrid,
  renderPriorityPanel,
  renderIncidentTable,
  renderIncidentDetail,
  renderHealth,
  showToast,
  playAlertBeep,
} from "./render.js";
import { WSClient } from "./ws.js";

const STORAGE_KEY = "scsrg_session";

const els = {
  loginView: document.getElementById("login-view"),
  loginForm: document.getElementById("login-form"),
  loginError: document.getElementById("login-error"),
  dashboardView: document.getElementById("dashboard-view"),
  connBadge: document.getElementById("conn-badge"),
  whoUser: document.getElementById("who-user"),
  logoutBtn: document.getElementById("logout-btn"),
  urgentBanner: document.getElementById("urgent-banner"),
  zoneGrid: document.getElementById("zone-grid"),
  prioritySection: document.getElementById("priority-section"),
  priorityList: document.getElementById("priority-list"),
  incidentFilters: document.getElementById("incident-filters"),
  filterZone: document.getElementById("filter-zone"),
  filterStatus: document.getElementById("filter-status"),
  filterFrom: document.getElementById("filter-from"),
  filterTo: document.getElementById("filter-to"),
  incidentTableBody: document.getElementById("incident-table-body"),
  adminSection: document.getElementById("admin-section"),
  overrideForm: document.getElementById("override-form"),
  overrideZone: document.getElementById("override-zone"),
  overrideTarget: document.getElementById("override-target"),
  overrideReason: document.getElementById("override-reason"),
  overrideResult: document.getElementById("override-result"),
  healthList: document.getElementById("health-list"),
  toastContainer: document.getElementById("toast-container"),
  incidentModal: document.getElementById("incident-modal"),
  incidentModalBody: document.getElementById("incident-modal-body"),
  incidentModalClose: document.getElementById("incident-modal-close"),
};

let session = null; // { token, role, username }
let ws = null;
let latestSnapshot = { zones: [], priority_queue: [] };
const toastedIncidentIds = new Set();
let healthInterval = null;
let lastZonesSignature = null;

function saveSession(s) {
  session = s;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
}

function clearSession() {
  session = null;
  localStorage.removeItem(STORAGE_KEY);
}

function loadStoredSession() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (_) {
    return null;
  }
}

function setConnected(connected) {
  els.connBadge.dataset.connected = String(connected);
  els.connBadge.textContent = connected ? "LIVE" : "RECONNECTING…";
}

function populateZoneSelects(zones) {
  const options = zones.map((z) => `<option value="${z.zone_id}">${z.name}</option>`).join("");
  els.filterZone.innerHTML = `<option value="">All</option>${options}`;
  els.overrideZone.innerHTML = options;
}

function renderSnapshot(snapshot) {
  latestSnapshot = snapshot;

  // The periodic broadcast (backend/broadcaster.py) fires every ~1s even
  // when nothing changed (it's what keeps unacked-seconds escalation live).
  // Rebuilding the whole grid via innerHTML on every tick would blow away
  // in-flight interactions (a button mid-click, an open <select>) for no
  // reason - only rebuild when the zones data actually differs.
  const zonesSignature = JSON.stringify(snapshot.zones);
  if (zonesSignature !== lastZonesSignature) {
    lastZonesSignature = zonesSignature;
    renderZoneGrid(els.zoneGrid, snapshot.zones, {
      role: session?.role,
      onAck: handleAck,
      onOpenTimeline: openIncidentModal,
    });
  }

  renderPriorityPanel(els.prioritySection, els.priorityList, els.urgentBanner, snapshot.priority_queue);

  const openNow = snapshot.zones.filter((z) => z.incident_status === "open");
  const freshAlerts = openNow.filter((z) => !toastedIncidentIds.has(z.open_incident_id));
  for (const zone of freshAlerts) {
    toastedIncidentIds.add(zone.open_incident_id);
    showToast(els.toastContainer, {
      title: `⚠ CRITICAL — ${zone.name}`,
      body: `Risk score ${Math.round(zone.risk_score)}. Acknowledge to silence.`,
    });
  }
  if (freshAlerts.length > 0) playAlertBeep();
}

async function handleAck(incidentId, button) {
  button.disabled = true;
  try {
    await api.ack(session.token, incidentId);
  } catch (err) {
    if (err.status === 409) {
      alert("Already acknowledged by someone else.");
    } else {
      alert(`Ack failed: ${err.message}`);
    }
    button.disabled = false;
  }
  // No local mutation: the ack broadcast (single source of truth) will
  // re-render this card via the next WS message.
}

async function openIncidentModal(incidentId) {
  try {
    const data = await api.incidentTimeline(session.token, incidentId);
    renderIncidentDetail(els.incidentModalBody, data);
    els.incidentModal.hidden = false;
  } catch (err) {
    alert(`Could not load incident: ${err.message}`);
  }
}

async function refreshIncidentTable() {
  const filters = {
    zone: els.filterZone.value,
    status: els.filterStatus.value,
    from: els.filterFrom.value ? new Date(els.filterFrom.value).toISOString() : "",
    to: els.filterTo.value ? new Date(els.filterTo.value).toISOString() : "",
  };
  const incidents = await api.incidents(session.token, filters);
  renderIncidentTable(els.incidentTableBody, incidents, openIncidentModal);
}

async function refreshHealth() {
  if (session.role !== "admin") return;
  try {
    const health = await api.health(session.token);
    renderHealth(els.healthList, health);
  } catch (_) {
    /* non-fatal - admin panel just stays stale until next tick */
  }
}

async function fullRefetch() {
  const snapshot = await api.zonesStatus(session.token);
  populateZoneSelects(snapshot.zones);
  renderSnapshot(snapshot);
  await refreshIncidentTable();
}

function showDashboard() {
  els.loginView.hidden = true;
  els.dashboardView.hidden = false;
  els.whoUser.textContent = `${session.username} (${session.role})`;
  els.adminSection.hidden = session.role !== "admin";

  ws = new WSClient({
    token: session.token,
    onMessage: (msg) => {
      if (msg.event === "incident_ack" || msg.event === "state_change" || msg.event === "periodic_snapshot" || msg.event === "snapshot") {
        renderSnapshot(msg);
      }
      // Meaningful events only (not the 1s periodic tick) - the timeline
      // table is a filtered historical view, not worth refetching every
      // second, but it should never sit stale through an ack or transition.
      if (msg.event === "incident_ack" || msg.event === "state_change") {
        refreshIncidentTable().catch(() => {});
      }
    },
    onReconnect: () => {
      fullRefetch().catch(() => {});
    },
    onStatusChange: setConnected,
  });

  fullRefetch().catch((err) => {
    if (err.status === 401) logout();
  });

  if (session.role === "admin") {
    refreshHealth();
    healthInterval = setInterval(refreshHealth, 5000);
  }
}

function logout() {
  ws?.close();
  ws = null;
  if (healthInterval) clearInterval(healthInterval);
  toastedIncidentIds.clear();
  lastZonesSignature = null;
  clearSession();
  els.dashboardView.hidden = true;
  els.loginView.hidden = false;
}

els.loginForm.addEventListener("submit", async (evt) => {
  evt.preventDefault();
  els.loginError.hidden = true;
  const username = document.getElementById("login-username").value.trim();
  const password = document.getElementById("login-password").value;
  try {
    const result = await api.login(username, password);
    saveSession(result);
    showDashboard();
  } catch (err) {
    els.loginError.textContent = err.status === 401 ? "Invalid username or password." : err.message;
    els.loginError.hidden = false;
  }
});

els.logoutBtn.addEventListener("click", logout);

els.incidentFilters.addEventListener("submit", (evt) => {
  evt.preventDefault();
  refreshIncidentTable().catch((err) => alert(`Filter failed: ${err.message}`));
});

els.overrideForm.addEventListener("submit", async (evt) => {
  evt.preventDefault();
  els.overrideResult.textContent = "Applying...";
  try {
    const result = await api.override(session.token, {
      zone_id: Number(els.overrideZone.value),
      target_state: els.overrideTarget.value,
      reason: els.overrideReason.value,
    });
    els.overrideResult.textContent = `Zone is now ${result.state}${result.transitioned ? "" : " (no change)"}.`;
  } catch (err) {
    els.overrideResult.textContent =
      err.status === 403 ? "Forbidden - admin role required." : `Failed: ${err.message}`;
  }
});

els.incidentModalClose.addEventListener("click", () => {
  els.incidentModal.hidden = true;
});
els.incidentModal.addEventListener("click", (evt) => {
  if (evt.target === els.incidentModal) els.incidentModal.hidden = true;
});

const stored = loadStoredSession();
if (stored) {
  session = stored;
  showDashboard();
}
