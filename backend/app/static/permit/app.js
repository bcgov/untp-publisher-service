/** Comprehensive conformity assessment permit preview — UNTP 0.7.0 DCC visual record. */

const UNIT_LABELS = {
  C62: "one (unit)",
};

const IMAGE_BASE = "/static/permit/images";

/** @type {{ labels: Record<string, string>, info: Record<string, string> }} */
let oca = { labels: {}, info: {} };
/** @type {object | null} */
let ocaBundle = null;
let ocaLang = "en";

function sampleApiBase() {
  // /discovery/samples/{credential_type}[/…]
  const parts = window.location.pathname.replace(/\/+$/, "").split("/");
  const samplesIdx = parts.indexOf("samples");
  if (samplesIdx >= 0 && parts[samplesIdx + 1]) {
    return `/discovery/samples/${parts[samplesIdx + 1]}/api`;
  }
  return "/discovery/samples/BCMinesActPermitCredential/api";
}

function applyOcaLanguage(lang) {
  ocaLang = lang === "fr" ? "fr" : "en";
  oca = { labels: {}, info: {} };
  for (const overlay of ocaBundle?.overlays || []) {
    if (!overlay || typeof overlay !== "object") continue;
    if (overlay.language && overlay.language !== ocaLang) continue;
    if (overlay.type === "spec/overlays/label/1.0") {
      Object.assign(oca.labels, overlay.attribute_labels || {});
    }
    if (overlay.type === "spec/overlays/information/1.0") {
      Object.assign(oca.info, overlay.attribute_information || {});
    }
  }
  document.documentElement.lang = ocaLang;
}

function ingestOca(bundle) {
  ocaBundle = bundle;
  applyOcaLanguage(ocaLang);
}

function ocaLabel(pointer, fallback) {
  return oca.labels[pointer] || fallback;
}

function applyOcaLabels() {
  document.querySelectorAll("[data-oca]").forEach((el) => {
    const pointer = el.getAttribute("data-oca");
    if (!pointer) return;
    // Status / value nodes keep computed text; OCA still supplies hover help.
    if (!el.hasAttribute("data-oca-tip-only")) {
      const label = oca.labels[pointer];
      if (label) el.textContent = label;
    }
    const tip = oca.info[pointer];
    if (tip) {
      el.setAttribute("title", tip);
      el.classList.add("oca-tipped");
    } else {
      el.removeAttribute("title");
      el.classList.remove("oca-tipped");
    }
  });
}

