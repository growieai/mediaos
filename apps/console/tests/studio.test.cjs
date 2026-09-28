// Offline real event-handler regressions. No provider calls, browser or credentials.
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { load, mount, elements, text, change, field, form, submit, deferred } = require("./component-harness.cjs");

const icons = { default: () => null, categoryIcon: { business: "book" } };
const dependencies = { "react": {}, "react/jsx-runtime": {}, "./Icons": icons, "./Modal": { default: () => null } };
function exported(name, overrides = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../app/studio", name), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)(name => {
    if (name in overrides) return overrides[name];
    if (name in dependencies) return dependencies[name];
    throw new Error(`Unexpected import: ${name}`);
  }, result, result.exports);
  return result.exports;
}
const types = exported("types.ts");
const { evidenceFrom, manualEvidenceRisk, manualGeneralEvidence } = exported("SourceComposer.tsx", { "./types": types });
const { creatorPayload } = exported("CreatorWizard.tsx", { "./types": types });
const session = { tenant: "tenant-a", token: "local-test-token" };
const creator = { id: "creator-a", mission_id: "mission-a", name: "Local Educator", category_id: "business", visual_config_version_id: "visual-a", portrait_available: false };
const categories = [{ id: "business", name: "Business", description: "Practical business content", accent: "#000000" }];
function button(host, label) {
  const result = elements(host.tree).find(node => node.type === "button" && text(node).trim() === label);
  assert.ok(result, `button exists: ${label}`);
  return result;
}
function click(host, label) { const node = button(host, label); assert.ok(!node.props.disabled, `${label} enabled`); node.props.onClick(); host.render(); }
function setup(name, props, handler = async () => ({})) {
  const calls = [];
  const request = async (...args) => { calls.push(args); return handler(...args); };
  const component = load(`studio/${name}.tsx`, {
    "./types": { ...types, request }, "./Icons": icons, "./Modal": { default: () => null },
    ...Object.fromEntries(["VisualReview", "SocialPanel", "MediaPanel", "CommunityReview", "ConversionPanel"].map(key => [`../${key}`, { default: () => null }])),
  });
  return { host: mount(component, props), calls };
}
function composer(handler) {
  const created = [];
  const props = { session, creators: [creator], initialCreatorId: creator.id, onClose() {}, onCreated: async value => { created.push(value); } };
  return { ...setup("SourceComposer", props, handler), props, created };
}
function fillSource(host) {
  change(host, "Story / source title", "Supported story"); change(host, "Source URL or origin", "https://example.invalid/source");
  change(host, "Publisher", "Source publisher"); change(host, "Original source text", "Prefix. Source evidence supports each factual claim. Further details.");
  change(host, "Evidence 1", "Source evidence supports each factual claim.");
  change(host, "Claim type", "GENERAL_EVERGREEN"); change(host, "Source relationship", "INTERNAL");
  change(host, "I confirm these excerpts are evergreen general facts", true);
}
function wizard(handler) {
  const created = [];
  const props = { session, categories, initialCategory: "business", onClose() {}, onCreated: async value => { created.push(value); } };
  return { ...setup("CreatorWizard", props, handler), props, created };
}
function fillCreator(host) {
  submit(host, 0); change(host, "Creator name", "  New Educator  "); change(host, "Who is their content for?", "Owners, Local teams");
  submit(host, 0); change(host, "Editorial mission", "Explain practical opportunities from official evidence.");
}
function channels(handler, extra = {}) {
  const props = { session, creators: [creator, { ...creator, id: "creator-b", name: "Another Creator" }], accounts: { connections: [], revocations: [] }, dependencies: { connect_enabled: true, app_configured: true, vault_configured: true }, admin: true, refresh: async () => {}, ...extra };
  return { ...setup("Channels", props, handler), props };
}
const oauth = "https://www.instagram.com/oauth/authorize?client_id=fixture&state=fixture";

test("source evidence preserves exact excerpts and source offsets", () => {
  const raw = "First.  Applications open in October. Last.";
  const [evidence] = evidenceFrom(raw, [" Applications open in October. "]);
  assert.deepEqual(evidence, { start: 8, end: 37, statement: "Applications open in October." });
  assert.equal(raw.slice(evidence.start, evidence.end), evidence.statement);
});

test("source spans count Unicode code points like PostgreSQL and Python", () => {
  for (const [raw, statement] of [["🌍 News: Applications open.", "Applications open."], ["News: Grants 🪴 open.", "Grants 🪴 open."]]) {
    const [evidence] = evidenceFrom(raw, [statement]);
    assert.equal(Array.from(raw).slice(evidence.start, evidence.end).join(""), statement);
    assert.equal(evidence.end - evidence.start, Array.from(statement).length);
  }
});

