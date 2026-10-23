"use strict";
/* Fenêtre de décision « nouveau périphérique » — suit l'état du moteur par SSE. */

const TOKEN = new URLSearchParams(location.hash.slice(1)).get("t") || "";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CATEGORY = {
  keyboard: "Clavier", mouse: "Souris", storage: "Stockage", phone: "Téléphone", hid: "Périphérique HID",
  usb: "USB", network: "Réseau / Wi-Fi", bluetooth: "Bluetooth", printer: "Imprimante", display: "Écran",
  audio: "Audio", camera: "Caméra", serial: "Port série", other: "Autre",
};
const ARC = 106.8;
let prompt = null, deadline = 0, sent = false;

function render(p) {
  if (!p) return;
  if (!prompt || prompt.id !== p.id) sent = false;
  prompt = p;
  deadline = performance.now() + p.left * 1000;
  $("#kb").hidden = !p.keyboard_blocked;
  $("#devices").innerHTML = p.devices.map(d =>
    `<li><b>${esc(d.name)}</b><span>${esc(CATEGORY[d.category] || d.category)} · ${esc(d.key)}</span></li>`).join("");
  document.querySelectorAll("button").forEach(b => { b.disabled = sent; });
}

function tick() {
  if (prompt) {
    const left = Math.max(0, (deadline - performance.now()) / 1000);
    $("#left").textContent = Math.ceil(left);
    $("#arc").style.strokeDashoffset = ARC * (1 - left / prompt.timeout);
    $(".timer").classList.toggle("urgent", left <= 2);
  }
  requestAnimationFrame(tick);
}

async function decide(decision) {
  if (!prompt || sent) return;
  sent = true;
  document.querySelectorAll("button").forEach(b => { b.disabled = true; });
  try {
    const r = await fetch("/api/devices/decide", {
      method: "POST", headers: { "X-PG-Token": TOKEN, "Content-Type": "application/json" },
      body: JSON.stringify({ id: prompt.id, decision }),
    });
    if (!r.ok) throw new Error(String(r.status));
  } catch (e) {
    sent = false;
    document.querySelectorAll("button").forEach(b => { b.disabled = false; });
  }
}

document.querySelector(".actions").addEventListener("click", e => {
  const b = e.target.closest("button[data-d]");
  if (b) decide(b.dataset.d);
});

const es = new EventSource(`/sse?t=${encodeURIComponent(TOKEN)}`);
es.addEventListener("state", e => render(JSON.parse(e.data).device_prompt));
requestAnimationFrame(tick);
