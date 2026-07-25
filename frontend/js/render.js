const STATE_LABEL = { SAFE: "SAFE", WARNING: "WARNING", CRITICAL: "CRITICAL" };
const STATE_ICON = { SAFE: "✓", WARNING: "▲", CRITICAL: "✕" };

const SENSOR_ICON = { fire: "FIRE", gas: "GAS", water: "WATER", pir: "PIR" };

function formatSensorValue(type, value) {
  if (value === null || value === undefined) return "--";
  if (type === "fire" || type === "pir") return value ? "ON" : "off";
  return `${Math.round(value * 100)}%`;
}

function relativeTime(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${seconds.toFixed(0)}s ago`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(0)}m ago`;
  return `${(seconds / 3600).toFixed(1)}h ago`;
}

export function renderZoneGrid(container, zones, { role, onAck, onOpenTimeline }) {
  container.innerHTML = "";
  for (const zone of zones) {
    const card = document.createElement("div");
    card.className = "zone-card";
    card.dataset.state = zone.state;
    card.dataset.offline = String(zone.offline);
    if (zone.incident_status === "open") card.classList.add("pulsing");

    const head = document.createElement("div");
    head.className = "zone-card-head";

    const name = document.createElement("div");
    name.className = "zone-name";
    name.textContent = zone.name;
    head.appendChild(name);

    const badges = document.createElement("div");
    const stateBadge = document.createElement("span");
    stateBadge.className = "state-badge";
    stateBadge.dataset.state = zone.state;
    stateBadge.textContent = `${STATE_ICON[zone.state] || ""} ${STATE_LABEL[zone.state] || zone.state}`;
    badges.appendChild(stateBadge);
    if (zone.offline) {
      const offlineBadge = document.createElement("span");
      offlineBadge.className = "offline-badge";
      offlineBadge.textContent = "OFFLINE";
      badges.appendChild(offlineBadge);
    }
    head.appendChild(badges);
    card.appendChild(head);

    const scoreLabel = document.createElement("div");
    scoreLabel.className = "risk-score-label";
    scoreLabel.textContent = "Risk score";
    card.appendChild(scoreLabel);

    const score = document.createElement("div");
    score.className = "risk-score";
    score.textContent = zone.offline ? "--" : Math.round(zone.risk_score);
    card.appendChild(score);

    const sensorRow = document.createElement("div");
    sensorRow.className = "sensor-row";
    for (const sensor of zone.sensors) {
      const chip = document.createElement("span");
      chip.className = "sensor-chip";
      chip.dataset.status = sensor.status;
      const label = SENSOR_ICON[sensor.type] || sensor.type.toUpperCase();
      chip.textContent =
        sensor.status === "offline" ? `${label} OFFLINE` : `${label} ${formatSensorValue(sensor.type, sensor.value)}`;
      sensorRow.appendChild(chip);
    }
    card.appendChild(sensorRow);

    const meta = document.createElement("div");
    meta.className = "hint-text";
    meta.textContent = `Last reading: ${relativeTime(zone.last_reading_at)}`;
    card.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "zone-card-actions";

    if (zone.incident_status === "open" && (role === "staff" || role === "admin")) {
      const ackBtn = document.createElement("button");
      ackBtn.textContent = "Acknowledge";
      ackBtn.addEventListener("click", () => onAck(zone.open_incident_id, ackBtn));
      actions.appendChild(ackBtn);
    } else if (zone.incident_status === "acked") {
      const ackedNote = document.createElement("span");
      ackedNote.className = "hint-text";
      ackedNote.textContent = "Acknowledged";
      actions.appendChild(ackedNote);
    }

    if (zone.open_incident_id) {
      const viewBtn = document.createElement("button");
      viewBtn.className = "ghost-btn";
      viewBtn.textContent = "View incident";
      viewBtn.addEventListener("click", () => onOpenTimeline(zone.open_incident_id));
      actions.appendChild(viewBtn);
    }

    card.appendChild(actions);
    container.appendChild(card);
  }
}