test("source evidence rejects invented paraphrases, repeated evidence and oversized excerpts", () => {
  assert.throws(() => evidenceFrom("Applications open in October.", ["All businesses receive money."]), /exactly/);
  assert.throws(() => evidenceFrom("Supported text.", ["Supported text.", "Supported text."]), /once/);
  assert.throws(() => evidenceFrom("x".repeat(2001), ["x".repeat(2001)]), /2,000/);
});

test("creator payload contains only entered identity and category data", () => {
  assert.deepEqual(creatorPayload({ name: "  Someone  ", category_id: "business", language: "es", tone: "WARM", audience: " Owners, , Teams ", objective: " Useful verified notes " }),
    { name: "Someone", category_id: "business", language: "es", tone: "WARM", audience: ["Owners", "Teams"], objective: "Useful verified notes" });
});

test("saving a source preserves exact text, explicit fixture flag and generates no content", async () => {
  const { host, calls, created } = composer(async () => ({ id: "run-a" })); fillSource(host);
  change(host, "This is test or demonstration evidence.", true); submit(host, 0); await host.settle();
  assert.equal(calls.length, 1); const [, endpoint, method, body] = calls[0];
  assert.equal(endpoint, "workflow-runs"); assert.equal(method, "POST");
  assert.equal(body.influencer_id, creator.id); assert.equal(body.mission_id, creator.mission_id);
  assert.equal(body.source.is_fixture, true); assert.equal(body.source.source_type, "MANUAL");
  assert.equal(body.source.classification, "INTERNAL"); assert.equal(body.source.evidence[0].fact_type, "GENERAL");
  assert.deepEqual(body.source.metadata, { manual_claim_classification: "GENERAL_EVERGREEN", manual_classification_policy: "evergreen-review-v1", general_claim_declaration: "USER_CONFIRMED" });
  assert.equal(body.source.raw_content.slice(body.source.evidence[0].start, body.source.evidence[0].end), body.source.evidence[0].statement);
  assert.equal(created.length, 1); assert.ok(body.idempotency_key); assert.ok(body.source.captured_at);
});

test("unsupported manual evidence never sends a workflow request", async () => {
  const { host, calls } = composer(); fillSource(host); change(host, "Evidence 1", "An invented fact."); submit(host, 0); await host.settle();
  assert.equal(calls.length, 0); assert.match(text(host.tree), /exactly/);
});

test("manual source has no default claim type or source relationship", async () => {
  const { host, calls } = composer();
  assert.equal(field(host, "Claim type").props.value, ""); assert.equal(field(host, "Source relationship").props.value, "");
  assert.equal(button(host, "Save source for review").props.disabled, true);
  submit(host, 0); await host.settle(); assert.equal(calls.length, 0);
  fillSource(host); change(host, "Claim type", ""); submit(host, 0); await host.settle();
  assert.equal(calls.length, 0); assert.match(text(host.tree), /Choose the kind of claims/);
});

test("source span extraction cannot silently classify a grant as GENERAL", () => {
  const statement = "The grant deadline was 1 January 2020.";
  assert.equal("fact_type" in evidenceFrom(statement, [statement])[0], false);
  assert.throws(() => manualGeneralEvidence(statement, [statement], "GENERAL_EVERGREEN", true), /structured verification/);
});

test("evergreen evidence requires an explicit human classification declaration", () => {
  const statement = "Source evidence supports each factual claim.";
  assert.equal(manualEvidenceRisk([statement]), null);
  assert.throws(() => manualGeneralEvidence(statement, [statement], "", true), /Choose the kind/);
  assert.throws(() => manualGeneralEvidence(statement, [statement], "GENERAL_EVERGREEN", false), /Confirm/);
  assert.equal(manualGeneralEvidence(statement, [statement], "GENERAL_EVERGREEN", true)[0].fact_type, "GENERAL");
});

for (const excerpt of [
  "The grant deadline was 1 January 2020.", "Las subvenciones están disponibles para autónomos.",
  "Las pymes son elegibles.", "El plazo finaliza el 15/01/2020.", "Applications open in October.",
  "Applicants can receive €5000.", "The rate is 50%.", "This offer is valid until May 5.",
  "La convocatoria está vigente.", "La financiación es de 500 euros.", "The programme closed in 2020.",
]) {
  test(`time-sensitive or funding evidence cannot be saved as evergreen: ${excerpt}`, async () => {
    const { host, calls } = composer(); fillSource(host);
    change(host, "Original source text", excerpt); change(host, "Evidence 1", excerpt);
    change(host, "I confirm these excerpts are evergreen general facts", true);
    change(host, "This is test or demonstration evidence.", true);
    assert.ok(manualEvidenceRisk([excerpt])); assert.equal(button(host, "Save source for review").props.disabled, true);
    submit(host, 0); await host.settle(); assert.equal(calls.length, 0);
    assert.match(text(host.tree), /Discover opportunities/);
  });
}

