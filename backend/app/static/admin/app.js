const API_KEY_STORAGE = "untp_publisher_admin_api_key";
const PAGE_SIZE = 50;

const state = {
  collection: null,
  collectionMeta: null,
  skip: 0,
  search: "",
  listColumns: [],
  idField: "id",
  collectionsByName: {},
  activeNav: "home",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function getApiKey() {
  return sessionStorage.getItem(API_KEY_STORAGE) || "";
}

function setApiKey(key) {
  sessionStorage.setItem(API_KEY_STORAGE, key.trim());
  updateApiStatus();
}

function clearApiKey() {
  sessionStorage.removeItem(API_KEY_STORAGE);
  updateApiStatus();
}

function updateApiStatus() {
  const el = document.getElementById("api-status");
  if (!el) return;
  el.textContent = getApiKey() ? "API key set" : "No API key";
}

function showAlert(message) {
  const el = document.getElementById("alert");
  el.textContent = message;
  el.classList.remove("d-none");
}

function hideAlerts() {
  document.getElementById("alert").classList.add("d-none");
  document.getElementById("alert-success").classList.add("d-none");
}

async function apiFetch(path) {
  const key = getApiKey();
  if (!key) throw new Error("Set an API key first.");
  const response = await fetch(path, {
    headers: { Accept: "application/json", "X-API-Key": key },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.json();
}

function showView(name) {
  document.getElementById("view-home").classList.toggle("d-none", name !== "home");
  document.getElementById("view-list").classList.toggle("d-none", name !== "list");
  document.getElementById("view-detail").classList.toggle("d-none", name !== "detail");
}

function setActiveNav(nav) {
  state.activeNav = nav;
  document.querySelectorAll("[data-nav]").forEach((el) => {
    el.classList.toggle("active", el.getAttribute("data-nav") === nav);
  });
}

function bindSidebarNav() {
  document.getElementById("nav-home")?.addEventListener("click", (e) => {
    e.preventDefault();
    goHome();
  });
  document.getElementById("nav-home-brand")?.addEventListener("click", (e) => {
    e.preventDefault();
    goHome();
  });
}

function goHome() {
  hideAlerts();
  state.collection = null;
  document.getElementById("page-title").textContent = "Overview";
  setActiveNav("home");
  showView("home");
  loadCollections().catch((err) => showAlert(err.message));
}

function renderSidebar(collections) {
  const root = document.getElementById("sidebar-collections");
  root.innerHTML = collections
    .map(
      (col) => `
      <a href="#" class="bcgov-left-nav-link" data-nav="collection:${escapeHtml(col.name)}">
        ${escapeHtml(col.title || col.name)}
        <span class="text-secondary small">${col.count ?? 0}</span>
      </a>`
    )
    .join("");
  root.querySelectorAll("[data-nav]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      const name = el.dataset.nav.replace("collection:", "");
      const meta = state.collectionsByName[name];
      if (meta) openCollection(name, meta).catch((err) => showAlert(err.message));
    });
  });
}

function renderOverview(collections) {
  const root = document.getElementById("collection-sections");
  const byPhase = {};
  for (const col of collections) {
    const phase = col.phase || "Other";
    (byPhase[phase] ||= []).push(col);
  }
  root.innerHTML = Object.entries(byPhase)
    .map(
      ([phase, cols]) => `
      <div class="mb-4">
        <h3 class="h4">${escapeHtml(phase)}</h3>
        <div class="row g-3">
          ${cols
            .map(
              (col) => `
            <div class="col-md-6">
              <div class="card h-100">
                <div class="card-body">
                  <h4 class="card-title">${escapeHtml(col.title || col.name)}</h4>
                  <p class="text-secondary small">${escapeHtml(col.description || "")}</p>
                  <p class="small mb-2"><strong>${col.count ?? 0}</strong> records</p>
                  <p class="small text-secondary mb-3">${escapeHtml(col.create_via || "")}</p>
                  <button type="button" class="btn btn-sm btn-outline-primary collection-card-open" data-collection="${escapeHtml(col.name)}">
                    Open
                  </button>
                </div>
              </div>
            </div>`
            )
            .join("")}
        </div>
      </div>`
    )
    .join("");
  root.querySelectorAll(".collection-card-open").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.dataset.collection;
      const meta = state.collectionsByName[name];
      if (meta) openCollection(name, meta).catch((err) => showAlert(err.message));
    });
  });
}

async function loadCollections() {
  hideAlerts();
  if (!getApiKey()) {
    state.collectionsByName = {};
    document.getElementById("collection-sections").innerHTML =
      '<p class="text-secondary">Set an API key to browse provisioned records.</p>';
    document.getElementById("sidebar-collections").innerHTML = "";
    return;
  }
  const data = await apiFetch("/admin/api/collections");
  const collections = data.collections || [];
  state.collectionsByName = Object.fromEntries(collections.map((c) => [c.name, c]));
  renderSidebar(collections);
  renderOverview(collections);
}

