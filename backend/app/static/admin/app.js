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
  lastWorkflow: [],
  activeNav: "home",
};

let issuerWizardStep = 1;
let templateWizardStep = 1;
let scopeSearchTimer = null;
let scopeSearchRequestId = 0;
let roleSearchTimer = null;
let roleSearchRequestId = 0;
let issuerDescriptionLastPrefill = "";

/** UNTP 0.7.0 DCC template preset (JSON Pointer paths on issued credential). */
const TEMPLATE_DCC_PRESET = {
  subjectType: "PetroleumAndNaturalGasTitle",
  additionalType: "DigitalConformityCredential",
  corePaths: {
    entityId: "/credentialSubject/issuedToParty/registeredId",
    cardinalityId: "/credentialSubject/id",
  },
  subjectPaths: {
    name: "/credentialSubject/name",
    description: "/credentialSubject/description",
    assessorLevel: "/credentialSubject/assessorLevel",
    assessmentLevel: "/credentialSubject/assessmentLevel",
    attestationType: "/credentialSubject/attestationType",
  },
  /** Keys must match `options.additionalData` in the publication payload. */
  additionalPaths: {
    assessedFacility: "/credentialSubject/conformityAssessment/0/assessedFacility",
    assessedProduct: "/credentialSubject/conformityAssessment/0/assessedProduct",
  },
};

/**
 * Incoming publication payload (`options`) → credential subject (fixed contract).
 * Mirrors OCA overlay/mapping used at issuance.
 */
const TEMPLATE_OPTIONS_MAPPINGS = [
  {
    from: "/options/entityId",
    to: "/credentialSubject/issuedToParty/registeredId",
  },
  {
    from: "/options/cardinalityId",
    to: "/credentialSubject/id",
  },
  {
    from: "/options/cardinalityId",
    to: "/credentialSubject/conformityAssessment/0/id",
  },
  {
    from: "/options/cardinalityId",
    to: "/credentialSubject/conformityAssessment/0/registeredId",
  },
  {
    from: "/options/additionalData/assessedFacility",
    to: "/credentialSubject/conformityAssessment/0/assessedFacility",
  },
  {
    from: "/options/additionalData/assessedProduct",
    to: "/credentialSubject/conformityAssessment/0/assessedProduct",
  },
];

function buildTemplateRegistrationPayload(form) {
  return {
    type: form.type.value.trim(),
    version: form.version.value.trim(),
    issuer: form.issuer.value.trim(),
    subjectType: TEMPLATE_DCC_PRESET.subjectType,
    additionalType: TEMPLATE_DCC_PRESET.additionalType,
    corePaths: { ...TEMPLATE_DCC_PRESET.corePaths },
    subjectPaths: { ...TEMPLATE_DCC_PRESET.subjectPaths },
    additionalPaths: { ...TEMPLATE_DCC_PRESET.additionalPaths },
    relatedResources: {
      context: deriveContextUrl(form.type.value, form.version.value),
      legalAct: form.legalAct.value.trim(),
      governance: form.governance.value.trim(),
    },
  };
}