test("explicit structured claim selection never falls back to GENERAL", async () => {
  const { host, calls } = composer(); fillSource(host); change(host, "Claim type", "GRANT_OR_TIME_SENSITIVE");
  assert.equal(button(host, "Save source for review").props.disabled, true);
  submit(host, 0); await host.settle(); assert.equal(calls.length, 0);
  assert.match(text(host.tree), /cannot save those claims as general facts/);
});

for (const [label, value] of [["Original source text", "Changed source."], ["Evidence 1", "Changed evidence."], ["Source relationship", "SECONDARY"]]) {
  test(`changing ${label} clears the evergreen declaration`, async () => {
    const { host, calls } = composer(); fillSource(host); change(host, label, value);
    assert.equal(field(host, "I confirm these excerpts are evergreen general facts").props.checked, false);
    assert.equal(button(host, "Save source for review").props.disabled, true);
    submit(host, 0); await host.settle(); assert.equal(calls.length, 0);
  });
}

for (const relationship of ["PRIMARY", "SECONDARY", "INTERNAL"]) {
  test(`manual source preserves explicit ${relationship} relationship`, async () => {
    const { host, calls } = composer(); fillSource(host); change(host, "Source relationship", relationship);
    change(host, "I confirm these excerpts are evergreen general facts", true); submit(host, 0); await host.settle();
    assert.equal(calls.length, 1); assert.equal(calls[0][3].source.classification, relationship);
  });
}

for (const kind of ["source", "creator"]) {
  test(`${kind} form is single-flight and uncertain retries retain exact idempotency`, async () => {
    const response = deferred(); let first = true;
    const fixture = (kind === "source" ? composer : wizard)(async () => { if (first) { first = false; return response.promise; } throw new Error("Response interrupted"); });
    const { host, calls } = fixture; (kind === "source" ? fillSource : fillCreator)(host);
    const handler = form(host, 0).props.onSubmit; handler({ preventDefault() {} }); handler({ preventDefault() {} });
    assert.equal(calls.length, 1); response.reject(new Error("Response interrupted")); await host.settle();
    submit(host, 0); await host.settle(); assert.equal(calls.length, 2);
    assert.deepEqual(calls[0][3], calls[1][3], "retry retains key, payload and capture time");
  });

  test(`${kind} form edits create a new request key`, async () => {
    const { host, calls } = (kind === "source" ? composer : wizard)(async () => { throw new Error("Response interrupted"); });
    (kind === "source" ? fillSource : fillCreator)(host); submit(host, 0); await host.settle();
    change(host, kind === "source" ? "Story / source title" : "Editorial mission", "A different purpose supported by source evidence.");
    submit(host, 0); await host.settle(); assert.notEqual(calls[0][3].idempotency_key, calls[1][3].idempotency_key);
  });

  test(`${kind} reverting an uncertain payload recovers its original request key`, async () => {
    const { host, calls } = (kind === "source" ? composer : wizard)(async () => { throw new Error("Response interrupted"); });
    (kind === "source" ? fillSource : fillCreator)(host); submit(host, 0); await host.settle();
    const label = kind === "source" ? "Story / source title" : "Editorial mission";
    const original = field(host, label).props.value;
    change(host, label, "A different purpose supported by source evidence."); submit(host, 0); await host.settle();
    change(host, label, original); submit(host, 0); await host.settle();
    assert.deepEqual(calls[2][3], calls[0][3]); assert.notEqual(calls[1][3].idempotency_key, calls[0][3].idempotency_key);
  });

  test(`${kind} late completion after unmount cannot open another content panel`, async () => {
    const response = deferred(); const { host, created } = (kind === "source" ? composer : wizard)(() => response.promise);
    (kind === "source" ? fillSource : fillCreator)(host); submit(host, 0); host.unmount(); response.resolve({ id: "old-result" }); await host.settle();
    assert.equal(created.length, 0);
  });

  test(`${kind} tenant change discards old callbacks and form content`, async () => {
    const response = deferred(); const { host, props, calls, created } = (kind === "source" ? composer : wizard)(() => response.promise);
    (kind === "source" ? fillSource : fillCreator)(host); const oldSubmit = form(host, 0).props.onSubmit; submit(host, 0);
    host.render({ ...props, session: { tenant: "tenant-b", token: "different-local-token" } });
    response.resolve({ id: "old-result" }); await host.settle(); oldSubmit({ preventDefault() {} }); await host.settle();
    assert.equal(created.length, 0, "old scope never triggers onCreated");
    assert.equal(calls.length, 1, "stale submit never makes another request");
    assert.ok(!text(host.tree).includes("Supported story"));
  });
}

