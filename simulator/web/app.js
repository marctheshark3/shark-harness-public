"use strict";

const $ = (id) => document.getElementById(id);
const display = $("display");
const frame = $("frame");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let state = null;
let actionQueue = Promise.resolve();
let pendingActions = 0;
let requestSequence = 0;
let appliedSequence = 0;
let pointer = null;
let keyboardHold = false;
let bootHeld = false;
let activeControlHold = null;
let lastMoveAt = 0;
let controlsKey = "";
let scenariosKey = "";
let eventsKey = "";
let frameKey = "";
let paintedImageVersion = null;
let frameBusy = false;
let frameLoad = null;
let displayedVersion = null;
let feedbackUntil = 0;
let reconnectCancel = false;

const screenNames = {
  projects: "Project library", home: "Session", listening: "Listening",
  review: "Review prompt", confirm: "Confirm destination", transcribing: "Transcribing",
  sending: "Sending", sent: "Sent", offline: "Disconnected", "empty-home": "No sessions",
  "empty-project": "Empty project", empty: "No speech detected", uncertain: "Send unconfirmed",
  error: "Voice interrupted", "control-error": "Action unavailable", scene: "Visual output",
  "voice-offline": "Voice disconnected", "current-error": "Choose a destination", "t3-choices": "Open T3 windows"
};
const scenarioNotes = {
  projects: "Browse the project library. Tap Open sessions to choose where your voice goes.",
  sessions: "Tap PREV or NEXT to move through sessions. Swipe the card to reveal its neighbor.",
  paused: "Voice is paused. Resume enables the next hold; waking the display leaves Pause intact.",
  review: "Read every page of a synthetic transcript, then confirm its original destination.",
  confirm: "This separate confirmation is the last step before a simulated send.",
  idle: "Tap once to wake. That first touch is consumed, including a hold or a swipe.",
  offline: "Disconnected state. No real T3 connection is attempted by the simulator.",
  uncertain: "An acknowledgement was lost. The prompt is never automatically sent again.",
  empty: "An empty state makes the next step clear, without choosing a destination for you.",
  scene: "A synthetic visual temporarily takes the screen. Tap to return to your session."
};

function clockLabel(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function setText(id, text) {
  const element = $(id);
  if (element.textContent !== text) element.textContent = text;
}

function connectionError(error) {
  $("connection-status").dataset.error = "true";
  setText("connection-status", "Simulator unavailable");
  $("connection-error").hidden = false;
  setText("connection-error", `The local simulator did not respond. Keep its Python process running; this page will reconnect. ${error.message || ""}`);
}

async function request(path, data) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {
      method: data === undefined ? "GET" : "POST",
      headers: data === undefined ? {} : { "Content-Type": "application/json", "X-Simulator-Token": state?.csrfToken || "" },
      body: data === undefined ? undefined : JSON.stringify(data),
      cache: "no-store", signal: controller.signal, credentials: "same-origin",
      keepalive: Boolean(data && ((data.type === "pointer" && ["up", "cancel"].includes(data.phase))
        || (data.type === "key" && !data.pressed)))
    });
    const result = await response.json();
    if (!response.ok) {
      const error = new Error(result.error || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    if (result.synthetic !== true) throw new Error("This page requires a synthetic simulator response.");
    return result;
  } finally {
    window.clearTimeout(timeout);
  }
}

function newContact(payload) {
  return ["tap", "hold", "swipe", "boot"].includes(payload.type)
    || (payload.type === "pointer" && payload.phase === "down")
    || (payload.type === "key" && payload.pressed);
}

function inputReady() {
  return Boolean(state && displayedVersion === state.frameVersion && pendingActions === 0);
}

function sendAction(payload) {
  if (newContact(payload)) {
    if (!inputReady()) return Promise.resolve();
    payload = { ...payload, frameVersion: displayedVersion };
  }
  pendingActions += 1;
  actionQueue = actionQueue.then(async () => {
    const sequence = ++requestSequence;
    try {
      const next = await request("/api/action", payload);
      update(next, sequence);
    } catch (error) {
      if (payload.type === "pointer" || payload.type === "key") reconnectCancel = true;
      if (error.status && error.status < 500) {
        feedbackUntil = performance.now() + 5000;
        setText("last-action", error.status === 409 ? "The screen changed before that input arrived. Try again on the current screen." : error.message);
        window.setTimeout(poll, 0);
      } else connectionError(error);
    } finally {
      pendingActions -= 1;
    }
  });
  return actionQueue;
}

