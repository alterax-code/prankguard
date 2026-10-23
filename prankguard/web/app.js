"use strict";
/* PrankGuard UI — vanilla JS, aucune dépendance. */

const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t") || "";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = (id, cls = "") => `<svg class="${cls}"><use href="#${id}"/></svg>`;
const q = p => `${p}${p.includes("?") ? "&" : "?"}t=${encodeURIComponent(TOKEN)}`;

async function api(path, body) {
  const opt = { headers: { "X-PG-Token": TOKEN } };
  if (body !== undefined) {
    opt.method = "POST";
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// ── Store ───────────────────────────────────────────────────────────────────
const S = { state: null, settings: null, profiles: [], page: "dashboard", privacy: false, stats: {}, info: {} };

const STATES = {
  starting:    { k: "Initialisation", t: "Démarrage…", s: "Ouverture de la caméra et chargement des modèles.", i: "i-shield" },
  arming:      { k: "Armement", t: "Surveillance en cours d'armement", s: "Protection active dans quelques secondes.", i: "i-shield" },
  idle:        { k: "Protégé", t: "Poste libre", s: "Personne devant l'écran — surveillance active.", i: "i-shield-check" },
  secure:      { k: "Protégé", t: "Bonjour", s: "Vous êtes reconnu. Tout est en ordre.", i: "i-shield-check" },
  watch:       { k: "Vigilance", t: "Visage détecté", s: "Identification en cours…", i: "i-eye" },
  alert:       { k: "Intrusion", t: "Inconnu devant l'écran", s: "Verrouillage imminent.", i: "i-alert" },
  surfer:      { k: "Regard indiscret", t: "Quelqu'un regarde votre écran", s: "Une personne non autorisée lit par-dessus votre épaule.", i: "i-eye" },
  camera_lost: { k: "Caméra", t: "Caméra indisponible", s: "La caméra est masquée, débranchée ou utilisée ailleurs.", i: "i-alert" },
  paused:      { k: "En pause", t: "Surveillance désactivée", s: "Aucun verrouillage automatique.", i: "i-pause" },
  locked:      { k: "Verrouillé", t: "Session verrouillée", s: "Caméra libérée jusqu'au déverrouillage.", i: "i-lock" },
  setup:       { k: "Configuration", t: "Enregistrez votre visage", s: "Sans profil, seule la règle « périphériques : Toujours » peut verrouiller.", i: "i-scan" },
};
const SHORT = { starting: "Démarrage…", arming: "Armement…", idle: "Protégé", secure: "Protégé", watch: "Vigilance",
  alert: "Intrusion !", surfer: "Regard indiscret", camera_lost: "Caméra indisponible", paused: "En pause", locked: "Verrouillé", setup: "À configurer" };
const RING_C = 2 * Math.PI * 52;

// ── Navigation ──────────────────────────────────────────────────────────────
function go(page) {
  S.page = page;
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach(p => p.classList.toggle("active", p.id === `page-${page}`));
  if (page === "journal") { loadJournal(true); $("#navJournal").textContent = ""; unseen = 0; }
  if (page === "profiles") loadProfiles();
  if (page === "settings") renderSettings();
  syncStreams();
}
$$(".nav-item").forEach(b => b.addEventListener("click", () => go(b.dataset.page)));
document.addEventListener("click", e => {
  const g = e.target.closest("[data-goto]");
  if (g) go(g.dataset.goto);
  if (!e.target.closest(".dropdown")) $$(".dropdown").forEach(d => d.classList.remove("open"));
});

// ── Flux vidéo : un seul flux actif, uniquement quand il est visible ────────
function syncStreams() {
  const enrollOpen = !$("#enrollModal").hidden && !$('[data-step="capture"]').hidden;
  const dashVisible = S.page === "dashboard" && !document.hidden && !S.privacy && !enrollOpen;
  setStream($("#camImg"), dashVisible);
  setStream($("#enrollImg"), enrollOpen && !document.hidden);
}
function setStream(img, on) {
  const want = on ? q("/stream.mjpg") : "";
  if (on && !img.dataset.on) {
    img.dataset.on = "1";
    img.onload = () => img.classList.add("on");
    img.src = want + "&_=" + Date.now();
  } else if (!on && img.dataset.on) {
    delete img.dataset.on;
    img.classList.remove("on");
    img.removeAttribute("src");
  }
}
document.addEventListener("visibilitychange", syncStreams);

$("#privacyBtn").addEventListener("click", () => {
  S.privacy = !S.privacy;
  $("#cam").classList.toggle("private", S.privacy);
  $("#privacyBtn use").setAttribute("href", S.privacy ? "#i-eye" : "#i-eye-off");
  $("#privacyBtn").title = S.privacy ? "Afficher l'aperçu" : "Masquer l'aperçu (la surveillance continue)";
  syncStreams();
  renderCamEmpty();
});

// ── Temps réel (SSE) ────────────────────────────────────────────────────────
let es, unseen = 0;
function connect() {
  es = new EventSource(q("/sse"));
  es.addEventListener("state", e => onState(JSON.parse(e.data)));
  es.addEventListener("event", e => onEvent(JSON.parse(e.data)));
  es.onerror = () => { $("#brandState").textContent = "Reconnexion…"; };
}

function onState(st) {
  const prev = S.state;
  S.state = st;
  document.body.dataset.state = st.state;
  const meta = STATES[st.state] || STATES.starting;
  $("#heroKicker").textContent = meta.k;
  let title = meta.t, sub = meta.s;
  if (st.state === "secure") title = `Bonjour ${st.owner || ""}`.trim();
  if (st.state === "paused" && st.snooze_left) sub = `Reprise automatique dans ${fmtDur(st.snooze_left)}.`;
  if (st.state === "camera_lost" && st.camera.dark) sub = "L'objectif semble masqué.";
  if (st.state === "camera_lost" && st.camera.error) sub = st.camera.error;
  if (st.state === "camera_lost" && st.camera_waived) sub = "Objectif fermé après votre déverrouillage : pas de nouveau verrouillage tant que l'image ne revient pas.";
  if (st.state === "idle" && !st.profiles) sub = "Aucun profil enregistré : tout visage sera considéré comme inconnu.";
  if (st.countdown != null) {
    if (st.state === "alert") sub = `Verrouillage dans ${st.countdown.toFixed(1)} s`;
    else if (st.state === "surfer") sub = `Verrouillage dans ${st.countdown.toFixed(1)} s si le regard persiste.`;
    else if (st.state === "camera_lost") sub = `Verrouillage dans ${st.countdown.toFixed(0)} s`;
    else if (st.state === "idle" || st.state === "watch") sub = `Verrouillage pour absence dans ${fmtDur(Math.ceil(st.countdown))}.`;
  }
  $("#heroTitle").textContent = title;
  $("#heroSub").textContent = sub;
  $("#heroIconUse").setAttribute("href", "#" + meta.i);
  $("#brandState").textContent = SHORT[st.state] || st.state;
  $("#ringFg").style.strokeDashoffset = RING_C * (1 - ringProgress(st));

  $("#armToggle").checked = st.armed;
  $("#armLabel").textContent = st.armed ? "Surveillance active" : (st.snooze_left ? `Pause · ${fmtDur(st.snooze_left)}` : "Surveillance coupée");
  $("#resumeBtn").hidden = st.armed;
  $("#setupBtn").hidden = st.profiles > 0;
  $("#snoozeDrop").hidden = !st.armed;
  $("#navProfiles").textContent = st.profiles || "";

  const m = st.metrics;
  $("#hudFps").textContent = `${Math.round(m.camera_fps || 0)} fps`;
  $("#hudMs").textContent = `${(m.analysis_ms || 0).toFixed(0)} ms`;
  $("#camInfo").textContent = st.camera.open ? `${st.camera.width}×${st.camera.height} · ${st.camera.backend} · ${m.analysis_fps.toFixed(0)} analyses/s` : "";
  renderFaces(st.faces);
  renderCamEmpty();
  if (st.enroll) {
    renderEnroll(st.enroll);
    // enrôlement orphelin (page rechargée, modale fermée) : on ne laisse jamais la protection suspendue
    if (!enrollActive && $("#enrollModal").hidden && !S.cancelSent) {
      S.cancelSent = true;
      api("/api/enroll/cancel", {}).finally(() => setTimeout(() => { S.cancelSent = false; }, 2000));
    }
  }
  if (!prev || prev.profiles !== st.profiles) { if (S.page === "profiles") loadProfiles(); renderRules(); }
}

function ringProgress(st) {
  const s = S.settings;
  if (st.countdown == null || !s) return ["alert", "camera_lost", "surfer"].includes(st.state) ? 1 : 1;
  const total = { alert: s.stranger_delay, surfer: s.surfer_delay, camera_lost: s.camera_lost_delay,
    idle: s.absence_delay, watch: s.absence_delay, arming: s.rearm_delay }[st.state] || 1;
  return Math.max(0, Math.min(1, st.countdown / total));
}

function renderCamEmpty() {
  const st = S.state, box = $("#camEmpty");
  let txt = "", spin = true;
  if (!st) txt = "Connexion…";
  else if (S.privacy) { txt = "Aperçu masqué — la surveillance continue"; spin = false; }
  else if (st.state === "locked") { txt = "Caméra libérée pendant le verrouillage"; spin = false; }
  else if (!st.camera.open) { txt = st.camera.error || "Ouverture de la caméra…"; spin = !st.camera.error; }
  else if (!$("#camImg").classList.contains("on")) txt = "Chargement de l'aperçu…";
  box.hidden = !txt;
  $("#camEmptyText").textContent = txt;
  $(".spinner", box).hidden = !spin;
}

const boxes = new Map();
function renderFaces(faces) {
  const ov = $("#camOverlay");
  const seen = new Set();
  for (const f of faces || []) {
    seen.add(f.id);
    let b = boxes.get(f.id);
    if (!b) {
      b = document.createElement("div");
      b.innerHTML = `<div class="tag"></div><div class="gaze"></div>`;
      ov.appendChild(b);
      boxes.set(f.id, b);
    }
    b.className = `fbox ${f.status}${f.looking ? " looking" : ""}`;
    // l'image est affichée en miroir : on inverse l'axe X
    b.style.left = `${(1 - f.x - f.w) * 100}%`;
    b.style.top = `${f.y * 100}%`;
    b.style.width = `${f.w * 100}%`;
    b.style.height = `${f.h * 100}%`;
    const pct = f.sim != null ? `<small>${Math.round(Math.max(0, f.sim) * 100)}%</small>` : "";
    const label = { known: esc(f.name), unknown: "Inconnu", pending: "Analyse…", spoof: "Photo / écran" }[f.status] || "";
    $(".tag", b).innerHTML = `${label} ${f.status === "spoof" ? "" : pct}`;
    $(".gaze", b).textContent = f.looking ? "REGARDE" : "";
    $(".gaze", b).hidden = !f.looking;
  }
  for (const [id, b] of boxes) if (!seen.has(id)) { b.remove(); boxes.delete(id); }
}

// ── Événements ──────────────────────────────────────────────────────────────
const EV_ICON = { lock: "i-lock", warn: "i-eye", device: "i-plug", session: "i-unlock", system: "i-power", enroll: "i-scan" };
function onEvent(ev) {
  if (ev.kind === "lock") S.stats.lock = (S.stats.lock || 0) + 1;
  if (ev.kind === "warn") S.stats.warn = (S.stats.warn || 0) + 1;
  if (ev.kind === "device") S.stats.device = (S.stats.device || 0) + 1;
  renderStats();
  const li = feedItem(ev);
  $("#feedMini").prepend(li);
  $(".feed-empty", $("#feedMini"))?.remove();
  while ($("#feedMini").children.length > 12) $("#feedMini").lastChild.remove();
  if (S.page === "journal" && (!journalKind || journalKind === ev.kind)) {
    $("#timeline").prepend(timelineItem(ev));
    $("#journalEmpty").hidden = true;
  } else if (ev.level !== "info") {
    $("#navJournal").textContent = ++unseen;
  }
  if (ev.level === "critical") toast(ev.title, "err", "i-lock");
  else if (ev.level === "warning") toast(ev.title, "warn", EV_ICON[ev.kind] || "i-info");
}

function feedItem(ev) {
  const li = document.createElement("li");
  li.className = `lv-${ev.level}`;
  li.innerHTML = `<span class="ic">${icon(EV_ICON[ev.kind] || "i-info")}</span>
    <div><div class="t">${esc(ev.title)}</div><div class="d">${fmtTime(ev.ts)}${ev.detail ? " · " + esc(ev.detail) : ""}</div></div>`;
  return li;
}

function timelineItem(ev) {
  const li = document.createElement("li");
  li.className = `lv-${ev.level}`;
  li.dataset.id = ev.id;
  const snap = ev.snapshot ? `<img class="snap" loading="lazy" src="${q("/media/snap/" + encodeURIComponent(ev.snapshot))}" alt="">` : "";
  let extra = "";
  if (ev.kind === "device") {
    const key = (ev.detail || "").split(" · ")[1];
    if (key) {
      const trusted = (S.settings?.trusted_devices || []).includes(key);
      extra = trusted ? `<span class="muted small">Approuvé</span>`
        : `<button class="btn sm ghost" data-trust="${esc(key)}">${icon("i-check")}Approuver</button>`;
    }
  }
  li.innerHTML = `<span class="ic">${icon(EV_ICON[ev.kind] || "i-info")}</span>
    <div class="body"><div class="t">${esc(ev.title)}</div><div class="d">${esc(ev.detail || "")}</div></div>
    ${extra}${snap}<div class="when">${fmtTime(ev.ts)}</div>`;
  return li;
}

let journalKind = "", journalOldest = null;
async function loadJournal(reset) {
  if (reset) { $("#timeline").innerHTML = ""; journalOldest = null; }
  const p = new URLSearchParams({ limit: 60 });
  if (journalKind) p.set("kind", journalKind);
  if (journalOldest) p.set("before", journalOldest);
  const r = await api(`/api/events?${p}`);
  let lastDay = $("#timeline").dataset.lastDay || "";
  if (reset) lastDay = "";
  for (const ev of r.events) {
    const day = new Date(ev.ts * 1000).toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" });
    if (day !== lastDay) {
      const sep = document.createElement("li");
      sep.className = "day-sep";
      sep.textContent = day;
      $("#timeline").appendChild(sep);
      lastDay = day;
    }
    $("#timeline").appendChild(timelineItem(ev));
    journalOldest = ev.id;
  }
  $("#timeline").dataset.lastDay = lastDay;
  $("#moreEvents").hidden = r.events.length < 60;
  $("#journalEmpty").hidden = !!$("#timeline").children.length;
}
$("#journalFilter").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (!b) return;
  $$("#journalFilter button").forEach(x => x.classList.toggle("active", x === b));
  journalKind = b.dataset.kind;
  loadJournal(true);
});
$("#moreEvents").addEventListener("click", () => loadJournal(false));
$("#timeline").addEventListener("click", async e => {
  const img = e.target.closest(".snap");
  if (img) { $("#lightbox img").src = img.src; $("#lightbox").hidden = false; return; }
  const t = e.target.closest("[data-trust]");
  if (t) {
    await api("/api/devices/trust", { key: t.dataset.trust });
    await loadSettings();
    t.outerHTML = `<span class="muted small">Approuvé</span>`;
    toast("Périphérique approuvé", "ok", "i-check");
  }
});
$("#lightbox").addEventListener("click", () => { $("#lightbox").hidden = true; });

