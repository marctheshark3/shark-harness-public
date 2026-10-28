"use strict";

const $ = (selector) => document.querySelector(selector);
const canvas = $("#screen");
const ctx = canvas.getContext("2d");
const TAU = Math.PI * 2;
const palette = {
  working: "#ff5c35",
  waiting: "#edca78",
  done: "#b6c99c",
  idle: "#c4c6b5",
  error: "#ff8a76",
  offline: "#a0a391",
};
const labels = {
  working: "In progress",
  waiting: "Input required",
  done: "Task complete",
  idle: "Standing by",
  error: "Check your session",
  offline: "T3 offline",
};
let scene = "aurora",
  moment = "live";
let live = { connected: false, state: "offline", threads: [], counts: {} };
let overlay = null,
  artwork = null,
  artworkKey = "",
  artworkFailed = false;
let lastFrame = 0,
  reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let motionPaused = reducedMotion.matches;
try {
  motionPaused ||= localStorage.getItem("shark-motion") === "paused";
} catch { /* Preferences are optional in private/locked-down browsers. */ }
let activityKey = "";
let sceneBusy = false;
let renderedFrame = null;
// Used by the optional USB sender. Metadata always describes the same completed
// canvas frame as its pixels; browser-only operation needs no device packages.
window.captureT3Display = (includePixels = false) =>
  renderedFrame && {
    ...renderedFrame,
    ...(includePixels ? { png: canvas.toDataURL("image/png") } : {}),
  };

function colorAlpha(hex, alpha) {
  return (
    hex +
    Math.round(Math.max(0, Math.min(1, alpha)) * 255)
      .toString(16)
      .padStart(2, "0")
  );
}
function line(x1, y1, x2, y2, color, width = 1) {
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}
function circle(x, y, r, color) {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.fill();
}
function text(value, y, size, color = "#f1f0eb", weight = 400, x = 233) {
  ctx.font = `${weight} ${size}px Arial, sans-serif`;
  ctx.textAlign = "center";
  ctx.fillStyle = color;
  ctx.fillText(value, x, y);
}
function mono(value, y, size = 10, color = "#a8aba8") {
  ctx.font = `${size}px ui-monospace, monospace`;
  ctx.textAlign = "center";
  ctx.fillStyle = color;
  ctx.fillText(value, 233, y);
}
function wrap(value, y, size, maxWidth, color, maxLines = 2) {
  ctx.font = `500 ${size}px Arial, sans-serif`;
  const words = String(value).split(/\s+/),
    lines = [];
  let current = "";
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (ctx.measureText(next).width > maxWidth && current) {
      lines.push(current);
      current = word;
    } else current = next;
  }
  if (current) lines.push(current);
  lines.slice(0, maxLines).forEach((value, i) => {
    let line = value;
    const more = i === maxLines - 1 && lines.length > maxLines;
    while (
      line.length &&
      ctx.measureText(line + (more ? "…" : "")).width > maxWidth
    )
      line = line.slice(0, -1);
    text(
      line + (more || line !== value ? "…" : ""),
      y + i * (size + 6),
      size,
      color,
      500,
    );
  });
}

function dial() {
  for (let i = 0; i < 60; i++) {
    const angle = (i / 60) * TAU;
    const inner = i % 5 === 0 ? 208 : 213;
    line(
      233 + Math.cos(angle) * inner,
      233 + Math.sin(angle) * inner,
      233 + Math.cos(angle) * 218,
      233 + Math.sin(angle) * 218,
      i % 5 === 0 ? "#747968" : "#383c32",
    );
  }
}