function stateHelp(next) {
  if (next.idle || next.wakePending) return "First touch wakes only. Your selection and voice preference stay in place.";
  if (next.recording) return "Synthetic speech is being captured. The destination is locked until this prompt finishes.";
  if (["review", "confirm"].includes(next.screen.kind)) return "This prompt keeps the destination selected when recording began. Review never sends by itself.";
  if (next.screen.kind === "uncertain") return "Check the simulated session. An uncertain send is never retried automatically.";
  if (next.screen.kind === "sending") return "Waiting for acknowledgement. Extra taps cannot send the same prompt twice.";
  if (next.screen.kind === "transcribing") return "Converting a fixed sample phrase into a transcript. The original destination stays locked.";
  if (next.screen.kind === "sent") return "The synthetic prompt was accepted. Tap to return; this does not imply the agent's work is finished.";
  if (next.screen.kind === "offline") return "The interface is disconnected. The simulator does not connect to a real T3 instance.";
  if (["empty-home", "empty-project"].includes(next.screen.kind)) return "There are no sessions to choose here. BOOT selects the simulated open conversation when one is available.";
  if (["error", "control-error", "empty"].includes(next.screen.kind)) return "This action did not complete. Read the on-screen explanation, then tap to return.";
  if (["current-error", "voice-offline", "t3-choices"].includes(next.screen.kind)) return "Choose a destination explicitly. A missing or ambiguous session never redirects your prompt.";
  if (next.screen.kind === "scene") return "This synthetic visual temporarily takes the display. Tap to return to your selected session.";
  if (next.paused) return "Voice input is disabled. Tap Resume voice before holding to record.";
  if (next.screen.kind === "projects") return "Left PREV selects the previous project; right NEXT selects the next. At either end, the list wraps around.";
  return next.mode === "review" ? "Release a hold to review the sample phrase before a simulated send." : "Release a hold to transcribe and send the sample phrase to this synthetic session.";
}

function update(next, sequence) {
  if (sequence < appliedSequence) return;
  appliedSequence = sequence;
  state = next;
  $("connection-status").dataset.error = "false";
  setText("connection-status", "Simulator connected");
  $("connection-error").hidden = true;
  $("reset").disabled = false;
  $("advance").disabled = false;
  setText("panel-level", `${Math.round(next.brightness)}% brightness`);
  setText("clock", clockLabel(next.clock.seconds));
  setText("destination-project", next.destination?.project || "NO SESSION SELECTED");
  setText("destination-title", next.destination?.title || "Choose your direction.");
  const canHold = (next.controls || []).some((control) => control.kind === "hold");
  const locked = ["transcribing", "review", "confirm", "sending"].includes(next.screen.kind);
  const voiceState = next.recording ? "recording" : next.idle || next.wakePending ? "idle" : next.paused ? "paused" : canHold ? "ready" : "inactive";
  const voiceLabel = next.recording ? "SIM RECORDING" : next.idle ? "IDLE · TAP TO WAKE" : next.wakePending ? "WAKING" : next.paused ? "VOICE PAUSED" : locked ? "DESTINATION LOCKED" : canHold ? "VOICE ENABLED" : "VOICE UNAVAILABLE";
  $("voice-status").dataset.state = voiceState;
  $("voice-led").dataset.state = voiceState;
  setText("voice-status", voiceLabel);
  setText("send-mode", next.mode === "review" ? "RELEASE → REVIEW" : "RELEASE → SEND");
  setText("state-help", stateHelp(next));
  if (performance.now() >= feedbackUntil) setText("last-action", next.lastAction || "Ready for your next interaction.");
  setText("submission-count", `${next.submissions || 0} SIM SEND${next.submissions === 1 ? "" : "S"}`);
  setText("scenario-note", scenarioNotes[next.scenario] || "Explore this state using the actual device controls.");
  setText("simulation-note", next.idle ? "Ambient artwork is a simulator approximation. Wake behavior uses native input logic." : "Real renderer. Native gesture logic. Simulated hardware and synthetic speech.");
  updateScenarios(next);
  if (displayedVersion === next.frameVersion) updatePresented(next);
  updateEvents(next.events || []);
  updateFrame();
}