function setOcaLangToggle(lang) {
  document.querySelectorAll("[data-oca-lang]").forEach((btn) => {
    const active = btn.getAttribute("data-oca-lang") === lang;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function switchOcaLanguage(lang) {
  const next = lang === "fr" ? "fr" : "en";
  if (next === ocaLang && Object.keys(oca.labels).length) {
    setOcaLangToggle(next);
    return;
  }
  applyOcaLanguage(next);
  setOcaLangToggle(next);
  if (currentCredential) {
    renderAssessment(currentCredential, currentPublicationPayload);
  } else {
    applyOcaLabels();
  }
}

let currentCredential = null;
let currentPublicationPayload = null;

function firstAssessment(subject) {
  const raw = subject?.conformityAssessment;
  if (Array.isArray(raw)) return raw[0] || {};
  return raw || {};
}

function formatDate(value) {
  if (!value) return "—";
  const iso = String(value).split("T")[0];
  const date = new Date(`${iso}T12:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-CA", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function humanizeToken(value) {
  if (!value) return "—";
  return String(value)
    .replace(/[-._]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function truncateMiddle(value, head = 28, tail = 12) {
  const text = String(value || "");
  if (text.length <= head + tail + 3) return text;
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value || "";
}

function setLink(id, href, label) {
  const el = document.getElementById(id);
  if (!el) return;
  if (href) {
    el.href = href;
    el.textContent = label || href;
    el.classList.remove("d-none");
  } else {
    el.removeAttribute("href");
    el.textContent = "—";
  }
}

function formatMeasure(measure) {
  if (!measure) return "";
  const unit = UNIT_LABELS[measure.unit] || measure.unit || "";
  return `Measure: ${measure.value}${unit ? ` (${unit})` : ""}`;
}

function extractLocation(facilityEntry) {
  const facility = facilityEntry?.facility || facilityEntry || {};
  const loc = facility.locationInformation;
  if (!loc) return { label: "", uri: "" };
  if (typeof loc === "string") return { label: "Plus Code", uri: loc };
  const plus = loc.plusCode || loc.plus_code;
  if (plus) return { label: "Plus Code", uri: plus };
  return { label: "", uri: "" };
}

function productImageFor(name) {
  const slug = String(name || "").toLowerCase();
  if (slug.includes("aggregate")) return `${IMAGE_BASE}/product-aggregate.svg`;
  if (slug.includes("metallurg")) return `${IMAGE_BASE}/product-metallurgic.svg`;
  return `${IMAGE_BASE}/product-aggregate.svg`;
}

function buildFigures(data) {
  const figures = [
    {
      id: "untp-credential",
      label: ocaLabel("/name", "Credential name"),
      title: data.credentialTypes,
      image: `${IMAGE_BASE}/untp-credential.svg`,
      imageAlt: "UNTP Digital Conformity Credential card",
      description:
        oca.info["/name"] ||
        "The published artefact is a W3C Verifiable Credential typed as DigitalConformityCredential under the UNTP 0.7.0 vocabulary. One conformity assessment inside the attestation represents the Mines Act permit.",
      featured: true,
      envelope: true,
    },
    {
      id: "issuer",
      label: ocaLabel("/issuer/name", "Issuer name"),
      title: data.issuerName,
      image: `${IMAGE_BASE}/issuer-cpo.svg`,
      imageAlt: "Chief Permitting Officer authority seal",
      description:
        oca.info["/issuer/name"] ||
        `Credentials are issued by the ${data.issuerName} using DID ${data.issuerDid}. This authority attests that the conformity assessment satisfies the Mines Act permit register.`,
      link: data.issuerDid,
      envelope: true,
    },
    {
      id: "organisation",
      label: ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
        "Organisation name",
      ),
      title: data.orgName,
      image: `${IMAGE_BASE}/organisation.svg`,
      imageAlt: "Registered mining organisation",
      description:
        oca.info[
          "/credentialSubject/conformityAssessment/0/assessedOrganisation/name"
        ] ||
        `${data.orgName} is the permit holder recorded as both issuedToParty and assessedOrganisation. ${data.orgId || "Registry identifiers link the party to BC Registry."}`,
      link: data.orgUri,
      scope: true,
    },
    {
      id: "facility",
      label: ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
        "Facility name",
      ),
      title: data.facilityName,
      image: `${IMAGE_BASE}/facility-mine.svg`,
      imageAlt: "Assessed mine facility",
      description:
        oca.info[
          "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name"
        ] ||
        `${data.facilityName} is the mine or facility in scope. ${data.facilityId || ""} ${data.facilityVerified ? "Facility identifiers were verified by the conformity assessment body." : "Facility identifiers were supplied in the publication payload."}`.trim(),
      link: data.facilityUri,
      scope: true,
    },
  ];

  if (data.facilityLocationUri) {
    figures.push({
      id: "location",
      label: ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/locationInformation/plusCode",
        "Facility Plus Code",
      ),
      title: data.facilityName,
      image: `${IMAGE_BASE}/location-map.svg`,
      imageAlt: "Facility location on a map",
      description:
        oca.info[
          "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/locationInformation/plusCode"
        ] ||
        `Geographic reference for the assessed facility using an Open Location Code (Plus Code): ${data.facilityLocationUri}. The code resolves to a physical area for the mine site; it is stored in assessedFacility.locationInformation.`,
      link: data.facilityLocationUri,
      scope: true,
    });
  }

  for (const [index, product] of data.products.entries()) {
    figures.push({
      id: `product-${index}`,
      label: ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
        "Product name",
      ),
      title: product.name,
      image: productImageFor(product.name),
      imageAlt: `Assessed product: ${product.name}`,
      description:
        oca.info[
          "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name"
        ] ||
        `${product.name} is listed under assessedProduct in the conformity assessment. ${product.verified ? "Product identifiers were verified by the CAB." : "Product scope is declared in the publication payload; ID verification was not asserted."}`,
      link: product.id,
      scope: true,
    });
  }

  figures.push(
    {
      id: "governance",
      label: ocaLabel("/credentialSubject/referenceProfile/name", "Governance profile"),
      title: data.profile,
      image: `${IMAGE_BASE}/governance-profile.svg`,
      imageAlt: "BC Mines Act Permit Governance profile",
      description:
        oca.info["/credentialSubject/referenceProfile/name"] ||
        `${data.profile} defines how permit data is modelled as a UNTP conformity attestation, including cardinality rules, assessed facility/product arrays, and performance metrics.`,
      link: data.profileUri,
      envelope: true,
    },
    {
      id: "statute",
      label: ocaLabel("/credentialSubject/referenceScheme/name", "Legal scheme"),
      title: data.scheme,
      image: `${IMAGE_BASE}/mines-act-statute.svg`,
      imageAlt: "Mines Act reference scheme",
      description:
        oca.info["/credentialSubject/referenceScheme/name"] ||
        `${data.scheme} is the conformity scheme (referenceScheme). referenceRegulation cites the Health, Safety and Reclamation Code; assessmentCriteria points at Mines Act s.10 (Permits).`,
      link: data.schemeUri,
      envelope: true,
    },
  );

  if (data.proof) {
    figures.push({
      id: "proof",
      label: "Cryptographic proof",
      title: data.proof.type,
      image: `${IMAGE_BASE}/untp-credential.svg`,
      imageAlt: "Signed credential verification",
      description:
        `This sample includes a ${data.proof.cryptosuite} Data Integrity proof with purpose ${data.proof.purpose}. Verification uses ${data.proof.verificationMethod}.`,
      envelope: true,
    });
  }

  return figures;
}

function createFigureCard(figure) {
  const card = document.createElement("figure");
  card.className = `figure-card${figure.featured ? " figure-card-featured" : ""}`;
  card.dataset.figureId = figure.id;

  const media = document.createElement("div");
  media.className = "figure-media";
  const img = document.createElement("img");
  img.src = figure.image;
  img.alt = figure.imageAlt;
  img.loading = "lazy";
  img.width = 320;
  img.height = 200;
  media.appendChild(img);

  const caption = document.createElement("figcaption");
  caption.className = "figure-caption-wrap";

  const head = document.createElement("div");
  head.className = "figure-head";

  const kicker = document.createElement("span");
  kicker.className = "figure-kicker";
  kicker.textContent = figure.label;

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "figure-toggle";
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", `figure-desc-${figure.id}`);
  toggle.innerHTML =
    '<span class="figure-toggle-show">About this image</span><span class="figure-toggle-hide d-none">Hide description</span>';

  head.append(kicker, toggle);

  const title = document.createElement("p");
  title.className = "figure-title";
  title.textContent = figure.title;

  const description = document.createElement("div");
  description.className = "figure-description d-none";
  description.id = `figure-desc-${figure.id}`;
  const descText = document.createElement("p");
  descText.textContent = figure.description;
  description.appendChild(descText);

  if (figure.link) {
    const link = document.createElement("a");
    link.className = "figure-link";
    link.href = figure.link;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = truncateMiddle(figure.link, 42, 18);
    link.title = figure.link;
    description.appendChild(link);
  }

  caption.append(head, title, description);
  card.append(media, caption);

  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
    description.classList.toggle("d-none", expanded);
    toggle.querySelector(".figure-toggle-show")?.classList.toggle("d-none", !expanded);
    toggle.querySelector(".figure-toggle-hide")?.classList.toggle("d-none", expanded);
  });

  return card;
}

function renderFigureGallery(container, figures) {
  if (!container) return;
  container.replaceChildren();
  for (const figure of figures) {
    container.appendChild(createFigureCard(figure));
  }
}

function renderScopeFigures(container, figures) {
  if (!container) return;
  const scopeFigures = figures.filter((figure) => figure.scope);
  container.replaceChildren();
  for (const figure of scopeFigures) {
    container.appendChild(createFigureCard(figure));
  }
  container.classList.toggle("d-none", scopeFigures.length === 0);
}

export function normalizeAssessment(vc) {
  const subject = vc?.credentialSubject || {};
  const assessment = firstAssessment(subject);
  const party = subject.issuedToParty || assessment.assessedOrganisation || {};
  const org = assessment.assessedOrganisation || party;
  const facilityEntry = (assessment.assessedFacility || [])[0] || {};
  const facility = facilityEntry.facility || facilityEntry;
  const location = extractLocation(facilityEntry);
  const products = (assessment.assessedProduct || []).map((entry) => ({
    name: entry.product?.name || entry.name || "Product",
    id: entry.product?.id || "",
    verified: entry.idVerifiedByCAB === true,
  }));
  const criteria = (assessment.assessmentCriteria || [])[0] || {};
  const performance = (assessment.assessedPerformance || [])[0] || {};
  const evidenceItems = Array.isArray(assessment.evidence)
    ? assessment.evidence
    : assessment.evidence
      ? [assessment.evidence]
      : [];
  const evidence = evidenceItems[0] || {};
  const regulations = Array.isArray(assessment.referenceRegulation)
    ? assessment.referenceRegulation
    : assessment.referenceRegulation
      ? [assessment.referenceRegulation]
      : [];
  const regulation = regulations[0] || {};
  const proof = Array.isArray(vc?.proof) ? vc.proof[0] : vc?.proof;
  const credentialTypes = Array.isArray(vc?.type) ? vc.type : [vc?.type].filter(Boolean);
  const contexts = Array.isArray(vc?.["@context"])
    ? vc["@context"]
    : [vc?.["@context"]].filter(Boolean);

  return {
    assessmentName: assessment.name || subject.name || "—",
    assessmentDescription:
      assessment.description ||
      subject.description ||
      "This conformity assessment is the Mines Act permit.",
    subjectDescription: subject.description || "",
    registeredId: assessment.registeredId || "—",
    assessmentDate: formatDate(assessment.assessmentDate || vc.validFrom),
    validFrom: formatDate(vc.validFrom),
    idScheme: assessment.idScheme?.name || "BC Mines NRS",
    registerScheme: assessment.idScheme?.id
      ? `${assessment.idScheme.name || "Permit register"}`
      : "BC Mines Act Permit Register",
    assessmentUri: assessment.id || "",
    subjectUri: subject.id || "",
    assessmentLevel: humanizeToken(subject.assessmentLevel),
    assessorLevel: humanizeToken(subject.assessorLevel),
    attestationType: humanizeToken(subject.attestationType),
    conformityTopic:
      assessment.conformityTopic?.name ||
      criteria.conformityTopic?.name ||
      "Governance.Compliance",
    partyName: party.name || "—",
    partyId: party.registeredId ? `BC Registry · ${party.registeredId}` : "",
    partyUri: party.id || "",
    orgName: org.name || party.name || "—",
    orgId: org.registeredId ? `BC Registry · ${org.registeredId}` : "",
    orgUri: org.id || party.id || "",
    facilityName: facility.name || "—",
    facilityId: facility.registeredId ? `Mine ID · ${facility.registeredId}` : "",
    facilityUri: facility.id || "",
    facilityLocation: location.uri ? `${location.label}: ${location.uri}` : "",
    facilityLocationUri: location.uri || "",
    facilityVerified: facilityEntry.idVerifiedByCAB === true,
    products,
    scheme: subject.referenceScheme?.name || "Mines Act (British Columbia)",
    schemeUri: subject.referenceScheme?.id || "",
    profile: subject.referenceProfile?.name || "BC Mines Act Permit Governance",
    profileUri: subject.referenceProfile?.id || "",
    issuerName: vc.issuer?.name || "Chief Permitting Officer",
    issuerDid: vc.issuer?.id || "",
    regulation:
      regulations.map((r) => r.name).filter(Boolean).join(" · ") ||
      subject.referenceScheme?.name ||
      "Mines Act",
    regulationUri: regulation.id || subject.referenceScheme?.id || "",
    regulations: regulations
      .filter((r) => r?.id || r?.name)
      .map((r) => ({ name: r.name || r.id, id: r.id || "" })),
    criteria: criteria.name || "Mines Act",
    criteriaUri: criteria.id || "",
    metric: performance.metric?.name || "Permit issued",
    metricUri: performance.metric?.id || "",
    measure: formatMeasure(performance.measure),
    evidenceName: evidence.linkName || "",
    evidenceUri: evidence.linkURL || "",
    evidence: evidenceItems
      .filter((item) => item?.linkURL || item?.linkName)
      .map((item) => ({
        name: item.linkName || item.linkURL,
        id: item.linkURL || "",
      })),
    conformance: assessment.conformance !== false,
    credentialId: vc.id || "(assigned at issuance)",
    credentialTypes: credentialTypes.join(" · ") || "DigitalConformityCredential",
    contexts,
    proof: proof
      ? {
          type: proof.type || "—",
          cryptosuite: proof.cryptosuite || "—",
          purpose: proof.proofPurpose || "—",
          verificationMethod: proof.verificationMethod || "—",
          proofValue: proof.proofValue || "—",
        }
      : null,
    links: buildLinks(vc, {
      subject,
      assessment,
      party,
      org,
      facility,
      facilityEntry,
      products,
      criteria,
      performance,
      regulations,
      evidenceItems,
    }),
    fieldMap: buildFieldMap(vc, {
      subject,
      assessment,
      party,
      org,
      facility,
      products,
      criteria,
      performance,
      evidence,
    }),
  };
}

function buildLinks(vc, parts) {
  const {
    subject,
    assessment,
    party,
    org,
    facility,
    products,
    criteria,
    performance,
    regulations,
    evidenceItems,
  } = parts;
  const rows = [];

  const push = (role, label, uri) => {
    if (!uri) return;
    rows.push({ role, label, uri });
  };

  push("Credential", "Credential ID", vc.id);
  push("Issuer", vc.issuer?.name || "Issuer", vc.issuer?.id);
  push("Attestation", "Subject", subject.id);
  push("Assessment", assessment.name || "Assessment", assessment.id);
  push("Register", assessment.idScheme?.name, assessment.idScheme?.id);
  push("Party", party.name, party.id);
  push("Party", "BC Registry scheme", party.idScheme?.id);
  push("Organisation", org.name, org.id);
  push("Facility", facility.name, facility.id);
  push("Scheme", subject.referenceScheme?.name, subject.referenceScheme?.id);
  push("Profile", subject.referenceProfile?.name, subject.referenceProfile?.id);
  for (const reg of regulations || []) {
    push("Regulation", reg.name, reg.id);
  }
  push("Criterion", criteria.name, criteria.id);
  push("Topic", assessment.conformityTopic?.name, assessment.conformityTopic?.id);
  push("Metric", performance.metric?.name, performance.metric?.id);
  for (const item of evidenceItems || []) {
    push("Evidence", item.linkName || "Evidence", item.linkURL);
  }

  for (const product of products) {
    push("Product", product.name, product.id);
  }

  return rows;
}

function buildFieldMap(vc, parts) {
  const { subject, assessment, party, org, facility, products, evidence } = parts;
  const rows = [
    [ocaLabel("/name", "Credential name"), "/name", vc.name],
    [ocaLabel("/validFrom", "Valid from"), "/validFrom", vc.validFrom],
    [ocaLabel("/issuer/id", "Issuer ID"), "/issuer/id", vc.issuer?.id],
    [ocaLabel("/issuer/name", "Issuer name"), "/issuer/name", vc.issuer?.name],
    [
      ocaLabel("/credentialSubject/id", "Permit URI"),
      "/credentialSubject/id",
      subject.id,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/registeredId",
        "Permit number",
      ),
      "/credentialSubject/conformityAssessment/0/registeredId",
      assessment.registeredId,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessmentDate",
        "Assessment date",
      ),
      "/credentialSubject/conformityAssessment/0/assessmentDate",
      assessment.assessmentDate,
    ],
    [
      ocaLabel("/credentialSubject/issuedToParty/name", "Holder name"),
      "/credentialSubject/issuedToParty/name",
      party.name,
    ],
    [
      ocaLabel("/credentialSubject/issuedToParty/registeredId", "BC Registry ID"),
      "/credentialSubject/issuedToParty/registeredId",
      party.registeredId,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
        "Organisation name",
      ),
      "/credentialSubject/conformityAssessment/0/assessedOrganisation/name",
      org.name,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
        "Facility name",
      ),
      "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/name",
      facility.name,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
        "Facility registration ID",
      ),
      "/credentialSubject/conformityAssessment/0/assessedFacility/0/facility/registeredId",
      facility.registeredId,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
        "Product name",
      ),
      "/credentialSubject/conformityAssessment/0/assessedProduct/0/product/name",
      products[0]?.name,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/evidence/0/linkName",
        "Evidence name",
      ),
      "/credentialSubject/conformityAssessment/0/evidence/0/linkName",
      evidence?.linkName,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/evidence/0/linkURL",
        "Evidence URI",
      ),
      "/credentialSubject/conformityAssessment/0/evidence/0/linkURL",
      evidence?.linkURL,
    ],
    [
      ocaLabel(
        "/credentialSubject/conformityAssessment/0/conformance",
        "Conforms",
      ),
      "/credentialSubject/conformityAssessment/0/conformance",
      assessment.conformance,
    ],
  ];
  return rows
    .filter(([, , value]) => value !== undefined && value !== null && value !== "")
    .map(([label, path, value]) => ({ label, path, value: String(value) }));
}

function renderProducts(productsEl, products) {
  productsEl.replaceChildren();
  const items = products.length
    ? products
    : [{ name: "As stated in permit scope", verified: false, id: "" }];
  for (const product of items) {
    const li = document.createElement("li");
    li.className = "product-row";
    const main = document.createElement("div");
    main.className = "product-main";
    const nameSpan = document.createElement("span");
    nameSpan.className = "product-name";
    nameSpan.textContent = product.name;
    main.appendChild(nameSpan);
    if (product.id) {
      const link = document.createElement("a");
      link.className = "product-uri";
      link.href = product.id;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = truncateMiddle(product.id);
      main.appendChild(link);
    }
    li.appendChild(main);
    const badge = document.createElement("span");
    badge.className = `verify-badge ${product.verified ? "verified" : "unverified"}`;
    badge.textContent = product.verified ? "ID verified" : "Not verified";
    li.appendChild(badge);
    productsEl.appendChild(li);
  }
}

function renderLinksTable(tbody, links) {
  tbody.replaceChildren();
  for (const link of links) {
    const tr = document.createElement("tr");
    const role = document.createElement("td");
    role.textContent = link.role;
    const label = document.createElement("td");
    label.textContent = link.label;
    const uri = document.createElement("td");
    const a = document.createElement("a");
    a.href = link.uri;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = truncateMiddle(link.uri, 36, 16);
    a.title = link.uri;
    uri.appendChild(a);
    tr.append(role, label, uri);
    tbody.appendChild(tr);
  }
}

function renderContextList(listEl, contexts) {
  listEl.replaceChildren();
  for (const ctx of contexts) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = ctx;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = truncateMiddle(ctx, 40, 20);
    a.title = ctx;
    li.appendChild(a);
    listEl.appendChild(li);
  }
}

function renderFieldMap(tbody, rows) {
  tbody.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${row.label}</td><td><code>${row.path}</code></td><td>${row.value}</td>`;
    tbody.appendChild(tr);
  }
}

export function renderAssessment(vc, publicationPayload = null) {
  applyOcaLabels();
  const data = normalizeAssessment(vc);

  setText("field-scheme", data.scheme);
  setText("field-subject-description", data.subjectDescription || "One assessment represents this Mines Act permit");
  setText("field-credential-types", "UNTP 0.7.0");
  setText("field-valid-from", data.validFrom);
  setText("field-validity-assessment-date", data.assessmentDate);
  setText("field-assessment-name", data.assessmentName);
  setText("field-assessment-description", data.assessmentDescription);
  setText("field-registered-id", data.registeredId);
  setText("field-assessment-date", data.assessmentDate);
  setText("field-id-scheme", data.idScheme);
  setText("field-register-scheme", data.registerScheme);
  setText("field-assessment-level", data.assessmentLevel);
  setText("field-assessor-level", data.assessorLevel);
  setText("field-attestation-type", data.attestationType);
  setText("field-conformity-topic", data.conformityTopic);
  setText("field-party-name", data.partyName);
  setText("field-party-id", data.partyId);
  setText("field-issuer", data.issuerName);
  setText("field-issuer-did", data.issuerDid);
  setText("field-profile-link", data.profile);
  setLink("field-profile-link", data.profileUri, data.profile);
  setText("field-scheme-link", data.scheme);
  setLink("field-scheme-link", data.schemeUri, data.scheme);
  setText("field-org-name", data.orgName);
  setText("field-org-id", data.orgId);
  setLink("field-org-uri", data.orgUri, truncateMiddle(data.orgUri));
  setText("field-facility-name", data.facilityName);
  setText("field-facility-id", data.facilityId);
  setText("field-facility-location", data.facilityLocation);
  setLink("field-facility-uri", data.facilityUri, truncateMiddle(data.facilityUri));
  const regulationList = document.getElementById("field-regulation-list");
  if (regulationList && data.regulations?.length) {
    regulationList.innerHTML = "";
    for (const reg of data.regulations) {
      const name = document.createElement("p");
      name.className = "criteria-value";
      name.textContent = reg.name || "—";
      regulationList.appendChild(name);
      if (reg.id) {
        const link = document.createElement("a");
        link.className = "criteria-link";
        link.href = reg.id;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = truncateMiddle(reg.id);
        regulationList.appendChild(link);
      }
    }
  } else {
    setText("field-regulation", data.regulation);
    setLink("field-regulation-link", data.regulationUri, truncateMiddle(data.regulationUri));
  }
  setText("field-criteria", data.criteria);
  setLink("field-criteria-link", data.criteriaUri, truncateMiddle(data.criteriaUri));
  setText("field-metric", data.metric);
  setText("field-measure", data.measure);
  setLink("field-metric-link", data.metricUri, truncateMiddle(data.metricUri));
  const evidenceBlock = document.getElementById("evidence-block");
  const evidenceList = document.getElementById("field-evidence-list");
  if (evidenceBlock) {
    evidenceBlock.classList.toggle("d-none", !(data.evidence?.length));
  }
  if (evidenceList && data.evidence?.length) {
    evidenceList.innerHTML = "";
    for (const item of data.evidence) {
      const name = document.createElement("p");
      name.className = "criteria-value";
      name.textContent = item.name || "—";
      evidenceList.appendChild(name);
      if (item.id) {
        const link = document.createElement("a");
        link.className = "criteria-link";
        link.href = item.id;
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = truncateMiddle(item.id);
        evidenceList.appendChild(link);
      }
    }
  } else {
    setText("field-evidence", data.evidenceName || "—");
    setLink(
      "field-evidence-link",
      data.evidenceUri,
      truncateMiddle(data.evidenceUri),
    );
  }
  setText("field-credential-id", data.credentialId);

  setText("stat-party", data.partyName);
  setText("stat-facility", data.facilityName);
  setText("stat-products", data.products.length ? data.products.map((p) => p.name).join(", ") : "—");
  setText("stat-profile", data.profile);

  setText("flow-issuer", data.issuerName);
  setText("flow-assessment-id", data.registeredId);
  setText("flow-org", data.orgName);
  setText("flow-facility", data.facilityName);
  setText("flow-products", data.products.map((p) => p.name).join(", ") || "—");

  const conformanceEl = document.getElementById("field-conformance");
  if (conformanceEl) {
    conformanceEl.textContent = data.conformance ? "Conforms" : "Non-conforming";
    conformanceEl.classList.toggle("non-conforming", !data.conformance);
  }

  const facilityVerifiedEl = document.getElementById("field-facility-verified");
  if (facilityVerifiedEl) {
    facilityVerifiedEl.textContent = data.facilityVerified ? "ID verified" : "Not verified";
    facilityVerifiedEl.classList.toggle("verified", data.facilityVerified);
    facilityVerifiedEl.classList.toggle("unverified", !data.facilityVerified);
  }

  const productsEl = document.getElementById("field-products");
  if (productsEl) renderProducts(productsEl, data.products);

  setLink("field-assessment-uri", data.assessmentUri, data.assessmentUri);
  setLink("field-subject-uri", data.subjectUri, data.subjectUri);

  const linksBody = document.getElementById("links-table-body");
  if (linksBody) renderLinksTable(linksBody, data.links);

  const contextList = document.getElementById("field-context-list");
  if (contextList) renderContextList(contextList, data.contexts);

  const proofPanel = document.getElementById("proof-panel");
  if (proofPanel) {
    if (data.proof) {
      proofPanel.classList.remove("d-none");
      setText("field-proof-type", data.proof.type);
      setText("field-proof-cryptosuite", data.proof.cryptosuite);
      setText("field-proof-purpose", data.proof.purpose);
      setText("field-proof-vm", data.proof.verificationMethod);
      setText("field-proof-value", data.proof.proofValue);
    } else {
      proofPanel.classList.add("d-none");
    }
  }

  const jsonCredential = document.getElementById("json-credential");
  if (jsonCredential) {
    jsonCredential.textContent = JSON.stringify(vc, null, 2);
  }

  const jsonPublication = document.getElementById("json-publication");
  if (jsonPublication) {
    jsonPublication.textContent = publicationPayload
      ? JSON.stringify(publicationPayload, null, 2)
      : "— Publication payload not available for this sample.";
  }

  const signedBadge = document.getElementById("technical-signed-badge");
  if (signedBadge) {
    signedBadge.textContent = data.proof ? "Signed (sample proof)" : "Unsigned";
    signedBadge.classList.toggle("is-signed", Boolean(data.proof));
  }

  const fieldMapBody = document.getElementById("field-map-body");
  if (fieldMapBody) renderFieldMap(fieldMapBody, data.fieldMap);

  const figures = buildFigures(data);
  renderFigureGallery(
    document.getElementById("figure-gallery"),
    figures.filter((figure) => figure.envelope),
  );
  renderScopeFigures(
    document.getElementById("scope-figures"),
    figures.filter((figure) => figure.scope),
  );
}

async function loadOcaBundle() {
  const ocaUrl = document.body?.dataset?.ocaUrl;
  if (!ocaUrl) {
    ingestOca(null);
    return;
  }
  const ocaRes = await fetch(ocaUrl);
  if (ocaRes.ok) {
    ingestOca(await ocaRes.json());
  } else {
    ingestOca(null);
  }
}

async function loadSample(key) {
  const api = sampleApiBase();
  await loadOcaBundle();
  if (key === "kootenay") {
    const response = await fetch(`${api}/build`);
    if (!response.ok) throw new Error("Could not build sample credential");
    const body = await response.json();
    return {
      credential: body.credential,
      publicationPayload: body.publicationPayload,
    };
  }
  const [credRes, pubRes] = await Promise.all([
    fetch(`${api}/sample`),
    fetch(`${api}/publication-payload`),
  ]);
  if (!credRes.ok) throw new Error("Could not load sample credential");
  const credential = await credRes.json();
  const publicationPayload = pubRes.ok ? await pubRes.json() : null;
  return { credential, publicationPayload };
}

function showToast(message) {
  const toast = document.getElementById("toast");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.remove("d-none");
  window.setTimeout(() => toast.classList.add("d-none"), 2400);
}

function setActiveView(view) {
  document.querySelectorAll(".view-tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    const active = panel.id === `panel-${view}`;
    panel.classList.toggle("is-active", active);
    panel.hidden = !active;
  });
}

async function showSample(key) {
  const loading = document.getElementById("loading");
  const panels = document.getElementById("preview-panels");
  loading.classList.remove("d-none");
  panels.classList.add("d-none");

  try {
    const { credential, publicationPayload } = await loadSample(key);
    currentCredential = credential;
    currentPublicationPayload = publicationPayload;
    renderAssessment(credential, publicationPayload);
    loading.classList.add("d-none");
    panels.classList.remove("d-none");
  } catch (err) {
    loading.textContent = err.message || "Failed to load conformity assessment.";
  }
}

document.getElementById("btn-print")?.addEventListener("click", () => {
  window.print();
});

document.getElementById("btn-copy-json")?.addEventListener("click", async () => {
  if (!currentCredential) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(currentCredential, null, 2));
    showToast("Credential JSON copied to clipboard");
  } catch {
    showToast("Could not copy to clipboard");
  }
});

document.getElementById("sample-select")?.addEventListener("change", (event) => {
  showSample(event.target.value);
});

document.querySelectorAll(".view-tab").forEach((tab) => {
  tab.addEventListener("click", () => setActiveView(tab.dataset.view));
});

document.querySelectorAll("[data-oca-lang]").forEach((btn) => {
  btn.addEventListener("click", () => switchOcaLanguage(btn.getAttribute("data-oca-lang")));
});

showSample("kootenay");