function aurora(t, compact) {
  ctx.save();
  for (let layer = 0; layer < 3; layer++) {
    const gradient = ctx.createLinearGradient(35, 0, 431, 0);
    gradient.addColorStop(0, "#ff704400");
    gradient.addColorStop(0.2, "#ff704480");
    gradient.addColorStop(0.5, layer === 1 ? "#ead8afb0" : "#ff7044d0");
    gradient.addColorStop(0.8, "#ff704480");
    gradient.addColorStop(1, "#ff704400");
    ctx.strokeStyle = gradient;
    ctx.lineWidth = 1;
    for (let ribbon = 0; ribbon < 12; ribbon++) {
      ctx.beginPath();
      for (let x = 35; x <= 431; x += 4) {
        const k = x / 466;
        const y =
          174 +
          Math.sin(k * 6.3 + t * 0.16 + layer * 0.2) * 40 +
          Math.sin(k * 10.8 - t * 0.21 + ribbon * 0.055) * 22 +
          ribbon * 4 +
          layer * 13;
        const py = compact ? 160 + (y - 180) * 0.65 : y;
        if (x === 35) ctx.moveTo(x, py);
        else ctx.lineTo(x, py);
      }
      ctx.stroke();
    }
  }
  ctx.restore();
}

function orbit(t, accent) {
  ctx.save();
  ctx.translate(233, 205);
  for (let ring = 0; ring < 4; ring++) {
    ctx.save();
    ctx.rotate(-0.5 + ring * 0.5 + t * 0.06);
    ctx.strokeStyle = colorAlpha(accent, 0.12 + ring * 0.07);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.ellipse(0, 0, 58 + ring * 17, 23 + ring * 10, 0, 0, TAU);
    ctx.stroke();
    const angle = t * (0.28 + ring * 0.04) + ring * 1.8;
    circle(
      Math.cos(angle) * (58 + ring * 17),
      Math.sin(angle) * (23 + ring * 10),
      2.5,
      accent,
    );
    ctx.restore();
  }
  circle(0, 0, 18, "#f77448");
  circle(0, 0, 7, "#151617");
  ctx.restore();
}

function shark(t, accent) {
  ctx.save();
  ctx.translate(238 + Math.sin(t * 0.45) * 9, 204 + Math.sin(t * 0.8) * 7);
  ctx.rotate(Math.sin(t * 0.4) * 0.025);
  ctx.shadowColor = colorAlpha(accent, 0.2);
  ctx.shadowBlur = 0;
  const body = ctx.createLinearGradient(-60, -50, 45, 40);
  body.addColorStop(0, "#efe9d6");
  body.addColorStop(0.45, "#c9c9b4");
  body.addColorStop(1, "#858f73");
  ctx.fillStyle = body;
  ctx.beginPath();
  ctx.moveTo(98, 6);
  ctx.bezierCurveTo(64, -27, 23, -40, -25, -27);
  ctx.lineTo(-39, -72);
  ctx.quadraticCurveTo(-61, -53, -61, -13);
  ctx.quadraticCurveTo(-89, -2, -109, 6);
  ctx.lineTo(-145, -37);
  ctx.quadraticCurveTo(-139, -3, -128, 14);
  ctx.lineTo(-143, 50);
  ctx.lineTo(-104, 23);
  ctx.quadraticCurveTo(-69, 43, -18, 31);
  ctx.lineTo(-31, 66);
  ctx.quadraticCurveTo(2, 54, 19, 25);
  ctx.quadraticCurveTo(75, 27, 98, 6);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.fillStyle = "#f1e8d2";
  ctx.beginPath();
  ctx.moveTo(-101, 22);
  ctx.quadraticCurveTo(11, 35, 98, 6);
  ctx.quadraticCurveTo(74, 31, 17, 27);
  ctx.quadraticCurveTo(-50, 44, -101, 22);
  ctx.fill();
  circle(64, 2, 3.2, "#151617");
  circle(65, 1, 0.9, "#f4efe2");
  for (let i = 0; i < 3; i++)
    line(27 - i * 7, -6, 25 - i * 7, 7, "#555f47", 1.6);
  ctx.restore();
}