async function loadFeed() {
  const r = await api("/api/events?limit=12");
  const ul = $("#feedMini");
  ul.innerHTML = "";
  r.events.forEach(ev => ul.appendChild(feedItem(ev)));
  if (!r.events.length) ul.innerHTML = `<li class="feed-empty">Aucune activité pour le moment.</li>`;
  S.stats = r.stats24 || {};
  renderStats();
}
function renderStats() {
  $("#stLocks").textContent = S.stats.lock || 0;
  $("#stWarn").textContent = S.stats.warn || 0;
  $("#stDev").textContent = S.stats.device || 0;
}

// ── Actions principales ─────────────────────────────────────────────────────
$("#armToggle").addEventListener("change", e => api("/api/arm", { armed: e.target.checked }));
$("#resumeBtn").addEventListener("click", () => api("/api/arm", { armed: true }));
$("#setupBtn").addEventListener("click", () => { go("profiles"); openEnroll(); });
$("#lockBtn").addEventListener("click", () => api("/api/lock", {}));
$("#snoozeBtn").addEventListener("click", e => { e.stopPropagation(); $("#snoozeDrop").classList.toggle("open"); });
$$("[data-snooze]").forEach(b => b.addEventListener("click", () => {
  $("#snoozeDrop").classList.remove("open");
  api("/api/snooze", { minutes: +b.dataset.snooze });
}));