function renderTemplatePresetReview() {
  const list = document.getElementById("template-review-paths");
  if (!list) return;
  list.innerHTML = TEMPLATE_OPTIONS_MAPPINGS.map(
    ({ from, to }) =>
      `<li><code>${escapeHtml(from)}</code> → <code>${escapeHtml(to)}</code></li>`
  ).join("");
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

function openApiKeyModal() {
  const modal = document.getElementById("api-key-modal");
  const input = document.getElementById("api-key-input");
  const toggle = document.getElementById("api-key-toggle");
  if (!modal || !input) return;
  hideModalAlert("api-key-modal-alert");
  input.value = "";
  input.type = "password";
  if (toggle) {
    toggle.textContent = "Show";
    toggle.setAttribute("aria-label", "Show API key");
    toggle.setAttribute("title", "Show API key");
  }
  const hasKey = Boolean(getApiKey());
  input.placeholder = hasKey
    ? "Key saved — paste a new value to replace"
    : "Paste your TRACTION_API_KEY";
  document.getElementById("api-key-clear")?.classList.toggle("d-none", !hasKey);
  modal.classList.remove("d-none");
  window.setTimeout(() => input.focus(), 50);
}

function closeApiKeyModal() {
  const modal = document.getElementById("api-key-modal");
  if (modal) modal.classList.add("d-none");
  const input = document.getElementById("api-key-input");
  if (input) {
    input.value = "";
    input.type = "password";
  }
}

function toggleApiKeyVisibility() {
  const input = document.getElementById("api-key-input");
  const toggle = document.getElementById("api-key-toggle");
  if (!input || !toggle) return;
  const show = input.type === "password";
  input.type = show ? "text" : "password";
  toggle.textContent = show ? "Hide" : "Show";
  toggle.setAttribute("aria-label", show ? "Hide API key" : "Show API key");
  toggle.setAttribute("title", show ? "Hide API key" : "Show API key");
}

function submitApiKeyForm(event) {
  event.preventDefault();
  hideModalAlert("api-key-modal-alert");
  const key = document.getElementById("api-key-input")?.value.trim() || "";
  if (!key) {
    showModalAlert("api-key-modal-alert", "API key is required.");
    return;
  }
  setApiKey(key);
  closeApiKeyModal();
  const helpOpen = !document.getElementById("getting-started-modal")?.classList.contains("d-none");
  loadCollections({ navigateHome: true })
    .then(() => {
      if (helpOpen) openGettingStarted();
    })
    .catch((err) => showAlert(err.message));
}

function handleClearApiKey() {
  clearApiKey();
  closeApiKeyModal();
  loadCollections({ navigateHome: true }).catch((err) => showAlert(err.message));
}

function updateApiStatus() {
  const el = document.getElementById("api-status");
  const key = getApiKey();
  el.textContent = key ? "API key set" : "No API key";
  el.classList.toggle("text-success", Boolean(key));
  if (!key) {
    el.style.color = "";
  }
  syncHomeBrowseHeader();
  if (Object.keys(state.collectionsByName).length) {
    renderOverviewDashboard();
  }
}

async function apiFetch(path) {
  const key = getApiKey();
  if (!key) {
    throw new Error("Set your X-API-Key first (TRACTION_API_KEY).");
  }
  const res = await fetch(path, {
    headers: { "X-API-Key": key },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body || res.statusText}`);
  }
  return res.json();
}

function showAlert(message) {
  hideSuccessAlert();
  const el = document.getElementById("alert");
  el.textContent = message;
  el.classList.remove("d-none");
}

function showSuccessAlert(message) {
  hideAlert();
  const el = document.getElementById("alert-success");
  el.textContent = message;
  el.classList.remove("d-none");
}

function hideAlert() {
  document.getElementById("alert").classList.add("d-none");
}

function hideSuccessAlert() {
  document.getElementById("alert-success").classList.add("d-none");
}

function hideAlerts() {
  hideAlert();
  hideSuccessAlert();
}

function showModalAlert(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.classList.remove("d-none");
}

function hideModalAlert(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add("d-none");
  el.textContent = "";
}

async function apiPost(path, body) {
  const key = getApiKey();
  if (!key) {
    throw new Error("Set your X-API-Key first (TRACTION_API_KEY).");
  }
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "X-API-Key": key,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }
  if (!res.ok) {
    const detail =
      typeof payload?.detail === "string"
        ? payload.detail
        : JSON.stringify(payload?.detail ?? payload ?? text);
    throw new Error(`${res.status}: ${detail}`);
  }
  return payload;
}

function setPageTitle(title) {
  document.getElementById("page-title").textContent = title;
}

function setActiveNav(nav) {
  state.activeNav = nav;
  document.querySelectorAll(".bcgov-left-nav-link").forEach((el) => {
    el.classList.toggle("active", el.dataset.nav === nav);
  });
}

function showView(name) {
  for (const id of ["view-home", "view-list", "view-detail"]) {
    document.getElementById(id).classList.toggle("d-none", id !== `view-${name}`);
  }
}

function goHome() {
  hideAlerts();
  setActiveNav("home");
  setPageTitle("Overview");
  showView("home");
  if (Object.keys(state.collectionsByName).length || !getApiKey()) {
    const next = renderOverviewDashboard();
    const workflow = state.lastWorkflow || [];
    renderGettingStartedWizard(workflow, next);
  }
}

function renderSidebar(collections) {
  const container = document.getElementById("sidebar-collections");
  container.innerHTML = collections
    .map(
      (col) => `
    <a href="#" class="bcgov-left-nav-link" data-nav="collection:${escapeHtml(col.name)}">
      <span>${escapeHtml(col.title)}</span>
      <span class="bcgov-left-nav-link-meta">${col.count}</span>
    </a>`
    )
    .join("");

  container.querySelectorAll(".bcgov-left-nav-link").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const name = link.dataset.nav.replace("collection:", "");
      const meta = state.collectionsByName[name];
      if (meta) openCollection(name, meta);
    });
  });

  setActiveNav(state.activeNav);
}

function bindSidebarNav() {
  const goHomeHandler = (e) => {
    e.preventDefault();
    goHome();
  };
  document.getElementById("nav-home").addEventListener("click", goHomeHandler);
  document.getElementById("nav-home-brand").addEventListener("click", goHomeHandler);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function formatCell(value) {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function shortDid(value) {
  const str = String(value || "");
  if (!str.startsWith("did:")) return str;
  if (str.length <= 48) return str;
  return `${str.slice(0, 22)}…${str.slice(-18)}`;
}

function issuerInitial(nameOrId) {
  const source = String(nameOrId || "").trim();
  if (!source) return "🏛️";
  const chars = source
    .split(/[\s:-]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase())
    .filter(Boolean)
    .slice(0, 2)
    .join("");
  return chars || "🏛️";
}

function parseDidWebvh(did) {
  const value = String(did || "");
  if (!value.startsWith("did:webvh:")) return null;
  const parts = value.split(":");
  if (parts.length < 6) return null;
  return {
    scid: parts[2],
    domain: parts[3],
  };
}

function issuerExplorerUrl(did) {
  const parsed = parseDidWebvh(did);
  if (!parsed?.scid || !parsed?.domain) return null;
  return `https://${parsed.domain}/api/explorer/dids?scid=${encodeURIComponent(parsed.scid)}`;
}

function issuerRowForDisplay(row) {
  const id = String(row?.id || "");
  const parts = id.split(":");
  const scid = parts[2] || "";
  const scope = parts[4] || "";
  return {
    ...row,
    scope,
    scid,
  };
}

function scopeFromIssuerDid(issuerDid) {
  const id = String(issuerDid || "");
  const parts = id.split(":");
  return parts[4] || "";
}

function bestActMatchFromScope(acts, scope) {
  const list = Array.isArray(acts) ? acts : [];
  if (!list.length) return null;
  const term = String(scope || "").trim().toLowerCase();
  if (!term) return list[0];
  const exact =
    list.find((act) => String(act?.name || "").trim().toLowerCase() === term) ||
    list.find((act) => String(act?.title || "").trim().toLowerCase() === term);
  if (exact) return exact;
  const starts =
    list.find((act) => String(act?.name || "").toLowerCase().startsWith(term)) ||
    list.find((act) => String(act?.title || "").toLowerCase().startsWith(term));
  if (starts) return starts;
  return list[0];
}

async function deriveLegalActForIssuer(issuerDid, fallbackUrl = "") {
  const scope = scopeFromIssuerDid(issuerDid);
  if (!scope) return { legalActUrl: fallbackUrl, matched: false };
  try {
    const response = await apiFetch(
      `/bclaws/acts?q=${encodeURIComponent(scope)}&limit=20&offset=0`
    );
    const match = bestActMatchFromScope(response?.acts || [], scope);
    const legalActUrl = match?.id || fallbackUrl;
    return { legalActUrl, matched: Boolean(match) };
  } catch {
    return { legalActUrl: fallbackUrl, matched: false };
  }
}

function deriveContextUrl(type, version) {
  const fallback =
    "https://bcgov.github.io/digital-trust-toolkit/contexts/BCPetroleumAndNaturalGasTitle/v1.jsonld";
  const cleanType = String(type || "").trim();
  const cleanVersion = String(version || "").trim();
  if (!cleanType || !cleanVersion) return fallback;
  const contextName = cleanType.replace(/Credential$/i, "") || cleanType;
  const major = (cleanVersion.match(/\d+/) || [null])[0];
  if (!major) return fallback;
  return `https://bcgov.github.io/digital-trust-toolkit/contexts/${encodeURIComponent(contextName)}/v${major}.jsonld`;
}

function renderIssuerSecretOnce(clientId, clientSecret) {
  const panel = document.getElementById("issuer-secret-once");
  if (!panel) return;
  if (!clientSecret) {
    panel.classList.add("d-none");
    panel.innerHTML = "";
    return;
  }
  panel.innerHTML = `
    <section class="issuer-secret-once-shell" role="status" aria-live="polite">
      <div class="issuer-secret-once-head">
        <span class="issuer-secret-once-icon" aria-hidden="true">🔑</span>
        <div>
          <h3 class="issuer-secret-once-title">Client secret generated once</h3>
          <p class="issuer-secret-once-subtitle">Store this now for <code>${escapeHtml(clientId)}</code>. It will not be shown again.</p>
        </div>
      </div>
      <div class="issuer-secret-once-row">
        <code class="issuer-secret-once-code" id="issuer-secret-once-code">${escapeHtml(clientSecret)}</code>
        <button type="button" class="btn btn-sm btn-outline-primary issuer-secret-copy-btn" id="issuer-secret-copy-btn" title="Copy to clipboard" aria-label="Copy client secret">
          📋
        </button>
      </div>
    </section>`;
  panel.classList.remove("d-none");

  const copyBtn = document.getElementById("issuer-secret-copy-btn");
  copyBtn?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(clientSecret);
      copyBtn.textContent = "✅";
      copyBtn.title = "Copied";
      setTimeout(() => {
        copyBtn.textContent = "📋";
        copyBtn.title = "Copy to clipboard";
      }, 1200);
    } catch {
      showAlert("Unable to copy automatically. Select and copy the secret manually.");
    }
  });
}

async function generateIssuerSecret(clientId) {
  const response = await apiPost("/auth/secret", { client_id: clientId });
  const secret = response?.client_secret;
  if (!secret) throw new Error("Secret was not returned by /auth/secret.");
  renderIssuerSecretOnce(clientId, secret);
}

function renderIssuerFunPanel(data) {
  const panel = document.getElementById("issuer-fun-panel");
  if (!panel) return;
  if (state.collection !== "IssuerRecord") {
    panel.classList.add("d-none");
    panel.innerHTML = "";
    return;
  }

  const items = (Array.isArray(data?.items) ? data.items : []).map(issuerRowForDisplay);
  const total = Number(data?.total ?? items.length);
  const keyCount = items.filter((row) => row?.authorized_key).length;
  const playfulTitle =
    total >= 3
      ? "Issuer hall of fame"
      : total === 2
        ? "Issuer duo in flight"
        : total === 1
          ? "First issuer onboard"
          : "Ready to launch an issuer";
  const subtitle =
    total > 0
      ? `${total} issuer${total === 1 ? "" : "s"} registered · ${keyCount} signing key${keyCount === 1 ? "" : "s"} linked`
      : "Register your first issuer to light up this panel.";

  const cards = items
    .slice(0, 6)
    .map((row) => {
      const id = row?.id || "—";
      const name = row?.name || "Unnamed issuer";
      const hasKey = Boolean(row?.authorized_key);
      return `
        <article class="issuer-fun-card" title="${escapeHtml(String(id))}">
          <div class="issuer-fun-avatar" aria-hidden="true">${escapeHtml(issuerInitial(name))}</div>
          <div class="issuer-fun-content">
            <div class="issuer-fun-name">${escapeHtml(String(name))}</div>
            <div class="issuer-fun-id">${escapeHtml(shortDid(id))}</div>
          </div>
          <span class="badge ${hasKey ? "bg-success-lt text-success" : "bg-yellow-lt text-warning"} issuer-fun-badge">
            ${hasKey ? "Key bound" : "Needs key"}
          </span>
        </article>`;
    })
    .join("");

  panel.innerHTML = `
    <section class="issuer-fun-shell">
      <div class="issuer-fun-head">
        <h3 class="issuer-fun-title">${escapeHtml(playfulTitle)}</h3>
        <p class="issuer-fun-subtitle">${escapeHtml(subtitle)}</p>
      </div>
      <div class="issuer-fun-grid">${cards || '<p class="text-secondary small mb-0">No issuers yet.</p>'}</div>
    </section>`;
  panel.classList.remove("d-none");
}

