/* Ad Factory CDP Bridge — background service worker
 *
 * Connects to the Ad Factory server via WebSocket and bridges
 * Chrome DevTools Protocol commands from the server to local Chrome.
 */

const SERVER_ORIGINS = [
  "https://ad-factory-3rn5.onrender.com",
  "http://localhost:4090",
];

let ws = null;
let reconnectTimer = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;
const HEARTBEAT_INTERVAL = 15000;

let heartbeatTimer = null;
let pendingCommands = new Map(); // id → { resolve, reject, timer }
let attachedTabs = new Map();   // targetId → { tabId, debuggerAttached }
let debuggerEventTabs = new Set(); // tabIds with event forwarding installed
let eventListeners = new Map(); // method → Set<callback>

let connectionState = "disconnected"; // disconnected | connecting | connected
let serverOrigin = SERVER_ORIGINS[0];

/* ─── Storage helpers ─── */

async function getStoredSession() {
  const data = await chrome.storage.local.get(["sessionToken", "serverOrigin"]);
  return {
    sessionToken: data.sessionToken || "",
    serverOrigin: data.serverOrigin || SERVER_ORIGINS[0],
  };
}

async function setStoredSession(sessionToken, origin) {
  await chrome.storage.local.set({
    sessionToken: sessionToken || "",
    serverOrigin: origin || SERVER_ORIGINS[0],
  });
}

/* ─── Connection management ─── */

function setConnectionState(state) {
  connectionState = state;
  chrome.runtime.sendMessage({ type: "CONNECTION_STATE", state }).catch(() => {});
  updateBadge();
}

function updateBadge() {
  const colors = { connected: "#34A853", connecting: "#FBBC05", disconnected: "#EA4335" };
  const texts = { connected: "ON", connecting: "...", disconnected: "OFF" };
  chrome.action.setBadgeBackgroundColor({ color: colors[connectionState] || colors.disconnected });
  chrome.action.setBadgeText({ text: texts[connectionState] || "" });
}

async function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }

  const { sessionToken, serverOrigin: storedOrigin } = await getStoredSession();
  serverOrigin = storedOrigin;

  if (!sessionToken) {
    setConnectionState("disconnected");
    return;
  }

  setConnectionState("connecting");
  const wsUrl = `${serverOrigin.replace(/^http/, "ws")}/api/extension/ws?session=${encodeURIComponent(sessionToken)}`;

  try {
    ws = new WebSocket(wsUrl);
  } catch (err) {
    console.error("[bridge] WebSocket creation failed:", err);
    setConnectionState("disconnected");
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log("[bridge] Connected to server");
    setConnectionState("connected");
    reconnectDelay = 1000;
    startHeartbeat();
    // Announce connected tabs
    announceTargets();
  };

  ws.onmessage = (evt) => {
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch {
      console.warn("[bridge] Non-JSON message:", evt.data);
      return;
    }
    handleServerMessage(msg);
  };

  ws.onclose = (evt) => {
    console.log("[bridge] Disconnected:", evt.code, evt.reason);
    setConnectionState("disconnected");
    stopHeartbeat();
    // Reject all pending commands
    for (const [id, pending] of pendingCommands) {
      pending.reject(new Error("WebSocket closed"));
      clearTimeout(pending.timer);
    }
    pendingCommands.clear();
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.error("[bridge] WebSocket error:", err);
  };
}

function disconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (ws) {
    ws.close(1000, "User disconnected");
    ws = null;
  }
  setConnectionState("disconnected");
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
}

function startHeartbeat() {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, HEARTBEAT_INTERVAL);
}

function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

/* ─── Command dispatch ─── */