test("creator cannot advance with an audience consisting only of separators", () => {
  const { host } = wizard(); submit(host, 0); change(host, "Creator name", "Creator"); change(host, "Who is their content for?", ", , ,");
  assert.equal(button(host, "Continue").props.disabled, true);
});

for (const fieldName of ["connect_enabled", "app_configured", "vault_configured"]) {
  test(`missing ${fieldName} prevents OAuth even if handler is invoked`, async () => {
    const deps = { connect_enabled: true, app_configured: true, vault_configured: true }; delete deps[fieldName];
    const { host, calls } = channels(async () => ({ authorization_url: oauth }), { dependencies: deps });
    const connect = button(host, "Connect Instagram"); assert.equal(connect.props.disabled, true);
    connect.props.onClick(); await host.settle(); assert.equal(calls.length, 0);
  });
}

test("only an administrator with a creator can start account connection", async () => {
  for (const extra of [{ admin: false }, { creators: [] }]) {
    const { host, calls } = channels(async () => ({ authorization_url: oauth }), extra);
    button(host, "Connect Instagram").props.onClick(); await host.settle(); assert.equal(calls.length, 0);
  }
});

test("OAuth initialization is single-flight and never publishes a post", async () => {
  const response = deferred(); const { host, calls } = channels(() => response.promise);
  const connect = button(host, "Connect Instagram").props.onClick; connect(); connect(); assert.equal(calls.length, 1);
  response.resolve({ authorization_url: oauth }); await host.settle();
  assert.deepEqual(calls[0].slice(1, 4), ["social/connect", "POST", { influencer_id: creator.id }]);
  const link = elements(host.tree).find(node => node.type === "a"); assert.equal(link.props.href, oauth);
  assert.equal(link.props.rel, "noopener noreferrer");
});

for (const url of ["javascript:alert(1)", "https://instagram.com.evil.invalid/oauth/authorize", "https://www.instagram.com/not-authorize", "https://user:pass@www.instagram.com/oauth/authorize"]) {
  test(`OAuth rejects a noncanonical destination: ${url}`, async () => {
    const { host } = channels(async () => ({ authorization_url: url })); click(host, "Connect Instagram"); await host.settle();
    assert.ok(!elements(host.tree).some(node => node.type === "a"));
    assert.match(text(host.tree), /could not be verified/);
  });
}

test("changing the selected creator discards an in-flight OAuth result", async () => {
  const response = deferred(); const { host } = channels(() => response.promise); click(host, "Connect Instagram");
  change(host, "Influencer", "creator-b"); response.resolve({ authorization_url: oauth }); await host.settle();
  assert.ok(!elements(host.tree).some(node => node.type === "a"), "old creator OAuth link must not appear under new selection");
});

test("tenant change discards pending account authorization", async () => {
  const response = deferred(); const { host, props } = channels(() => response.promise); click(host, "Connect Instagram");
  host.render({ ...props, session: { tenant: "tenant-b", token: "another-token" } }); response.resolve({ authorization_url: oauth }); await host.settle();
  assert.ok(!elements(host.tree).some(node => node.type === "a"));
});

test("current account list omits revoked and superseded connections without inventing accounts", () => {
  const base = { influencer_id: creator.id, username: "fixture", account_id: "same", api_version: "fixture" };
  const accounts = { connections: [{ ...base, id: "old", version: 1 }, { ...base, id: "current", version: 2 }, { ...base, id: "revoked", account_id: "other", version: 1 }], revocations: [{ connection_id: "revoked" }] };
  assert.deepEqual(types.currentConnections(accounts).map(row => row.id), ["current"]);
  assert.deepEqual(types.currentConnections({ connections: [], revocations: [] }), []);
});