const PHASE_ICONS = {
  Setup: "🔐",
  Configuration: "⚙️",
  Runtime: "📜",
  Operations: "📦",
  Other: "📁",
};

const COLLECTION_ICONS = {
  IssuerRecord: "🏛️",
  CredentialTypeRecord: "📋",
  StatusListRecord: "📊",
  CredentialRecord: "🪪",
  CredentialPickupRecord: "📬",
};

function stepBadge(step, optional) {
  if (optional) {
    return `<span class="badge bg-secondary-lt">Step ${step} · optional</span>`;
  }
  return `<span class="badge bg-primary-lt">Step ${step}</span>`;
}

function getSetupProgress() {
  const issuers = state.collectionsByName.IssuerRecord?.count ?? 0;
  const types = state.collectionsByName.CredentialTypeRecord?.count ?? 0;
  const credentials = state.collectionsByName.CredentialRecord?.count ?? 0;
  return {
    hasKey: Boolean(getApiKey()),
    issuers,
    types,
    credentials,
  };
}

function computeNextAction() {
  const { hasKey, issuers, types, credentials } = getSetupProgress();

  if (!hasKey) {
    return {
      workflowStep: 0,
      complete: false,
      title: "Set your API key",
      focusCollection: null,
    };
  }
  if (issuers === 0) {
    return {
      workflowStep: 1,
      complete: false,
      title: "Register Issuer",
      focusCollection: null,
    };
  }
  if (types === 0) {
    return {
      workflowStep: 2,
      complete: false,
      title: "Create Template",
      focusCollection: "IssuerRecord",
    };
  }
  if (credentials === 0) {
    return {
      workflowStep: 3,
      complete: false,
      title: "Issue Credentials",
      focusCollection: "CredentialTypeRecord",
    };
  }
  return {
    workflowStep: 4,
    complete: true,
    title: "Monitor and operate",
    focusCollection: "CredentialRecord",
  };
}

function executeOverviewAction(action) {
  if (!action) return;
  switch (action.action) {
    case "set-key":
      openApiKeyModal();
      break;
    case "register-issuer":
      openCreateIssuer();
      break;
    case "collection": {
      const meta = state.collectionsByName[action.collection];
      if (meta) openCollection(action.collection, meta);
      break;
    }
    case "docs":
      window.open("/docs", "_blank", "noopener");
      break;
    default:
      break;
  }
}

function currentMilestoneId(next) {
  if (next.complete) return -1;
  if (next.workflowStep === 0) return 0;
  if (next.workflowStep === 1) return 1;
  if (next.workflowStep === 2) return 2;
  if (next.workflowStep === 3) return 3;
  return -1;
}

function renderSetupProgress(next) {
  const container = document.getElementById("overview-progress");
  if (!container) return;

  const { hasKey, issuers, types, credentials } = getSetupProgress();
  const currentId = currentMilestoneId(next);
  const milestones = [
    { id: 0, label: "API key", done: hasKey },
    { id: 1, label: "Register Issuer", done: issuers > 0 },
    { id: 2, label: "Create Template", done: types > 0 },
    { id: 3, label: "Issue Credentials", done: credentials > 0 },
  ];

  container.innerHTML = milestones
    .map((m) => {
      let stateClass = "";
      if (m.done) stateClass = " overview-progress-step--done";
      else if (m.id === currentId) stateClass = " overview-progress-step--current";
      const dot = m.done ? "✓" : m.id + 1;
      return `
        <span class="overview-progress-step${stateClass}">
          <span class="overview-progress-dot" aria-hidden="true">${dot}</span>
          ${escapeHtml(m.label)}
        </span>`;
    })
    .join("");
}

function updateCollectionsSubtitle() {
  const subtitle = document.getElementById("collections-subtitle");
  if (!subtitle) return;
  subtitle.textContent = "Open a collection to browse and verify records.";
}

function syncHomeBrowseHeader() {
  const head = document.querySelector("#view-home .overview-section-head");
  if (!head) return;
  head.classList.toggle("d-none", !getApiKey());
}

function renderApiKeyGate() {
  const container = document.getElementById("collection-sections");
  if (!container) return;
  container.innerHTML = `
    <section class="admin-api-gate card" aria-labelledby="api-gate-heading">
      <div class="card-body text-center px-4 py-5">
        <div class="admin-api-gate-icon" aria-hidden="true">🔐</div>
        <h3 class="admin-api-gate-title" id="api-gate-heading">Connect to UNTP Publisher</h3>
        <p class="admin-api-gate-text text-secondary mb-4">
          Set your <code>TRACTION_API_KEY</code> to load collections, register issuers, and create templates.
        </p>
        <div class="d-flex flex-wrap justify-content-center gap-2">
          <button type="button" class="btn btn-bcgov-gold" id="api-gate-set-key">Set API key</button>
          <button type="button" class="btn btn-outline-primary" id="api-gate-open-help">Getting started</button>
        </div>
      </div>
    </section>`;
  document.getElementById("api-gate-set-key")?.addEventListener("click", openApiKeyModal);
  document.getElementById("api-gate-open-help")?.addEventListener("click", () => {
    openGettingStarted();
  });
  syncHomeBrowseHeader();
}

function renderOverviewDashboard() {
  const next = computeNextAction();
  renderSetupProgress(next);
  syncHomeBrowseHeader();
  updateCollectionsSubtitle();
  const workflowSubtitle = document.getElementById("workflow-subtitle");
  if (workflowSubtitle) {
    workflowSubtitle.textContent = next.complete
      ? "Setup is complete. You can repeat any step or browse collections below."
      : "The current step is highlighted. Complete each action before moving on.";
  }
  return next;
}

const WORKFLOW_STEP_ACTIONS = {
  1: { label: "Register Issuer", action: "register-issuer" },
  2: { label: "Create Template (API)", action: "docs" },
  3: { label: "View credential types", action: "collection", collection: "CredentialTypeRecord" },
  4: { label: "View pickup queue", action: "collection", collection: "CredentialPickupRecord" },
};

const GETTING_STARTED_STEPS_FALLBACK = [
  { step: 1, title: "Register Issuer", summary: "", api: null },
  { step: 2, title: "Create Template", summary: "", api: null },
  { step: 3, title: "Issue Credentials", summary: "", api: null },
];

function workflowStepState(item, next) {
  if (next.complete) return item.optional ? "optional" : "done";
  if (next.workflowStep === 1) {
    return item.step === 1 ? "current" : "upcoming";
  }
  if (next.workflowStep === 2) {
    if (item.step === 1) return "done";
    if (item.step === 2) return "current";
    return "upcoming";
  }
  if (next.workflowStep === 3) {
    if (item.step <= 2) return "done";
    if (item.step === 3) return "current";
    return "upcoming";
  }
  return "upcoming";
}