function chart(values, accent) {
  const min = Math.min(...values),
    max = Math.max(...values),
    span = max - min || 1;
  const points = values.map((v, i) => [
    90 + (i / (values.length - 1)) * 286,
    268 - ((v - min) / span) * 112,
  ]);
  for (let i = 0; i < 4; i++)
    line(90, 156 + i * 38, 376, 156 + i * 38, "#727d5266");
  const fill = ctx.createLinearGradient(0, 150, 0, 280);
  fill.addColorStop(0, colorAlpha(accent, 0.3));
  fill.addColorStop(1, colorAlpha(accent, 0));
  ctx.fillStyle = fill;
  ctx.beginPath();
  ctx.moveTo(90, 280);
  points.forEach(([x, y]) => ctx.lineTo(x, y));
  ctx.lineTo(376, 280);
  ctx.closePath();
  ctx.fill();
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2.5;
  ctx.lineJoin = "round";
  ctx.beginPath();
  points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  const last = points.at(-1);
  circle(last[0], last[1], 4, "#efe9d6");
  text(String(Number(max.toPrecision(5))), 139, 10, "#a8aba8", 400, 105);
  text(String(Number(min.toPrecision(5))), 293, 10, "#a8aba8", 400, 105);
  text(String(Number(values.at(-1).toPrecision(5))), 115, 36, "#efe9d6", 500);
}

function prepareImage(content) {
  const key = content?.kind === "image" ? content.image : "";
  if (key === artworkKey) return;
  artworkKey = key;
  artwork = null;
  artworkFailed = false;
  if (!key) return;
  const image = new Image();
  image.onload = () => {
    if (artworkKey === key) artwork = image;
  };
  image.onerror = () => {
    if (artworkKey === key) {
      artworkFailed = true;
      $("#message-feedback").textContent =
        "That image could not be decoded. Try another file.";
    }
  };
  image.src = key;
}

let listenState = null;
let voiceOverlay = null;
function render(ms) {
  requestAnimationFrame(render);
  if (ms - lastFrame < (motionPaused ? 250 : 33)) return;
  lastFrame = ms;
  const t = motionPaused ? 0 : ms / 1000;
  const state = moment === "live" ? live.state : moment;
  const accent = palette[state] || palette.idle;
  const activeOverlay = voiceOverlay || overlay;
  const content =
    activeOverlay && (activeOverlay.expiresAt === null || activeOverlay.expiresAt > Date.now())
      ? activeOverlay
      : null;
  const style = content?.scene || scene;
  prepareImage(content);
  ctx.clearRect(0, 0, 466, 466);
  ctx.fillStyle = "#151617";
  ctx.fillRect(0, 0, 466, 466);
  if (content?.kind === "image" && artwork) {
    const scale = Math.max(466 / artwork.width, 466 / artwork.height);
    ctx.drawImage(
      artwork,
      (466 - artwork.width * scale) / 2,
      (466 - artwork.height * scale) / 2,
      artwork.width * scale,
      artwork.height * scale,
    );
    const shade = ctx.createLinearGradient(0, 0, 0, 466);
    shade.addColorStop(0, "#15161788");
    shade.addColorStop(0.4, "#15161700");
    shade.addColorStop(0.65, "#15161733");
    shade.addColorStop(1, "#151617f5");
    ctx.fillStyle = shade;
    ctx.fillRect(0, 0, 466, 466);
  } else if (content?.kind === "chart") chart(content.values, accent);
  else if (style === "shark") shark(t, accent);
  else if (style === "orbit") orbit(t, accent);
  else aurora(t, Boolean(content));

  const legibility = ctx.createLinearGradient(0, 280, 0, 410);
  legibility.addColorStop(0, "#15161700");
  legibility.addColorStop(1, "#151617e6");
  ctx.fillStyle = legibility;
  ctx.fillRect(0, 280, 466, 186);
  dial();

  // A thin awareness ring leaves the main canvas to the visual. It is activity,
  // not a made-up percentage complete.
  ctx.strokeStyle = colorAlpha(accent, 0.13);
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(233, 233, 225, 0, TAU);
  ctx.stroke();
  const rotate = state === "working" ? t * 0.22 : -0.6;
  ctx.strokeStyle = colorAlpha(accent, 0.7);
  ctx.lineWidth = 2;
  ctx.lineCap = "butt";
  ctx.beginPath();
  ctx.arc(233, 233, 225, rotate, rotate + (state === "done" ? TAU : 0.65));
  ctx.stroke();
  mono(
    "SH—01   /   " + (content ? "VISUAL OUTPUT" : "AMBIENT PROGRAM"),
    72,
    9,
    "#bdbeb9",
  );
  if (content) {
    wrap(
      artworkFailed ? "Image unavailable" : content.title,
      337,
      23,
      302,
      "#f1f0eb",
      2,
    );
    if (content.detail) wrap(content.detail, 384, 11, 270, "#a8aba8", 1);
    else mono("TRANSMITTED FROM T3", 384, 8, "#a8aba8");
  } else {
    text(style.toUpperCase(), 337, 32, "#f1f0eb", 700);
    mono("SHARK HARNESS / " + labels[state].toUpperCase(), 365, 8, "#a8aba8");
  }
  const status =
    state === "waiting"
      ? "T3 needs your input"
      : state === "working"
        ? "T3 is working"
        : labels[state];
  circle(167, 418, 2, accent);
  text(status, 421, 9, accent, 450, 239);
  $("#screen").setAttribute(
    "aria-label",
    content
      ? `${content.kind}: ${content.title}. ${status}`
      : `${style} animation. ${status}`,
  );
  renderedFrame = {
    state,
    overlay: content,
    imageReady: content?.kind !== "image" || Boolean(artwork),
  };
  $("#scene-lifetime").textContent = content
    ? content.expiresAt === null ? "RECORDING"
      : `${Math.max(1, Math.ceil((content.expiresAt - Date.now()) / 1000))}s LEFT`
    : "AMBIENT";
}