// ── Profils ─────────────────────────────────────────────────────────────────
async function loadProfiles() {
  const r = await api("/api/profiles");
  S.profiles = r.profiles;
  const grid = $("#profiles");
  grid.innerHTML = "";
  $("#profilesEmpty").hidden = r.profiles.length > 0;
  grid.hidden = !r.profiles.length;
  for (const p of r.profiles) {
    const c = document.createElement("div");
    c.className = "card profile";
    c.innerHTML = `<div class="tools">
        <button class="icon-btn" data-rename="${esc(p.name)}" title="Renommer">${icon("i-edit")}</button>
        <button class="icon-btn del" data-del="${esc(p.name)}" title="Supprimer">${icon("i-trash")}</button></div>
      <img class="avatar" src="${q("/media/face/" + encodeURIComponent(p.name))}&v=${p.created}" alt="">
      <h3>${esc(p.name)}</h3>
      <div class="meta">${p.samples} échantillons · ${new Date(p.created * 1000).toLocaleDateString("fr-FR")}</div>
      <button class="btn sm ghost" data-reenroll="${esc(p.name)}">${icon("i-scan")}Réenregistrer</button>`;
    grid.appendChild(c);
  }
  if (r.profiles.length) {
    const add = document.createElement("div");
    add.className = "card profile add";
    add.dataset.action = "enroll";
    add.innerHTML = `<div class="plus">${icon("i-plus")}</div><b>Ajouter une personne</b><span class="small">Collègue, proche…</span>`;
    grid.appendChild(add);
  }
}
$("#page-profiles").addEventListener("click", async e => {
  const del = e.target.closest("[data-del]");
  if (del) {
    if (!del.classList.contains("confirm")) {
      del.classList.add("confirm");
      del.title = "Cliquer à nouveau pour confirmer";
      toast("Cliquez à nouveau pour supprimer", "warn", "i-trash");
      setTimeout(() => del.classList.remove("confirm"), 3000);
      return;
    }
    await api("/api/profiles/delete", { name: del.dataset.del });
    toast("Profil supprimé", "ok", "i-check");
    return loadProfiles();
  }
  const ren = e.target.closest("[data-rename]");
  if (ren) {
    const h = ren.closest(".profile").querySelector("h3");
    const old = ren.dataset.rename;
    h.innerHTML = `<input class="input" value="${esc(old)}" maxlength="40" style="width:150px;text-align:center">`;
    const inp = $("input", h);
    inp.focus(); inp.select();
    let finished = false;   // Entrée/Échap puis blur ne doivent valider qu'une fois
    const done = async ok => {
      if (finished) return;
      finished = true;
      const v = inp.value.trim();
      if (ok && v && v !== old) {
        const r = await api("/api/profiles/rename", { old, new: v });
        toast(r.ok ? "Profil renommé" : "Nom déjà utilisé", r.ok ? "ok" : "err", r.ok ? "i-check" : "i-x");
      }
      loadProfiles();
    };
    inp.addEventListener("keydown", ev => { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); });
    inp.addEventListener("blur", () => done(true));
    return;
  }
  const re = e.target.closest("[data-reenroll]");
  if (re) return openEnroll(re.dataset.reenroll);
  if (e.target.closest('[data-action="enroll"]')) openEnroll();
});
$("#addProfileBtn").addEventListener("click", () => openEnroll());

