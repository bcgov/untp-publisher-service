/* Discovery page: filter, sort, pagination, copy, accordion. */
(function () {
  const live = document.getElementById("live");
  const q = document.getElementById("q");
  const type = document.getElementById("type");
  const status = document.getElementById("status");
  const groupsList = document.getElementById("groups");
  let groups = Array.from(document.querySelectorAll(".group"));
  const noMatches = document.getElementById("no-matches");
  const results = document.querySelector(".results");
  const countsRow = document.getElementById("counts-row");
  const counts = document.getElementById("counts");
  const pager = document.getElementById("pager");
  const pagerStatus = document.getElementById("pager-status");
  const pagePrevButtons = Array.from(
    document.querySelectorAll("[data-page-prev]")
  );
  const pageNextButtons = Array.from(
    document.querySelectorAll("[data-page-next]")
  );
  const sortButtons = Array.from(
    document.querySelectorAll(".col-sort[data-sort]")
  );
  const clearButtons = Array.from(
    document.querySelectorAll("[data-clear]")
  );
  const totalGroups = countsRow
    ? Number(countsRow.getAttribute("data-total-groups") || 0)
    : 0;
  const PAGE_SIZE = 10;
  let currentPage = 1;
  let sortKey = "";
  let sortDir = "asc";

  function announce(msg) {
    if (live) live.textContent = msg;
  }

  function flash(btn, ok) {
    if (!btn) return;
    btn.classList.remove("is-copied", "is-failed");
    btn.classList.add(ok ? "is-copied" : "is-failed");
    const prev = btn.getAttribute("aria-label") || "Copy credential URL";
    btn.setAttribute(
      "aria-label",
      ok ? "Copied" : "Could not copy the URL"
    );
    window.clearTimeout(btn._t);
    btn._t = window.setTimeout(function () {
      btn.classList.remove("is-copied", "is-failed");
      btn.setAttribute("aria-label", prev);
    }, 1600);
  }

  async function copyUrl(url, btn) {
    let ok = false;
    try {
      await navigator.clipboard.writeText(url);
      ok = true;
    } catch (err) {
      const ta = document.createElement("textarea");
      ta.value = url;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        ok = document.execCommand("copy");
      } catch (e2) {
        ok = false;
      }
      document.body.removeChild(ta);
    }
    announce(ok ? "Copied" : "Could not copy the URL");
    flash(btn, ok);
  }

  function setOpen(group, open) {
    if (open) {
      groups.forEach(function (other) {
        if (other === group) return;
        other.classList.remove("is-open");
        const otherToggle = other.querySelector("[data-toggle]");
        if (otherToggle) otherToggle.setAttribute("aria-expanded", "false");
      });
    }
    const toggle = group.querySelector("[data-toggle]");
    group.classList.toggle("is-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function matchedGroups() {
    const query = (q && q.value ? q.value : "").trim().toLowerCase();
    const typeVal = type ? type.value : "";
    const statusVal = status ? status.value : "";
    return groups.filter(function (el) {
      const search = el.getAttribute("data-search") || "";
      const okQ = !query || search.indexOf(query) !== -1;
      const okT = !typeVal || el.getAttribute("data-type") === typeVal;
      const okS = !statusVal || el.getAttribute("data-status") === statusVal;
      return okQ && okT && okS;
    });
  }

  function sortValue(el, key) {
    return (el.getAttribute("data-sort-" + key) || "").trim();
  }

  function updateSortHeaders() {
    sortButtons.forEach(function (btn) {
      const key = btn.getAttribute("data-sort") || "";
      const active = key && key === sortKey;
      btn.classList.toggle("is-asc", active && sortDir === "asc");
      btn.classList.toggle("is-desc", active && sortDir === "desc");
      btn.setAttribute(
        "aria-sort",
        active ? (sortDir === "asc" ? "ascending" : "descending") : "none"
      );
    });
  }

  function applySort() {
    if (!sortKey || !groupsList) {
      updateSortHeaders();
      return;
    }
    groups.sort(function (a, b) {
      const av = sortValue(a, sortKey);
      const bv = sortValue(b, sortKey);
      const cmp = av.localeCompare(bv, undefined, {
        numeric: true,
        sensitivity: "base",
      });
      if (cmp !== 0) return sortDir === "asc" ? cmp : -cmp;
      const ad = sortValue(a, "document");
      const bd = sortValue(b, "document");
      return ad.localeCompare(bd, undefined, {
        numeric: true,
        sensitivity: "base",
      });
    });
    groups.forEach(function (el) {
      groupsList.appendChild(el);
    });
    updateSortHeaders();
  }

  function applyFilters(options) {
    const resetPage = !options || options.resetPage !== false;
    if (resetPage) currentPage = 1;

    applySort();

    const query = (q && q.value ? q.value : "").trim().toLowerCase();
    const typeVal = type ? type.value : "";
    const statusVal = status ? status.value : "";
    const filtered = Boolean(query || typeVal || statusVal);
    const matched = matchedGroups();
    const matchCount = matched.length;
    const pageCount = Math.max(1, Math.ceil(matchCount / PAGE_SIZE) || 1);
    if (currentPage > pageCount) currentPage = pageCount;
    if (currentPage < 1) currentPage = 1;

    const start = (currentPage - 1) * PAGE_SIZE;
    const end = Math.min(start + PAGE_SIZE, matchCount);
    const onPage = new Set(matched.slice(start, end));

    groups.forEach(function (el) {
      el.hidden = !onPage.has(el);
      if (el.hidden) setOpen(el, false);
    });

    clearButtons.forEach(function (btn) {
      if (btn.classList.contains("meta-clear")) btn.hidden = !filtered;
    });

    const empty = matchCount === 0 && groups.length > 0;
    if (noMatches) noMatches.hidden = !empty;
    if (results) results.classList.toggle("is-empty", empty);

    if (counts) {
      const entries = matchCount === 1 ? "entry" : "entries";
      if (matchCount === 0) {
        counts.innerHTML = filtered
          ? "No matching entries"
          : "<strong>0</strong> entries";
      } else if (matchCount <= PAGE_SIZE && !filtered) {
        counts.innerHTML =
          "<strong>" + matchCount + "</strong> " + entries;
      } else {
        const range =
          matchCount === 0
            ? "0"
            : start + 1 === end
              ? String(end)
              : start + 1 + "–" + end;
        counts.innerHTML =
          "Showing <strong>" +
          range +
          "</strong> of <strong>" +
          matchCount +
          "</strong> " +
          entries;
      }
    }

    const showPager = matchCount > PAGE_SIZE && !empty;
    if (pager) pager.hidden = !showPager;
    if (pagerStatus && showPager) {
      pagerStatus.innerHTML =
        "Page <strong>" +
        currentPage +
        "</strong> of <strong>" +
        pageCount +
        "</strong>";
    }
    pagePrevButtons.forEach(function (btn) {
      btn.disabled = currentPage <= 1;
    });
    pageNextButtons.forEach(function (btn) {
      btn.disabled = currentPage >= pageCount;
    });
  }

  document.addEventListener("click", function (ev) {
    const copyBtn = ev.target.closest("[data-copy-url]");
    if (copyBtn) {
      ev.preventDefault();
      copyUrl(copyBtn.getAttribute("data-copy-url") || "", copyBtn);
      return;
    }

    if (ev.target.closest("[data-clear]")) {
      if (q) q.value = "";
      if (type) type.value = "";
      if (status) status.value = "";
      applyFilters();
      if (q) q.focus();
      return;
    }

    if (ev.target.closest("[data-page-prev]")) {
      if (currentPage > 1) {
        currentPage -= 1;
        applyFilters({ resetPage: false });
      }
      return;
    }

    if (ev.target.closest("[data-page-next]")) {
      currentPage += 1;
      applyFilters({ resetPage: false });
      return;
    }

    const sortBtn = ev.target.closest(".col-sort[data-sort]");
    if (sortBtn) {
      const key = sortBtn.getAttribute("data-sort") || "";
      if (key === sortKey) {
        sortDir = sortDir === "asc" ? "desc" : "asc";
      } else {
        sortKey = key;
        sortDir = "asc";
      }
      applyFilters();
      return;
    }

    const toggle = ev.target.closest("[data-toggle]");
    if (toggle) {
      const group = toggle.closest(".group");
      if (group) setOpen(group, !group.classList.contains("is-open"));
      return;
    }

    // Anywhere else on the summary row also expands, as long as the
    // click was not a link, a control, or the end of a text selection.
    const head = ev.target.closest(".group-head");
    if (!head || ev.target.closest("a, button, input, select")) return;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) return;
    const group = head.closest(".group");
    if (group) setOpen(group, !group.classList.contains("is-open"));
  });

  if (q) q.addEventListener("input", applyFilters);
  if (type) type.addEventListener("change", applyFilters);
  if (status) status.addEventListener("change", applyFilters);
  applyFilters();
})();