function renderActivity() {
  const list = $("#thread-list");
  const query = $("#session-search").value.trim().toLocaleLowerCase();
  const threads = live.threads.filter((t) => `${t.title} ${t.project}`.toLocaleLowerCase().includes(query));
  $("#thread-count").textContent = live.connected || live.demo
    ? `${threads.length}${query ? ` / ${live.threads.length}` : ""} SESSION${threads.length === 1 ? "" : "S"} · ${live.demo ? "SAMPLE DATA" : "LIVE T3"}`
    : "T3 UNAVAILABLE";
  for (const state of ["working", "waiting", "done"])
    $(`#count-${state}`).textContent = String(live.counts[state] || 0).padStart(2, "0");
  const active = listenState?.job && !["sent", "discarded", "empty", "error", "uncertain", "review"].includes(listenState.job.state);
  const key = JSON.stringify([threads.map((t) => [t.id, t.title, t.project, t.state]), live.connected, live.demo,
    query, listenState?.target?.id, Boolean(active), listenBusy]);
  if (key === activityKey) return;
  activityKey = key;
  // Do not rebuild unchanged controls on every poll: keyboard focus belongs to the user.
  const focusedId = document.activeElement?.dataset?.threadId;
  list.replaceChildren();
  if (!threads.length) {
    const p = document.createElement("p");
    p.className = "activity-note";
    p.textContent = query ? "No matching sessions. Try a different title or project."
      : live.connected
        ? "A clean slate. Start a session in T3 and it will appear here."
        : live.message || "Start T3 on this computer to bring your sessions into view. The display canvas is ready to explore.";
    list.append(p);
  }
  for (const thread of threads) {
    const card = document.createElement("article");
    card.className = "thread-card";
    const top = document.createElement("div");
    top.className = "thread-top";
    const project = document.createElement("span");
    project.className = "thread-project";
    project.textContent = thread.project;
    const badge = document.createElement("span");
    badge.className = `state-badge ${thread.state}`;
    badge.textContent = {
      working: "WORKING",
      waiting: "NEEDS YOU",
      done: "DONE",
      idle: "IDLE",
      error: "ERROR",
    }[thread.state];
    const title = document.createElement("h3");
    title.className = "thread-title";
    title.textContent = thread.title;
    const action = document.createElement("button");
    action.type = "button";
    action.className = "thread-select";
    action.dataset.threadId = thread.id;
    const selected = listenState?.target?.id === thread.id;
    action.textContent = live.demo ? "Sample session" : selected ? "Selected for voice ✓" : "Select for voice ↗";
    action.setAttribute("aria-label", `${selected ? "Selected" : "Select"} ${thread.title} in ${thread.project} for voice`);
    action.setAttribute("aria-pressed", String(selected));
    action.disabled = Boolean(live.demo || !live.connected || active || listenBusy);
    action.addEventListener("click", async () => {
      await listenAction("/configure", "POST", { threadId: thread.id, enabled: Boolean(listenState?.enabled) });
      if (listenState?.target?.id === thread.id) {
        $("#voice").scrollIntoView({ behavior: motionPaused ? "instant" : "smooth" });
        $("#listen-enable").focus({ preventScroll: true });
      }
    });
    top.append(project, badge);
    card.append(top, title, action);
    list.append(card);
    if (focusedId === thread.id) action.focus({ preventScroll: true });
  }
  $("#accessible-status").textContent =
    `${live.demo ? "Demo with sample sessions" : live.connected ? "Connected to T3" : "T3 unavailable"}. ${live.threads.length} sessions. ${labels[live.state]}.`;
  $("#session-feedback").textContent = `${threads.length} matching sessions.`;
}