export function renderPriorityPanel(sectionEl, listEl, bannerEl, priorityQueue) {
  if (!priorityQueue.length) {
    sectionEl.hidden = true;
    bannerEl.hidden = true;
    bannerEl.textContent = "";
    return;
  }

  sectionEl.hidden = false;
  bannerEl.hidden = false;
  bannerEl.textContent = `⚠ MOST URGENT: ${priorityQueue[0].zone_name}`;

  listEl.innerHTML = "";
  priorityQueue.forEach((entry, index) => {
    const li = document.createElement("li");
    li.className = "priority-item";
    if (index === 0) li.classList.add("top");

    const zoneLine = document.createElement("div");
    const rank = document.createElement("span");
    rank.className = "priority-rank";
    rank.textContent = `#${index + 1}`;
    zoneLine.appendChild(rank);
    const name = document.createElement("span");
    name.className = "priority-zone";
    name.textContent = entry.zone_name;
    zoneLine.appendChild(name);
    li.appendChild(zoneLine);

    const justification = document.createElement("div");
    justification.className = "priority-justification";
    justification.textContent = entry.justification;
    li.appendChild(justification);

    listEl.appendChild(li);
  });
}

export function renderIncidentTable(tbody, incidents, onRowClick) {
  tbody.innerHTML = "";
  for (const incident of incidents) {
    const tr = document.createElement("tr");
    tr.addEventListener("click", () => onRowClick(incident.id));

    const zoneTd = document.createElement("td");
    zoneTd.textContent = incident.zone_name;
    tr.appendChild(zoneTd);

    const statusTd = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "status-pill";
    pill.dataset.status = incident.status;
    pill.textContent = incident.status;
    statusTd.appendChild(pill);
    tr.appendChild(statusTd);

    const peakTd = document.createElement("td");
    peakTd.textContent = Math.round(incident.peak_risk);
    tr.appendChild(peakTd);

    const openedTd = document.createElement("td");
    openedTd.textContent = new Date(incident.opened_at).toLocaleString();
    tr.appendChild(openedTd);

    const resolvedTd = document.createElement("td");
    resolvedTd.textContent = incident.resolved_at ? new Date(incident.resolved_at).toLocaleString() : "--";
    tr.appendChild(resolvedTd);

    tbody.appendChild(tr);
  }
}

export function renderIncidentDetail(container, data) {
  container.innerHTML = "";
  const { incident, transitions } = data;

  const title = document.createElement("h3");
  title.textContent = `${incident.zone_name} — Incident #${incident.id}`;
  container.appendChild(title);

  const summary = document.createElement("p");
  summary.className = "hint-text";
  summary.textContent = `Peak risk ${Math.round(incident.peak_risk)} · status ${incident.status}${
    incident.ack ? ` · acked by user #${incident.ack.user_id} at ${new Date(incident.ack.ts).toLocaleString()}` : ""
  }`;
  container.appendChild(summary);

  for (const t of transitions) {
    const row = document.createElement("div");
    row.className = "timeline-row";

    const time = document.createElement("div");
    time.className = "timeline-time";
    time.textContent = new Date(t.ts).toLocaleTimeString();
    row.appendChild(time);

    const desc = document.createElement("div");
    desc.textContent = `${t.from_state} → ${t.to_state} (risk ${Math.round(t.risk_score)}, ${t.cause}${
      t.reason ? `: ${t.reason}` : ""
    })`;
    row.appendChild(desc);

    container.appendChild(row);
  }
}

export function renderHealth(dl, health) {
  dl.innerHTML = "";
  const rows = [
    ["Status", health.status],
    ["Zones", health.zone_count],
    ["Zones online", health.zones_online],
    ["Readings stored", health.reading_count],
    ["Open incidents", health.open_incidents],
    ["Server time", new Date(health.server_time).toLocaleString()],
  ];
  for (const [label, value] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    dl.appendChild(dt);
    dl.appendChild(dd);
  }
}

export function showToast(container, { title, body }) {
  const toast = document.createElement("div");
  toast.className = "toast";

  const titleEl = document.createElement("div");
  titleEl.className = "toast-title";
  titleEl.textContent = title;
  toast.appendChild(titleEl);

  const bodyEl = document.createElement("div");
  bodyEl.className = "toast-body";
  bodyEl.textContent = body;
  toast.appendChild(bodyEl);

  container.appendChild(toast);
  setTimeout(() => toast.remove(), 8000);
}

export function playAlertBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
    osc.onended = () => ctx.close();
  } catch (_) {
    /* audio not available in this environment - non-fatal */
  }
}
