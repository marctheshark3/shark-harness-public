// Appended to the verified T3 0.0.45 client runtime by install_t3_focus.py.
// Pc is that runtime's activity-change notifier. Its existing authenticated
// connection sends the report; this helper never reads or copies credentials.
function sharkCurrentRoute() {
  const route = window.location.hash.startsWith("#/")
    ? window.location.hash.slice(1).split("?")[0]
    : window.location.pathname;
  // Native router parameters are raw IDs. Decode URL components once here;
  // decoding again would corrupt percent sequences inside app-owned IDs.
  try { return route.split("/").map(decodeURIComponent).join("/"); }
  catch { return ""; }
}
function sharkCurrentThreadScopes(environmentId) {
  if (document.visibilityState !== "visible" || !document.hasFocus()) return [];
  try {
    const path = sharkCurrentRoute();
    const parts = path.split("/");
    if (parts.length !== 3 || parts[0] !== "") return [];
    const environment = parts[1];
    const thread = parts[2];
    if (environment !== environmentId || !/^[A-Za-z0-9_:%-]{1,200}$/.test(thread)) return [];
    // Draft routes are /draft/<id>; their first component is not an environment.
    return [{ type: "thread", threadId: thread }];
  } catch {
    return [];
  }
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  for (const method of ["pushState", "replaceState"]) {
    const original = window.history[method];
    window.history[method] = function (...args) {
      const result = original.apply(this, args);
      Pc();
      return result;
    };
  }
  window.addEventListener("popstate", Pc);
  window.addEventListener("hashchange", Pc);
  // T3 already reports focus/visibility changes. Refresh focused selections
  // more frequently so a lost client cannot leave the device armed indefinitely.
  window.setInterval(() => {
    if (document.visibilityState === "visible" && document.hasFocus()) Pc();
  }, 2000);
}

// The proof is public; only this tab holds the corresponding random secret.
// The companion verifies the proof against T3 before accepting HTTPS requests.
let sharkVoiceProof = null;
function sharkVoiceIdentity(fallback) { return sharkVoiceProof || fallback; }

if (typeof window !== "undefined" && window.crypto?.subtle) {
  const secret = Array.from(crypto.getRandomValues(new Uint8Array(32)), x => x.toString(16).padStart(2, "0")).join("");
  const endpoint = "/shark-voice/api/voice-client";
  const selections = new Map(), results = new Map(), contexts = new Map();
  let context = null, signature = null, selected = null, busy = false;
  window.sharkVoice = {register(value) {
    contexts.set(value.route, value);
    if (contexts.size > 512) contexts.delete(contexts.keys().next().value);
  }};
  function selection() {
    const route = sharkCurrentRoute();
    context = contexts.get(route);
    if (!context || route !== context.route) throw Error("Open a conversation in T3.");
    const snapshot = context.capture();
    const nextSignature = JSON.stringify(snapshot.destination);
    if (nextSignature !== signature) {
      signature = nextSignature;
      selected = {id: crypto.randomUUID(), ...snapshot.destination};
      selections.set(selected.id, snapshot);
      if (selections.size > 512) selections.delete(selections.keys().next().value);
    }
    return {id:selected.id, threadId:selected.threadId, environmentId:selected.environmentId,
      environment:selected.environment, projectId:selected.projectId, project:selected.project,
      title:selected.title, new:selected.new};
  }
  async function execute(job) {
    if (results.has(job.id)) return;
    results.set(job.id, null); // Claim before any asynchronous work; repeated polls cannot resend.
    let receipt;
    try {
      const target = selections.get(job.selectionId);
      if (!target) throw Error("The selected conversation is no longer available in this tab.");
      const result = await target.send(job);
      receipt = {id:job.id, ...result};
    } catch {
      receipt = {id:job.id, state:"uncertain"};
    }
    results.set(job.id, receipt);
    if (results.size > 512) results.delete(results.keys().next().value);
  }
  async function poll() {
    if (busy || !sharkVoiceProof) return;
    busy = true;
    try {
      let destination = null, error = "";
      try { destination = selection(); } catch (e) { error = e.message; }
      const response = await fetch(endpoint, {method:"POST", credentials:"omit", cache:"no-store",
        headers:{"Content-Type":"application/json", Authorization:`Bearer ${secret}`},
        signal:AbortSignal.timeout(4000), body:JSON.stringify({selection:destination, error,
          focused:document.hasFocus(), visible:document.visibilityState === "visible",
          receipts:Array.from(results.values()).filter(Boolean).slice(-20)})});
      if (!response.ok) return;
      for (const job of (await response.json()).jobs || []) void execute(job);
    } catch { /* Loss of this connection leaves selection unavailable on the device. */ }
    finally { busy = false; }
  }
  crypto.subtle.digest("SHA-256", new TextEncoder().encode(secret)).then(buffer => {
    sharkVoiceProof = "shark-voice:" + Array.from(new Uint8Array(buffer), x => x.toString(16).padStart(2, "0")).join("");
    Pc();
    window.setInterval(poll, 1000);
  });
}