function showConnection() {
  $("#connection-label").textContent = live.demo ? "Demo mode" : live.connected ? "T3 connected" : "T3 unavailable";
  $("#connection-dot").classList.toggle("online", live.connected);
  $("#connection-dot").classList.toggle("demo", Boolean(live.demo));
  $("#link-state").textContent = live.demo ? "DEMO WORKBENCH" : live.connected ? "LIVE WORKSPACE" : "AWAITING T3";
  $("#link-detail").textContent = live.demo
    ? "Sample sessions. Explore the display; voice is disabled."
    : live.connected ? "Your sessions are in view. Select one below to set your voice destination."
      : "Your canvas is ready. Open Setup to connect your sessions and device.";
  $("#live-state-label").textContent = live.demo ? "Sample" : "Live T3";
  $("#mode-label").textContent = moment !== "live" ? "DEMO STATE · SCREEN SIMULATOR"
    : live.demo ? "SAMPLE DATA · SCREEN SIMULATOR" : "LIVE DATA · SCREEN SIMULATOR";
}

async function poll() {
  try {
    const response = await fetch("/api/status", {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) throw new Error("Status unavailable");
    live = await response.json();
    overlay = live.overlay;
  } catch {
    live = {
      connected: false,
      state: "offline",
      threads: [],
      counts: {},
      message:
        "The companion connection was lost. Reconnect to resume live status.",
    };
    overlay = null;
  }
  showConnection();
  renderActivity();
  setTimeout(poll, 1500);
}

async function sendScene(value) {
  const response = await fetch("/api/scene", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
    signal: AbortSignal.timeout(5000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Could not send visual");
  overlay = result.overlay;
  $("#message-feedback").textContent =
    "On your canvas. It will return to ambient automatically.";
}
function feedback(error) {
  $("#message-feedback").textContent =
    error.message || "The preview is unavailable.";
}

async function sceneAction(button, action) {
  if (sceneBusy) return;
  sceneBusy = true;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { await action(); }
  catch (error) { feedback(error); }
  finally {
    sceneBusy = false;
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
}

document.querySelectorAll("[data-scene]").forEach((button) =>
  button.addEventListener("click", () => {
    scene = button.dataset.scene;
    document.querySelectorAll("[data-scene]").forEach((b) => {
      const selected = b === button;
      b.classList.toggle("active", selected);
      b.setAttribute("aria-pressed", String(selected));
    });
  }),
);
document.querySelectorAll("[data-state]").forEach((button) =>
  button.addEventListener("click", () => {
    moment = button.dataset.state;
    document.querySelectorAll("[data-state]").forEach((b) => {
      const selected = b === button;
      b.classList.toggle("active", selected);
      b.setAttribute("aria-pressed", String(selected));
    });
    showConnection();
  }),
);
$("#message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("#message").value.trim();
  if (!title) return feedback(new Error("Write a message first."));
  await sceneAction($("#message-submit"), async () => {
    await sendScene({ title, scene, seconds: 20 });
    $("#message").value = "";
    $("#message-count").textContent = "0 / 60";
  });
});
$("#chart-button").addEventListener("click", async () => {
  try {
    await sendScene({
      title: "A lighter build.",
      detail: "DEMO · seconds across 8 runs",
      values: [92, 88, 91, 74, 63, 59, 45, 38],
      seconds: 30,
      scene,
    });
  } catch (error) {
    feedback(error);
  }
});
$("#clear-button").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/scene", {
      method: "DELETE",
      signal: AbortSignal.timeout(4000),
    });
    if (!response.ok) throw new Error("Could not clear canvas");
    overlay = null;
    $("#message-feedback").textContent =
      "Back to ambient. Ready for the next idea.";
  } catch (error) {
    feedback(error);
  }
});
$("#image-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 262144)
      throw new Error(
        "Choose an image under 256 KiB. A 466 × 466 image is ideal.",
      );
    const image = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("Could not read image"));
      reader.readAsDataURL(file);
    });
    await sendScene({
      title: "A new perspective.",
      detail: "YOUR IMAGE · ROUND CANVAS",
      image,
      seconds: 60,
    });
  } catch (error) {
    feedback(error);
  }
});
requestAnimationFrame(render);
poll();

