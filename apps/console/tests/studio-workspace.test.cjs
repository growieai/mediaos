const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { load, mount, elements, text, field, change, deferred } = require("./component-harness.cjs");

const session = { tenant: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", token: "fixture-access-a" };
const secondSession = { tenant: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", token: "fixture-access-b" };
const id = n => `${String(n).padStart(8, "0")}-0000-4000-8000-000000000000`;
const creator = { id: id(1), name: "Saved Creator A", mission_id: id(2), opportunity_discovery: true, category_id: "business", language: "en", tone: "CLEAR", objective: "Explain useful evidence", audience: ["Owners"], portrait_available: false };
const workflow = { id: id(3), influencer_id: creator.id, mission_id: creator.mission_id, state: "AWAITING_APPROVAL", title: "Saved story A", source_snapshot_id: id(4), asset_version_id: id(5), research_version_id: id(6), qa_report_id: id(7), created_at: "2026-09-28T00:00:00Z", updated_at: "2026-09-28T00:00:00Z" };
const category = { id: "business", name: "Business", description: "Verified business ideas", accent: "forest" };
const icons = { default: () => null, categoryIcon: { business: "book" } };
const stubs = Object.fromEntries(["Modal", "Avatar", "CreatorWizard", "SourceComposer", "ContentDetail", "Channels", "Discovery"].map(name => {
  const component = () => null; Object.defineProperty(component, "name", { value: name }); return [`./${name}`, { default: component }];
}));
function exported(name, dependencies = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../app/studio", name), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const result = { exports: {} };
  const imports = { react: {}, "react/jsx-runtime": {}, "./Icons": icons, ...stubs, ...dependencies };
  new Function("require", "module", "exports", code)(name => { if (name in imports) return imports[name]; throw new Error(`Unexpected import: ${name}`); }, result, result.exports);
  return result.exports;
}
const types = exported("types.ts");
const creatorHelpers = exported("CreatorWizard.tsx", { "./types": types });
const discoveryHelpers = exported("Discovery.tsx", { "./types": types });
function button(host, name) {
  const nodes = elements(host.tree);
  const result = nodes.find(node => node.type === "button" && node.props["aria-label"] === name) ?? nodes.find(node => node.type === "button" && text(node).trim() === name);
  assert.ok(result, `button exists: ${name}`); return result;
}
function click(host, name) { const node = button(host, name); assert.ok(!node.props.disabled); node.props.onClick(); host.render(); }
function child(host, name) { return elements(host.tree).find(node => typeof node.type === "function" && node.type.name === name); }
function navigate(host, name) {
  const nav = elements(host.tree).find(node => node.type === "nav" && node.props["aria-label"] === "Main navigation");
  const node = elements(nav).find(node => node.type === "button" && text(node).startsWith(name)); assert.ok(node); node.props.onClick(); host.render();
}
function dataset(current = session, changes = {}) {
  return { identity: { tenant_id: current.tenant, memberships: [{ roles: ["OPERATOR", "APPROVER", "ADMIN"] }] }, overview: { tenant_id: current.tenant, tenant_name: current.tenant === session.tenant ? "Workspace A" : "Workspace B", counts: { influencers: 1, workflow_runs: 1, awaiting_approval: 1 }, workflows: [workflow] }, influencers: [creator], creationEnabled: true, categories: [category], accounts: { connections: [], revocations: [] }, dependencies: { connect_enabled: false }, ...changes };
}
function endpointData(data, endpoint) {
  return { context: data.identity, "studio/overview": data.overview, "studio/influencers": { influencers: data.influencers, creation_enabled: data.creationEnabled }, "studio/categories": { categories: data.categories }, "social/connections": data.accounts, "social/dependencies": data.dependencies }[endpoint];
}
function workspace(handler = async (current, endpoint) => endpointData(dataset(current), endpoint)) {
  const calls = [];
  const request = async (...args) => { calls.push(args); return handler(...args); };
  const component = load("studio/CreatorStudio.tsx", { "./types": { ...types, request }, "./Icons": icons, ...stubs });
  return { host: mount(component, {}), calls };
}
function connect(host, current = session, data = dataset(current)) {
  click(host, "Open workspace"); const login = child(host, "WorkspaceLogin"); assert.ok(login);
  login.props.onConnected(current, data); host.render();
}

test("workspace load keeps every request in the authenticated scope and requires matching tenant identity", async () => {
  const calls = [], data = dataset();
  const { loadStudio } = exported("CreatorStudio.tsx", { "./types": { ...types, request: async (...args) => { calls.push(args); return endpointData(data, args[1]); } } });
  const controller = new AbortController(); const result = await loadStudio(session, controller.signal);
  assert.equal(calls.length, 6); assert.ok(calls.every(call => call[0] === session && call[2] === "GET" && call[4] === controller.signal));
  assert.equal(result.influencers[0].id, creator.id); assert.equal(result.creationEnabled, true);
  data.identity.tenant_id = secondSession.tenant;
  await assert.rejects(() => loadStudio(session, controller.signal), /identity did not match/);
});

test("unauthenticated workspace shows unknown counts and no saved private identity", () => {
  const { host, calls } = workspace(); assert.equal(calls.length, 0);
  assert.ok(!text(host.tree).includes(creator.name)); assert.ok(!text(host.tree).includes(workflow.title));
  assert.equal(elements(host.tree).filter(node => node.props.className === "stat-card").length, 4);
  assert.ok(elements(host.tree).filter(node => node.props.className === "stat-card").every(node => text(node).includes("—")));
});

test("category choice opens sign-in and retains only the authorized creator intent", () => {
  const { host } = workspace();
  const tile = elements(host.tree).find(node => node.type === "button" && node.props.className?.includes("category-tile category-business"));
  tile.props.onClick(); host.render(); child(host, "WorkspaceLogin").props.onConnected(session, dataset()); host.render();
  assert.equal(child(host, "CreatorWizard").props.initialCategory, "business");
});

test("cancelling sign-in clears the earlier category intent", () => {
  const { host } = workspace(); click(host, "Create new"); child(host, "WorkspaceLogin").props.onClose(); host.render();
  connect(host); assert.equal(child(host, "CreatorWizard"), undefined);
});

for (const mode of ["feature-disabled", "reviewer-only"]) {
  test(`creator form stays inaccessible for ${mode}`, () => {
    const { host } = workspace(); const data = dataset();
    if (mode === "feature-disabled") data.creationEnabled = false; else data.identity.memberships[0].roles = ["APPROVER"];
    connect(host, session, data); click(host, "Create new"); assert.equal(child(host, "CreatorWizard"), undefined);
    assert.match(text(host.tree), mode === "feature-disabled" ? /not activated/ : /operator or administrator/);
  });
}

test("mobile topbar exposes sign-out and clears all saved workspace state", () => {
  const { host } = workspace(); connect(host); navigate(host, "Content studio"); click(host, "Create content"); assert.ok(child(host, "SourceComposer"));
  const topbar = elements(host.tree).find(node => node.type === "header" && node.props.className === "studio-topbar");
  assert.ok(elements(topbar).some(node => node.type === "button" && node.props["aria-label"] === "Sign out / switch access"));
  click(host, "Sign out / switch access"); assert.equal(child(host, "SourceComposer"), undefined);
  assert.ok(!text(host.tree).includes("Workspace A")); assert.ok(!text(host.tree).includes(workflow.title));
});

test("late workspace refresh cannot restore data after logout and another login", async () => {
  const pending = deferred(); const { host, calls } = workspace(async (current, endpoint) => { await pending.promise; return endpointData(dataset(current), endpoint); });
  connect(host); const refresh = button(host, "Refresh workspace").props.onClick; refresh(); refresh(); assert.equal(calls.length, 6);
  click(host, "Sign out / switch access"); connect(host, secondSession, dataset(secondSession, { influencers: [], overview: { ...dataset(secondSession).overview, workflows: [], counts: { influencers: 0, workflow_runs: 0, awaiting_approval: 0 } } }));
  pending.resolve(); await host.settle(); assert.match(text(host.tree), /Workspace B/); assert.ok(!text(host.tree).includes("Workspace A")); assert.ok(!text(host.tree).includes(workflow.title));
});

test("old content and creator callbacks cannot open another tenant's modals", async () => {
  const { host } = workspace(); connect(host); click(host, "Create new"); const oldCreator = child(host, "CreatorWizard").props.onCreated;
  child(host, "CreatorWizard").props.onClose(); host.render(); navigate(host, "Content studio"); click(host, "Create content"); const oldRun = child(host, "SourceComposer").props.onCreated;
  click(host, "Sign out / switch access"); connect(host, secondSession); await oldRun({ ...workflow, title: "Private old callback" }); await oldCreator({ ...creator, name: "Private old creator" }); host.render();
  assert.equal(child(host, "ContentDetail"), undefined); assert.ok(!text(host.tree).includes("Private old"));
});

function openSavedStory(host) {
  navigate(host, "Content studio");
  const card = elements(host.tree).find(node => node.type === "button" && node.props.className === "content-card");
  assert.ok(card); card.props.onClick(); host.render(); return child(host, "ContentDetail");
}

test("replacement opens a fresh source composer for the exact same saved influencer without writes", () => {
  const { host, calls } = workspace(); connect(host);
  const detail = openSavedStory(host); assert.equal(detail.props.initialRun.id, workflow.id);
  detail.props.onReplace(); host.render();
  assert.equal(child(host, "ContentDetail"), undefined);
  assert.equal(child(host, "SourceComposer").props.initialCreatorId, creator.id);
  assert.equal(calls.length, 0, "replacement is a new form, not mutation or approval of the old story");
});

test("a captured replacement callback cannot reopen a source form after logout", () => {
  const { host } = workspace(); connect(host); const replace = openSavedStory(host).props.onReplace;
  click(host, "Sign out / switch access"); connect(host, secondSession); replace(); host.render();
  assert.equal(child(host, "SourceComposer"), undefined);
});

test("a captured replacement callback is revoked when operator permission is removed", async () => {
  const changed = dataset(); changed.identity.memberships[0].roles = ["APPROVER"];
  const { host } = workspace(async (_current, endpoint) => endpointData(changed, endpoint));
  connect(host); const replace = openSavedStory(host).props.onReplace;
  click(host, "Refresh workspace"); await host.settle();
  assert.equal(child(host, "ContentDetail").props.onReplace, undefined);
  replace(); host.render(); assert.equal(child(host, "SourceComposer"), undefined);
});

test("replacement is unavailable when the saved influencer is no longer in the workspace", () => {
  const { host } = workspace(); connect(host, session, dataset(session, { influencers: [] }));
  assert.equal(openSavedStory(host).props.onReplace, undefined);
});

test("insights distinguish illustrative guidance from actual observed performance", () => {
  const { host } = workspace(); connect(host); navigate(host, "Insights");
  assert.match(text(host.tree), /Illustration of score components, not workspace results/);
  assert.match(text(host.tree), /No aggregate dashboard connected/);
  assert.match(text(host.tree), /not a promise of virality/);
});

test("workspace request forwards authentication and handles a null error payload safely", async () => {
  const previous = global.fetch; const calls = [];
  global.fetch = async (...args) => { calls.push(args); return new Response("null", { status: 503, headers: { "content-type": "application/json" } }); };
  try {
    await assert.rejects(() => types.request(session, "studio/overview"), /Request failed \(503\)/);
    assert.equal(calls[0][1].headers["X-Tenant-ID"], session.tenant); assert.equal(calls[0][1].cache, "no-store");
    assert.equal(calls[0][1].headers.Authorization, `Bearer ${session.token}`);
  } finally { global.fetch = previous; }
});

const validCreator = { name: "Teacher", category_id: "education", language: "en", tone: "CLEAR", audience: "Owners, Teams", objective: "Explain useful facts clearly." };
for (const [update, step, expected] of [[{ audience: Array.from({ length: 9 }, (_, n) => `Group ${n}`).join(",") }, 1, /1–8/], [{ audience: "Owners, Owners" }, 1, /only once/], [{ audience: "x".repeat(101) }, 1, /100/], [{ audience: "Office\u0007owners" }, 1, /one line/], [{ objective: "x".repeat(501) }, 2, /500/], [{ objective: "First line\nSecond line" }, 2, /line breaks/]]) {
  test(`creator validates backend limits locally: ${JSON.stringify(update).slice(0, 65)}`, () => {
    assert.match(creatorHelpers.creatorFieldError({ ...validCreator, ...update }, step), expected);
  });
}

const source = { id: id(10), source_key: "BDNS", enabled: true };
const audience = { id: id(11), code: "GENERIC_SMB" };
const opportunity = { id: id(12), canonical_external_id: "ES:BDNS:fixture", current_version: { id: id(13), title: "Recorded official opportunity", status: "OPEN", closing_date: "2026-12-31" } };
function evaluation(decision = "CREATE_CONTENT", version = opportunity.current_version.id) {
  return [{ audience_segment: audience, score: { opportunity_version_id: version, payload: { final_score: 70, eligibility: "UNKNOWN" } }, decision: { decision, payload: { reasons: ["Recorded deterministic policy reason"] } } }];
}
function discovery(handler) {
  const calls = [], created = [], completion = deferred();
  const props = { session, creators: [creator], operator: true, onClose() {}, onCreated: async value => { created.push(value); completion.resolve(value); } };
  const request = async (...args) => {
    calls.push(args); if (handler) { const result = handler(...args); if (result !== undefined) return result; }
    const endpoint = args[1];
    if (endpoint === "intelligence/sources") return [source];
    if (endpoint === "intelligence/audiences") return [audience];
    if (endpoint === "intelligence/opportunities") return [opportunity];
    if (endpoint.endsWith("/evaluate")) return evaluation();
    if (endpoint.endsWith("/workflow")) return workflow;
    if (endpoint === "intelligence/ingestions") return { id: id(14) };
    return {};
  };
  const component = load("studio/Discovery.tsx", { "./types": { ...types, request }, "./Icons": icons, "./Modal": stubs["./Modal"] });
  return { host: mount(component, props), props, calls, created, completed: completion.promise };
}

test("discovery workflow key is deterministic, bounded and specific to every exact input", async () => {
  const values = [opportunity.id, opportunity.current_version.id, creator.id, creator.mission_id, audience.id];
  const key = await discoveryHelpers.discoveryWorkflowKey(...values);
  assert.ok(key.length <= 128); assert.match(key, /^studio-opportunity:[a-f0-9]{64}$/);
  assert.equal(await discoveryHelpers.discoveryWorkflowKey(...values), key);
  for (let n = 0; n < values.length; n++) { const changed = [...values]; changed[n] = id(n + 50); assert.notEqual(await discoveryHelpers.discoveryWorkflowKey(...changed), key); }
});

test("discovery is bounded and reuses the persisted ingestion for identical resume", async () => {
  const { host, calls } = discovery(); await host.settle(); click(host, "Discover / resume"); await host.settle(); click(host, "Discover / resume"); await host.settle();
  const starts = calls.filter(call => call[1] === "intelligence/ingestions"); assert.equal(starts.length, 1);
  assert.equal(starts[0][3].page_size, 5); assert.equal(starts[0][3].max_pages, 1); assert.equal(starts[0][3].mode, "MANUAL");
  assert.equal((Date.parse(starts[0][3].until) - Date.parse(starts[0][3].since)) / 86400000, 14);
  assert.equal(calls.filter(call => call[1].endsWith("/execute")).length, 2);
});

test("discovery rejects oversized search and disabled sources without a POST", async () => {
  const { host, calls } = discovery(); await host.settle(); assert.equal(field(host, "Search terms").props.maxLength, 150);
  change(host, "Search terms", "x".repeat(151)); click(host, "Discover / resume"); await host.settle();
  assert.equal(calls.filter(call => call[2] === "POST").length, 0); assert.match(text(host.tree), /150 characters/);
  change(host, "Search terms", ""); change(host, "Official source", "unavailable"); button(host, "Discover / resume").props.onClick(); await host.settle();
  assert.equal(calls.filter(call => call[2] === "POST").length, 0);
});

test("a qualified exact opportunity revision produces one sourced workflow with a bounded key", { timeout: 5000 }, async () => {
  const { host, calls, created, completed } = discovery(); await host.settle(); click(host, "Check audience fit"); await host.settle();
  const create = button(host, "Create sourced draft").props.onClick; create(); create();
  // Native WebCrypto work can outlast any fixed number of setImmediate turns.
  await completed; await host.settle();
  const writes = calls.filter(call => call[1].endsWith("/workflow")); assert.equal(writes.length, 1);
  assert.equal(writes[0][3].audience_segment_id, audience.id); assert.ok(writes[0][3].idempotency_key.length <= 128);
  assert.equal(created[0], workflow, "the UI does not fabricate a title or approval state");
});

for (const mode of ["WATCH", "HUMAN_REVIEW", "IGNORE", "wrong-version"]) {
  test(`discovery cannot create content from ${mode}`, async () => {
    const { host, calls } = discovery((_session, endpoint) => endpoint.endsWith("/evaluate") ? evaluation(mode === "wrong-version" ? "CREATE_CONTENT" : mode, mode === "wrong-version" ? id(99) : opportunity.current_version.id) : undefined);
    await host.settle(); click(host, "Check audience fit"); await host.settle();
    assert.equal(button(host, "Create sourced draft").props.disabled, true); button(host, "Create sourced draft").props.onClick(); await host.settle();
    assert.equal(calls.filter(call => call[1].endsWith("/workflow")).length, 0);
  });
}

test("discovery tenant change aborts the old cycle and resets its lock", async () => {
  const old = deferred(); const { host, props, calls } = discovery((current, endpoint) => current.tenant === session.tenant && endpoint === "intelligence/ingestions" ? old.promise : undefined);
  await host.settle(); const start = button(host, "Discover / resume").props.onClick; start(); host.render({ ...props, session: secondSession }); await host.settle();
  old.resolve({ id: "old-tenant-ingestion" }); await host.settle(); start(); await host.settle();
  assert.equal(calls.filter(call => call[1].includes("old-tenant-ingestion")).length, 0);
  assert.equal(calls.filter(call => call[1] === "intelligence/ingestions").length, 1);
  click(host, "Discover / resume"); await host.settle(); assert.equal(calls.filter(call => call[1] === "intelligence/ingestions").length, 2);
});

test("discovery unmount suppresses the continuation after an ingestion request", async () => {
  const pending = deferred(); const { host, calls } = discovery((_session, endpoint) => endpoint === "intelligence/ingestions" ? pending.promise : undefined);
  await host.settle(); click(host, "Discover / resume"); host.unmount(); pending.resolve({ id: "orphan-ingestion" }); await host.settle();
  assert.equal(calls.filter(call => call[1].includes("orphan-ingestion")).length, 0);
});

test("read-only discovery users cannot evaluate or create even through captured handlers", async () => {
  const { host, props, calls } = discovery(); host.render({ ...props, operator: false }); await host.settle();
  button(host, "Discover / resume").props.onClick(); button(host, "Check audience fit").props.onClick(); button(host, "Create sourced draft").props.onClick(); await host.settle();
  assert.equal(calls.filter(call => call[2] === "POST").length, 0);
});