// ── Enrôlement ──────────────────────────────────────────────────────────────
const POSES = [["center", "Face"], ["left", "Gauche"], ["right", "Droite"], ["up", "Haut"], ["down", "Bas"]];
let enrollActive = false;

function enrollStep(step) {
  $$(".enroll-step").forEach(s => { s.hidden = s.dataset.step !== step; });
  syncStreams();
}
function openEnroll(name) {
  $("#enrollModal").hidden = false;
  $("#enrollErr").textContent = "";
  $("#enrollName").value = name || (S.profiles.length ? "" : "");
  enrollStep("name");
  setTimeout(() => $("#enrollName").focus(), 50);
  if (name) startEnroll();
}
async function startEnroll() {
  const name = $("#enrollName").value.trim();
  if (!name) { $("#enrollErr").textContent = "Indiquez un nom."; return; }
  const r = await api("/api/enroll/start", { name });
  if (!r.ok) { $("#enrollErr").textContent = r.error || "Impossible de démarrer."; return; }
  enrollActive = true;
  $("#enrollTitle").textContent = `Enregistrement de ${name}`;
  $("#enrollFinish").disabled = true;
  buildEnrollRing();
  enrollStep("capture");
}
function closeEnroll() {
  if (enrollActive) api("/api/enroll/cancel", {});
  enrollActive = false;
  $("#enrollModal").hidden = true;
  enrollStep("name");
}
$("#enrollGo").addEventListener("click", startEnroll);
$("#enrollName").addEventListener("keydown", e => { if (e.key === "Enter") startEnroll(); });
$("#enrollCancel").addEventListener("click", closeEnroll);
$$("#enrollModal [data-close]").forEach(b => b.addEventListener("click", closeEnroll));
$("#enrollFinish").addEventListener("click", finishEnroll);