function detail(extra = {}, readiness = {}, savedRun = {}, savedSource = {}) {
  const run = { id: "run-a", influencer_id: creator.id, mission_id: creator.mission_id, state: "AWAITING_APPROVAL", title: "Supported story", source_snapshot_id: "source-a", asset_version_id: "asset-2", research_version_id: "research-3", qa_report_id: "qa-4", ...savedRun };
  const artifacts = { source_snapshots: [{ id: "source-a", title: "Original source", raw_content: "Evidence", verification_status: "VERIFIED", is_fixture: false, ...savedSource }], qa_reports: [{ id: "qa-4", asset_version_id: "asset-2", research_version_id: "research-3", payload: { status: "PASS", findings: [] } }] };
  const props = { session, initialRun: run, creator, roles: ["APPROVER"], onClose() {}, onChange: async () => {}, ...extra };
  return { ...setup("ContentDetail", props, async (_session, endpoint, method) => {
    if (method === "POST") return {};
    if (endpoint.endsWith("/artifacts")) return artifacts;
    if (endpoint.endsWith("/audit")) return [];
    if (endpoint.endsWith("/readiness")) return { score: 80, gate: "NOT_READY", summary: "Render pending", disclaimer: "Not a probability of going viral", current_ids: { asset_version_id: "asset-2", research_version_id: "research-3", qa_report_id: "qa-4", render_run_id: null }, components: [], blockers: [], ...readiness };
    return run;
  }), props, run };
}

test("content approval requires reviewer acknowledgement and exact saved revisions", async () => {
  const { host, calls } = detail(); await host.settle(); assert.equal(button(host, "Approve this content").props.disabled, true);
  change(host, "Review note", "Reviewed exact evidence and every slide.");
  change(host, "I reviewed the exact content and source evidence.", true); click(host, "Approve this content"); await host.settle();
  const writes = calls.filter(call => call[2] === "POST"); assert.equal(writes.length, 1);
  assert.deepEqual(writes[0].slice(1, 4), ["workflow-runs/run-a/approve", "POST", { asset_version_id: "asset-2", research_version_id: "research-3", qa_report_id: "qa-4", comment: "Reviewed exact evidence and every slide." }]);
});

test("a high readiness score cannot enable approval after policy BLOCK", async () => {
  const { host, calls } = detail({}, { score: 95, gate: "BLOCKED", blockers: [{ code: "STALE_RESEARCH", message: "Evidence expired" }] }); await host.settle();
  change(host, "Review note", "Reviewed"); change(host, "I reviewed the exact content and source evidence.", true);
  assert.equal(button(host, "Approve this content").props.disabled, true); assert.match(text(host.tree), /Evidence expired/);
  assert.equal(calls.filter(call => call[2] === "POST").length, 0);
});

test("operators see the review requirement and no approve button", async () => {
  const { host } = detail({ roles: ["OPERATOR"] }); await host.settle();
  assert.ok(!elements(host.tree).some(node => node.type === "button" && text(node).includes("Approve this content")));
  assert.match(text(host.tree), /authorized reviewer/);
});

test("editing the review note clears the exact-content acknowledgement", async () => {
  const { host } = detail(); await host.settle(); change(host, "Review note", "First review");
  change(host, "I reviewed the exact content and source evidence.", true); change(host, "Review note", "Second review");
  assert.equal(field(host, "I reviewed the exact content and source evidence.").props.checked, false);
});

for (const current_ids of [undefined, { asset_version_id: "old", research_version_id: "research-3", qa_report_id: "qa-4" }, { asset_version_id: "asset-2", research_version_id: "old", qa_report_id: "qa-4" }, { asset_version_id: "asset-2", research_version_id: "research-3", qa_report_id: "old" }]) {
  test(`approval stays disabled when readiness lineage is absent or stale: ${JSON.stringify(current_ids)}`, async () => {
    const { host, calls } = detail({}, { current_ids }); await host.settle();
    change(host, "Review note", "Reviewed"); change(host, "I reviewed the exact content and source evidence.", true);
    const approve = button(host, "Approve this content"); assert.equal(approve.props.disabled, true);
    approve.props.onClick(); await host.settle(); assert.equal(calls.filter(call => call[2] === "POST").length, 0);
  });
}

test("a revision-required readiness gate cannot be approved even with an old saved PASS", async () => {
  const { host, calls } = detail({}, { gate: "REVISION_REQUIRED" }); await host.settle();
  change(host, "Review note", "Reviewed"); change(host, "I reviewed the exact content and source evidence.", true);
  assert.equal(button(host, "Approve this content").props.disabled, true);
  button(host, "Approve this content").props.onClick(); await host.settle();
  assert.equal(calls.filter(call => call[2] === "POST").length, 0);
});