function renderWorkflow(workflow, next, containerId = "workflow-steps") {
  const container = document.getElementById(containerId);
  if (!container) return;
  const items = workflow;
  container.innerHTML = items
    .map((item) => {
      const stepState = workflowStepState(item, next);
      let stateClass = "";
      if (stepState === "current") stateClass = " workflow-step-card--current";
      if (stepState === "done") stateClass = " workflow-step-card--done";
      const optionalClass = item.optional ? " workflow-step-card--optional" : "";

      const markerContent = stepState === "done" ? "✓" : item.step;
      const api = item.api
        ? `<div class="workflow-step-card-api"><code>${escapeHtml(item.api)}</code></div>`
        : "";

      const stepAction = WORKFLOW_STEP_ACTIONS[item.step];
      let primaryBtn = "";
      if (stepAction && stepState === "current") {
        const collectionAttr = stepAction.collection
          ? ` data-collection="${escapeHtml(stepAction.collection)}"`
          : "";
        primaryBtn = `<button type="button" class="btn btn-sm btn-primary workflow-step-primary-action" data-action="${escapeHtml(stepAction.action)}"${collectionAttr}>${escapeHtml(stepAction.label)}</button>`;
      }

      const optionalBadge = item.optional
        ? '<span class="badge bg-secondary-lt ms-1">Optional</span>'
        : "";
      const statusBadge =
        stepState === "current"
          ? '<span class="badge bg-yellow-lt ms-1">Do this now</span>'
          : stepState === "done"
            ? '<span class="badge bg-success-lt ms-1">Done</span>'
            : "";

      return `
        <article class="workflow-step-card${optionalClass}${stateClass}">
          <div class="workflow-step-card-header">
            <div class="workflow-step-marker" aria-hidden="true">${markerContent}</div>
            <h3 class="workflow-step-card-title">${escapeHtml(item.title)}${optionalBadge}${statusBadge}</h3>
          </div>
          <p class="workflow-step-card-summary">${escapeHtml(item.summary)}</p>
          ${api}
          ${item.note ? `<p class="workflow-step-card-summary mb-2"><em>${escapeHtml(item.note)}</em></p>` : ""}
          <div class="workflow-step-card-actions">${primaryBtn}</div>
        </article>`;
    })
    .join("");

  container.querySelectorAll(".workflow-step-primary-action").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = { action: btn.dataset.action };
      if (btn.dataset.collection) action.collection = btn.dataset.collection;
      executeOverviewAction(action);
    });
  });
}

function workflowWizardItems(workflow) {
  const items = [];
  for (const item of workflow || []) {
    if (item?.optional) continue;
    if (item?.step >= 1 && item?.step <= 3) {
      items.push(item);
    }
  }
  return items.length > 0 ? items : GETTING_STARTED_STEPS_FALLBACK;
}

function workflowWizardState(step, next) {
  if (!getApiKey()) return "upcoming";
  if (next.complete) return "done";
  if (next.workflowStep === 0) return "upcoming";
  if (step < next.workflowStep) return "done";
  if (step === next.workflowStep) return "current";
  return "upcoming";
}

function bindWorkflowPrimaryActions(root) {
  root?.querySelectorAll(".workflow-step-primary-action").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = { action: btn.dataset.action };
      if (btn.dataset.collection) action.collection = btn.dataset.collection;
      executeOverviewAction(action);
    });
  });
}

function renderGettingStartedPrerequisite() {
  const prereq = document.getElementById("getting-started-prerequisite");
  if (!prereq) return;
  prereq.classList.remove("d-none");
  prereq.innerHTML = `
    <p class="getting-started-prerequisite-text mb-2">
      <strong>Prerequisite.</strong>
      Set your <code>TRACTION_API_KEY</code> so this dashboard can load data and run admin actions.
    </p>
    <button type="button" class="btn btn-sm btn-bcgov-gold workflow-step-primary-action" data-action="set-key">Set API key</button>`;
  bindWorkflowPrimaryActions(prereq);
}

function renderGettingStartedWizard(workflow, next) {
  const stepper = document.getElementById("getting-started-stepper");
  const counter = document.getElementById("getting-started-counter");
  const current = document.getElementById("getting-started-current");
  const prereq = document.getElementById("getting-started-prerequisite");
  if (!stepper || !counter || !current) return;

  const items = workflowWizardItems(workflow);
  stepper.classList.toggle("getting-started-stepper--locked", !getApiKey());

  if (!getApiKey()) {
    renderGettingStartedPrerequisite();
    counter.textContent = "Prerequisite";
    stepper.innerHTML = items
      .map((item) => {
        const cls = "getting-started-step getting-started-step--upcoming";
        return `
          <div class="${cls}">
            <span class="getting-started-step-dot" aria-hidden="true">${item.step}</span>
            <span class="getting-started-step-label">${escapeHtml(item.title)}</span>
          </div>`;
      })
      .join("");
    current.innerHTML = `
      <p class="text-secondary small mb-0">
        Complete the prerequisite above, then work through the three setup steps.
      </p>`;
    return;
  }

  prereq?.classList.add("d-none");
  prereq.innerHTML = "";

  const currentIndex = next.complete
    ? items.length - 1
    : next.workflowStep === 0
      ? 0
      : Math.max(0, items.findIndex((item) => item.step === next.workflowStep));
  counter.textContent = `Step ${currentIndex + 1} of ${items.length}`;

  stepper.innerHTML = items
    .map((item) => {
      const state = workflowWizardState(item.step, next);
      const cls = `getting-started-step getting-started-step--${state}`;
      const marker = state === "done" ? "✓" : String(item.step);
      return `
        <div class="${cls}">
          <span class="getting-started-step-dot" aria-hidden="true">${marker}</span>
          <span class="getting-started-step-label">${escapeHtml(item.title)}</span>
        </div>`;
    })
    .join("");

  const activeItem = next.complete
    ? items[items.length - 1]
    : next.workflowStep === 0
      ? items[0]
      : items.find((item) => item.step === next.workflowStep) || items[0];
  const stepAction = WORKFLOW_STEP_ACTIONS[activeItem.step];
  const collectionAttr = stepAction?.collection
    ? ` data-collection="${escapeHtml(stepAction.collection)}"`
    : "";
  const actionBtn =
    !next.complete && next.workflowStep !== 0 && stepAction
      ? `<button type="button" class="btn btn-sm btn-primary workflow-step-primary-action" data-action="${escapeHtml(stepAction.action)}"${collectionAttr}>${escapeHtml(stepAction.label)}</button>`
      : "";
  const api = activeItem.api
    ? `<div class="workflow-step-card-api"><code>${escapeHtml(activeItem.api)}</code></div>`
    : "";
  const doneBadge = next.complete
    ? '<span class="badge bg-success-lt ms-1">Done</span>'
    : '<span class="badge bg-yellow-lt ms-1">Current</span>';
  const marker = next.complete ? "✓" : String(activeItem.step);

  current.innerHTML = `
    <article class="workflow-step-card workflow-step-card--current">
      <div class="workflow-step-card-header">
        <div class="workflow-step-marker" aria-hidden="true">${marker}</div>
        <h3 class="workflow-step-card-title">${escapeHtml(activeItem.title)}${doneBadge}</h3>
      </div>
      <p class="workflow-step-card-summary">${escapeHtml(activeItem.summary || "")}</p>
      ${api}
      <div class="workflow-step-card-actions">${actionBtn}</div>
    </article>`;

  bindWorkflowPrimaryActions(current);
}

function openGettingStarted() {
  const modal = document.getElementById("getting-started-modal");
  if (!modal) return;
  const next = computeNextAction();
  const workflow = state.lastWorkflow || [];
  renderGettingStartedWizard(workflow, next);
  modal.classList.remove("d-none");
}

function closeGettingStarted() {
  const modal = document.getElementById("getting-started-modal");
  if (!modal) return;
  modal.classList.add("d-none");
}

function groupCollectionsByPhase(collections) {
  const phases = [];
  const seen = new Set();
  for (const col of collections) {
    const phase = col.phase || "Other";
    if (!seen.has(phase)) {
      seen.add(phase);
      phases.push({ phase, items: [] });
    }
    phases.find((p) => p.phase === phase).items.push(col);
  }
  return phases;
}