async function finishEnroll() {
  if (!enrollActive) return;
  enrollActive = false;
  $("#enrollFinish").disabled = true;
  let r;
  try { r = await api("/api/enroll/finish", {}); }
  catch { r = { ok: false, error: "Moteur injoignable" }; }
  if (r.ok) {
    $("#enrollDoneText").textContent = `${r.samples} échantillons enregistrés. PrankGuard vous reconnaît désormais.`;
    enrollStep("done");
    loadProfiles();
    if (S.settings && !S.settings.onboarding_done) saveSetting({ onboarding_done: true }, true);
  } else {
    toast(r.error || "Échec de l'enregistrement", "err", "i-x");
    closeEnroll();
  }
}

function buildEnrollRing() {
  // 5 arcs (un par pose) autour de la vignette
  const svg = $("#enrollRing"), r = 140, cx = 150, cy = 150, gap = 6, n = POSES.length;
  svg.setAttribute("viewBox", "0 0 300 300");
  let html = "";
  POSES.forEach(([k], i) => {
    const a0 = (i / n) * 360 + gap / 2 - 90, a1 = ((i + 1) / n) * 360 - gap / 2 - 90;
    const p = a => [cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)];
    const [x0, y0] = p(a0), [x1, y1] = p(a1);
    const d = `M${x0} ${y0} A${r} ${r} 0 0 1 ${x1} ${y1}`;
    const len = (a1 - a0) * Math.PI / 180 * r;
    html += `<path class="seg-bg" d="${d}" stroke-width="8" fill="none" stroke-linecap="round"/>
             <path class="seg-fg" data-pose="${k}" d="${d}" stroke-width="8" fill="none" stroke-linecap="round"
               stroke-dasharray="${len}" stroke-dashoffset="${len}" data-len="${len}"/>`;
  });
  svg.innerHTML = html;
  $("#poseRow").innerHTML = POSES.map(([k, l]) => `<span class="pose" data-pose="${k}">${icon("i-check")}${l}</span>`).join("");
}

function zoomOnFace(img, face, size) {
  // l'image 4:3 couvre un carré : on recadre et zoome pour centrer le visage dans le cercle
  if (!face) { img.style.transform = "scaleX(-1)"; return; }
  const w = size * 4 / 3, ox = (w - size) / 2;
  const cx = (1 - (face.x + face.w / 2)) * w - ox - size / 2;
  const cy = (face.y + face.h / 2) * size - size / 2;
  const k = Math.max(1, Math.min(2.6, (size * 0.55) / (face.h * size)));
  img.style.transform = `translate(${-cx * k}px, ${-cy * k}px) scale(${k}) scaleX(-1)`;
}

