// Offline source-drafting event flows. Generated text never becomes authentic evidence.
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { load, mount, elements, text, change, field, form, submit, deferred } = require("./component-harness.cjs");

const session = { tenant: "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", token: "fixture-access" };
const id = number => `${String(number).padStart(8, "0")}-0000-4000-8000-000000000000`;
const creators = [
  { id: id(1), mission_id: id(2), name: "Local Educator", language: "en", category_id: "education" },
  { id: id(3), mission_id: id(4), name: "Another Educator", language: "en", category_id: "education" },
];
const title = "A thoughtful study routine";
const paragraph = "Choose a topic that interests your audience. Keep each explanation focused on a single idea.";
const raw = `Mock writing draft. Not verified source evidence.\n\n${paragraph}`;
function draft(overrides = {}) {
  return { schema_version: 1, provider: "mock", mode: "MOCK", cost: 0,
    notice: "Mock writing draft. No AI call or source verification. This generated material cannot be published.",
    influencer_id: creators[0].id, mission_id: creators[0].mission_id, language: "en", title,
    publisher: "Media OS draft assistant (mock)", raw_content: raw, source_type: "GENERATED",
    classification: "INTERNAL", is_fixture: true,
    origin: `generated:studio-source-draft-v1:${"a".repeat(64)}`,
    metadata: { source_draft_policy: "studio-source-draft-v1" }, ...overrides };
}
const icons = { default: () => null };
const modal = { default: () => null };
function composer(handler, extra = {}) {
  const calls = [], created = [];
  const props = { session, creators, initialCreatorId: creators[0].id, mockMode: true, onClose() {}, onCreated: async value => { created.push(value); }, ...extra };
  const request = async (...args) => {
    calls.push(args);
    return handler ? handler(...args) : args[1] === "studio/source-drafts" ? draft() : { id: id(5), state: "SOURCE_CAPTURED" };
  };
  const component = load("studio/SourceComposer.tsx", { "./types": { request }, "./Icons": icons, "./Modal": modal });
  return { host: mount(component, props), props, calls, created };
}
function buttons(host, name) { return elements(host.tree).filter(node => node.type === "button" && text(node).trim() === name); }
function button(host, name) { const found = buttons(host, name)[0]; assert.ok(found, `button exists: ${name}`); return found; }
function click(host, name) { const found = button(host, name); assert.ok(!found.props.disabled, `${name} enabled`); found.props.onClick(); host.render(); }
function start(host) { change(host, "Story / source title", title); click(host, "Generate draft"); }
async function apply(host) { start(host); await host.settle(); click(host, "Use draft"); }
function classify(host, excerpt = paragraph) {
  change(host, "Evidence 1", excerpt); change(host, "Claim type", "GENERAL_EVERGREEN");
  change(host, "I confirm these excerpts are evergreen general facts", true);
}
function helperExports() {
  const source = fs.readFileSync(path.join(__dirname, "../app/studio/SourceComposer.tsx"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)(name => ({ react: {}, "react/jsx-runtime": {}, "./Icons": icons, "./Modal": modal, "./types": {} })[name], result, result.exports);
  return result.exports;
}

test("empty source can preview and explicitly insert a typed mock draft without saving evidence", async () => {
  const { host, calls, created } = composer();
  assert.equal(button(host, "Generate draft").props.disabled, true); start(host); await host.settle();
  assert.deepEqual(calls[0].slice(0, 4), [session, "studio/source-drafts", "POST", { influencer_id: creators[0].id, title }]);
  assert.equal(field(host, "Original source text").props.value, "", "preview requires an explicit choice");
  assert.match(text(host.tree), /mock/i); assert.match(text(host.tree), /not.*(?:evidence|verif)|cannot be published/i);
  click(host, "Use draft");
  assert.equal(field(host, "Original source text").props.value, raw);
  assert.equal(field(host, "Source URL or origin").props.value, draft().origin);
  assert.equal(field(host, "Publisher").props.value, draft().publisher);
  assert.equal(field(host, "Source relationship").props.value, "INTERNAL");
  assert.equal(field(host, "Claim type").props.value, "");
  assert.equal(field(host, "Evidence 1").props.value, "", "generated prose cannot attest itself");
  assert.equal(button(host, "Save source for review").props.disabled, true);
  assert.equal(calls.length, 1); assert.deepEqual(created, []);
});

test("existing manual text cannot be overwritten even through a captured generation handler", async () => {
  const { host, calls } = composer(); change(host, "Story / source title", title);
  const generate = button(host, "Generate draft").props.onClick;
  change(host, "Original source text", "Real source text kept exactly.");
  assert.equal(button(host, "Generate draft").props.disabled, true); generate(); await host.settle();
  assert.equal(calls.length, 0); assert.equal(field(host, "Original source text").props.value, "Real source text kept exactly.");
});

for (const scenario of ["missing-title", "missing-creator", "unknown-creator", "live-mode"]) {
  test(`source drafting cannot run for ${scenario}, including direct disabled handlers`, async () => {
    const extra = scenario === "missing-creator" ? { creators: [], initialCreatorId: "" } : scenario === "live-mode" ? { mockMode: false } : {};
    const { host, calls } = composer(undefined, extra);
    if (scenario !== "missing-title") change(host, "Story / source title", title);
    if (scenario === "unknown-creator") change(host, "Influencer", id(99));
    const action = button(host, "Generate draft"); assert.equal(action.props.disabled, true); action.props.onClick(); await host.settle();
    assert.equal(calls.length, 0);
  });
}

test("rapid source-draft clicks result in only one request", async () => {
  const response = deferred(); const { host, calls } = composer(() => response.promise);
  change(host, "Story / source title", title); const generate = button(host, "Generate draft").props.onClick;
  generate(); generate(); host.render(); assert.equal(calls.length, 1);
  response.resolve(draft()); await host.settle(); assert.equal(buttons(host, "Use draft").length, 1);
});

for (const [label, value] of [["Story / source title", "A different story"], ["Influencer", creators[1].id], ["Original source text", "New manual text."]]) {
  test(`changing ${label} discards an in-flight source draft`, async () => {
    const response = deferred(); const { host, calls } = composer(() => response.promise); start(host);
    change(host, label, value); response.resolve(draft()); await host.settle();
    assert.equal(calls[0][4].aborted, true); assert.equal(buttons(host, "Use draft").length, 0);
    assert.equal(field(host, "Original source text").props.value, label === "Original source text" ? value : "");
  });
}

test("editing after a preview revokes the captured Use draft action", async () => {
  const { host } = composer(); start(host); await host.settle(); const use = button(host, "Use draft").props.onClick;
  change(host, "Original source text", "Manual text entered after preview."); use(); host.render();
  assert.equal(field(host, "Original source text").props.value, "Manual text entered after preview.");
  assert.equal(field(host, "Publisher").props.value, "");
});

for (const scope of ["tenant", "token", "initial-creator", "mock-mode"]) {
  test(`${scope} change rejects old source-draft responses and handlers`, async () => {
    const response = deferred(); const { host, props, calls } = composer(() => response.promise);
    change(host, "Story / source title", title); const generate = button(host, "Generate draft").props.onClick; start(host);
    const next = scope === "tenant" ? { session: { ...session, tenant: id(90) } } : scope === "token" ? { session: { ...session, token: "other-fixture-token" } } : scope === "initial-creator" ? { initialCreatorId: creators[1].id } : { mockMode: false };
    host.render({ ...props, ...next }); response.resolve(draft()); await host.settle(); generate(); await host.settle();
    assert.equal(calls.length, 1); assert.equal(calls[0][4].aborted, true);
    assert.equal(buttons(host, "Use draft").length, 0); assert.equal(field(host, "Original source text").props.value, "");
  });
}

test("unmount aborts source drafting and discards the pending result", async () => {
  const response = deferred(); const { host, calls, created } = composer(() => response.promise);
  start(host); host.unmount(); response.resolve(draft()); await host.settle();
  assert.equal(calls[0][4].aborted, true); assert.equal(buttons(host, "Use draft").length, 0); assert.deepEqual(created, []);
});

test("StrictMode replay recovers draft requests without reviving canceled previews", async () => {
  const old = deferred(); let first = true;
  const { host, calls } = composer(() => { if (first) { first = false; return old.promise; } return draft(); });
  start(host); host.replayEffects(); start(host); await host.settle();
  old.resolve(draft({ raw_content: "Canceled response must not show." })); await host.settle();
  assert.equal(calls.length, 2); assert.equal(calls[0][4].aborted, true); assert.ok(!text(host.tree).includes("Canceled response must not show."));
  click(host, "Use draft"); assert.equal(field(host, "Original source text").props.value, raw);
});

test("helper failure preserves input and allows retry", async () => {
  let count = 0; const { host, calls } = composer(() => { if (++count === 1) throw new Error("Drafting unavailable"); return draft(); });
  change(host, "Publisher", "My publisher"); start(host); await host.settle();
  assert.match(text(host.tree), /Drafting unavailable/); assert.equal(field(host, "Publisher").props.value, "My publisher");
  assert.equal(field(host, "Original source text").props.value, ""); click(host, "Generate draft"); await host.settle();
  assert.equal(calls.length, 2); assert.equal(buttons(host, "Use draft").length, 1);
});

test("generated source stays fixture-marked through edits and a direct uncheck attempt", async () => {
  const { host, calls, created } = composer(); await apply(host);
  const fixture = field(host, "This is test or demonstration evidence."); assert.equal(fixture.props.checked, true); assert.equal(fixture.props.disabled, true);
  fixture.props.onChange({ target: { checked: false } }); host.render();
  change(host, "Original source text", `${raw}\nEdited wording remains generated.`); classify(host);
  submit(host, 0); await host.settle();
  const writes = calls.filter(call => call[1] === "workflow-runs"); assert.equal(writes.length, 1);
  const source = writes[0][3].source; assert.equal(source.source_type, "GENERATED"); assert.equal(source.is_fixture, true);
  assert.equal(source.classification, "INTERNAL"); assert.equal(source.origin, draft().origin);
  assert.equal(source.metadata.source_draft_policy, "studio-source-draft-v1"); assert.equal(source.raw_content, `${raw}\nEdited wording remains generated.`);
  assert.equal(source.raw_content.slice(source.evidence[0].start, source.evidence[0].end), paragraph); assert.equal(created.length, 1);
});

test("changing disabled source controls cannot relabel generated writing as official evidence", async () => {
  const { host, calls } = composer(); await apply(host);
  change(host, "Source URL or origin", "https://official-looking.invalid/source");
  change(host, "Publisher", "An official-sounding publisher"); change(host, "Source relationship", "PRIMARY");
  classify(host); submit(host, 0); await host.settle();
  const source = calls.find(call => call[1] === "workflow-runs")[3].source;
  assert.equal(source.publisher, draft().publisher); assert.equal(source.origin, draft().origin);
  assert.equal(source.classification, "INTERNAL"); assert.equal(source.canonical_url, null);
  assert.equal(source.is_fixture, true); assert.equal(source.source_type, "GENERATED");
});

test("generated source still needs exact excerpts and explicit human classification", async () => {
  const { host, calls } = composer(); await apply(host); submit(host, 0); await host.settle();
  assert.equal(calls.filter(call => call[1] === "workflow-runs").length, 0);
  classify(host, "An invented excerpt."); submit(host, 0); await host.settle();
  assert.equal(calls.filter(call => call[1] === "workflow-runs").length, 0); assert.match(text(host.tree), /exactly/);
});

test("starting again clears generated text and all provenance before accepting a real source", async () => {
  const { host, calls } = composer(); await apply(host); classify(host); click(host, "Start again with real source");
  for (const label of ["Original source text", "Evidence 1", "Source URL or origin", "Publisher"]) assert.equal(field(host, label).props.value, "");
  assert.equal(field(host, "This is test or demonstration evidence.").props.checked, false);
  change(host, "Story / source title", "Actual documentation"); change(host, "Source URL or origin", "repository:docs/policy.md"); change(host, "Publisher", "Workspace docs");
  change(host, "Original source text", "Reviewers check the exact saved source."); change(host, "Source relationship", "INTERNAL"); classify(host, "Reviewers check the exact saved source.");
  submit(host, 0); await host.settle(); const source = calls.find(call => call[1] === "workflow-runs")[3].source;
  assert.equal(source.source_type, "MANUAL"); assert.equal(source.is_fixture, false); assert.equal(source.metadata.source_draft_policy, undefined);
});

test("captured start-again action cannot clear a new authenticated workspace's source", async () => {
  const { host, props } = composer(); await apply(host);
  const restart = button(host, "Start again with real source").props.onClick;
  host.render({ ...props, session: { ...session, tenant: id(90), token: "other-token" } });
  change(host, "Original source text", "Private source entered in the new workspace.");
  change(host, "Source URL or origin", "repository:tenant-b/source.md");
  restart(); host.render();
  assert.equal(field(host, "Original source text").props.value, "Private source entered in the new workspace.");
  assert.equal(field(host, "Source URL or origin").props.value, "repository:tenant-b/source.md");
});

test("captured start-again action cannot erase writing after an earlier restart", async () => {
  const { host } = composer(); await apply(host);
  const restart = button(host, "Start again with real source").props.onClick;
  click(host, "Start again with real source"); change(host, "Original source text", "A newly entered authentic source.");
  restart(); host.render(); assert.equal(field(host, "Original source text").props.value, "A newly entered authentic source.");
});

test("a generated-source retry preserves its exact request key and provenance", async () => {
  const { host, calls } = composer((_session, endpoint) => { if (endpoint === "studio/source-drafts") return draft(); throw new Error("Save response interrupted"); });
  await apply(host); classify(host); submit(host, 0); await host.settle(); submit(host, 0); await host.settle();
  const writes = calls.filter(call => call[1] === "workflow-runs"); assert.equal(writes.length, 2); assert.deepEqual(writes[0][3], writes[1][3]);
  assert.equal(writes[0][3].source.source_type, "GENERATED"); assert.equal(writes[0][3].source.is_fixture, true);
});

const invalid = [
  ["null", () => null], ["provider", () => draft({ provider: "openai" })],
  ["paid mode", () => draft({ mode: "LIVE", cost: 1 })], ["schema version", () => draft({ schema_version: 2 })],
  ["missing fixture", () => draft({ is_fixture: false })], ["manual source type", () => draft({ source_type: "MANUAL" })],
  ["primary authority", () => draft({ classification: "PRIMARY" })], ["real origin", () => draft({ origin: "https://example.invalid" })],
  ["invented publisher", () => draft({ publisher: "Official source" })], ["missing policy", () => draft({ metadata: {} })],
  ["empty content", () => draft({ raw_content: "" })], ["wrong creator", () => draft({ influencer_id: creators[1].id })],
  ["wrong mission", () => draft({ mission_id: creators[1].mission_id })], ["wrong title", () => draft({ title: "Another source title" })],
  ["wrong language", () => draft({ language: "es" })],
];
for (const [name, result] of invalid) {
  test(`invalid ${name} source draft never enters the source form`, async () => {
    const { host } = composer(result); start(host); await host.settle();
    assert.equal(buttons(host, "Use draft").length, 0); assert.equal(field(host, "Original source text").props.value, "");
    assert.ok(elements(host.tree).some(node => node.props.role === "alert"), "a validation error is visible");
  });
}

test("source draft parser validates immutable generated provenance", () => {
  const { checkedSourceDraft } = helperExports(); assert.equal(typeof checkedSourceDraft, "function");
  const value = draft(); assert.equal(checkedSourceDraft(value), value);
  for (const result of invalid.slice(0, 11)) assert.throws(() => checkedSourceDraft(result[1]()));
});