async function openCollection(name, meta) {
  hideAlerts();
  state.collection = name;
  state.collectionMeta = meta;
  state.skip = 0;
  state.search = "";
  state.listColumns = meta.list_columns || ["id"];
  state.idField = meta.id_field || "id";
  document.getElementById("page-title").textContent = meta.title || name;
  document.getElementById("list-title").textContent = meta.title || name;
  document.getElementById("search-input").value = "";
  setActiveNav(`collection:${name}`);
  showView("list");
  await loadRecords();
}

async function loadRecords() {
  const params = new URLSearchParams({
    skip: String(state.skip),
    limit: String(PAGE_SIZE),
  });
  if (state.search) params.set("q", state.search);
  const data = await apiFetch(
    `/admin/api/collections/${encodeURIComponent(state.collection)}?${params}`
  );
  document.getElementById("list-total").textContent = String(data.total ?? 0);
  document.getElementById("page-info").textContent =
    `${data.skip + 1}–${Math.min(data.skip + data.items.length, data.total)} of ${data.total}`;
  document.getElementById("btn-prev").disabled = state.skip <= 0;
  document.getElementById("btn-next").disabled = state.skip + PAGE_SIZE >= data.total;

  const thead = document.getElementById("records-thead");
  thead.innerHTML = `<tr>${state.listColumns
    .map((col) => `<th>${escapeHtml(col)}</th>`)
    .join("")}<th></th></tr>`;

  const tbody = document.getElementById("records-tbody");
  tbody.innerHTML = (data.items || [])
    .map((row) => {
      const id = row[state.idField];
      return `<tr>${state.listColumns
        .map((col) => `<td class="text-break">${escapeHtml(row[col] ?? "")}</td>`)
        .join("")}<td><button type="button" class="btn btn-sm btn-outline-secondary row-open-btn" data-id="${escapeHtml(String(id))}">View</button></td></tr>`;
    })
    .join("");

  tbody.querySelectorAll(".row-open-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      openRecord(btn.dataset.id).catch((err) => showAlert(err.message));
    });
  });
}

async function openRecord(recordId) {
  hideAlerts();
  const data = await apiFetch(
    `/admin/api/collections/${encodeURIComponent(state.collection)}/records/${encodeURIComponent(recordId)}`
  );
  showView("detail");
  document.getElementById("page-title").textContent = data.title || state.collection;
  document.getElementById("detail-title").textContent = recordId;
  document.getElementById("detail-json").textContent = JSON.stringify(data.record, null, 2);
  document.getElementById("detail-context").innerHTML = `
    <div class="card-body d-flex gap-2 align-items-center">
      <button type="button" class="btn btn-outline-secondary btn-sm" id="detail-back">Back to list</button>
      <span class="text-secondary small">${escapeHtml(data.description || "")}</span>
    </div>`;
  document.getElementById("detail-back").addEventListener("click", () => {
    showView("list");
    document.getElementById("page-title").textContent =
      state.collectionMeta?.title || state.collection;
  });
}

function openApiKeyModal() {
  document.getElementById("api-key-input").value = getApiKey();
  document.getElementById("api-key-modal").classList.remove("d-none");
}

function closeApiKeyModal() {
  document.getElementById("api-key-modal").classList.add("d-none");
}

document.getElementById("btn-set-key").addEventListener("click", openApiKeyModal);
document.getElementById("api-key-close").addEventListener("click", closeApiKeyModal);
document.getElementById("api-key-backdrop").addEventListener("click", closeApiKeyModal);
document.getElementById("api-key-save").addEventListener("click", () => {
  setApiKey(document.getElementById("api-key-input").value);
  closeApiKeyModal();
  loadCollections().catch((err) => showAlert(err.message));
});
document.getElementById("api-key-clear").addEventListener("click", () => {
  clearApiKey();
  document.getElementById("api-key-input").value = "";
  closeApiKeyModal();
  loadCollections().catch((err) => showAlert(err.message));
});

document.getElementById("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  state.search = document.getElementById("search-input").value.trim();
  state.skip = 0;
  loadRecords().catch((err) => showAlert(err.message));
});

document.getElementById("btn-prev").addEventListener("click", () => {
  state.skip = Math.max(0, state.skip - PAGE_SIZE);
  loadRecords().catch((err) => showAlert(err.message));
});

document.getElementById("btn-next").addEventListener("click", () => {
  state.skip += PAGE_SIZE;
  loadRecords().catch((err) => showAlert(err.message));
});

bindSidebarNav();
updateApiStatus();
showView("home");
loadCollections().catch((err) => showAlert(err.message));