function renderCollectionCard(col, { focus = false } = {}) {
  const flags = [];
  if (col.auto_created) flags.push('<span class="badge bg-yellow-lt">Auto-created</span>');
  if (col.optional) flags.push('<span class="badge bg-secondary-lt">Optional</span>');
  if (col.also_creates?.length) {
    flags.push(
      `<span class="badge bg-azure-lt" title="Also creates: ${col.also_creates.join(", ")}">+ ${escapeHtml(col.also_creates.join(", "))}</span>`
    );
  }
  const icon = COLLECTION_ICONS[col.name] || "📁";
  const openLabel = col.count > 0 ? `Open ${col.count} record${col.count === 1 ? "" : "s"}` : "Open collection";

  return `
    <div class="col-md-6 col-lg-4">
      <div class="card collection-card h-100" data-collection="${escapeHtml(col.name)}" tabindex="0" role="button">
        <div class="card-body d-flex flex-column">
          <div class="collection-card-icon" aria-hidden="true">${icon}</div>
          <div class="d-flex flex-wrap align-items-center gap-2 mb-1">
            ${stepBadge(col.step, col.optional)}
          </div>
          <h3 class="card-title collection-card-title">${escapeHtml(col.title)}</h3>
          <p class="text-secondary small flex-grow-1 mb-2">${escapeHtml(col.description)}</p>
          <div class="d-flex flex-wrap gap-1 mb-2">${flags.join(" ")}</div>
          <button type="button" class="btn btn-sm btn-outline-primary collection-card-open" data-collection="${escapeHtml(col.name)}">
            ${escapeHtml(openLabel)} →
          </button>
        </div>
      </div>
    </div>`;
}

function renderContextPanel(meta, { showPrereqs = true, showNext = true } = {}) {
  if (!meta) return "";
  const description = (meta.description || "").trim();
  const createVia = (meta.create_via || "").trim();
  const prereqs =
    showPrereqs && meta.prerequisites?.length
      ? `<div class="mb-2"><span class="text-secondary">Before:</span><ul class="small mb-0 ps-3">${meta.prerequisites.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul></div>`
      : "";
  const next =
    showNext && meta.next_steps?.length
      ? `<div class="mb-0"><span class="text-secondary">Then:</span><ul class="small mb-0 ps-3">${meta.next_steps.map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul></div>`
      : "";
  return `
    <div class="card-body">
      <div class="d-flex flex-wrap align-items-center gap-2 mb-2">
        ${stepBadge(meta.step, meta.optional)}
        <span class="badge bg-secondary-lt">${escapeHtml(meta.phase)}</span>
        ${meta.auto_created ? '<span class="badge bg-yellow-lt">Auto-created</span>' : ""}
      </div>
      ${description ? `<p class="mb-2">${escapeHtml(description)}</p>` : ""}
      ${createVia ? `<p class="small mb-2"><span class="text-secondary">How records appear here:</span> <code>${escapeHtml(createVia)}</code></p>` : ""}
      ${prereqs}
      ${next}
    </div>`;
}

function updateListActions(meta) {
  const actions = document.getElementById("list-actions");
  actions.innerHTML = "";
  if (meta?.admin_create) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary btn-sm";
    btn.textContent = "Register issuer";
    btn.addEventListener("click", () => openCreateIssuer());
    actions.appendChild(btn);
  }
  if (meta?.name === "CredentialTypeRecord") {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-primary btn-sm";
    btn.textContent = "Create template";
    btn.addEventListener("click", () => openCreateTemplate());
    actions.appendChild(btn);
  }
}

function openCreateIssuer() {
  hideAlerts();
  hideModalAlert("issuer-wizard-alert");
  document.getElementById("issuer-create-form").reset();
  document.getElementById("issuer-result-card").classList.add("d-none");
  document.getElementById("issuer-submit").disabled = false;
  document.getElementById("issuer-submit").textContent = "Register issuer";
  clearScopeAutocomplete();
  document.getElementById("issuer-scope-legal-act-url").value = "";
  issuerWizardStep = 1;
  syncIssuerWizard();
  document.getElementById("issuer-wizard-modal").classList.remove("d-none");
}

function closeCreateIssuer() {
  document.getElementById("issuer-wizard-modal").classList.add("d-none");
}

async function openCreateTemplate() {
  hideAlerts();
  hideModalAlert("template-wizard-alert");
  const modal = document.getElementById("template-wizard-modal");
  const form = document.getElementById("template-create-form");
  form.reset();
  document.getElementById("template-type").value = "BCPetroleumAndNaturalGasTitleCredential";
  document.getElementById("template-version").value = "v1.0";
  document.getElementById("template-legal-act").value =
    "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/00_96361_01";
  document.getElementById("template-governance").value =
    "https://bcgov.github.io/digital-trust-toolkit/docs/governance/pilots/bc-petroleum-and-natural-gas-title";

  const issuerSelect = document.getElementById("template-issuer");
  const legalActInput = document.getElementById("template-legal-act");
  const defaultLegalActUrl = legalActInput.value;
  issuerSelect.innerHTML = '<option value="">Loading issuers...</option>';
  const issuerData = await apiFetch("/admin/api/collections/IssuerRecord?skip=0&limit=200");
  const opts = (issuerData.items || [])
    .map(
      (row) =>
        `<option value="${escapeHtml(String(row.id))}">${escapeHtml(
          String(row.name || row.id)
        )}</option>`
    )
    .join("");
  issuerSelect.innerHTML = opts || '<option value="">No issuers found</option>';

  const applyIssuerScopeLegalAct = async () => {
    const selectedIssuer = issuerSelect.value;
    if (!selectedIssuer) return;
    const previous = legalActInput.value.trim();
    legalActInput.value = "Resolving from issuer scope…";
    legalActInput.disabled = true;
    const result = await deriveLegalActForIssuer(
      selectedIssuer,
      previous || defaultLegalActUrl
    );
    legalActInput.value = result.legalActUrl || defaultLegalActUrl;
    legalActInput.disabled = false;
  };

  issuerSelect.onchange = () => {
    applyIssuerScopeLegalAct().catch(() => {
      legalActInput.disabled = false;
      if (!legalActInput.value.trim()) legalActInput.value = defaultLegalActUrl;
    });
  };
  if (issuerSelect.value) {
    await applyIssuerScopeLegalAct();
  }

  templateWizardStep = 1;
  syncTemplateWizard();
  modal.classList.remove("d-none");
}

function closeCreateTemplate() {
  document.getElementById("template-wizard-modal").classList.add("d-none");
}

function syncTemplateWizard() {
  const stepLabel = document.getElementById("template-wizard-step-label");
  if (stepLabel) stepLabel.textContent = `Step ${templateWizardStep} of 3`;
  document.querySelectorAll(".template-wizard-step").forEach((el) => {
    const step = Number(el.getAttribute("data-step") || "0");
    el.classList.toggle("d-none", step !== templateWizardStep);
  });
  document.getElementById("template-wizard-back").classList.toggle("d-none", templateWizardStep === 1);
  document.getElementById("template-wizard-next").classList.toggle("d-none", templateWizardStep === 3);
  document.getElementById("template-submit").classList.toggle("d-none", templateWizardStep !== 3);

  if (templateWizardStep === 3) {
    const form = document.getElementById("template-create-form");
    document.getElementById("template-review-issuer").textContent = form.issuer.value || "—";
    document.getElementById("template-review-type").textContent = form.type.value || "—";
    document.getElementById("template-review-version").textContent = form.version.value || "—";
    renderTemplatePresetReview();
  }
}

function templateWizardNext() {
  const form = document.getElementById("template-create-form");
  hideModalAlert("template-wizard-alert");
  if (templateWizardStep === 1) {
    if (!form.issuer.value || !form.type.value.trim() || !form.version.value.trim()) {
      showModalAlert("template-wizard-alert", "Issuer, type, and version are required.");
      return;
    }
  }
  if (templateWizardStep === 2) {
    if (
      !form.legalAct.value.trim() ||
      !form.governance.value.trim()
    ) {
      showModalAlert(
        "template-wizard-alert",
        "Legal act and governance URLs are required."
      );
      return;
    }
  }
  templateWizardStep = Math.min(3, templateWizardStep + 1);
  syncTemplateWizard();
}

function templateWizardBack() {
  templateWizardStep = Math.max(1, templateWizardStep - 1);
  syncTemplateWizard();
}