function renderEnroll(p) {
  if (!enrollActive) return;
  zoomOnFace($("#enrollImg"), (S.state.faces || [])[0], 256);
  $("#enrollHint").textContent = p.hint;
  for (const [k] of POSES) {
    const done = Math.min(p.counts[k], p.targets[k]), frac = done / p.targets[k];
    const arc = $(`.seg-fg[data-pose="${k}"]`);
    if (arc) {
      arc.style.strokeDashoffset = arc.dataset.len * (1 - frac);
      arc.classList.toggle("seg-active", p.next === k && frac < 1);
    }
    const chip = $(`.pose[data-pose="${k}"]`);
    if (chip) {
      chip.classList.toggle("done", frac >= 1);
      chip.classList.toggle("active", p.next === k);
    }
  }
  $("#enrollFinish").disabled = !p.can_finish;
  if (p.complete) finishEnroll();
}

// ── Réglages ────────────────────────────────────────────────────────────────
const deg = v => `±${Math.round(v * 55)}°`;  // yaw normalisé ≈ 0.18 par 10°
const SCHEMA = [
  { title: "Verrouillage", icon: "i-lock", rows: [
    { k: "lock_on_stranger", type: "toggle", label: "Inconnu devant l'écran", desc: "Verrouille quand un visage non autorisé regarde l'écran en votre absence." },
    { k: "stranger_delay", type: "range", min: .5, max: 10, step: .5, unit: " s", label: "Délai avant verrouillage", sub: true, dep: s => s.lock_on_stranger },
    { k: "surfer_action", type: "seg", label: "Regard par-dessus l'épaule", desc: "Quelqu'un lit votre écran alors que vous êtes là.", options: [["off", "Ignorer"], ["warn", "Avertir"], ["lock", "Verrouiller"]] },
    { k: "surfer_delay", type: "range", min: 1, max: 15, step: .5, unit: " s", label: "Délai", sub: true, dep: s => s.surfer_action !== "off" },
    { k: "lock_on_absence", type: "toggle", label: "Verrouiller quand je m'absente", desc: "Si aucun profil autorisé n'est vu pendant un moment." },
    { k: "absence_delay", type: "range", min: 5, max: 300, step: 5, unit: " s", label: "Délai d'absence", sub: true, dep: s => s.lock_on_absence, fmt: v => fmtDur(v) },
    { k: "lock_on_camera_lost", type: "toggle", label: "Caméra masquée ou débranchée", desc: "Verrouille si l'objectif est obstrué ou la caméra indisponible." },
    { k: "camera_lost_delay", type: "range", min: 2, max: 60, step: 1, unit: " s", label: "Délai", sub: true, dep: s => s.lock_on_camera_lost },
    { k: "rearm_delay", type: "range", min: 0, max: 30, step: 1, unit: " s", label: "Réarmement", desc: "Délai de grâce après le déverrouillage de la session." },
  ]},
  { title: "Reconnaissance", icon: "i-scan", rows: [
    { k: "match_threshold", type: "range", min: .3, max: .6, step: .01, label: "Exigence de reconnaissance", desc: "Plus strict = moins de risques de confusion, mais reconnaissance plus exigeante.", ends: ["Tolérant", "Strict"], fmt: v => Math.round(v * 100) + "%" },
    { k: "min_face_size", type: "range", min: .05, max: .3, step: .01, label: "Portée de détection", desc: "Taille minimale d'un visage pour estimer qu'il lit l'écran.", ends: ["Loin", "Proche"], fmt: v => Math.round(v * 100) + "%" },
    { k: "max_yaw", type: "range", min: .2, max: .9, step: .05, label: "Angle de regard toléré", desc: "Au-delà, une personne de profil n'est pas considérée comme lisant l'écran.", ends: ["Strict", "Large"], fmt: deg },
    { k: "anti_spoof", type: "toggle", label: "Ignorer photos et écrans", desc: "Anti-spoofing : un poster ou une photo ne déclenche rien et ne vous imite pas." },
  ]},
  { title: "Périphériques", icon: "i-plug", rows: [
    { k: "device_action", type: "seg", label: "Branchement d'un périphérique", desc: "« Si absent » : verrouille seulement si vous n'êtes pas devant l'écran.", options: [["off", "Ignorer"], ["notify", "Notifier"], ["lock_if_away", "Si absent"], ["lock", "Toujours"]] },
    { type: "trusted" },
  ]},
  { title: "Caméra", icon: "i-video", rows: [
    { k: "camera_index", type: "select", label: "Caméra utilisée", options: [[0, "Caméra 1 (par défaut)"], [1, "Caméra 2"], [2, "Caméra 3"], [3, "Caméra 4"]] },
    { k: "resolution", type: "select", label: "Résolution", desc: "640×480 suffit et consomme le moins.", options: [["640x480", "640 × 480"], ["1280x720", "1280 × 720"]] },
  ]},
  { title: "Alertes", icon: "i-bell", rows: [
    { k: "alarm_enabled", type: "toggle", label: "Sirène au verrouillage", desc: "Joue une sirène quelques secondes quand un intrus déclenche le verrouillage." },
    { k: "alarm_volume", type: "range", min: 10, max: 100, step: 5, unit: " %", label: "Volume", sub: true, dep: s => s.alarm_enabled },
    { k: "save_snapshots", type: "toggle", label: "Photo de l'intrus", desc: "Conserve une photo dans le journal à chaque verrouillage." },
    { k: "notifications", type: "toggle", label: "Notifications Windows", desc: "Regards indiscrets et périphériques branchés en votre présence." },
  ]},
  { title: "Système", icon: "i-power", rows: [
    { k: "start_with_windows", type: "toggle", label: "Lancer au démarrage de Windows", desc: "Démarre discrètement dans la zone de notification." },
    { k: "start_minimized", type: "toggle", label: "Démarrer réduit", desc: "Au démarrage automatique, ne pas ouvrir la fenêtre." },
    { k: "armed_on_start", type: "toggle", label: "Surveillance active au lancement" },
    { type: "actions" },
  ]},
];