function showSpeech(result) {
  const job = result.job;
  $("#speech-text").disabled = Boolean(result.demo);
  $("#speech-form button").disabled = Boolean(result.demo);
  $("#speech-volume").disabled = Boolean(result.demo);
  $("#speech-stop").disabled = Boolean(result.demo);
  $("#speech-feedback").textContent = result.demo ? "Demo mode · connect a device to hear local speech." : job
    ? job.error ||
      {
        armed: "Tap the display when you’re ready to listen.",
        queued: "Queued for your device…",
        synthesizing: "Preparing local voice…",
        uploading: "Sending to the speaker…",
        playing: "Speaking · tap the display to stop.",
        done: "Finished. Ready for the next idea.",
        stopped: "Speech stopped.",
        error: "Could not play this message.",
      }[job.state]
    : "Local voice · plays on your device.";
  if (document.activeElement !== $("#speech-volume")) {
    $("#speech-volume").value = result.volume;
    $("#volume-value").textContent =
      result.volume === 0 ? "Muted" : result.volume;
  }
}
async function speechRequest(path = "/api/speech", method = "GET", value) {
  const response = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(value ? { body: JSON.stringify(value) } : {}),
    signal: AbortSignal.timeout(4000),
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Speech unavailable");
  showSpeech(result);
}
async function speechAction(path, method, value) {
  try {
    await speechRequest(path, method, value);
  } catch (error) {
    $("#speech-feedback").textContent = error.message || "Speech unavailable";
  }
}
$("#speech-form").addEventListener("submit", (event) => {
  event.preventDefault();
  speechAction("/api/speech", "POST", { text: $("#speech-text").value });
});
$("#speech-stop").addEventListener("click", () =>
  speechAction("/api/speech", "DELETE"),
);
$("#speech-volume").addEventListener("input", () => {
  const volume = Number($("#speech-volume").value);
  $("#volume-value").textContent = volume === 0 ? "Muted" : volume;
});
$("#speech-volume").addEventListener("change", () =>
  speechAction("/api/speech/volume", "POST", {
    volume: Number($("#speech-volume").value),
  }),
);
async function pollSpeech() {
  await speechAction();
  setTimeout(pollSpeech, 1000);
}
pollSpeech();

