import { appendLog } from "./ui.js";

const selectedProductImages = new Set();
let initialized = false;
let refreshTimer = null;
let currentItems = [];

const $ = (id) => document.getElementById(id);

function updateSummary() {
  const summary = $("referenceProductImageSummary");
  if (!summary) return;
  summary.textContent = currentItems.length
    ? `${currentItems.length} stored · ${selectedProductImages.size} selected`
    : "No product images stored yet.";
}

function productPathForCard(card) {
  const image = card.querySelector("img");
  if (!image) return "";
  const sourcePath = new URL(image.src, window.location.origin).pathname;
  const match = currentItems.find((item) => new URL(item.url, window.location.origin).pathname === sourcePath);
  return match?.path || "";
}

function decorateProductCards() {
  const gallery = $("referenceProductGallery");
  if (!gallery) return;
  gallery.querySelectorAll(".product-asset-slide").forEach((card) => {
    const path = productPathForCard(card);
    if (!path) return;
    card.dataset.productPath = path;
    let checkbox = card.querySelector(".product-select-checkbox");
    if (!checkbox) {
      checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "product-select-checkbox";
      checkbox.setAttribute("aria-label", "Select product image for this run");
      checkbox.addEventListener("click", (event) => event.stopPropagation());
      checkbox.addEventListener("change", () => {
        checkbox.checked ? selectedProductImages.add(path) : selectedProductImages.delete(path);
        card.classList.toggle("selected", checkbox.checked);
        updateSummary();
      });
      card.appendChild(checkbox);
      card.addEventListener("click", (event) => {
        if (event.target.closest("button,input")) return;
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change"));
      });
    }
    checkbox.checked = selectedProductImages.has(path);
    card.classList.toggle("selected", checkbox.checked);
  });
  updateSummary();
}

async function refreshProductSelection({ selectNew = false } = {}) {
  try {
    const response = await fetch(`/api/reference-workspace?t=${Date.now()}`);
    if (!response.ok) return;
    const workspace = await response.json();
    const nextItems = workspace.product_images || [];
    const valid = new Set(nextItems.map((item) => item.path));
    [...selectedProductImages].forEach((path) => {
      if (!valid.has(path)) selectedProductImages.delete(path);
    });
    if (!initialized || selectNew) {
      nextItems.forEach((item) => selectedProductImages.add(item.path));
    }
    initialized = true;
    currentItems = nextItems;
    decorateProductCards();
  } catch (error) {
    appendLog(`Could not refresh product selections: ${String(error)}`);
  }
}

function scheduleRefresh(options = {}) {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => refreshProductSelection(options), 80);
}

const gallery = $("referenceProductGallery");
if (gallery) {
  new MutationObserver(() => scheduleRefresh()).observe(gallery, { childList: true, subtree: true });
}

const originalFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input?.url || "";
  if (url.includes("/api/runs/execute-reference") && init.body instanceof FormData) {
    const raw = init.body.get("config");
    if (typeof raw === "string") {
      const config = JSON.parse(raw);
      config.product_image_paths = [...selectedProductImages];
      init.body.set("config", JSON.stringify(config));
    }
  }
  const response = await originalFetch(input, init);
  if (url.includes("/api/reference-workspace/product-images") && response.ok) {
    scheduleRefresh({ selectNew: init.method?.toUpperCase() === "POST" });
  }
  return response;
};

$("referenceRunBtn")?.addEventListener("click", (event) => {
  if (selectedProductImages.size) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  appendLog("Select at least one product image for Reference Image Flow.");
}, true);

window.addEventListener("reference-workspace-loaded", () => scheduleRefresh());
refreshProductSelection();