async function loadSettings() { S.settings = await api("/api/settings"); renderRules(); return S.settings; }
async function saveSetting(changes, silent = false) {
  try {
    const r = await api("/api/settings", changes);
    S.settings = r.settings;
    renderRules();
    refreshDeps();
    if (!silent) toast("Réglage enregistré", "ok", "i-check", 1400);
  } catch { toast("Échec de l'enregistrement", "err", "i-x"); }
}

function val(k) {
  const s = S.settings;
  return k === "resolution" ? `${s.camera_width}x${s.camera_height}` : s[k];
}

function renderSettings() {
  if (!S.settings) return;
  const root = $("#settings");
  root.innerHTML = "";
  for (const g of SCHEMA) {
    const card = document.createElement("div");
    card.className = "card set-group";
    card.innerHTML = `<h3>${icon(g.icon)}${g.title}</h3>`;
    for (const row of g.rows) card.appendChild(settingRow(row));
    root.appendChild(card);
  }
  refreshDeps();
}

function settingRow(row) {
  const el = document.createElement("div");
  el.className = "set-row" + (row.sub ? " sub" : "");
  if (row.dep) el._dep = row.dep;
  const lbl = `<div class="lbl"><b>${row.label || ""}</b>${row.desc ? `<span>${row.desc}</span>` : ""}</div>`;
  const v = row.k ? val(row.k) : null;
  if (row.type === "toggle") {
    el.innerHTML = `${lbl}<label class="tgl"><input type="checkbox" ${v ? "checked" : ""}><span class="switch"></span></label>`;
    $("input", el).addEventListener("change", e => saveSetting({ [row.k]: e.target.checked }));
  } else if (row.type === "range") {
    const fmt = row.fmt || (x => (Number.isInteger(row.step) ? x : (+x).toFixed(1)) + (row.unit || ""));
    el.innerHTML = `${lbl}<div><div class="range"><input type="range" min="${row.min}" max="${row.max}" step="${row.step}" value="${v}"><output>${fmt(v)}</output></div>
      ${row.ends ? `<div class="range-ends"><span>${row.ends[0]}</span><span>${row.ends[1]}</span></div>` : ""}</div>`;
    const inp = $("input", el), out = $("output", el);
    const paint = () => inp.style.setProperty("--p", `${(inp.value - row.min) / (row.max - row.min) * 100}%`);
    paint();
    inp.addEventListener("input", () => { out.textContent = fmt(+inp.value); paint(); });
    inp.addEventListener("change", () => saveSetting({ [row.k]: +inp.value }));
  } else if (row.type === "seg") {
    el.innerHTML = `${lbl}<div class="seg">${row.options.map(([k, l]) => `<button data-v="${k}" class="${k === v ? "active" : ""}">${l}</button>`).join("")}</div>`;
    $(".seg", el).addEventListener("click", e => {
      const b = e.target.closest("button");
      if (!b) return;
      $$("button", el).forEach(x => x.classList.toggle("active", x === b));
      saveSetting({ [row.k]: b.dataset.v });
    });
  } else if (row.type === "select") {
    el.innerHTML = `${lbl}<select>${row.options.map(([k, l]) => `<option value="${k}" ${String(k) === String(v) ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    $("select", el).addEventListener("change", e => {
      if (row.k === "resolution") {
        const [w, h] = e.target.value.split("x").map(Number);
        saveSetting({ camera_width: w, camera_height: h });
      } else saveSetting({ [row.k]: +e.target.value });
    });
  } else if (row.type === "trusted") {
    const list = S.settings.trusted_devices || [];
    el.className = "trusted";
    el.innerHTML = list.length
      ? `<div class="lbl small muted" style="padding:4px 0 2px">Périphériques approuvés (jamais de verrouillage)</div>` +
        list.map(k => `<div class="trusted-item">${icon("i-plug")}<code>${esc(k)}</code><button class="icon-btn del" data-untrust="${esc(k)}" title="Retirer">${icon("i-x")}</button></div>`).join("")
      : `<div class="small muted">Astuce : approuvez vos propres périphériques depuis le Journal pour ne plus être dérangé.</div>`;
    el.addEventListener("click", async e => {
      const b = e.target.closest("[data-untrust]");
      if (!b) return;
      await api("/api/devices/untrust", { key: b.dataset.untrust });
      await loadSettings();
      renderSettings();
    });
  } else if (row.type === "actions") {
    el.innerHTML = `<div class="lbl"><b>Données</b><span id="dataDir">${esc(S.info.data_dir || "")}</span></div>
      <button class="btn sm ghost" id="openDir">${icon("i-folder")}Ouvrir</button>
      <button class="btn sm danger" id="clearJournal">${icon("i-trash")}Vider le journal</button>`;
    $("#openDir", el).addEventListener("click", () => api("/api/open-folder", {}));
    $("#clearJournal", el).addEventListener("click", async e => {
      const b = e.currentTarget;
      if (!b.classList.contains("confirm")) { b.classList.add("confirm"); b.lastChild.textContent = "Confirmer ?"; setTimeout(() => { b.classList.remove("confirm"); b.lastChild.textContent = "Vider le journal"; }, 3000); return; }
      await api("/api/events/clear", {});
      S.stats = {}; renderStats(); loadFeed();
      toast("Journal vidé", "ok", "i-check");
    });
  }
  return el;
}
function refreshDeps() {
  $$(".set-row").forEach(r => { if (r._dep) r.classList.toggle("disabled", !r._dep(S.settings)); });
}

function renderRules() {
  const s = S.settings;
  if (!s) return;
  const st = (on, label = on ? "Actif" : "Off", cls = on ? "on" : "") => `<span class="state ${cls}">${label}</span>`;
  const surfer = { off: st(false), warn: st(true, "Avertir", "warn"), lock: st(true, "Verrouiller") }[s.surfer_action];
  const dev = { off: st(false), notify: st(true, "Notifier", "warn"), lock_if_away: st(true, "Si absent"), lock: st(true, "Toujours") }[s.device_action];
  $("#rules").innerHTML = `
    <div class="rule">${icon("i-scan")}Inconnu devant l'écran${st(s.lock_on_stranger, s.lock_on_stranger ? s.stranger_delay + " s" : "Off")}</div>
    <div class="rule">${icon("i-eye")}Regard indiscret${surfer}</div>
    <div class="rule">${icon("i-plug")}Périphériques${dev}</div>
    <div class="rule">${icon("i-clock")}Absence${st(s.lock_on_absence, s.lock_on_absence ? fmtDur(s.absence_delay) : "Off")}</div>
    <div class="rule">${icon("i-video")}Caméra masquée${st(s.lock_on_camera_lost)}</div>`;
}

// ── Utilitaires UI ──────────────────────────────────────────────────────────
function toast(msg, kind = "ok", ic = "i-info", ms = 3200) {
  const box = $("#toasts");
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.innerHTML = `${icon(ic)}<span>${esc(msg)}</span>`;
  box.appendChild(t);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, ms);
}
function fmtTime(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const hm = d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return d.toDateString() === now.toDateString() ? hm : `${d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" })} ${hm}`;
}
function fmtDur(s) {
  s = Math.round(s);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${m % 60 ? (m % 60) + " min" : ""}`.trim();
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    if (!$("#lightbox").hidden) $("#lightbox").hidden = true;
    else if (!$("#enrollModal").hidden) closeEnroll();
  }
});
document.addEventListener("contextmenu", e => { if (!S.info.debug) e.preventDefault(); });

// ── Bienvenue ───────────────────────────────────────────────────────────────
$("#welcomeGo").addEventListener("click", () => { $("#welcomeModal").hidden = true; saveSetting({ onboarding_done: true }, true); openEnroll(); });
$("#welcomeSkip").addEventListener("click", () => { $("#welcomeModal").hidden = true; saveSetting({ onboarding_done: true }, true); });

// ── Démarrage ───────────────────────────────────────────────────────────────
(async function init() {
  try {
    const [info] = await Promise.all([api("/api/info"), loadSettings()]);
    S.info = info;
    $("#version").textContent = `PrankGuard v${info.version}`;
    await Promise.all([loadFeed(), loadProfiles()]);
    if (!S.settings.onboarding_done && !S.profiles.length) $("#welcomeModal").hidden = false;
  } catch (e) {
    toast("Connexion au moteur impossible", "err", "i-x");
  }
  connect();
  syncStreams();
})();