function updatePresented(next) {
  setText("screen-kind", next.idle ? "Ambient idle" : screenNames[next.screen.kind] || next.screen.kind);
  const alt = next.idle ? "Dim ambient idle screen; first touch wakes only" : `${screenNames[next.screen.kind] || next.screen.kind}${next.screen.title ? `: ${next.screen.title}` : ""}`;
  frame.alt = alt;
  display.setAttribute("aria-label", `${alt}. Simulated touch display`);
  updateControls(next);
}

function updateScenarios(next) {
  const key = JSON.stringify(next.scenarios);
  if (key !== scenariosKey) {
    scenariosKey = key;
    $("scenario").replaceChildren(...next.scenarios.map((scenario) => {
      const option = document.createElement("option");
      option.value = scenario.id;
      option.textContent = scenario.label;
      return option;
    }));
    $("scenario").disabled = false;
  }
  $("scenario").value = next.scenario;
}

function updateControls(next) {
  // Keep a held accessible control mounted through the listening transition so
  // its eventual release still belongs to the same native contact.
  if (activeControlHold) return;
  const controls = next.controls || [];
  const key = JSON.stringify(controls);
  if (key === controlsKey) return;
  controlsKey = key;
  const focusedId = document.activeElement?.closest(".control-button")?.dataset.id;
  const buttons = controls.map((control) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "control-button";
    button.dataset.id = control.id;
    button.setAttribute("aria-label", control.label);
    if (["previous", "next"].includes(control.id)) {
      const arrow = document.createElement("span");
      arrow.className = "key-arrow";
      arrow.setAttribute("aria-hidden", "true");
      arrow.textContent = control.id === "previous" ? "←" : "→";
      button.append(arrow);
    }
    button.append(document.createTextNode(control.label));
    if (control.kind === "hold") bindHold(button, control);
    else button.addEventListener("click", () => {
      marker(control.x, control.y, false);
      sendAction({ type: "tap", x: control.x, y: control.y });
    });
    return button;
  });
  $("device-controls").replaceChildren(...buttons);
  if (focusedId) buttons.find((button) => button.dataset.id === focusedId)?.focus({ preventScroll: true });
  const regions = { previous: [0, 130, 96, 185], next: [370, 130, 96, 185], header: [90, 45, 286, 81], left: [46, 327, 166, 125], right: [255, 327, 165, 125], center: [133, 143, 200, 170] };
  $("hit-regions").replaceChildren(...controls.filter((control) => regions[control.id]).map((control) => {
    const [x, y, width, height] = regions[control.id];
    const region = document.createElement("span");
    region.className = "hit-region";
    region.dataset.id = control.id;
    region.style.left = `${x / 466 * 100}%`;
    region.style.top = `${y / 466 * 100}%`;
    region.style.width = `${width / 466 * 100}%`;
    region.style.height = `${height / 466 * 100}%`;
    region.textContent = control.label;
    return region;
  }));
}

function updateEvents(events) {
  const recent = events.slice(-6).reverse();
  const key = JSON.stringify(recent);
  if (key === eventsKey) return;
  eventsKey = key;
  if (!recent.length) {
    const empty = document.createElement("li");
    empty.className = "event-placeholder";
    empty.textContent = "Actions will appear here.";
    $("event-list").replaceChildren(empty);
    return;
  }
  $("event-list").replaceChildren(...recent.map((event) => {
    const item = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = clockLabel(event.time);
    const description = document.createElement("span");
    const kind = document.createElement("span");
    kind.className = "event-kind";
    kind.textContent = event.kind;
    description.append(kind, document.createTextNode(event.message));
    item.append(time, description);
    return item;
  }));
}