async function submitTemplateRegistration(event) {
  event.preventDefault();
  hideModalAlert("template-wizard-alert");
  if (templateWizardStep < 3) {
    templateWizardNext();
    return;
  }
  hideAlerts();
  const form = event.target;
  const submitBtn = document.getElementById("template-submit");
  submitBtn.disabled = true;
  submitBtn.textContent = "Creating…";
  const payload = buildTemplateRegistrationPayload(form);
  try {
    await apiPost("/registrations/credentials", payload);
    showSuccessAlert(`Template created: ${payload.type} ${payload.version}`);
    closeCreateTemplate();
    const meta = state.collectionsByName.CredentialTypeRecord;
    if (meta) await openCollection("CredentialTypeRecord", meta);
    await loadCollections();
  } catch (err) {
    showModalAlert("template-wizard-alert", err.message || "Failed to create template.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Create template";
  }
}

function syncIssuerWizard() {
  const stepLabel = document.getElementById("issuer-wizard-step-label");
  if (stepLabel) stepLabel.textContent = `Step ${issuerWizardStep} of 3`;
  document.querySelectorAll(".issuer-wizard-step").forEach((el) => {
    const step = Number(el.getAttribute("data-step") || "0");
    el.classList.toggle("d-none", step !== issuerWizardStep);
  });
  document.getElementById("issuer-wizard-back").classList.toggle("d-none", issuerWizardStep === 1);
  document.getElementById("issuer-wizard-next").classList.toggle("d-none", issuerWizardStep === 3);
  document.getElementById("issuer-submit").classList.toggle("d-none", issuerWizardStep !== 3);
  if (issuerWizardStep === 3) {
    const form = document.getElementById("issuer-create-form");
    document.getElementById("issuer-review-name").textContent = form.name.value.trim() || "—";
    document.getElementById("issuer-review-scope").textContent = form.scope.value.trim() || "—";
    document.getElementById("issuer-review-description").textContent =
      form.description.value.trim() || "—";
    document.getElementById("issuer-review-multikey").textContent =
      form.multikey.value.trim() || "None";
  }
}

function issuerWizardNext() {
  const form = document.getElementById("issuer-create-form");
  hideModalAlert("issuer-wizard-alert");
  if (issuerWizardStep === 1) {
    if (!form.name.value.trim() || !form.scope.value.trim()) {
      showModalAlert("issuer-wizard-alert", "Name and scope are required.");
      return;
    }
  }
  if (issuerWizardStep === 2) {
    if (!form.description.value.trim()) {
      showModalAlert("issuer-wizard-alert", "Description is required.");
      return;
    }
  }
  issuerWizardStep = Math.min(3, issuerWizardStep + 1);
  syncIssuerWizard();
}

function clearScopeAutocomplete() {
  const results = document.getElementById("issuer-scope-results");
  if (!results) return;
  results.innerHTML = "";
  results.classList.add("d-none");
}

function clearNameAutocomplete() {
  const results = document.getElementById("issuer-name-results");
  if (!results) return;
  results.innerHTML = "";
  results.classList.add("d-none");
}

function buildIssuerDescriptionPrefill() {
  const form = document.getElementById("issuer-create-form");
  if (!form) return "";
  const name = form.name.value.trim();
  const scope = form.scope.value.trim();
  if (!name || !scope) return "";
  return `${name} as defined by the ${scope}.`;
}

function updateIssuerDescriptionPrefill() {
  const form = document.getElementById("issuer-create-form");
  if (!form) return;
  const descriptionInput = form.description;
  const candidate = buildIssuerDescriptionPrefill();
  if (!candidate) return;
  const current = descriptionInput.value.trim();
  // Only auto-update when description is empty or still matches previous auto-prefill.
  if (!current || current === issuerDescriptionLastPrefill) {
    descriptionInput.value = candidate;
    issuerDescriptionLastPrefill = candidate;
  }
}

function selectScopeAct(act) {
  const scopeInput = document.getElementById("issuer-scope");
  const legalActInput = document.getElementById("issuer-scope-legal-act-url");
  scopeInput.value = act?.name || act?.title || "";
  if (legalActInput) legalActInput.value = act?.id || "";
  updateIssuerDescriptionPrefill();
  clearScopeAutocomplete();
}

function renderScopeAutocomplete(acts) {
  const results = document.getElementById("issuer-scope-results");
  if (!results) return;
  if (!acts.length) {
    results.innerHTML =
      '<button type="button" class="issuer-scope-item" disabled>No matching acts found</button>';
    results.classList.remove("d-none");
    return;
  }
  results.innerHTML = acts
    .map(
      (act, index) => `
      <button
        type="button"
        class="issuer-scope-item"
        data-index="${index}"
        title="${escapeHtml(String(act.title || act.name || ""))}"
      >
        <span class="issuer-scope-item-name">${escapeHtml(String(act.name || act.title || ""))}</span>
        <span class="issuer-scope-item-meta">${escapeHtml(String(act.status || "Active"))}</span>
      </button>`
    )
    .join("");
  results.classList.remove("d-none");
  results.querySelectorAll(".issuer-scope-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.index);
      if (Number.isInteger(idx) && acts[idx]) selectScopeAct(acts[idx]);
    });
  });
}

function selectNameRole(role) {
  const nameInput = document.getElementById("issuer-name");
  if (!nameInput) return;
  // Use role title as issuer display name, falling back safely.
  nameInput.value = role?.title || role?.name || "";
  updateIssuerDescriptionPrefill();
  clearNameAutocomplete();
}

function renderNameAutocomplete(roles) {
  const results = document.getElementById("issuer-name-results");
  if (!results) return;
  if (!roles.length) {
    results.innerHTML =
      '<button type="button" class="issuer-scope-item" disabled>No matching roles found</button>';
    results.classList.remove("d-none");
    return;
  }
  results.innerHTML = roles
    .map(
      (role, index) => `
      <button
        type="button"
        class="issuer-scope-item"
        data-index="${index}"
        title="${escapeHtml(String(role.title || ""))}"
      >
        <span class="issuer-scope-item-name">${escapeHtml(String(role.title || role.name || ""))}</span>
        <span class="issuer-scope-item-meta">${escapeHtml(
          [role.organizationalUnit, role.orgCode].filter(Boolean).join(" • ") || "BC Directory"
        )}</span>
      </button>`
    )
    .join("");
  results.classList.remove("d-none");
  results.querySelectorAll(".issuer-scope-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.dataset.index);
      if (Number.isInteger(idx) && roles[idx]) selectNameRole(roles[idx]);
    });
  });
}

async function searchScopeActs(query) {
  const trimmed = query.trim();
  if (trimmed.length < 2) {
    clearScopeAutocomplete();
    return;
  }
  scopeSearchRequestId += 1;
  const requestId = scopeSearchRequestId;
  try {
    const response = await apiFetch(
      `/bclaws/acts?q=${encodeURIComponent(trimmed)}&limit=12&offset=0`
    );
    if (requestId !== scopeSearchRequestId) return;
    renderScopeAutocomplete(response?.acts || []);
  } catch {
    if (requestId !== scopeSearchRequestId) return;
    clearScopeAutocomplete();
  }
}

async function searchNameRoles(query) {
  const trimmed = query.trim();
  if (trimmed.length < 2) {
    clearNameAutocomplete();
    return;
  }
  roleSearchRequestId += 1;
  const requestId = roleSearchRequestId;
  try {
    const response = await apiFetch(
      `/bclaws/roles?q=${encodeURIComponent(trimmed)}&limit=12`
    );
    if (requestId !== roleSearchRequestId) return;
    renderNameAutocomplete(response?.roles || []);
  } catch {
    if (requestId !== roleSearchRequestId) return;
    clearNameAutocomplete();
  }
}

function issuerWizardBack() {
  issuerWizardStep = Math.max(1, issuerWizardStep - 1);
  syncIssuerWizard();
}

function normalizeIssuerRegisterResponse(result, fallbackName) {
  const didDocument = result.did_document ?? (result.id ? result : null);
  const issuerFromPayload = result.issuer;
  const issuerId =
    issuerFromPayload?.id ??
    didDocument?.id ??
    didDocument?.["@id"] ??
    result.id;
  if (!issuerId) {
    throw new Error(
      "Registration response did not include an issuer DID. Check server logs and refresh the page."
    );
  }
  return {
    issuer: {
      id: issuerId,
      name: issuerFromPayload?.name ?? fallbackName,
      authorized_key: issuerFromPayload?.authorized_key,
    },
    did_document: didDocument ?? result,
  };
}