function sendToServer(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

function handleServerMessage(msg) {
  // Response to a command we sent
  if (msg.id && pendingCommands.has(msg.id)) {
    const pending = pendingCommands.get(msg.id);
    pendingCommands.delete(msg.id);
    clearTimeout(pending.timer);
    if (msg.error) {
      pending.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
    } else {
      pending.resolve(msg.result);
    }
    return;
  }

  // Command from server to execute
  if (msg.id && msg.method) {
    handleCommand(msg);
    return;
  }

  // Ping/pong
  if (msg.type === "pong") return;
  if (msg.type === "ping") {
    sendToServer({ type: "pong" });
    return;
  }

  // Event broadcast from server
  if (msg.method && msg.params) {
    dispatchEvent(msg.method, msg.params);
  }
}

async function handleCommand(msg) {
  const { id, method, params } = msg;
  try {
    const result = await executeCDPCommand(method, params || {});
    sendToServer({ id, result });
  } catch (err) {
    sendToServer({ id, error: { message: err.message, code: -1 } });
  }
}

function executeCDPCommand(method, params) {
  // Route to appropriate handler
  const [domain, command] = method.split(".");

  switch (domain) {
    case "Target":
      return handleTargetCommand(command, params);
    case "Page":
      return handlePageCommand(command, params);
    case "Runtime":
      return handleRuntimeCommand(command, params);
    case "DOM":
      return handleDOMCommand(command, params);
    case "Input":
      return handleInputCommand(command, params);
    case "Browser":
      return handleBrowserCommand(command, params);
    case "Network":
      return handleNetworkCommand(command, params);
    default:
      return cdpFallback(method, params);
  }
}

/* ─── Target domain ─── */

async function handleTargetCommand(command, params) {
  switch (command) {
    case "getTargets": {
      const tabs = await chrome.tabs.query({});
      return {
        targetInfos: tabs.map((t) => ({
          targetId: String(t.id),
          type: "page",
          title: t.title || "",
          url: t.url || "",
          attached: attachedTabs.has(String(t.id)),
          openerTargetId: null,
        })),
      };
    }
    case "createTarget": {
      const tab = await chrome.tabs.create({ url: params.url || "about:blank" });
      const targetId = String(tab.id);
      attachedTabs.set(targetId, { tabId: tab.id, debuggerAttached: false });
      return { targetId };
    }
    case "activateTarget": {
      const tabId = await resolveTabId({ _tabId: params.targetId });
      await chrome.tabs.update(tabId, { active: true });
      const tab = await chrome.tabs.get(tabId);
      if (tab.windowId) await chrome.windows.update(tab.windowId, { focused: true });
      return {};
    }
    case "closeTarget": {
      const tabId = await resolveTabId({ _tabId: params.targetId });
      await chrome.tabs.remove(tabId);
      attachedTabs.delete(String(tabId));
      return { success: true };
    }
    case "attachToTarget": {
      const targetId = params.targetId;
      const tabId = parseInt(targetId, 10);
      if (isNaN(tabId)) throw new Error("Invalid targetId");
      try {
        await ensureAttached(tabId);
        return { sessionId: targetId };
      } catch (err) {
        throw new Error(`Failed to attach to tab ${tabId}: ${err.message}`);
      }
    }
    case "detachFromTarget": {
      const targetId = params.targetId;
      const info = attachedTabs.get(targetId);
      if (info && info.debuggerAttached) {
        try {
          await chrome.debugger.detach({ tabId: info.tabId });
        } catch {}
        attachedTabs.delete(targetId);
      }
      return {};
    }
    default:
      throw new Error(`Unknown Target command: ${command}`);
  }
}

/* ─── Page domain ─── */

async function handlePageCommand(command, params) {
  const tabId = await resolveTabId(params);
  switch (command) {
    case "navigate": {
      await ensureAttached(tabId);
      return cdpSend(tabId, "Page.navigate", params);
    }
    case "reload": {
      await chrome.tabs.reload(tabId);
      return {};
    }
    case "captureScreenshot": {
      const dataUrl = await chrome.tabs.captureVisibleTab(null, {
        format: params.format || "png",
        quality: params.quality || 80,
      });
      const base64 = dataUrl.split(",")[1] || "";
      return { data: base64 };
    }
    case "getFrameTree": {
      // Simplified frame tree
      return {
        frameTree: {
          frame: { id: "main", url: (await chrome.tabs.get(tabId)).url || "" },
          childFrames: [],
        },
      };
    }
    default:
      return cdpFallback(`Page.${command}`, params);
  }
}

/* ─── Runtime domain ─── */

async function handleRuntimeCommand(command, params) {
  const tabId = await resolveTabId(params);
  switch (command) {
    case "evaluate": {
      await ensureAttached(tabId);
      return cdpSend(tabId, "Runtime.evaluate", params);
    }
    default:
      return cdpFallback(`Runtime.${command}`, params);
  }
}

/* ─── DOM domain ─── */

async function handleDOMCommand(command, params) {
  const tabId = await resolveTabId(params);
  switch (command) {
    case "getDocument": {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const doc = document.documentElement;
          return { nodeId: 1, nodeName: doc.nodeName, childNodeCount: doc.children.length };
        },
      });
      return { root: results?.[0]?.result || { nodeId: 1, nodeName: "HTML" } };
    }
    case "querySelector": {
      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: (sel) => {
          const el = document.querySelector(sel);
          return el ? { nodeId: Date.now(), nodeName: el.tagName, attributes: [] } : null;
        },
        args: [params.selector || ""],
      });
      return results?.[0]?.result || null;
    }
    default:
      return cdpFallback(`DOM.${command}`, params);
  }
}

/* ─── Input domain ─── */