function updateFrame(animated = false) {
  if (!state || frameBusy) return;
  const key = `${state.frameVersion}:${state.idle}:${state.recording}`;
  const characterPaint = !reducedMotion.matches && !state.idle && !state.recording
    && state.screen.kind === "home" && state.imageVersion !== paintedImageVersion;
  if (!animated && key === frameKey && !characterPaint) return;
  frameKey = key;
  paintedImageVersion = state.imageVersion;
  frameBusy = true;
  const url = new URL(state.frameUrl || `/frame.png?v=${state.frameVersion}`, location.href);
  if (url.origin !== location.origin || url.pathname !== "/frame.png") {
    frameBusy = false;
    connectionError(new Error("Unexpected simulator frame URL."));
    return;
  }
  if (animated) url.searchParams.set("tick", performance.now().toFixed(0));
  frameLoad = { version: Number(url.searchParams.get("v")), state };
  frame.src = url.href;
}
frame.addEventListener("load", () => {
  frameBusy = false;
  $("loading-screen").hidden = true;
  if (frameLoad) {
    displayedVersion = frameLoad.version;
    updatePresented(frameLoad.state);
  }
  if (state?.frameVersion !== displayedVersion) updateFrame();
});
frame.addEventListener("error", () => {
  frameBusy = false;
  frameKey = "";
  // A superseded version may be rejected during a transition. Refresh state;
  // never guess which control belongs to a frame that failed to load.
  window.setTimeout(poll, 0);
});

function coordinates(event) {
  const bounds = display.getBoundingClientRect();
  // Keep out-of-bounds motion out of bounds: the native input filter cancels it.
  return { x: Math.round((event.clientX - bounds.left) / bounds.width * 466),
    y: Math.round((event.clientY - bounds.top) / bounds.height * 466) };
}

let markerTimer;
function marker(x, y, held) {
  window.clearTimeout(markerTimer);
  const dot = $("touch-marker");
  dot.style.left = `${x / 466 * 100}%`;
  dot.style.top = `${y / 466 * 100}%`;
  dot.dataset.visible = "true";
  if (!held) markerTimer = window.setTimeout(() => { dot.dataset.visible = "false"; }, 300);
}

function startPointer(event) {
  if (!inputReady() || event.button !== 0 || pointer || keyboardHold || activeControlHold) return;
  event.preventDefault();
  display.focus({ preventScroll: true });
  display.setPointerCapture(event.pointerId);
  const point = coordinates(event);
  pointer = { id: event.pointerId, ...point };
  marker(point.x, point.y, true);
  lastMoveAt = performance.now();
  sendAction({ type: "pointer", phase: "down", ...point });
}

function movePointer(event) {
  if (!pointer || event.pointerId !== pointer.id) return;
  const point = coordinates(event);
  pointer = { ...pointer, ...point };
  marker(point.x, point.y, true);
  if (performance.now() - lastMoveAt < 40) return;
  lastMoveAt = performance.now();
  sendAction({ type: "pointer", phase: "move", ...point });
}

function endPointer(event, cancel = false) {
  if (!pointer || (event && event.pointerId !== pointer.id)) return;
  const point = event ? coordinates(event) : { x: pointer.x, y: pointer.y };
  const id = pointer.id;
  pointer = null;
  if (display.hasPointerCapture(id)) display.releasePointerCapture(id);
  marker(point.x, point.y, false);
  sendAction({ type: "pointer", phase: cancel ? "cancel" : "up", ...point });
}

display.addEventListener("pointerdown", startPointer);
display.addEventListener("pointermove", movePointer);
display.addEventListener("pointerup", (event) => endPointer(event));
display.addEventListener("pointercancel", (event) => endPointer(event, true));
display.addEventListener("lostpointercapture", (event) => endPointer(event, true));
display.addEventListener("contextmenu", (event) => event.preventDefault());
// A browser synthesizes click after a pointer or keyboard release. Native input
// above already owns that release, so never turn click into a second action.
display.addEventListener("click", (event) => event.preventDefault());

function beginKeyboardHold() {
  if (keyboardHold || pointer || activeControlHold || !inputReady()) return;
  keyboardHold = true;
  marker(233, 233, true);
  sendAction({ type: "pointer", phase: "down", x: 233, y: 233 });
}
function endKeyboardHold(cancel = false) {
  if (!keyboardHold) return;
  keyboardHold = false;
  marker(233, 233, false);
  sendAction({ type: "pointer", phase: cancel ? "cancel" : "up", x: 233, y: 233 });
}
display.addEventListener("keydown", (event) => {
  if (![" ", "Enter", "ArrowLeft", "ArrowRight", "b", "B"].includes(event.key)) return;
  event.preventDefault();
  if (event.repeat || !state) return;
  if (event.key === " ") beginKeyboardHold();
  else if (["b", "B"].includes(event.key)) sendAction({ type: "boot" });
  else if (event.key === "Enter") sendAction({ type: "tap", x: 233, y: 233 });
  else {
    const id = event.key === "ArrowLeft" ? "previous" : "next";
    const control = state.controls.find((item) => item.id === id);
    if (control) sendAction({ type: "tap", x: control.x, y: control.y });
  }
});
display.addEventListener("keyup", (event) => {
  if (event.key === " ") { event.preventDefault(); endKeyboardHold(); }
  if (event.key === "Enter") event.preventDefault();
});
display.addEventListener("blur", () => endKeyboardHold(true));