test("content tenant change invalidates captured approval handlers and review acknowledgement", async () => {
  const { host, props, calls } = detail(); await host.settle();
  change(host, "Review note", "Reviewed"); change(host, "I reviewed the exact content and source evidence.", true);
  const approve = button(host, "Approve this content").props.onClick;
  host.render({ ...props, session: { tenant: "tenant-b", token: "different-local-token" } }); await host.settle();
  approve(); await host.settle(); assert.equal(calls.filter(call => call[2] === "POST").length, 0);
  assert.equal(field(host, "I reviewed the exact content and source evidence.").props.checked, false);
  assert.equal(field(host, "Review note").props.value, "");
});

test("late content loading from the old run cannot replace a new run", async () => {
  const old = deferred();
  const original = { id: "old-run", influencer_id: creator.id, state: "SOURCE_CAPTURED", title: "Old story", source_snapshot_id: "old-source", asset_version_id: null, research_version_id: null, qa_report_id: null };
  const next = { ...original, id: "new-run", title: "New story", source_snapshot_id: "new-source" };
  const props = { session, initialRun: original, creator, roles: ["OPERATOR"], onClose() {}, onChange: async () => {} };
  const { host } = setup("ContentDetail", props, async (_session, endpoint) => {
    if (endpoint.includes("old-run")) return old.promise;
    if (endpoint.endsWith("/artifacts")) return {};
    if (endpoint.endsWith("/audit")) return [];
    if (endpoint.endsWith("/readiness")) return { score: null, gate: "NOT_READY", components: [], blockers: [], current_ids: {}, summary: "New run", disclaimer: "No prediction" };
    return next;
  });
  host.render({ ...props, initialRun: next }); await host.settle();
  old.resolve(original); await host.settle();
  assert.match(text(host.tree), /New story/); assert.ok(!text(host.tree).includes("Old story"));
});

for (const state of ["BLOCKED", "REVISION_REQUIRED", "FAILED"]) {
  test(`${state} content offers an operator a replacement without mutating old artifacts`, async () => {
    let replacements = 0;
    const { host, calls } = detail({ roles: ["OPERATOR"], onReplace() { replacements++; } }, {}, { state });
    await host.settle(); click(host, "Create replacement story"); await host.settle();
    assert.equal(replacements, 1); assert.equal(calls.filter(call => call[2] === "POST").length, 0);
    assert.match(text(host.tree), /fresh QA and approval/);
  });
}

for (const gate of ["BLOCKED", "REVISION_REQUIRED"]) {
  test(`readiness ${gate} exposes the replacement path without granting approval`, async () => {
    const { host, calls } = detail({ roles: ["OPERATOR", "APPROVER"], onReplace() {} }, { gate });
    await host.settle(); assert.equal(button(host, "Create replacement story").props.disabled, false);
    change(host, "Review note", "Reviewed"); change(host, "I reviewed the exact content and source evidence.", true);
    assert.equal(button(host, "Approve this content").props.disabled, true);
    assert.equal(calls.filter(call => call[2] === "POST").length, 0);
  });
}

test("approver-only users cannot create replacement workflows", async () => {
  const { host } = detail({ roles: ["APPROVER"], onReplace() { assert.fail("operator action"); } }, {}, { state: "BLOCKED" });
  await host.settle(); assert.ok(!elements(host.tree).some(node => node.type === "button" && text(node).includes("Create replacement story")));
});

for (const changed of ["tenant", "roles", "unmount"]) {
  test(`captured replacement handler is invalidated by ${changed}`, async () => {
    let replacements = 0;
    const { host, props } = detail({ roles: ["OPERATOR"], onReplace() { replacements++; } }, {}, { state: "BLOCKED" });
    await host.settle(); const replace = button(host, "Create replacement story").props.onClick;
    if (changed === "unmount") host.unmount();
    else host.render({ ...props, ...(changed === "tenant" ? { session: { tenant: "tenant-b", token: "other-token" } } : { roles: ["APPROVER"] }) });
    replace(); await host.settle(); assert.equal(replacements, 0);
  });
}

test("Design offers a replacement story for a renderer failure without editing the approved asset", async () => {
  let replacements = 0;
  const { host, calls } = detail({ roles: ["OPERATOR"], onReplace() { replacements++; } });
  await host.settle(); click(host, "Design"); click(host, "Create replacement story"); await host.settle();
  assert.equal(replacements, 1); assert.equal(calls.filter(call => call[2] === "POST").length, 0);
});