async function handleInputCommand(command, params) {
  const tabId = await resolveTabId(params);
  switch (command) {
    case "dispatchMouseEvent": {
      const typeMap = { mousePressed: "mousePressed", mouseReleased: "mouseReleased" };
      // Use chrome.debugger to send raw CDP Input events
      await ensureAttached(tabId);
      return cdpSend(tabId, "Input.dispatchMouseEvent", params);
    }
    case "dispatchKeyEvent": {
      await ensureAttached(tabId);
      return cdpSend(tabId, "Input.dispatchKeyEvent", params);
    }
    case "dispatchKeyEventRaw": {
      await ensureAttached(tabId);
      return cdpSend(tabId, "Input.dispatchKeyEvent", params);
    }
    default:
      return cdpFallback(`Input.${command}`, params);
  }
}

/* ─── Browser domain ─── */

async function handleBrowserCommand(command, params) {
  switch (command) {
    case "getVersion": {
      return {
        protocolVersion: "1.3",
        product: "Ad Factory CDP Bridge",
        userAgent: navigator.userAgent,
        jsVersion: "",
      };
    }
    default:
      return cdpFallback(`Browser.${command}`, params);
  }
}

/* ─── Network domain (stub) ─── */

async function handleNetworkCommand(command, params) {
  // Network domain commands are forwarded via debugger
  const tabId = await resolveTabId(params);
  await ensureAttached(tabId);
  return cdpSend(tabId, `Network.${command}`, params);
}

/* ─── Fallback: send raw CDP via debugger ─── */

async function cdpFallback(method, params) {
  const tabId = await resolveTabId(params);
  await ensureAttached(tabId);
  return cdpSend(tabId, method, params);
}

async function ensureAttached(tabId) {
  const targetId = String(tabId);
  if (!attachedTabs.has(targetId)) {
    await chrome.debugger.attach({ tabId }, "1.3");
    attachedTabs.set(targetId, { tabId, debuggerAttached: true });
  }
  if (!debuggerEventTabs.has(tabId)) {
    debuggerEventTabs.add(tabId);
    chrome.debugger.onEvent.addListener((source, method, eventParams) => {
      if (source.tabId === tabId) {
        sendToServer({ method, params: eventParams || {}, targetId });
      }
    });
  }
}

function cdpSend(tabId, method, params) {
  const cdpParams = cleanCDPParams(params);
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand({ tabId }, method, cdpParams, (result) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(result);
      }
    });
  });
}

function cleanCDPParams(params) {
  const clean = { ...(params || {}) };
  for (const key of Object.keys(clean)) {
    if (key.startsWith("_")) delete clean[key];
  }
  return clean;
}

/* ─── Helpers ─── */

async function resolveTabId(params) {
  if (params._tabId) {
    const tabId = parseInt(params._tabId, 10);
    if (!Number.isNaN(tabId)) return tabId;
  }
  const [activeTab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (activeTab?.id) return activeTab.id;
  const [firstTab] = await chrome.tabs.query({});
  if (firstTab?.id) return firstTab.id;
  throw new Error("No browser tab available for CDP command");
}

function dispatchEvent(method, params) {
  const listeners = eventListeners.get(method);
  if (listeners) {
    for (const cb of listeners) {
      try { cb(params); } catch {}
    }
  }
}

/* ─── Target announcements ─── */

async function announceTargets() {
  try {
    const tabs = await chrome.tabs.query({});
    const targets = tabs.map((t) => ({
      targetId: String(t.id),
      type: "page",
      title: t.title || "",
      url: t.url || "",
      attached: attachedTabs.has(String(t.id)),
    }));
    sendToServer({ method: "Target.targetListChanged", params: { targetInfos: targets } });
  } catch {}
}

/* ─── Message handler (from popup / content scripts) ─── */

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "CONNECT") {
    setStoredSession(msg.sessionToken, msg.serverOrigin).then(() => connect());
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "DISCONNECT") {
    disconnect();
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "GET_STATE") {
    sendResponse({ state: connectionState, origin: serverOrigin });
    return true;
  }
  if (msg.type === "EXECUTE_COMMAND") {
    executeCDPCommand(msg.method, msg.params || {})
      .then((result) => sendResponse({ result }))
      .catch((err) => sendResponse({ error: err.message }));
    return true;
  }
});

/* ─── Auto-connect on startup ─── */

(async () => {
  const { sessionToken } = await getStoredSession();
  if (sessionToken) {
    connect();
  }
  updateBadge();
})();

/* ─── Tab close cleanup ─── */

chrome.tabs.onRemoved.addListener((tabId) => {
  const targetId = String(tabId);
  if (attachedTabs.has(targetId)) {
    attachedTabs.delete(targetId);
  }
  debuggerEventTabs.delete(tabId);
  announceTargets();
});

chrome.tabs.onCreated.addListener(() => {
  announceTargets();
});

chrome.tabs.onUpdated.addListener(() => {
  announceTargets();
});