function bindHold(button, control) {
  let held = false;
  function begin(event) {
    if (held || pointer || keyboardHold || activeControlHold || !inputReady()) return;
    if (event.type === "pointerdown" && event.button !== 0) return;
    held = true;
    activeControlHold = { end };
    button.dataset.held = "true";
    if (event.type === "pointerdown") {
      event.preventDefault();
      button.focus({ preventScroll: true });
      button.setPointerCapture(event.pointerId);
    }
    marker(control.x, control.y, true);
    sendAction({ type: "pointer", phase: "down", x: control.x, y: control.y });
  }
  function end(cancel = false) {
    if (!held) return;
    held = false;
    activeControlHold = null;
    button.dataset.held = "false";
    marker(control.x, control.y, false);
    sendAction({ type: "pointer", phase: cancel ? "cancel" : "up", x: control.x, y: control.y });
  }
  button.addEventListener("pointerdown", begin);
  button.addEventListener("pointerup", () => end());
  button.addEventListener("pointercancel", () => end(true));
  button.addEventListener("lostpointercapture", () => end(true));
  button.addEventListener("keydown", (event) => { if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); if (!event.repeat) begin(event); } });
  button.addEventListener("keyup", (event) => { if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); end(); } });
  button.addEventListener("blur", () => end(true));
  button.addEventListener("click", (event) => event.preventDefault());
}

function bootStart(event) {
  if (bootHeld || !inputReady()) return;
  if (event.type === "pointerdown" && event.button !== 0) return;
  bootHeld = true;
  $("boot").dataset.held = "true";
  if (event.type === "pointerdown") {
    event.preventDefault();
    $("boot").focus({ preventScroll: true });
    $("boot").setPointerCapture(event.pointerId);
  }
  sendAction({ type: "key", pressed: true });
}
function bootEnd() {
  if (!bootHeld) return;
  bootHeld = false;
  $("boot").dataset.held = "false";
  sendAction({ type: "key", pressed: false });
}
$("boot").addEventListener("pointerdown", bootStart);
$("boot").addEventListener("pointerup", bootEnd);
$("boot").addEventListener("pointercancel", bootEnd);
$("boot").addEventListener("lostpointercapture", bootEnd);
$("boot").addEventListener("keydown", (event) => { if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); if (!event.repeat) bootStart(event); } });
$("boot").addEventListener("keyup", (event) => { if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); bootEnd(); } });
$("boot").addEventListener("blur", bootEnd);
$("boot").addEventListener("click", (event) => event.preventDefault());

function cancelInputs() { endPointer(null, true); endKeyboardHold(true); activeControlHold?.end(true); bootEnd(); }
window.addEventListener("blur", cancelInputs);
window.addEventListener("pagehide", cancelInputs);
document.addEventListener("visibilitychange", () => { if (document.hidden) cancelInputs(); });
$("scenario").addEventListener("change", () => { cancelInputs(); sendAction({ type: "scenario", scenario: $("scenario").value }); });
$("reset").addEventListener("click", () => { cancelInputs(); sendAction({ type: "reset" }); });
$("advance").addEventListener("click", () => sendAction({ type: "advance", seconds: 60 }));
$("show-regions").addEventListener("change", () => { display.dataset.regions = String($("show-regions").checked); });

let polling = false;
async function poll() {
  if (polling || pendingActions || document.hidden) return;
  polling = true;
  const sequence = ++requestSequence;
  try {
    const next = await request("/api/state");
    update(next, sequence);
    if (reconnectCancel) {
      reconnectCancel = false;
      await sendAction({ type: "pointer", phase: "cancel", x: 233, y: 233 });
      await sendAction({ type: "key", pressed: false });
    }
  } catch (error) { connectionError(error); }
  finally { polling = false; }
}
window.setInterval(poll, 400);
window.setInterval(() => {
  if (!document.hidden && !reducedMotion.matches && state && (state.idle || state.recording || state.screen.kind === "listening")) updateFrame(true);
}, 180);
poll();