test("a verified source enables generation without repeating human source attestation", async () => {
  const { host, calls } = detail({ roles: ["OPERATOR", "APPROVER"] }, {}, { state: "SOURCE_CAPTURED", asset_version_id: null, research_version_id: null, qa_report_id: null });
  await host.settle(); assert.match(text(host.tree), /Source verified/);
  assert.ok(!elements(host.tree).some(node => node.type === "button" && text(node).includes("Verify source evidence")));
  assert.ok(!elements(host.tree).some(node => node.type === "label" && text(node).startsWith("Review note")));
  click(host, "Generate / resume draft"); await host.settle();
  assert.deepEqual(calls.filter(call => call[2] === "POST").map(call => call[1]), ["workflow-runs/run-a/execute"]);
});

test("saved generated draft discloses its evidence limit and offers replacement without verification", async () => {
  let replacements = 0;
  const { host, calls } = detail({ roles: ["OPERATOR", "APPROVER"], onReplace() { replacements++; } }, {},
    { state: "SOURCE_CAPTURED", asset_version_id: null, research_version_id: null, qa_report_id: null },
    { source_type: "GENERATED", is_fixture: true, verification_status: "UNVERIFIED", metadata: { source_draft_policy: "studio-source-draft-v1" } });
  await host.settle(); assert.match(text(host.tree), /Generated planning draft saved/);
  assert.match(text(host.tree), /cannot be verified or approved for publication/);
  const verify = elements(host.tree).find(node => node.type === "button" && text(node).startsWith("Verify source evidence"));
  if (verify) { assert.equal(verify.props.disabled, true); verify.props.onClick(); await host.settle(); }
  const generate = elements(host.tree).find(node => node.type === "button" && text(node).startsWith("Generate / resume draft"));
  if (generate) assert.equal(generate.props.disabled, true);
  click(host, "Create replacement story"); await host.settle();
  assert.equal(replacements, 1); assert.equal(calls.filter(call => call[2] === "POST").length, 0);
});

test("avatar fetch uses tenant authorization and releases its private object URL", async () => {
  const originalFetch = global.fetch, create = URL.createObjectURL, revoke = URL.revokeObjectURL;
  const calls = [], revoked = [];
  global.fetch = async (...args) => { calls.push(args); return new Response(new Blob(["fixture-image"], { type: "image/png" }), { headers: { "content-type": "image/png" } }); };
  URL.createObjectURL = () => "blob:private-fixture"; URL.revokeObjectURL = value => revoked.push(value);
  try {
    const Avatar = load("studio/Avatar.tsx", { "./types": types });
    const host = mount(Avatar, { session, creator: { ...creator, portrait_available: true } }); await host.settle();
    assert.equal(calls[0][1].headers["X-Tenant-ID"], session.tenant);
    assert.equal(calls[0][1].headers.Authorization, `Bearer ${session.token}`);
    assert.equal(elements(host.tree).find(node => node.type === "img").props.src, "blob:private-fixture");
    host.unmount(); assert.deepEqual(revoked, ["blob:private-fixture"]);
  } finally { global.fetch = originalFetch; URL.createObjectURL = create; URL.revokeObjectURL = revoke; }
});

test("a stale avatar response cannot display another tenant's portrait", async () => {
  const originalFetch = global.fetch, create = URL.createObjectURL, revoke = URL.revokeObjectURL;
  const old = deferred(); let first = true, created = 0;
  global.fetch = async () => { if (first) { first = false; return old.promise; } return new Response("not found", { status: 404 }); };
  URL.createObjectURL = () => { created++; return "blob:wrong-tenant"; }; URL.revokeObjectURL = () => {};
  try {
    const Avatar = load("studio/Avatar.tsx", { "./types": types });
    const props = { session, creator: { ...creator, portrait_available: true } };
    const host = mount(Avatar, props);
    host.render({ ...props, session: { tenant: "tenant-b", token: "different-local-token" } });
    old.resolve(new Response(new Blob(["old-image"]), { headers: { "content-type": "image/png" } })); await host.settle();
    assert.equal(created, 0); assert.ok(!elements(host.tree).some(node => node.type === "img")); host.unmount();
  } finally { global.fetch = originalFetch; URL.createObjectURL = create; URL.revokeObjectURL = revoke; }
});

test("content extraction uses only the exact current asset revision", () => {
  const older = { slides: [{ index: 1 }], caption: { text: "Old" } }, current = { slides: [{ index: 1 }], caption: { text: "Current" } };
  const artifacts = { content_asset_versions: [{ id: "old", payload: older }, { id: "current", payload: current }] };
  assert.equal(types.carouselFrom(artifacts, { asset_version_id: "current" }), current);
  assert.equal(types.carouselFrom(artifacts, { asset_version_id: "missing" }), null);
});