async function submitIssuerRegistration(event) {
  event.preventDefault();
  hideModalAlert("issuer-wizard-alert");
  if (issuerWizardStep < 3) {
    issuerWizardNext();
    return;
  }
  hideAlerts();
  const form = event.target;
  const body = {
    name: form.name.value.trim(),
    scope: form.scope.value.trim(),
    description: form.description.value.trim(),
  };
  const multikey = form.multikey.value.trim();
  if (multikey) body.multikey = multikey;

  const submitBtn = document.getElementById("issuer-submit");
  submitBtn.disabled = true;
  submitBtn.textContent = "Registering…";

  try {
    const raw = await apiPost("/admin/api/issuers", body);
    const result = normalizeIssuerRegisterResponse(raw, body.name);
    document.getElementById("issuer-result-id").textContent = result.issuer.id;
    document.getElementById("issuer-result-json").textContent = JSON.stringify(
      result.did_document,
      null,
      2
    );
    document.getElementById("issuer-result-card").classList.remove("d-none");
    document.getElementById("issuer-view-record").onclick = () => {
      const meta = state.collectionsByName.IssuerRecord;
      closeCreateIssuer();
      openCollection("IssuerRecord", meta).then(() => openRecord(result.issuer.id));
    };
    showSuccessAlert(`Issuer registered: ${result.issuer.id}`);
    await loadCollections();
  } catch (err) {
    showModalAlert("issuer-wizard-alert", err.message || "Failed to register issuer.");
    submitBtn.disabled = false;
  } finally {
    submitBtn.textContent = "Register issuer";
  }
}

async function loadCollections({ navigateHome = false } = {}) {
  hideAlerts();

  if (!getApiKey()) {
    state.collectionsByName = {};
    state.lastWorkflow = [];
    const next = renderOverviewDashboard();
    renderGettingStartedWizard([], next);
    renderSidebar([]);
    renderApiKeyGate();
    if (navigateHome) goHome();
    else setActiveNav(state.activeNav);
    return;
  }

  const data = await apiFetch("/admin/api/collections");
  state.collectionsByName = Object.fromEntries(data.collections.map((c) => [c.name, c]));
  state.lastWorkflow = data.workflow;

  const next = renderOverviewDashboard();
  renderGettingStartedWizard(data.workflow, next);
  setPageTitle("Overview");

  const container = document.getElementById("collection-sections");
  container.innerHTML = "";
  for (const { phase, items } of groupCollectionsByPhase(data.collections)) {
    const phaseIcon = PHASE_ICONS[phase] || PHASE_ICONS.Other;
    const phaseTotal = items.reduce((n, c) => n + (c.count || 0), 0);
    const section = document.createElement("section");
    section.className = "overview-phase";
    section.innerHTML = `
      <div class="overview-phase-header">
        <span class="overview-phase-icon" aria-hidden="true">${phaseIcon}</span>
        <h3 class="overview-phase-title">${escapeHtml(phase)}</h3>
        <span class="overview-phase-count">${phaseTotal} records · ${items.length} collections</span>
      </div>
      <div class="row row-cards g-3"></div>`;
    const row = section.querySelector(".row");
    for (const col of items) {
      const wrap = document.createElement("div");
      wrap.innerHTML = renderCollectionCard(col);
      const card = wrap.firstElementChild;
      const open = () => openCollection(col.name, col);
      card.querySelector(".collection-card").addEventListener("click", (e) => {
        if (e.target.closest(".collection-card-open")) return;
        open();
      });
      card.querySelector(".collection-card").addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      });
      card.querySelector(".collection-card-open").addEventListener("click", (e) => {
        e.stopPropagation();
        open();
      });
      row.appendChild(card);
    }
    container.appendChild(section);
  }
  renderSidebar(data.collections);
  if (navigateHome) {
    goHome();
  } else {
    setActiveNav(state.activeNav);
  }
}

async function openCollection(name, meta, { search = "" } = {}) {
  state.collection = name;
  state.collectionMeta = meta || state.collectionsByName[name];
  state.skip = 0;
  state.search = search;
  state.listColumns = state.collectionMeta.list_columns;
  state.idField = state.collectionMeta.id_field;

  document.getElementById("list-title").textContent = state.collectionMeta.title;
  updateListActions(state.collectionMeta);
  document.getElementById("search-input").value = search;
  setActiveNav(`collection:${name}`);
  setPageTitle(state.collectionMeta.title);
  await loadRecords();
  renderIssuerSecretOnce("", "");
  showView("list");
}

async function loadRecords() {
  hideAlerts();
  const params = new URLSearchParams({
    skip: String(state.skip),
    limit: String(PAGE_SIZE),
  });
  if (state.search) params.set("q", state.search);

  const data = await apiFetch(
    `/admin/api/collections/${encodeURIComponent(state.collection)}?${params}`
  );
  state.collectionMeta = { ...state.collectionMeta, ...data };
  renderIssuerFunPanel(data);

  document.getElementById("list-total").textContent = `${data.total} total`;

  const thead = document.getElementById("records-thead");
  const tbody = document.getElementById("records-tbody");
  const showIssuerActions = state.collection === "IssuerRecord";
  const showRowActions = true;
  const displayColumns = showIssuerActions
    ? ["scope", "name", "scid"]
    : data.list_columns;
  const headers = [...displayColumns.map((c) => `<th>${escapeHtml(c)}</th>`)];
  if (showRowActions) headers.push('<th class="text-end">Actions</th>');
  thead.innerHTML = `<tr>${headers.join("")}</tr>`;

  tbody.innerHTML = "";
  if (!data.items.length) {
    const hint = state.collectionMeta?.admin_create
      ? 'No issuers yet. Click <strong>Register issuer</strong> to create one.'
      : "No records yet — follow the setup steps above to create data via the API.";
    tbody.innerHTML = `<tr><td colspan="${displayColumns.length + (showRowActions ? 1 : 0)}" class="text-secondary text-center py-4">${hint}</td></tr>`;
  }
  const idField = data.id_field || state.idField || "id";
  for (const row of data.items) {
    const displayRow = showIssuerActions ? issuerRowForDisplay(row) : row;
    const tr = document.createElement("tr");
    const recordId = row[idField];
    if (recordId == null || recordId === "") {
      tr.classList.add("text-muted");
    }
    tr.dataset.href = recordId ?? "";
    const cells = displayColumns.map(
      (col) =>
        `<td class="text-truncate" style="max-width:280px">${escapeHtml(formatCell(displayRow[col]))}</td>`
    );
    if (showRowActions) {
      const explorerUrl = issuerExplorerUrl(recordId);
      cells.push(`
        <td class="text-end">
          <button
            type="button"
            class="btn btn-sm btn-outline-secondary row-inspect-btn"
            data-record-id="${escapeHtml(String(recordId ?? ""))}"
            title="Inspect raw JSON"
            aria-label="Inspect raw JSON"
            ${recordId == null || recordId === "" ? "disabled" : ""}
          >🔍</button>
          ${showIssuerActions ? `
          <button
            type="button"
            class="btn btn-sm btn-outline-secondary issuer-explorer-open-btn"
            data-explorer-url="${escapeHtml(String(explorerUrl || ""))}"
            title="Open DID in WebVH explorer"
            aria-label="Open DID in WebVH explorer"
          >↗️</button>
          <button
            type="button"
            class="btn btn-sm btn-outline-primary issuer-secret-generate-btn"
            data-client-id="${escapeHtml(String(recordId ?? ""))}"
            title="Generate client secret"
            aria-label="Generate client secret"
          >🔑</button>
          ` : ""}
        </td>`);
    }
    tr.innerHTML = cells.join("");
    if (recordId != null && recordId !== "") {
      tr.addEventListener("click", () => openRecord(recordId));
    }
    if (showRowActions) {
      const inspectBtn = tr.querySelector(".row-inspect-btn");
      inspectBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        if (recordId == null || recordId === "") return;
        openRecordPreview(
          state.collection,
          String(recordId),
          `${state.collectionMeta?.title || state.collection} / ${recordId}`
        );
      });
    }
    if (showIssuerActions) {
      const explorerBtn = tr.querySelector(".issuer-explorer-open-btn");
      explorerBtn?.addEventListener("click", (e) => {
        e.stopPropagation();
        const url = explorerBtn.dataset.explorerUrl;
        if (!url) {
          showAlert("No SCID found on this issuer DID.");
          return;
        }
        window.open(url, "_blank", "noopener");
      });

      const btn = tr.querySelector(".issuer-secret-generate-btn");
      btn?.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          btn.disabled = true;
          btn.textContent = "⏳";
          await generateIssuerSecret(String(recordId));
          btn.textContent = "✅";
          setTimeout(() => {
            btn.textContent = "🔑";
          }, 1200);
        } catch (err) {
          showAlert(err.message || "Unable to generate client secret.");
          btn.textContent = "🔑";
        } finally {
          btn.disabled = false;
        }
      });
    }
    tbody.appendChild(tr);
  }

  const page = Math.floor(state.skip / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  document.getElementById("page-info").textContent = `Page ${page} of ${totalPages}`;
  document.getElementById("btn-prev").disabled = state.skip <= 0;
  document.getElementById("btn-next").disabled = state.skip + PAGE_SIZE >= data.total;
}

