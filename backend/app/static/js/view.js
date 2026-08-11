/* Progressive /view: SSE stream → compact check chips; details go to console. */
(function () {
  const root = document.getElementById("view-app");
  if (!root) return;

  const streamUrl = root.getAttribute("data-stream-url") || "";
  if (!streamUrl || typeof EventSource === "undefined") {
    showFatal("This browser cannot stream credential checks (EventSource missing).");
    return;
  }

  const titleEl = document.getElementById("view-title");
  const ledeEl = document.getElementById("view-lede");
  const progressPanel = document.getElementById("view-progress");
  const progressLabel = document.getElementById("view-progress-label");
  const progressFill = document.getElementById("view-progress-fill");
  const errorEl = document.getElementById("view-stream-error");
  const formPanel = document.getElementById("view-form-panel");
  const resultsPanel = document.getElementById("view-results");
  const checksBar = document.getElementById("view-checks");
  const actions = document.getElementById("view-actions");
  const ocaSlot = document.getElementById("view-oca-slot");
  const ocaFrame = document.getElementById("view-oca-frame");
  const jsonPanel = document.getElementById("view-json-modal");
  const jsonBody = document.getElementById("view-json-body");
  const jsonTree = document.getElementById("view-json-tree");
  const jsonStats = document.querySelector("[data-json-stats]");
  const jsonToggle = document.querySelector("[data-json-toggle]");
  const jsonCopy = document.querySelector("[data-copy-json]");
  const jsonExpand = document.querySelector("[data-json-expand]");
  const jsonCollapse = document.querySelector("[data-json-collapse]");
  const metaPanel = document.getElementById("view-meta-modal");
  const metaToggle = document.querySelector("[data-meta-toggle]");
  const metaBadge = document.querySelector("[data-meta-badge]");
  const metaBody = document.querySelector("[data-meta-body]");
  const metaSummary = document.querySelector("[data-meta-summary]");
  const pdfBtn = document.querySelector("[data-pdf]");
  let jsonLastFocus = null;
  let metaLastFocus = null;

  function focusEl(el) {
    if (!el || typeof el.focus !== "function") return;
    try {
      el.focus();
    } catch (err) {
      /* ignore */
    }
  }

  function setDialogOpen(cfg, open) {
    const panel = cfg.panel;
    const toggle = cfg.toggle;
    if (!panel || !toggle) return;
    const willOpen = !!open;
    if (willOpen) {
      cfg.setLastFocus(document.activeElement);
      if (typeof cfg.onOpen === "function") cfg.onOpen();
      if (typeof cfg.closeOther === "function") cfg.closeOther();
    }
    panel.hidden = !willOpen;
    document.body.classList.toggle(cfg.bodyClass, willOpen);
    toggle.classList.toggle("is-active", willOpen);
    toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
    if (cfg.ariaControls) {
      toggle.setAttribute("aria-controls", cfg.ariaControls);
    }
    if (willOpen) {
      focusEl(panel.querySelector(cfg.focusSelector || ".view-json-close"));
    } else {
      focusEl(cfg.getLastFocus());
      cfg.setLastFocus(null);
    }
  }

  function onDialogKeydown(evt) {
    if (evt.key !== "Escape") return;
    if (jsonPanel && !jsonPanel.hidden) {
      evt.preventDefault();
      setJsonPanelOpen(false);
      return;
    }
    if (metaPanel && !metaPanel.hidden) {
      evt.preventDefault();
      setMetaPanelOpen(false);
    }
  }

  const CHECK_ORDER = [
    "envelope",
    "vcdm",
    "untp",
    "jsonld",
    "proof",
    "issuer",
    "validity",
    "credentialStatus",
    "renderMethod",
  ];
  const CHECK_LABELS = {
    envelope: "Envelope",
    vcdm: "VCDM 2.0",
    untp: "UNTP 0.7.0",
    jsonld: "JSON-LD",
    proof: "Proof",
    issuer: "Issuer",
    validity: "Validity period",
    credentialStatus: "Status revocation",
    renderMethod: "Render method",
  };

  let source = null;
  // EventSource reconnects when the server closes the SSE response; without this
  // flag the pipeline re-runs and replace the OCA DOM (e.g. collapses <details>).
  let streamFinished = false;
  let contextApplied = false;
  let decodedCredential = null;
  let decodedCredentialText = "";
  let jsonMode = "tree";
  let downloadName = "";
  let checkResults = {};
  let checksSettled = false;
  let langSwitchBusy = false;
  let ocaInfoOpen = root.getAttribute("data-debug") === "1";
  let ocaFrameResizeObserver = null;

  function getOcaDocument() {
    if (!ocaFrame) return null;
    try {
      return ocaFrame.contentDocument;
    } catch (err) {
      return null;
    }
  }

  function getOcaRoot() {
    const doc = getOcaDocument();
    return doc ? doc.querySelector(".oca-doc") : null;
  }

  function brandRootCssText() {
    const styles = document.querySelectorAll("head style");
    for (let i = 0; i < styles.length; i += 1) {
      const text = styles[i].textContent || "";
      if (text.indexOf("--primary") !== -1) return text;
    }
    return "";
  }

  function buildOcaSrcdoc(fragmentHtml) {
    const lang = escapeHtml(document.documentElement.lang || "en");
    const sheets = Array.prototype.map
      .call(document.querySelectorAll('link[rel="stylesheet"]'), function (link) {
        const href = String(link.href || "").replace(/"/g, "");
        if (!href) return "";
        return '<link rel="stylesheet" href="' + href + '" />';
      })
      .join("");
    return (
      "<!DOCTYPE html><html lang=\"" +
      lang +
      "\"><head><meta charset=\"utf-8\" />" +
      sheets +
      "<style>" +
      brandRootCssText() +
      "html,body{margin:0;padding:0;background:transparent}" +
      "body{overflow:hidden}" +
      ".oca-doc{margin-top:0!important}" +
      "</style></head><body class=\"pub-chrome\">" +
      fragmentHtml +
      "</body></html>"
    );
  }

  function watchOcaFrameSize() {
    const doc = getOcaDocument();
    if (!ocaFrame || !doc || !doc.body) return;
    const sync = function () {
      try {
        const height = Math.ceil(
          Math.max(
            doc.body.scrollHeight,
            doc.documentElement ? doc.documentElement.scrollHeight : 0,
            1
          )
        );
        ocaFrame.style.height = height + "px";
      } catch (err) {
        /* ignore */
      }
    };
    if (ocaFrameResizeObserver) {
      try {
        ocaFrameResizeObserver.disconnect();
      } catch (err) {
        /* ignore */
      }
      ocaFrameResizeObserver = null;
    }
    sync();
    if (typeof ResizeObserver !== "undefined") {
      ocaFrameResizeObserver = new ResizeObserver(sync);
      ocaFrameResizeObserver.observe(doc.body);
      const rootEl = doc.querySelector(".oca-doc");
      if (rootEl) ocaFrameResizeObserver.observe(rootEl);
    }
    // Stylesheets may settle after first paint.
    window.setTimeout(sync, 50);
    window.setTimeout(sync, 250);
  }

  function mountOcaHtml(html) {
    return new Promise(function (resolve) {
      if (!ocaSlot || !ocaFrame) {
        resolve();
        return;
      }
      ocaSlot.hidden = false;
      ocaFrame.removeAttribute("hidden");
      const onLoad = function () {
        ocaFrame.removeEventListener("load", onLoad);
        bindOcaControls();
        setOcaInfoOpen(ocaInfoOpen);
        watchOcaFrameSize();
        resolve();
      };
      ocaFrame.addEventListener("load", onLoad);
      ocaFrame.srcdoc = buildOcaSrcdoc(html);
    });
  }

  function clearOcaFrame() {
    if (ocaFrameResizeObserver) {
      try {
        ocaFrameResizeObserver.disconnect();
      } catch (err) {
        /* ignore */
      }
      ocaFrameResizeObserver = null;
    }
    if (ocaFrame) {
      ocaFrame.removeAttribute("srcdoc");
      ocaFrame.src = "about:blank";
      ocaFrame.style.height = "";
    }
    if (ocaSlot) ocaSlot.hidden = true;
  }

  function logCheck(data) {
    const label = "[view] " + (data.id || "check");
    if (data.ok === false || data.error) {
      console.warn(label, data);
    } else {
      console.info(label, data);
    }
  }

  function setChip(id, summary, kind) {
    if (!checksBar) return;
    const chip = checksBar.querySelector('[data-check="' + id + '"]');
    if (!chip) return;
    chip.className = "check-chip";
    if (kind) chip.classList.add(kind);
    chip.classList.remove("is-fresh");
    // Retrigger pop animation when a pending chip resolves.
    void chip.offsetWidth;
    chip.classList.add("is-fresh");
    const status = chip.querySelector(".check-status");
    const compact = compactCheckSummary(id, summary);
    if (status) status.textContent = compact || "…";
    const nameEl = chip.querySelector(".check-name");
    const name = nameEl ? nameEl.textContent : id;
    chip.title = name + (summary ? ": " + summary : "");
    chip.classList.toggle("has-detail", shouldShowCheckDetail(kind, compact));
  }

  function shouldShowCheckDetail(kind, summary) {
    if (kind === "is-pending") return true;
    const value = String(summary || "").trim().toLowerCase();
    // Color alone is enough for binary pass/fail; keep words for actionable detail.
    if (
      !value ||
      value === "…" ||
      value === "ok" ||
      value === "safe" ||
      value === "loaded" ||
      value === "active" ||
      value === "valid" ||
      value === "none" ||
      value === "fail" ||
      value === "invalid"
    ) {
      return false;
    }
    return true;
  }

  function compactCheckSummary(id, summary) {
    const raw = String(summary || "").trim();
    if (!raw || raw === "…") return raw || "…";
    const lower = raw.toLowerCase();
    if (id === "validity") {
      // Keep the validFrom – validUntil range readable on the live chip.
      return raw.length > 42 ? raw.slice(0, 40) + "…" : raw;
    }
    if (id === "jsonld") {
      if (lower.includes("safe") && !lower.includes("unsafe")) return "safe";
      if (lower.includes("unsafe")) return "unsafe";
    }
    if (id === "envelope") {
      // Prefer compact subtype (vc+jwt); fall back to legacy JWT status words.
      if (raw.includes("+") || raw.includes("/")) {
        const short =
          raw.indexOf("application/") === 0 ? raw.slice(12) : raw;
        return short.length > 18 ? short.slice(0, 16) + "…" : short;
      }
      if (lower.includes("verified")) return "ok";
      if (lower.includes("invalid")) return "fail";
    }
    if (id === "issuer") {
      // did:method — keep as-is (short).
      if (lower.startsWith("did:")) {
        const parts = raw.split(":");
        if (parts.length >= 2 && parts[1]) {
          return "did:" + parts[1].replace(/:$/, "");
        }
      }
    }
    if (id === "renderMethod") {
      if (
        raw === "loaded" ||
        raw === "none" ||
        raw === "fail" ||
        raw === "ok"
      ) {
        return raw === "loaded" ? "ok" : raw;
      }
      return raw.length > 18 ? raw.slice(0, 16) + "…" : raw;
    }
    if (id === "untp" || id === "vcdm") {
      if (lower === "valid" || lower === "ok") return "ok";
      if (lower === "invalid" || lower === "fail") return "fail";
    }
    if (raw.length > 18) return raw.slice(0, 16) + "…";
    return raw;
  }

  function showFatal(message) {
    console.error("[view] fatal", message);
    if (progressPanel) progressPanel.hidden = true;
    if (errorEl) {
      errorEl.hidden = false;
      errorEl.textContent = message;
    }
    if (formPanel) formPanel.hidden = false;
    if (titleEl) titleEl.textContent = "Couldn’t open credential";
    if (ledeEl) ledeEl.hidden = true;
  }

  function setProgress(index, total, label) {
    if (progressLabel) {
      progressLabel.textContent =
        (label || "Working…") + (total ? " · " + index + "/" + total : "");
    }
    if (progressFill && total) {
      const pct = Math.max(0, Math.min(100, Math.round((index / total) * 100)));
      progressFill.style.width = pct + "%";
    }
  }

  function finishProgress() {
    if (progressPanel) progressPanel.hidden = true;
  }

  function applyMeta(data) {
    if (resultsPanel) resultsPanel.hidden = false;
    if (checksBar) {
      checksBar.hidden = false;
      checksBar.classList.remove("is-settled");
    }
    checksSettled = false;
    if (titleEl) {
      titleEl.textContent = "Credential View";
    }
    if (data.credential_name) {
      document.title = data.credential_name + " — Credential View";
    }
    if (ledeEl) {
      ledeEl.hidden = true;
    }

    if (actions) {
      const copyBtn = actions.querySelector("[data-copy-url]");
      const download = actions.querySelector("[data-download]");
      if (copyBtn && data.credential_url) {
        copyBtn.setAttribute("data-copy-url", data.credential_url);
      }
      if (download && data.download_url) {
        download.setAttribute("href", data.download_url);
        if (data.download_name) {
          downloadName = String(data.download_name);
          download.setAttribute("download", downloadName);
          download.setAttribute("title", "Download " + downloadName);
        }
      }
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10 * 1024 ? 1 : 0) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function linkHrefForString(value) {
    if (typeof value !== "string") return "";
    if (value.indexOf("did:") === 0) {
      return "https://uniresolver.io/#" + encodeURIComponent(value);
    }
    if (value.indexOf("https://") === 0 || value.indexOf("http://") === 0) {
      return value;
    }
    return "";
  }

  function renderJsonScalar(value) {
    if (value === null) {
      return '<span class="jtree-null">null</span>';
    }
    if (typeof value === "boolean") {
      return '<span class="jtree-bool">' + (value ? "true" : "false") + "</span>";
    }
    if (typeof value === "number") {
      return '<span class="jtree-num">' + escapeHtml(String(value)) + "</span>";
    }
    const text = JSON.stringify(value);
    const href = linkHrefForString(value);
    if (href) {
      return (
        '<a class="jtree-str jtree-link" href="' +
        escapeHtml(href) +
        '" target="_blank" rel="noopener noreferrer">' +
        escapeHtml(text) +
        "</a>"
      );
    }
    return '<span class="jtree-str">' + escapeHtml(text) + "</span>";
  }

  function renderJsonTree(value, key, path, depth) {
    const label =
      key === undefined
        ? ""
        : typeof key === "number"
          ? '<span class="jtree-index">' + key + "</span>"
          : '<span class="jtree-key">' + escapeHtml(JSON.stringify(key)) + "</span>";
    const prefix = label
      ? label + '<span class="jtree-punct">:</span> '
      : "";
    const title = path ? ' title="' + escapeHtml(path) + '"' : "";

    if (value !== null && typeof value === "object") {
      const isArr = Array.isArray(value);
      const keys = isArr ? value.map(function (_, i) { return i; }) : Object.keys(value);
      const open = depth < 2 ? " open" : "";
      const hint = isArr
        ? keys.length + (keys.length === 1 ? " item" : " items")
        : keys.length + (keys.length === 1 ? " key" : " keys");
      const openBrace = isArr ? "[" : "{";
      const closeBrace = isArr ? "]" : "}";
      if (!keys.length) {
        return (
          '<div class="jtree-leaf"' +
          title +
          ">" +
          prefix +
          '<span class="jtree-punct">' +
          openBrace +
          closeBrace +
          "</span></div>"
        );
      }
      let html =
        '<details class="jtree-node"' +
        open +
        "><summary" +
        title +
        ">" +
        prefix +
        '<span class="jtree-punct">' +
        openBrace +
        '</span> <span class="jtree-hint">' +
        hint +
        "</span></summary><div class=\"jtree-children\">";
      keys.forEach(function (childKey) {
        const childVal = value[childKey];
        const childPath = path
          ? isArr
            ? path + "[" + childKey + "]"
            : path + "." + childKey
          : isArr
            ? "[" + childKey + "]"
            : String(childKey);
        html += renderJsonTree(childVal, childKey, childPath, depth + 1);
      });
      html +=
        '</div><div class="jtree-leaf"><span class="jtree-punct">' +
        closeBrace +
        "</span></div></details>";
      return html;
    }

    return (
      '<div class="jtree-leaf"' +
      title +
      ">" +
      prefix +
      renderJsonScalar(value) +
      "</div>"
    );
  }

  function countJsonNodes(value) {
    if (value === null || typeof value !== "object") return 1;
    const kids = Array.isArray(value) ? value : Object.keys(value).map(function (k) {
      return value[k];
    });
    return 1 + kids.reduce(function (sum, child) {
      return sum + countJsonNodes(child);
    }, 0);
  }

  function updateJsonStats(credential, text) {
    if (!jsonStats) return;
    const topKeys =
      credential && typeof credential === "object" && !Array.isArray(credential)
        ? Object.keys(credential).length
        : 0;
    const types = [];
    if (credential && typeof credential === "object") {
      const t = credential.type || credential["@type"];
      if (Array.isArray(t)) {
        t.forEach(function (item) {
          if (item) types.push(String(item));
        });
      } else if (t) {
        types.push(String(t));
      }
    }
    const typeLabel =
      types.length > 0
        ? types
            .map(function (item) {
              const parts = item.split(/[/#]/);
              return parts[parts.length - 1] || item;
            })
            .slice(0, 2)
            .join(" · ")
        : "JSON";
    const nodes = countJsonNodes(credential);
    jsonStats.innerHTML =
      '<span class="view-json-stat"><span class="view-json-stat-label">Type</span>' +
      escapeHtml(typeLabel) +
      "</span>" +
      '<span class="view-json-stat"><span class="view-json-stat-label">Keys</span>' +
      topKeys +
      "</span>" +
      '<span class="view-json-stat"><span class="view-json-stat-label">Nodes</span>' +
      nodes +
      "</span>" +
      '<span class="view-json-stat"><span class="view-json-stat-label">Size</span>' +
      escapeHtml(formatBytes(new Blob([text || ""]).size)) +
      "</span>";
  }

  function applyJsonMode(mode) {
    jsonMode = mode === "raw" ? "raw" : "tree";
    if (jsonPanel) jsonPanel.setAttribute("data-json-mode", jsonMode);
    if (jsonTree) jsonTree.hidden = jsonMode !== "tree";
    if (jsonBody) jsonBody.hidden = jsonMode !== "raw";
    if (jsonExpand) jsonExpand.hidden = jsonMode !== "tree";
    if (jsonCollapse) jsonCollapse.hidden = jsonMode !== "tree";
    document.querySelectorAll("[data-json-mode].view-json-mode-btn").forEach(function (btn) {
      const active = btn.getAttribute("data-json-mode") === jsonMode;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function setDecodedCredential(credential) {
    if (!credential || typeof credential !== "object") {
      decodedCredential = null;
      decodedCredentialText = "";
      if (jsonToggle) jsonToggle.disabled = true;
      if (jsonBody) jsonBody.textContent = "";
      if (jsonTree) jsonTree.innerHTML = "";
      if (jsonStats) jsonStats.innerHTML = "";
      if (jsonPanel) jsonPanel.hidden = true;
      document.body.classList.remove("is-json-modal-open");
      if (jsonToggle) {
        jsonToggle.classList.remove("is-active");
        jsonToggle.setAttribute("aria-expanded", "false");
      }
      return;
    }
    decodedCredential = credential;
    try {
      decodedCredentialText = JSON.stringify(credential, null, 2);
    } catch (err) {
      decodedCredentialText = String(credential);
    }
    if (jsonBody) jsonBody.textContent = decodedCredentialText;
    if (jsonTree) {
      jsonTree.innerHTML = renderJsonTree(credential, undefined, "$", 0);
    }
    updateJsonStats(credential, decodedCredentialText);
    applyJsonMode(jsonMode);
    if (jsonToggle) jsonToggle.disabled = false;
  }

  function setJsonPanelOpen(open) {
    setDialogOpen(
      {
        panel: jsonPanel,
        toggle: jsonToggle,
        bodyClass: "is-json-modal-open",
        ariaControls: "view-json-modal",
        focusSelector: ".view-json-close",
        getLastFocus: function () {
          return jsonLastFocus;
        },
        setLastFocus: function (el) {
          jsonLastFocus = el;
        },
        onOpen: function () {
          applyJsonMode(jsonMode);
        },
        closeOther: function () {
          if (metaPanel && !metaPanel.hidden) setMetaPanelOpen(false);
        },
      },
      open
    );
  }

  function setJsonNodesOpen(open) {
    if (!jsonTree) return;
    jsonTree.querySelectorAll("details.jtree-node").forEach(function (node) {
      node.open = !!open;
    });
  }

  function applyCheck(data) {
    logCheck(data);
    const id = data.id;
    let kind = "is-pending";
    let summary = data.summary || "…";

    if (id === "envelope") {
      // Prefer the compact data-URI subtype (vc+jwt), then full media type.
      if (data.summary && data.summary.indexOf("/") === -1) {
        summary = data.summary;
      } else if (data.media_type) {
        const mt = String(data.media_type);
        summary = mt.indexOf("application/") === 0 ? mt.slice(12) : mt;
      }
      if (data.ok === true) kind = "is-ok";
      else if (data.ok === false) kind = "is-bad";
      else kind = "is-warn";
      setChip(id, summary, kind);
      const chip = checksBar && checksBar.querySelector('[data-check="envelope"]');
      if (chip) {
        const verify = data.verification || "";
        const full = data.media_type || summary;
        chip.title =
          "Envelope" +
          (full ? ": " + full : "") +
          (verify ? " · " + verify : "");
      }
      rememberCheck(id, kind, summary, data);
      return;
    } else if (id === "credentialStatus") {
      if (!data.present) {
        kind = "is-muted";
        summary = "none";
      } else if (data.ok) {
        kind =
          data.summary && data.summary !== "active" ? "is-warn" : "is-ok";
      } else {
        kind = "is-bad";
        summary = data.summary || "error";
      }
    } else if (id === "renderMethod") {
      if (!data.present) {
        kind = "is-muted";
        summary = data.source ? "fallback" : "none";
      } else if (data.ok) {
        kind = "is-ok";
        summary = data.render_suite || data.summary || "loaded";
      } else {
        kind = "is-bad";
        summary = data.render_suite || data.summary || "error";
      }
      setChip(id, summary, kind);
      const chip =
        checksBar && checksBar.querySelector('[data-check="renderMethod"]');
      if (chip) {
        const suite = data.render_suite || "";
        chip.title =
          "Render" +
          (suite ? ": " + suite : summary ? ": " + summary : "");
      }
      rememberCheck(id, kind, summary, data);
      return;
    } else if (id === "untp") {
      kind = data.ok ? "is-ok" : "is-bad";
      summary = data.ok ? "ok" : "fail";
    } else if (id === "jsonld") {
      const safe = data.safe === true || data.summary === "SAFE JSON-LD";
      kind = data.ok ? (safe ? "is-ok" : "is-warn") : "is-bad";
      summary = safe ? "SAFE JSON-LD" : "UNSAFE JSON-LD";
    } else if (id === "issuer") {
      if (data.ok) {
        kind = "is-ok";
        summary = data.method || data.summary || "ok";
      } else {
        kind = "is-bad";
        summary = data.summary || "fail";
      }
      setChip(id, summary, kind);
      const chip = checksBar && checksBar.querySelector('[data-check="issuer"]');
      if (chip) {
        const detail = data.name || data.did || data.summary || "";
        chip.title = detail ? "Issuer: " + detail : "Issuer";
      }
      rememberCheck(id, kind, summary, data);
      return;
    } else if (id === "validity") {
      const status = data.summary || "…";
      if (data.ok) kind = "is-ok";
      else if (status === "expired" || status === "not yet valid") {
        kind = "is-warn";
      } else {
        kind = "is-bad";
      }
      summary = data.period_display || status;
      setChip(id, summary, kind);
      const chip = checksBar && checksBar.querySelector('[data-check="validity"]');
      if (chip) {
        chip.title =
          "Validity" +
          (data.period_display ? ": " + data.period_display : "") +
          (status ? " · " + status : "");
      }
      rememberCheck(id, kind, summary, data);
      return;
    } else if (id === "proof") {
      if (data.ok) kind = "is-ok";
      else if (data.summary === "expired" || data.summary === "not yet valid") {
        kind = "is-warn";
      } else {
        kind = "is-bad";
      }
    } else {
      kind = data.ok ? "is-ok" : "is-bad";
      summary = data.ok ? "valid" : "invalid";
    }

    setChip(id, summary, kind);
    rememberCheck(id, kind, summary, data);
  }

  function rememberCheck(id, kind, summary, data) {
    if (!id) return;
    checkResults[id] = {
      id: id,
      kind: kind,
      summary: summary,
      data: data || {},
    };
    if (!checksSettled) {
      renderMetadataCard();
    }
  }

  function checkDetailText(entry) {
    const d = entry.data || {};
    const summary = String(entry.summary || "").trim();
    if (entry.id === "envelope") {
      const parts = [];
      if (d.media_type) parts.push(String(d.media_type));
      else if (summary) parts.push(summary);
      if (d.verification) parts.push(String(d.verification));
      return parts.join(" · ") || "—";
    }
    if (entry.id === "issuer") {
      const parts = [];
      if (d.method) parts.push(String(d.method));
      if (d.name) parts.push(String(d.name));
      else if (d.did) parts.push(String(d.did));
      else if (summary) parts.push(summary);
      return parts.join(" · ") || "—";
    }
    if (entry.id === "proof") {
      return summary || (d.cryptosuite ? String(d.cryptosuite) : "—");
    }
    if (entry.id === "validity") {
      return d.period_display || summary || "—";
    }
    if (entry.id === "renderMethod") {
      if (d.render_suite) return String(d.render_suite);
      return summary || "—";
    }
    if (entry.id === "jsonld") {
      return summary || "—";
    }
    if (entry.id === "credentialStatus") {
      return summary || (d.present ? "present" : "none");
    }
    if (d.error) return String(d.error);
    return summary || "—";
  }

  function checkNoteText(entry) {
    const d = entry.data || {};
    if (d.error) return String(d.error);
    if (entry.id === "issuer" && d.did && d.name) return String(d.did);
    if (entry.id === "validity" && d.summary) {
      return "Status: " + String(d.summary);
    }
    if (entry.id === "envelope" && d.verification) return "";
    return "";
  }

  function pillLabel(kind) {
    if (kind === "is-ok") return "Pass";
    if (kind === "is-bad") return "Fail";
    if (kind === "is-warn") return "Warn";
    if (kind === "is-muted") return "N/A";
    return "Pending";
  }

  function overallMetaKind() {
    let hasBad = false;
    let hasWarn = false;
    let hasOk = false;
    CHECK_ORDER.forEach(function (id) {
      const entry = checkResults[id];
      if (!entry) return;
      if (entry.kind === "is-bad") hasBad = true;
      else if (entry.kind === "is-warn") hasWarn = true;
      else if (entry.kind === "is-ok") hasOk = true;
    });
    if (hasBad) return "is-bad";
    if (hasWarn) return "is-warn";
    if (hasOk) return "is-ok";
    return "is-muted";
  }

  function overallMetaCounts() {
    let passed = 0;
    let failed = 0;
    let warned = 0;
    let muted = 0;
    let pending = 0;
    CHECK_ORDER.forEach(function (id) {
      const entry = checkResults[id];
      if (!entry) {
        pending += 1;
        return;
      }
      if (entry.kind === "is-ok") passed += 1;
      else if (entry.kind === "is-bad") failed += 1;
      else if (entry.kind === "is-warn") warned += 1;
      else if (entry.kind === "is-muted") muted += 1;
      else pending += 1;
    });
    return {
      passed: passed,
      failed: failed,
      warned: warned,
      muted: muted,
      pending: pending,
    };
  }

  function overallMetaSummaryText() {
    const c = overallMetaCounts();
    const bits = [];
    if (c.passed) bits.push(c.passed + " passed");
    if (c.warned) bits.push(c.warned + " warning" + (c.warned === 1 ? "" : "s"));
    if (c.failed) bits.push(c.failed + " failed");
    if (c.muted) bits.push(c.muted + " n/a");
    if (c.pending) bits.push(c.pending + " pending");
    if (!bits.length) return "No verification checks yet.";
    if (c.failed === 0 && c.warned === 0 && c.pending === 0) {
      return "All checks passed · " + c.passed + " validations";
    }
    return "Verification complete · " + bits.join(" · ");
  }

  function fillMetaSummary() {
    if (!metaSummary) return;
    const kind = overallMetaKind();
    metaSummary.className = "view-meta-summary " + kind;
    metaSummary.textContent = "";
    const c = overallMetaCounts();
    if (!c.passed && !c.failed && !c.warned && !c.muted && !c.pending) {
      metaSummary.textContent = "No verification checks yet.";
      return;
    }
    const lead = document.createElement("strong");
    if (c.failed === 0 && c.warned === 0 && c.pending === 0) {
      lead.textContent = "All checks passed";
      metaSummary.appendChild(lead);
      metaSummary.appendChild(
        document.createTextNode(" · " + c.passed + " validations")
      );
      return;
    }
    lead.textContent = "Verification complete";
    metaSummary.appendChild(lead);
    const bits = [];
    if (c.passed) bits.push(c.passed + " passed");
    if (c.warned) bits.push(c.warned + " warning" + (c.warned === 1 ? "" : "s"));
    if (c.failed) bits.push(c.failed + " failed");
    if (c.muted) bits.push(c.muted + " n/a");
    if (c.pending) bits.push(c.pending + " pending");
    metaSummary.appendChild(document.createTextNode(" · " + bits.join(" · ")));
  }

  function renderMetadataCard() {
    fillMetaSummary();
    if (!metaBody) return;
    metaBody.innerHTML = CHECK_ORDER.map(function (id) {
      const entry = checkResults[id] || {
        id: id,
        kind: "is-pending",
        summary: "…",
        data: {},
      };
      const kind = entry.kind || "is-pending";
      const note = checkNoteText(entry);
      return (
        '<div class="view-meta-row ' +
        kind +
        '">' +
        '<div class="view-meta-row-main">' +
        '<p class="view-meta-row-name">' +
        escapeHtml(CHECK_LABELS[id] || id) +
        "</p>" +
        '<p class="view-meta-row-detail">' +
        escapeHtml(checkDetailText(entry)) +
        "</p>" +
        (note
          ? '<p class="view-meta-row-note">' + escapeHtml(note) + "</p>"
          : "") +
        "</div>" +
        '<span class="view-meta-pill">' +
        pillLabel(kind) +
        "</span>" +
        "</div>"
      );
    }).join("");
  }

  function updateMetaBadge() {
    if (!metaToggle || !metaBadge) return;
    const kind = overallMetaKind();
    metaBadge.hidden = false;
    metaBadge.className = "meta-badge " + kind;
    metaToggle.classList.add("has-meta-badge");
    metaToggle.title = "Verification metadata · " + overallMetaSummaryText();
  }

  function settleVerification() {
    checksSettled = true;
    if (checksBar) {
      checksBar.classList.add("is-settled");
      checksBar.hidden = true;
    }
    if (resultsPanel) resultsPanel.hidden = true;
    renderMetadataCard();
    updateMetaBadge();
    if (metaToggle) metaToggle.disabled = false;
  }

  function setMetaPanelOpen(open) {
    setDialogOpen(
      {
        panel: metaPanel,
        toggle: metaToggle,
        bodyClass: "is-meta-modal-open",
        ariaControls: "view-meta-modal",
        focusSelector: "[data-meta-close].view-json-close",
        getLastFocus: function () {
          return metaLastFocus;
        },
        setLastFocus: function (el) {
          metaLastFocus = el;
        },
        onOpen: function () {
          renderMetadataCard();
        },
        closeOther: function () {
          if (jsonPanel && !jsonPanel.hidden) setJsonPanelOpen(false);
        },
      },
      open
    );
  }

  function bindMetaPanel() {
    if (metaToggle && !metaToggle._viewBound) {
      metaToggle._viewBound = true;
      metaToggle.setAttribute("aria-controls", "view-meta-modal");
      metaToggle.setAttribute("aria-haspopup", "dialog");
      metaToggle.addEventListener("click", function () {
        if (metaToggle.disabled) return;
        setMetaPanelOpen(metaPanel ? metaPanel.hidden : true);
      });
    }
    if (metaPanel && !metaPanel._viewBound) {
      metaPanel._viewBound = true;
      metaPanel.querySelectorAll("[data-meta-close]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          setMetaPanelOpen(false);
        });
      });
    }
  }

  function bindDialogKeys() {
    if (document.documentElement._viewDialogKeysBound) return;
    document.documentElement._viewDialogKeysBound = true;
    document.addEventListener("keydown", onDialogKeydown);
  }

  function applyContext(data) {
    if (actions) actions.hidden = false;
    if (pdfBtn) pdfBtn.disabled = false;
    if (data && data.credential) {
      setDecodedCredential(data.credential);
    }
    if (!ocaSlot || !ocaFrame) return Promise.resolve();
    if (data.html) {
      // Mount inside a sandboxed iframe (allow-same-origin, no scripts) so
      // credential/OCA markup cannot run script in the parent page.
      return mountOcaHtml(data.html);
    }
    clearOcaFrame();
    return Promise.resolve();
  }

  function setOcaInfoOpen(open) {
    ocaInfoOpen = !!open;
    const doc = getOcaDocument();
    if (!doc) return;
    const rootDoc = doc.querySelector(".oca-doc");
    const panel = doc.querySelector(".oca-overlays, .oca-all-fields");
    const btn = doc.querySelector("[data-oca-info-toggle]");
    if (rootDoc) rootDoc.classList.toggle("is-show-info", ocaInfoOpen);
    if (panel) {
      if (ocaInfoOpen) {
        panel.removeAttribute("hidden");
        panel.hidden = false;
        panel.classList.add("is-open");
        try {
          panel.scrollIntoView({ behavior: "smooth", block: "start" });
          watchOcaFrameSize();
        } catch (err) {
          /* ignore */
        }
      } else {
        panel.setAttribute("hidden", "");
        panel.hidden = true;
        panel.classList.remove("is-open");
        watchOcaFrameSize();
      }
    }
    if (btn) {
      btn.classList.toggle("is-active", ocaInfoOpen);
      btn.setAttribute("aria-pressed", ocaInfoOpen ? "true" : "false");
    }
  }

  function bindOcaControls() {
    const doc = getOcaDocument();
    if (!doc) return;

    const infoBtn = doc.querySelector("[data-oca-info-toggle]");
    if (infoBtn) {
      infoBtn.onclick = function (evt) {
        evt.preventDefault();
        evt.stopPropagation();
        setOcaInfoOpen(!ocaInfoOpen);
      };
    }

    if (doc.documentElement._ocaLangBound) return;
    doc.documentElement._ocaLangBound = true;
    doc.addEventListener("click", function (evt) {
      const btn =
        evt.target && evt.target.closest
          ? evt.target.closest("[data-oca-lang]")
          : null;
      if (!btn || !doc.documentElement.contains(btn)) return;
      const code = (btn.getAttribute("data-oca-lang") || "").trim().toLowerCase();
      const credUrl =
        btn.getAttribute("data-oca-url") ||
        root.getAttribute("data-page-url") ||
        "";
      if (!code || !credUrl || langSwitchBusy) return;
      if (btn.classList.contains("is-active")) return;
      switchOcaLanguage(credUrl, code);
    });
  }

  async function switchOcaLanguage(credUrl, code) {
    langSwitchBusy = true;
    const doc = getOcaDocument();
    const buttons = doc ? doc.querySelectorAll("[data-oca-lang]") : [];
    buttons.forEach(function (btn) {
      btn.disabled = true;
    });
    try {
      const endpoint =
        "/view/oca?url=" +
        encodeURIComponent(credUrl) +
        "&lang=" +
        encodeURIComponent(code) +
        (root.getAttribute("data-debug") === "1" ? "&debug=1" : "");
      const res = await fetch(endpoint, {
        headers: { Accept: "application/json" },
      });
      const data = await res.json();
      if (!res.ok || !data || data.type === "error" || !data.html) {
        console.warn("[view] language switch failed", data);
        buttons.forEach(function (btn) {
          btn.disabled = false;
        });
        return;
      }
      await applyContext(data);
      try {
        const next = new URL(window.location.href);
        next.searchParams.set("url", credUrl);
        next.searchParams.set("lang", code);
        if (root.getAttribute("data-debug") === "1") {
          next.searchParams.set("debug", "1");
        } else {
          next.searchParams.delete("debug");
        }
        window.history.replaceState({}, "", next.toString());
        document.documentElement.lang = code;
      } catch (err) {
        /* ignore history failures */
      }
    } catch (err) {
      console.warn("[view] language switch error", err);
      buttons.forEach(function (btn) {
        btn.disabled = false;
      });
    } finally {
      langSwitchBusy = false;
    }
  }

  function bindCopy() {
    document.querySelectorAll("[data-copy-url]").forEach(function (btn) {
      if (btn._viewBound) return;
      btn._viewBound = true;
      btn.addEventListener("click", async function () {
        const value = btn.getAttribute("data-copy-url") || "";
        if (!value) return;
        try {
          await navigator.clipboard.writeText(value);
          btn.classList.add("is-copied");
          window.setTimeout(function () {
            btn.classList.remove("is-copied");
          }, 1400);
        } catch (err) {
          /* ignore */
        }
      });
    });
  }

  function bindJsonPanel() {
    if (jsonToggle && !jsonToggle._viewBound) {
      jsonToggle._viewBound = true;
      jsonToggle.setAttribute("aria-controls", "view-json-modal");
      jsonToggle.setAttribute("aria-haspopup", "dialog");
      jsonToggle.addEventListener("click", function () {
        if (!decodedCredentialText) return;
        setJsonPanelOpen(jsonPanel ? jsonPanel.hidden : true);
      });
    }
    if (jsonPanel && !jsonPanel._viewBound) {
      jsonPanel._viewBound = true;
      jsonPanel.querySelectorAll("[data-json-close]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          setJsonPanelOpen(false);
        });
      });
    }
    document.querySelectorAll("[data-json-mode].view-json-mode-btn").forEach(function (btn) {
      if (btn._viewBound) return;
      btn._viewBound = true;
      btn.addEventListener("click", function () {
        applyJsonMode(btn.getAttribute("data-json-mode") || "tree");
      });
    });
    if (jsonExpand && !jsonExpand._viewBound) {
      jsonExpand._viewBound = true;
      jsonExpand.addEventListener("click", function () {
        setJsonNodesOpen(true);
      });
    }
    if (jsonCollapse && !jsonCollapse._viewBound) {
      jsonCollapse._viewBound = true;
      jsonCollapse.addEventListener("click", function () {
        setJsonNodesOpen(false);
      });
    }
    if (jsonCopy && !jsonCopy._viewBound) {
      jsonCopy._viewBound = true;
      jsonCopy.addEventListener("click", async function () {
        if (!decodedCredentialText) return;
        try {
          await navigator.clipboard.writeText(decodedCredentialText);
          jsonCopy.classList.add("is-copied");
          const prev = jsonCopy.textContent;
          jsonCopy.textContent = "Copied";
          window.setTimeout(function () {
            jsonCopy.classList.remove("is-copied");
            jsonCopy.textContent = prev || "Copy";
          }, 1400);
        } catch (err) {
          /* ignore */
        }
      });
    }
  }

  function pdfFilenameFromDownload(name) {
    const raw = String(name || "").trim();
    if (!raw) return "credential.pdf";
    if (/\.pdf$/i.test(raw)) return raw;
    if (/\.vc$/i.test(raw)) return raw.replace(/\.vc$/i, ".pdf");
    return raw.replace(/\.[^.]+$/, "") + ".pdf";
  }

  function pdfDocumentTitle() {
    // Browsers use <title> as the default "Save as PDF" filename (sans path).
    return pdfFilenameFromDownload(downloadName).replace(/\.pdf$/i, "");
  }

  function formatPrintedAt(date) {
    // Match server Rendered stamp: "10 Aug 2026, 23:43:07 UTC"
    const months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
    ];
    const dd = String(date.getUTCDate());
    const mon = months[date.getUTCMonth()];
    const yyyy = date.getUTCFullYear();
    const hh = String(date.getUTCHours()).padStart(2, "0");
    const mm = String(date.getUTCMinutes()).padStart(2, "0");
    const ss = String(date.getUTCSeconds()).padStart(2, "0");
    return dd + " " + mon + " " + yyyy + ", " + hh + ":" + mm + ":" + ss + " UTC";
  }

  function absolutizeAttr(el, attr) {
    const raw = el.getAttribute(attr);
    if (!raw || /^(https?:|data:|blob:|#|mailto:|tel:)/i.test(raw)) return;
    try {
      el.setAttribute(attr, new URL(raw, window.location.href).href);
    } catch (err) {
      /* ignore */
    }
  }

  function prepareOcaPrintClone(sourceDoc) {
    const clone = sourceDoc.cloneNode(true);
    clone
      .querySelectorAll(
        ".oca-overlays, .oca-all-fields, .oca-ribbon-tools, .oca-lang, .oca-info-btn"
      )
      .forEach(function (el) {
        el.remove();
      });
    clone.querySelectorAll("[src], [href]").forEach(function (el) {
      if (el.hasAttribute("src")) absolutizeAttr(el, "src");
      if (el.hasAttribute("href") && el.getAttribute("href")) {
        absolutizeAttr(el, "href");
      }
    });

    const now = new Date();
    const printedMeta = clone.querySelector(".oca-footer-printed");
    const printedTime = clone.querySelector("[data-oca-printed-at]");
    if (printedTime) {
      printedTime.setAttribute("datetime", now.toISOString());
      printedTime.textContent = formatPrintedAt(now);
    }
    if (printedMeta) {
      printedMeta.hidden = false;
      printedMeta.removeAttribute("hidden");
    }
    return clone;
  }

  function populatePrintWindow(printWin, sourceDoc) {
    const printDoc = printWin.document;
    printDoc.title = pdfDocumentTitle();
    printDoc.documentElement.lang = document.documentElement.lang || "en";

    document.querySelectorAll('link[rel="stylesheet"]').forEach(function (link) {
      const el = printDoc.createElement("link");
      el.rel = "stylesheet";
      el.href = link.href;
      printDoc.head.appendChild(el);
    });
    document.querySelectorAll("style").forEach(function (style) {
      const el = printDoc.createElement("style");
      el.textContent = style.textContent || "";
      printDoc.head.appendChild(el);
    });
    const printCss = printDoc.createElement("style");
    printCss.textContent =
      "@page{size:letter;margin:0.4in}" +
      "html,body{margin:0!important;padding:0!important;background:#fff!important}" +
      "body{print-color-adjust:exact;-webkit-print-color-adjust:exact}" +
      ".oca-doc{margin:0!important;max-width:none!important;box-shadow:none!important;border:0!important;border-radius:0!important}";
    printDoc.head.appendChild(printCss);

    printDoc.body.className = "pub-chrome is-printing-oca";
    printDoc.body.appendChild(
      printDoc.importNode(prepareOcaPrintClone(sourceDoc), true)
    );
  }

  function bindPdf() {
    if (!pdfBtn || pdfBtn._viewBound) return;
    pdfBtn._viewBound = true;
    pdfBtn.addEventListener("click", function () {
      const doc = getOcaRoot();
      if (!doc) return;

      // Print from about:blank so browser footer is not the long /view?url=… link.
      // <title> drives the default Save as PDF filename (same stem as .vc download).
      const printWin = window.open("", "_blank");
      if (!printWin) {
        const prevTitle = document.title;
        document.title = pdfDocumentTitle();
        window.print();
        window.setTimeout(function () {
          document.title = prevTitle;
        }, 500);
        return;
      }

      // Build the print document with DOM APIs (importNode) — avoid
      // serializing live DOM to HTML and document.write (CodeQL xss-through-dom).
      populatePrintWindow(printWin, doc);

      const runPrint = function () {
        try {
          printWin.focus();
          printWin.print();
        } catch (err) {
          /* ignore */
        }
        window.setTimeout(function () {
          try {
            printWin.close();
          } catch (err) {
            /* ignore */
          }
        }, 500);
      };

      // Allow stylesheets/images to settle before opening the dialog.
      window.setTimeout(runPrint, 300);
    });
  }

  function closeSource() {
    if (source) {
      source.close();
      source = null;
    }
  }

  source = new EventSource(streamUrl);
  source.onmessage = function (evt) {
    if (streamFinished) return;
    let data;
    try {
      data = JSON.parse(evt.data);
    } catch (err) {
      return;
    }
    const type = data && data.type;
    if (type === "progress") {
      setProgress(data.index || 0, data.total || 0, data.label || "");
      return;
    }
    if (type === "meta") {
      applyMeta(data);
      bindCopy();
      return;
    }
    if (type === "check") {
      applyCheck(data);
      return;
    }
    if (type === "context") {
      applyContext(data);
      contextApplied = true;
      bindCopy();
      bindJsonPanel();
      bindPdf();
      return;
    }
    if (type === "error") {
      streamFinished = true;
      finishProgress();
      closeSource();
      showFatal(data.message || "Could not open this credential.");
      return;
    }
    if (type === "done") {
      streamFinished = true;
      finishProgress();
      closeSource();
      settleVerification();
      if (actions) actions.hidden = false;
      if (pdfBtn) pdfBtn.disabled = false;
      bindCopy();
      bindMetaPanel();
      bindJsonPanel();
      bindDialogKeys();
      bindPdf();
      console.info("[view] done");
    }
  };
  source.onerror = function () {
    if (!source) return;
    // Normal end-of-stream looks like an error to EventSource; close so it
    // does not auto-reconnect and re-render the page a few seconds later.
    if (streamFinished || contextApplied) {
      streamFinished = true;
      finishProgress();
      closeSource();
      settleVerification();
      if (actions) actions.hidden = false;
      return;
    }
    streamFinished = true;
    closeSource();
    if (progressPanel && !progressPanel.hidden) {
      showFatal("Connection to the viewer stream was interrupted. Refresh to try again.");
    }
  };

  bindCopy();
  bindMetaPanel();
  bindJsonPanel();
  bindDialogKeys();
  bindPdf();
})();