const visualCollection = name => ({ configurations: [{ id: name, version: 1, created_at: "2026-09-28T00:00:00Z", payload: { display_name: name, template_version: "social-editorial-v2", required_disclosure: "AI creator" }, reference_sha256: null, reference_metadata: {} }], renders: [] });
const jsonResponse = value => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
async function visual(handler, work) {
  const previous = global.fetch, calls = [], refreshed = [];
  global.fetch = async (endpoint, options = {}) => {
    const call = { endpoint, ...options }; calls.push(call); return handler(call, calls.length);
  };
  const component = load("VisualReview.tsx", { "./DeliveryPreflight": { default: () => null }, "./MetricsReview": { default: () => null } });
  const props = { token: session.token, tenant: session.tenant, run: { id: "run-a", state: "AWAITING_APPROVAL", asset_version_id: "asset-a", research_version_id: "research-a", qa_report_id: "qa-a" }, operator: true, approver: true, refresh: async () => { refreshed.push(true); } };
  const host = mount(component, props);
  try { await work({ host, props, calls, refreshed }); } finally { host.unmount(); global.fetch = previous; }
}

test("carousel rendering is single-flight for rapid clicks and preserves the request key", async () => {
  const saved = deferred();
  await visual(call => call.method === "POST" ? saved.promise : jsonResponse(visualCollection("current-visual")), async ({ host, calls }) => {
    await host.settle(); const create = button(host, "Generate carousel").props.onClick;
    create(); create(); assert.equal(calls.filter(call => call.method === "POST").length, 1);
    const payload = JSON.parse(calls.find(call => call.method === "POST").body);
    assert.equal(payload.asset_version_id, "asset-a"); assert.equal(payload.visual_config_version_id, "current-visual"); assert.ok(payload.idempotency_key);
    saved.resolve(jsonResponse({ id: "saved-render" })); await host.settle();
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  });
});

for (const changeScope of ["tenant", "token", "run", "asset", "research", "qa", "state", "operator"]) {
  test(`captured visual action cannot POST after ${changeScope} changes`, async () => {
    await visual(() => jsonResponse(visualCollection("current-visual")), async ({ host, props, calls }) => {
      await host.settle(); const create = button(host, "Generate carousel").props.onClick;
      const next = { ...props, run: { ...props.run } };
      if (changeScope === "tenant") next.tenant = "tenant-b";
      else if (changeScope === "token") next.token = "other-token";
      else if (changeScope === "run") next.run.id = "run-b";
      else if (changeScope === "state") next.run.state = "APPROVED";
      else if (changeScope === "operator") next.operator = false;
      else next.run[`${changeScope === "qa" ? "qa_report" : `${changeScope}_version`}_id`] = "new-revision";
      host.render(next); await host.settle(); create(); await host.settle();
      assert.equal(calls.filter(call => call.method === "POST").length, 0);
    });
  });
}

test("old visual responses do not replace a new tenant's configuration", async () => {
  const old = deferred();
  await visual(call => call.headers["X-Tenant-ID"] === session.tenant ? old.promise : jsonResponse(visualCollection("new-tenant-visual")), async ({ host, props }) => {
    host.render({ ...props, tenant: "tenant-b" }); await host.settle();
    old.resolve(jsonResponse(visualCollection("old-tenant-visual"))); await host.settle();
    assert.equal(field(host, "Visual configuration").props.value, "new-tenant-visual");
    assert.ok(!text(host.tree).includes("old-tenant-visual"));
  });
});

test("StrictMode visual effect replay ignores the cancelled first load", async () => {
  const old = deferred();
  await visual((_call, count) => count === 1 ? old.promise : jsonResponse(visualCollection("replayed-visual")), async ({ host }) => {
    host.replayEffects(); await host.settle(); old.resolve(jsonResponse(visualCollection("cancelled-visual"))); await host.settle();
    assert.equal(field(host, "Visual configuration").props.value, "replayed-visual");
    assert.ok(!text(host.tree).includes("cancelled-visual"));
  });
});

test("late render completion after tenant change cannot refresh the new scope", async () => {
  const saved = deferred();
  await visual(call => call.method === "POST" ? saved.promise : jsonResponse(visualCollection("current-visual")), async ({ host, props, refreshed }) => {
    await host.settle(); click(host, "Generate carousel"); host.render({ ...props, tenant: "tenant-b" }); await host.settle();
    saved.resolve(jsonResponse({ id: "old-render" })); await host.settle(); assert.equal(refreshed.length, 0);
  });
});