let recordPreviewTarget = null;

async function openRecordPreview(collection, recordId, title) {
  const modal = document.getElementById("record-preview-modal");
  const pre = document.getElementById("record-preview-json");
  const titleEl = document.getElementById("record-preview-title");
  const subtitleEl = document.getElementById("record-preview-subtitle");
  if (!modal || !pre) return;

  recordPreviewTarget = { collection, recordId };
  const targetMeta = state.collectionsByName[collection];
  const collectionTitle = targetMeta?.title || collection;
  titleEl.textContent = title || `${collectionTitle} / ${recordId}`;
  if (subtitleEl) subtitleEl.textContent = collectionTitle;
  pre.textContent = "Loading…";
  modal.classList.remove("d-none");

  try {
    const data = await apiFetch(
      `/admin/api/collections/${encodeURIComponent(collection)}/records/${encodeURIComponent(recordId)}`
    );
    const record = data.record ?? data;
    pre.textContent = JSON.stringify(record, null, 2);
  } catch (err) {
    pre.textContent = err.message || "Failed to load record.";
  }
}

function closeRecordPreview() {
  const modal = document.getElementById("record-preview-modal");
  if (modal) modal.classList.add("d-none");
  recordPreviewTarget = null;
}

function openRecordPreviewFullPage() {
  if (!recordPreviewTarget) return;
  const { collection, recordId } = recordPreviewTarget;
  const meta = state.collectionsByName[collection];
  closeRecordPreview();
  if (meta) {
    openCollection(collection, meta).then(() => openRecord(recordId));
  }
}

function renderRelatedLinks(meta, record) {
  const card = document.getElementById("detail-related-card");
  const container = document.getElementById("detail-related");
  const links = meta.record_links || [];
  container.innerHTML = "";

  let any = false;
  for (const link of links) {
    const value = record[link.field];
    if (!value) continue;
    any = true;
    const targetMeta = state.collectionsByName[link.collection];
    const label = link.label || (targetMeta ? targetMeta.title : link.collection);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn-outline-primary btn-sm";
    btn.textContent = `${label}: ${value}`;
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (link.search) {
        openCollection(link.collection, targetMeta, { search: String(value) });
        return;
      }
      openRecordPreview(link.collection, String(value), `${label}: ${value}`);
    });
    container.appendChild(btn);
  }

  card.classList.toggle("d-none", !any);
}

async function openRecord(recordId) {
  hideAlerts();
  const data = await apiFetch(
    `/admin/api/collections/${encodeURIComponent(state.collection)}/records/${encodeURIComponent(recordId)}`
  );
  const record = data.record ?? data;
  const meta = { ...state.collectionMeta, ...data };

  document.getElementById("detail-title").textContent = `${meta.title} / ${recordId}`;
  document.getElementById("detail-context").innerHTML = renderContextPanel(meta, {
    showPrereqs: false,
    showNext: true,
  });
  document.getElementById("detail-json").textContent = JSON.stringify(record, null, 2);
  renderRelatedLinks(meta, record);
  setActiveNav(`collection:${state.collection}`);
  setPageTitle(`${meta.title} / ${recordId}`);
  showView("detail");
}

document.getElementById("btn-set-key").addEventListener("click", openApiKeyModal);
document.getElementById("api-key-form")?.addEventListener("submit", submitApiKeyForm);
document.getElementById("api-key-cancel")?.addEventListener("click", closeApiKeyModal);
document.getElementById("api-key-modal-close")?.addEventListener("click", closeApiKeyModal);
document.getElementById("api-key-modal-backdrop")?.addEventListener("click", closeApiKeyModal);
document.getElementById("api-key-toggle")?.addEventListener("click", toggleApiKeyVisibility);
document.getElementById("api-key-clear")?.addEventListener("click", handleClearApiKey);

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

document.getElementById("issuer-cancel").addEventListener("click", () => {
  closeCreateIssuer();
});

document.getElementById("issuer-create-form").addEventListener("submit", (e) => {
  submitIssuerRegistration(e).catch((err) => showAlert(err.message));
});

document.getElementById("issuer-wizard-next").addEventListener("click", issuerWizardNext);
document.getElementById("issuer-wizard-back").addEventListener("click", issuerWizardBack);
document.getElementById("issuer-wizard-close").addEventListener("click", closeCreateIssuer);
document.getElementById("issuer-wizard-backdrop").addEventListener("click", closeCreateIssuer);
document.getElementById("template-create-form").addEventListener("submit", (e) => {
  submitTemplateRegistration(e).catch((err) => showAlert(err.message));
});
document.getElementById("template-wizard-next").addEventListener("click", templateWizardNext);
document.getElementById("template-wizard-back").addEventListener("click", templateWizardBack);
document.getElementById("template-wizard-close").addEventListener("click", closeCreateTemplate);
document.getElementById("template-wizard-backdrop").addEventListener("click", closeCreateTemplate);
document.getElementById("template-cancel").addEventListener("click", closeCreateTemplate);
document.getElementById("btn-open-help")?.addEventListener("click", openGettingStarted);
document.getElementById("getting-started-close")?.addEventListener("click", closeGettingStarted);
document.getElementById("getting-started-backdrop")?.addEventListener("click", closeGettingStarted);
document.getElementById("record-preview-close")?.addEventListener("click", closeRecordPreview);
document.getElementById("record-preview-backdrop")?.addEventListener("click", closeRecordPreview);
document.getElementById("record-preview-open-page")?.addEventListener("click", () => {
  openRecordPreviewFullPage();
});

const issuerScopeInput = document.getElementById("issuer-scope");
issuerScopeInput?.addEventListener("input", () => {
  const hidden = document.getElementById("issuer-scope-legal-act-url");
  if (hidden) hidden.value = "";
  updateIssuerDescriptionPrefill();
  if (scopeSearchTimer) window.clearTimeout(scopeSearchTimer);
  scopeSearchTimer = window.setTimeout(() => {
    searchScopeActs(issuerScopeInput.value).catch(() => clearScopeAutocomplete());
  }, 280);
});
issuerScopeInput?.addEventListener("focus", () => {
  if (issuerScopeInput.value.trim().length >= 2) {
    searchScopeActs(issuerScopeInput.value).catch(() => clearScopeAutocomplete());
  }
});
issuerScopeInput?.addEventListener("blur", () => {
  window.setTimeout(() => clearScopeAutocomplete(), 150);
});

const issuerNameInput = document.getElementById("issuer-name");
issuerNameInput?.addEventListener("input", () => {
  updateIssuerDescriptionPrefill();
  if (roleSearchTimer) window.clearTimeout(roleSearchTimer);
  roleSearchTimer = window.setTimeout(() => {
    searchNameRoles(issuerNameInput.value).catch(() => clearNameAutocomplete());
  }, 280);
});
issuerNameInput?.addEventListener("focus", () => {
  if (issuerNameInput.value.trim().length >= 2) {
    searchNameRoles(issuerNameInput.value).catch(() => clearNameAutocomplete());
  }
});
issuerNameInput?.addEventListener("blur", () => {
  window.setTimeout(() => clearNameAutocomplete(), 150);
});

bindSidebarNav();
updateApiStatus();
renderOverviewDashboard();
loadCollections().catch((err) => showAlert(err.message));