let displayedTranscript = null;
let listenOptionsKey = "";
let listenBusy = false;
let listenActionVersion = 0;
let listenError = "";
function showListen(result) {
  // A failed action response may arrive after T3 accepted the prompt. Fresh job
  // progress supersedes that transport error without replaying the action.
  if (listenState && (result.job?.id !== listenState.job?.id || result.job?.state !== listenState.job?.state))
    listenError = "";
  listenState = result;
  const job = result.job;
  const active =
    job &&
    !["sent", "discarded", "empty", "error", "uncertain"].includes(job.state);
  const autoSend = active ? (job.autoSend ?? result.autoSend) : result.autoSend;
  const select = $("#listen-session");
  const threads = [...(live.threads || [])];
  if (result.target && !threads.some((t) => t.id === result.target.id))
    threads.unshift(result.target);
  const optionsKey = JSON.stringify(threads.map((t) => [t.id, t.title, t.project]));
  if (optionsKey !== listenOptionsKey && document.activeElement !== select) {
    const chosen = result.target?.id || "";
    select.replaceChildren(
      new Option("Choose a T3 session…", ""),
      ...threads.map((t) => new Option(`${t.project ? `${t.project} / ` : ""}${t.title}`, t.id)),
    );
    select.value = chosen;
    listenOptionsKey = optionsKey;
  }
  if (!listenBusy && document.activeElement !== select) select.value = result.target?.id || "";
  const connected = result.device.connected && result.device.ready;
  $("#listen-mode").textContent = autoSend
    ? "Hold to talk, release to send directly to the selected session. No confirmation needed."
    : "Hold to talk, release to transcribe, then review before sending.";
  $("#listen-enable").textContent = result.enabled
    ? "Disable voice input"
    : "Enable voice input";
  $("#listen-enable").setAttribute("aria-pressed", String(result.enabled));
  $("#voice-target").textContent = active && job?.threadTitle ? job.threadTitle
    : result.target ? `${result.target.project ? `${result.target.project} / ` : ""}${result.target.title}`
      : "No session selected";
  $("#voice-readiness").textContent = result.demo ? "DEMO · VOICE OFF" : !connected ? "DEVICE OFFLINE"
    : active ? (job.state === "listening" ? "● RECORDING · DESTINATION LOCKED" : job.state.toUpperCase())
      : !result.target ? "CHOOSE A SESSION" : result.destinationReady === false ? "SESSION UNAVAILABLE"
        : result.enabled ? "READY TO LISTEN" : "VOICE PAUSED";
  $("#voice-readiness").classList.toggle("recording", job?.state === "listening");
  $("#listen-record").disabled =
    result.demo || !result.enabled || !connected || result.destinationReady === false || active || listenBusy;
  $("#listen-stop").disabled = job?.state !== "listening" || listenBusy;
  $("#listen-enable").disabled =
    result.demo || !select.value || listenBusy || job?.state === "sending";
  select.disabled = result.demo || listenBusy || (active && job?.state !== "review");
  const labels = {
    listening: "Listening · release the glass or choose Finish.",
    transcribing: "Transcribing on this computer…",
    review: "Review the words and destination before sending.",
    sending: "Sending your prompt to T3…",
    sent: "Prompt accepted by T3.",
    discarded: "Recording discarded. Hold to try again.",
    empty: "No clear speech detected. Hold to try again.",
  };
  $("#listen-feedback").textContent = listenError || (result.demo
    ? "Demo mode · sample sessions cannot receive prompts. Open Setup to connect your own."
    : !connected
    ? "Device microphone offline. Start the companion with voice input; see Setup for the guide."
    : job?.error ||
      labels[job?.state] ||
      (result.destinationReady === false ? result.destinationError : "") ||
      (result.enabled
        ? "Ready · hold the glass to talk."
        : "Choose your destination, then enable voice input."));
  const review = job?.state === "review";
  $("#listen-review").hidden = !review;
  if (review && job.id !== displayedTranscript) {
    $("#listen-text").value = job.text;
    displayedTranscript = job.id;
  }
  if (review)
    $("#listen-destination").textContent = `Send to: ${job.threadTitle}`;
  $("#listen-send").disabled = listenBusy;
  const titles = {
    listening: "Listening…",
    transcribing: "Finding your words…",
    review: "Your prompt is ready",
  };
  // Keep metadata stable between polls so the legacy USB sender uploads only
  // state changes. Null means the recording has no absolute expiry; its sender
  // still applies the firmware's bounded frame duration.
  voiceOverlay = connected && titles[job?.state]
    ? {
        kind: "message",
        scene: "orbit",
        title: titles[job.state],
        detail: `${job.state === "listening" ? (autoSend ? "Release to send" : "Release to review")
          : job.state === "review" ? "Review & send" : "Transcribing"} · ${job.threadTitle}`,
        expiresAt: job.expiresAt === null ? null : job.expiresAt * 1000,
      }
    : null;
  renderActivity();
}
async function listenRequest(path = "", method = "GET", value) {
  const version = listenActionVersion;
  const response = await fetch(`/api/listen${path}`, {
    method,
    headers: value ? { "Content-Type": "application/json" } : {},
    body: value ? JSON.stringify(value) : undefined,
    signal: AbortSignal.timeout(5000),
    cache: "no-store",
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Voice input unavailable");
  if (method !== "GET" || (version === listenActionVersion && !listenBusy)) showListen(result);
  return result;
}
async function listenAction(path, method, value) {
  if (listenBusy) return;
  listenActionVersion++;
  listenError = "";
  listenBusy = true;
  if (listenState) showListen(listenState);
  try {
    await listenRequest(path, method, value);
  } catch (error) {
    listenError = error.message;
  } finally {
    listenBusy = false;
    if (listenState) showListen(listenState);
  }
}
$("#listen-session").addEventListener("change", () => {
  const threadId = $("#listen-session").value;
  listenAction("/configure", "POST", {
    threadId,
    enabled: Boolean(threadId && listenState?.enabled),
  });
});
$("#listen-enable").addEventListener("click", () =>
  listenAction("/configure", "POST", {
    threadId: $("#listen-session").value,
    enabled: !listenState?.enabled,
  }),
);
$("#listen-record").addEventListener("click", () =>
  listenAction("/control", "POST", { action: "record" }),
);
$("#listen-stop").addEventListener("click", () =>
  listenAction("/control", "POST", { action: "stop" }),
);
$("#listen-discard").addEventListener("click", () =>
  listenAction("", "DELETE"),
);
$("#listen-review").addEventListener("submit", (event) => {
  event.preventDefault();
  const job = listenState?.job;
  if (job?.state === "review")
    listenAction("/confirm", "POST", {
      id: job.id,
      threadId: job.threadId,
      text: $("#listen-text").value,
      confirm: true,
    });
});
async function pollListen() {
  try {
    if (!listenBusy) await listenRequest();
  } catch {
    if (!listenBusy) {
      $("#listen-feedback").textContent = "Voice connection lost. Reconnecting…";
      for (const id of ["listen-record", "listen-stop", "listen-enable", "listen-send", "listen-session"]) $(`#${id}`).disabled = true;
      $("#voice-readiness").textContent = "VOICE CONNECTION LOST";
    }
    voiceOverlay = null;
  }
  setTimeout(pollListen, 1000);
}
pollListen();

$("#session-search").addEventListener("input", renderActivity);
$("#message").addEventListener("input", () => {
  $("#message-count").textContent = `${$("#message").value.length} / 60`;
});
function showMotion() {
  const button = $("#motion-toggle");
  button.textContent = motionPaused ? "Motion off ▷" : "Motion on Ⅱ";
  button.setAttribute("aria-pressed", String(motionPaused));
  button.setAttribute("aria-label", motionPaused ? "Resume animation" : "Pause animation");
}
$("#motion-toggle").addEventListener("click", () => {
  motionPaused = !motionPaused;
  try { localStorage.setItem("shark-motion", motionPaused ? "paused" : "playing"); } catch { /* Optional preference. */ }
  showMotion();
});
reducedMotion.addEventListener("change", () => { motionPaused = reducedMotion.matches; showMotion(); });
showMotion();
$("#setup-open").addEventListener("click", () => $("#setup-dialog").showModal());
$("#setup-close").addEventListener("click", () => $("#setup-dialog").close());
