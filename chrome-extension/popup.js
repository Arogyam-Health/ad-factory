/* popup.js — Extension popup UI */

const sessionInput = document.getElementById("sessionInput");
const connectBtn = document.getElementById("connectBtn");
const statusText = document.getElementById("statusText");
const statusDot = document.getElementById("statusDot");
const tabsSection = document.getElementById("tabsSection");
const tabList = document.getElementById("tabList");

let currentState = "disconnected";

// Load saved session token
chrome.storage.local.get(["sessionToken"], (data) => {
  if (data.sessionToken) {
    sessionInput.value = data.sessionToken;
  }
});

// Get current state
chrome.runtime.sendMessage({ type: "GET_STATE" }, (resp) => {
  if (resp) {
    updateUI(resp.state);
  }
});

// Listen for state changes
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "CONNECTION_STATE") {
    updateUI(msg.state);
  }
});

connectBtn.addEventListener("click", () => {
  const token = sessionInput.value.trim();
  if (!token) {
    showStatus("Please enter a session token", "error");
    return;
  }
  if (currentState === "connected") {
    chrome.runtime.sendMessage({ type: "DISCONNECT" });
  } else {
    chrome.runtime.sendMessage({ type: "CONNECT", sessionToken: token, serverOrigin: "https://ad-factory-3rn5.onrender.com" });
    showStatus("Connecting...", "");
  }
});

function updateUI(state) {
  currentState = state;
  statusDot.className = `dot dot-${state}`;

  if (state === "connected") {
    showStatus("Connected to Ad Factory server", "success");
    connectBtn.textContent = "Disconnect";
    connectBtn.className = "btn btn-disconnect";
    loadTabs();
  } else if (state === "connecting") {
    showStatus("Connecting...", "");
    connectBtn.textContent = "Cancel";
    connectBtn.className = "btn btn-disconnect";
  } else {
    showStatus("Not connected", "");
    connectBtn.textContent = "Connect";
    connectBtn.className = "btn btn-connect";
    tabsSection.style.display = "none";
  }
}

function showStatus(text, type) {
  statusText.textContent = text;
  statusText.className = `status ${type}`;
}

function loadTabs() {
  chrome.tabs.query({}, (tabs) => {
    tabList.innerHTML = "";
    tabsSection.style.display = "";
    for (const tab of tabs) {
      if (tab.url && (tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://"))) continue;
      const div = document.createElement("div");
      div.className = "tab-item";
      div.innerHTML = `
        <span class="tab-dot unattached"></span>
        <span class="tab-title" title="${tab.url || ""}">${tab.title || tab.url || "Untitled"}</span>
      `;
      div.addEventListener("click", () => {
        chrome.tabs.update(tab.id, { active: true });
      });
      tabList.appendChild(div);
    }
    if (!tabList.children.length) {
      tabList.innerHTML = '<div style="padding:6px;color:#999;font-size:12px">No tabs found</div>';
    }
  });
}
