// Offline onboarding event flows. No provider calls or shared database.
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { load, mount, elements, text, change, field, form, submit, deferred } = require("./component-harness.cjs");

const session = { tenant: "onboarding-tenant-a", token: "fixture-access-a" };
const categories = [
  { id: "education", name: "Education", description: "Teach clearly", accent: "yellow" },
  { id: "technology", name: "Technology", description: "Explain digital tools", accent: "blue" },
];
const icons = { default: () => null, categoryIcon: { education: "book", technology: "book" } };
const modal = { default: () => null };
const saved = { id: "saved-creator", mission_id: "saved-mission", name: "Edited creator" };
function drafts() {
  return {
    schema_version: 1, provider: "mock", mode: "MOCK", cost: 0,
    notice: "Mock mode: template-based drafts. No AI model was called and nothing was saved.",
    suggestions: ["practical", "explainer", "community"].map((id, n) => ({
      id, label: ["Practical guide", "Clear explanations", "Useful conversations"][n],
      name: ["Noa", "Alex", "Sage"][n], audience: ["Curious learners", "Independent educators"],
      objective: `Help curious learners understand ${["daily study habits", "complex ideas", "learning questions"][n]} using sources and human review.`,
    })),
  };
}
function wizard(handler, extra = {}) {
  const calls = [], created = [];
  const props = { session, categories, initialCategory: "education", mockMode: true, onClose() {}, onCreated: async value => { created.push(value); }, ...extra };
  const request = async (...args) => { calls.push(args); return handler ? handler(...args) : args[1] === "studio/onboarding-drafts" ? drafts() : saved; };
  const component = load("studio/CreatorWizard.tsx", { "./types": { initials: name => name.slice(0, 2), request }, "./Icons": icons, "./Modal": modal });
  return { host: mount(component, props), props, calls, created };
}
function buttons(host, name) { return elements(host.tree).filter(node => node.type === "button" && text(node).trim() === name); }
function button(host, name, n = 0) { const node = buttons(host, name)[n]; assert.ok(node, `button exists: ${name}`); return node; }
function click(host, name, n = 0) { const node = button(host, name, n); assert.ok(!node.props.disabled, `${name} enabled`); node.props.onClick(); host.render(); }
function personality(host) { submit(host, 0); }
function mission(host) { personality(host); change(host, "Creator name", "My creator"); change(host, "Who is their content for?", "Owners, Local teams"); submit(host, 0); }
function helperExports() {
  const source = fs.readFileSync(path.join(__dirname, "../app/studio/CreatorWizard.tsx"), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)(name => ({ react: {}, "react/jsx-runtime": {}, "./Icons": icons, "./Modal": modal, "./types": {} })[name], result, result.exports);
  return result.exports;
}

test("Create new onboarding suggests, applies, edits and saves the exact reviewed identity", async () => {
  const { host, calls, created } = wizard();
  assert.equal(host.tree.props.title, "Create new");
  assert.equal(buttons(host, "Suggest with AI").length, 0);
  personality(host); click(host, "Suggest with AI"); await host.settle();
  assert.equal(calls.length, 1); assert.equal(calls[0][0], session);
  assert.deepEqual(calls[0].slice(1, 4), ["studio/onboarding-drafts", "POST", { category_id: "education", language: "en", tone: "CLEAR", audience: [] }]);
  assert.equal(buttons(host, "Use this starting point").length, 3);
  assert.equal(field(host, "Creator name").props.value, "", "preview cannot overwrite before explicit choice");
  assert.equal(field(host, "Who is their content for?").props.value, "");
  assert.match(text(host.tree), /Mock preview/); assert.match(text(host.tree), /No AI calls or charges/);
  click(host, "Use this starting point", 1);
  assert.equal(field(host, "Creator name").props.value, "Alex");
  change(host, "Creator name", "Edited creator"); change(host, "Who is their content for?", "Independent writers, Local teams");
  change(host, "Content language", "es"); change(host, "Voice & tone", "WARM");
  submit(host, 0); click(host, "Draft mission with AI"); await host.settle();
  assert.equal(field(host, "Editorial mission").props.value, "");
  assert.deepEqual(calls[1][3], { category_id: "education", language: "es", tone: "WARM", name: "Edited creator", audience: ["Independent writers", "Local teams"] });
  click(host, "Use this mission", 2);
  assert.equal(field(host, "Editorial mission").props.value, drafts().suggestions[2].objective);
  change(host, "Editorial mission", "Teach practical writing with cited examples and human editorial review.");
  assert.ok(!button(host, "Create new").props.disabled); submit(host, 0); await host.settle();
  assert.equal(calls.length, 3); assert.deepEqual(created, [saved]);
  const payload = calls[2][3]; assert.ok(payload.idempotency_key);
  assert.deepEqual({ ...payload, idempotency_key: undefined }, { name: "Edited creator", category_id: "education", language: "es", tone: "WARM", audience: ["Independent writers", "Local teams"], objective: "Teach practical writing with cited examples and human editorial review.", idempotency_key: undefined });
  assert.equal(calls.filter(call => call[1] === "studio/influencers").length, 1);
});

test("starting-point choice preserves a name already entered by the user", async () => {
  const { host } = wizard(); personality(host); change(host, "Creator name", "My chosen name");
  click(host, "Suggest with AI"); await host.settle(); click(host, "Use this starting point");
  assert.equal(field(host, "Creator name").props.value, "My chosen name");
});

test("mission choice only changes the objective and leaves the earlier identity untouched", async () => {
  const { host, calls } = wizard(); mission(host); change(host, "Editorial mission", "A mission I wrote myself.");
  click(host, "Draft mission with AI"); await host.settle();
  assert.equal(field(host, "Editorial mission").props.value, "A mission I wrote myself.");
  click(host, "Use this mission"); click(host, "Back");
  assert.equal(field(host, "Creator name").props.value, "My creator");
  assert.equal(field(host, "Who is their content for?").props.value, "Owners, Local teams");
  assert.equal(calls.length, 1, "preview does not save an identity or invoke a workflow");
});

test("double-clicked suggestion is a single request and can be regenerated after completion", async () => {
  const response = deferred(); const { host, calls } = wizard(() => response.promise); personality(host);
  const suggest = button(host, "Suggest with AI").props.onClick; suggest(); suggest(); host.render();
  assert.equal(calls.length, 1); assert.equal(button(host, "Drafting ideas…").props.disabled, true);
  response.resolve(drafts()); await host.settle(); click(host, "Suggest with AI"); await host.settle();
  assert.equal(calls.length, 2); assert.equal(buttons(host, "Use this starting point").length, 3);
});

test("suggestion errors preserve existing writing and permit a clean retry", async () => {
  let attempt = 0; const { host, calls } = wizard(async () => { if (++attempt === 1) throw new Error("Suggestions unavailable"); return drafts(); });
  mission(host); change(host, "Editorial mission", "A mission I want to keep."); click(host, "Draft mission with AI"); await host.settle();
  assert.match(text(host.tree), /Suggestions unavailable/); assert.equal(field(host, "Editorial mission").props.value, "A mission I want to keep.");
  click(host, "Draft mission with AI"); await host.settle(); assert.equal(calls.length, 2); assert.equal(buttons(host, "Use this mission").length, 3);
});

for (const [label, value] of [["Creator name", "New name"], ["Who is their content for?", "Different audience"], ["Content language", "es"], ["Voice & tone", "WARM"]]) {
  test(`editing ${label} cancels an in-flight suggestion without blocking another request`, async () => {
    const old = deferred(); let first = true;
    const { host, calls } = wizard(() => { if (first) { first = false; return old.promise; } return drafts(); }); personality(host);
    click(host, "Suggest with AI"); change(host, label, value);
    assert.equal(calls[0][4].aborted, true); click(host, "Suggest with AI"); await host.settle();
    const stale = drafts(); stale.suggestions.forEach(row => { row.name = "Old response"; }); old.resolve(stale); await host.settle();
    assert.equal(calls.length, 2); assert.ok(!text(host.tree).includes("Old response")); assert.equal(buttons(host, "Use this starting point").length, 3);
  });
}

test("editing after preview revokes a captured Apply handler", async () => {
  const { host } = wizard(); personality(host); click(host, "Suggest with AI"); await host.settle();
  const apply = button(host, "Use this starting point").props.onClick;
  change(host, "Creator name", "Keep this name"); change(host, "Who is their content for?", "Keep this audience");
  apply(); host.render(); assert.equal(field(host, "Creator name").props.value, "Keep this name");
  assert.equal(field(host, "Who is their content for?").props.value, "Keep this audience");
});

test("editing the mission during drafting prevents a late replacement", async () => {
  const response = deferred(); const { host, calls } = wizard(() => response.promise); mission(host);
  click(host, "Draft mission with AI"); change(host, "Editorial mission", "My freshly written editorial direction.");
  response.resolve(drafts()); await host.settle();
  assert.equal(calls[0][4].aborted, true); assert.equal(buttons(host, "Use this mission").length, 0);
  assert.equal(field(host, "Editorial mission").props.value, "My freshly written editorial direction.");
});

test("changing steps revokes captured suggestion and Apply handlers", async () => {
  const { host, calls } = wizard(); personality(host);
  const suggest = button(host, "Suggest with AI").props.onClick; click(host, "Suggest with AI"); await host.settle();
  const apply = button(host, "Use this starting point").props.onClick; click(host, "Back");
  suggest(); apply(); await host.settle(); assert.equal(calls.length, 1);
  assert.match(text(host.tree), /STEP 1 OF 3/); personality(host); assert.equal(field(host, "Creator name").props.value, "");
});

for (const mode of ["tenant", "token", "category", "mock-mode"]) {
  test(`${mode} changes discard old suggestions and their captured handlers`, async () => {
    const response = deferred(); const { host, props, calls } = wizard(() => response.promise); personality(host);
    const suggest = button(host, "Suggest with AI").props.onClick; click(host, "Suggest with AI");
    const changed = mode === "tenant" ? { session: { ...session, tenant: "tenant-b" } } : mode === "token" ? { session: { ...session, token: "other-token" } } : mode === "category" ? { initialCategory: "technology" } : { mockMode: false };
    host.render({ ...props, ...changed }); response.resolve(drafts()); await host.settle(); suggest(); await host.settle();
    assert.equal(calls.length, 1); assert.equal(calls[0][4].aborted, true); assert.equal(buttons(host, "Use this starting point").length, 0);
    assert.match(text(host.tree), /STEP 1 OF 3/);
  });
}

test("unmount aborts helper requests and suppresses their continuations", async () => {
  const response = deferred(); const { host, calls, created } = wizard(() => response.promise); personality(host);
  const suggest = button(host, "Suggest with AI").props.onClick; click(host, "Suggest with AI"); host.unmount();
  response.resolve(drafts()); await host.settle(); suggest(); await host.settle();
  assert.equal(calls.length, 1); assert.equal(calls[0][4].aborted, true); assert.equal(buttons(host, "Use this starting point").length, 0); assert.deepEqual(created, []);
});

test("StrictMode effect replay cancels old previews and permits a fresh helper request", async () => {
  const old = deferred(); let first = true;
  const { host, calls } = wizard(() => { if (first) { first = false; return old.promise; } return drafts(); });
  personality(host); click(host, "Suggest with AI"); host.replayEffects(); personality(host); click(host, "Suggest with AI"); await host.settle();
  const stale = drafts(); stale.suggestions.forEach(row => { row.name = "Canceled draft"; }); old.resolve(stale); await host.settle();
  assert.equal(calls.length, 2); assert.equal(calls[0][4].aborted, true); assert.ok(!text(host.tree).includes("Canceled draft")); assert.equal(buttons(host, "Use this starting point").length, 3);
});

test("disabled live-mode helper cannot call a provider even through a direct handler", async () => {
  const { host, calls } = wizard(undefined, { mockMode: false }); personality(host);
  const action = button(host, "Suggest with AI"); assert.equal(action.props.disabled, true); action.props.onClick(); await host.settle();
  assert.equal(calls.length, 0); assert.match(text(host.tree), /Live AI drafting is not configured/);
});

test("oversized identity inputs cannot be sent through a suggestion handler", async () => {
  const { host, calls } = wizard(); personality(host); change(host, "Creator name", "x".repeat(101));
  click(host, "Suggest with AI"); await host.settle(); assert.equal(calls.length, 0); assert.match(text(host.tree), /1–100/);
});

test("creating a reviewed identity cancels a pending helper without changing the submitted mission", async () => {
  const response = deferred(); const { host, calls, created } = wizard((_session, endpoint) => endpoint === "studio/onboarding-drafts" ? response.promise : saved);
  mission(host); change(host, "Editorial mission", "Reviewed mission ready to save."); click(host, "Draft mission with AI"); submit(host, 0); await host.settle();
  response.resolve(drafts()); await host.settle(); assert.equal(calls[0][4].aborted, true); assert.deepEqual(created, [saved]);
  assert.equal(calls[1][3].objective, "Reviewed mission ready to save."); assert.equal(buttons(host, "Use this mission").length, 0);
});

const invalidResponses = [
  ["null", () => null], ["schema version", value => ({ ...value, schema_version: 2 })],
  ["provider", value => ({ ...value, provider: "openai" })], ["paid mode", value => ({ ...value, mode: "REAL", cost: 1 })],
  ["missing suggestion", value => ({ ...value, suggestions: value.suggestions.slice(1) })],
  ["duplicate direction", value => { value.suggestions[1].id = "practical"; return value; }],
  ["oversized mission", value => { value.suggestions[0].objective = "x".repeat(501); return value; }],
  ["control characters", value => { value.suggestions[0].name = "Name\nInjected line"; return value; }],
  ["ambiguous audience delimiter", value => { value.suggestions[0].audience = ["One, Two"]; return value; }],
  ["duplicate audience", value => { value.suggestions[0].audience = ["Readers", "Readers"]; return value; }],
  ["untrimmed audience", value => { value.suggestions[0].audience = [" Readers"]; return value; }],
];
for (const [name, invalid] of invalidResponses) {
  test(`invalid ${name} response cannot populate the form`, async () => {
    const { host } = wizard(() => invalid(drafts())); mission(host); change(host, "Editorial mission", "Keep this reviewed mission.");
    click(host, "Draft mission with AI"); await host.settle(); assert.equal(buttons(host, "Use this mission").length, 0);
    assert.match(text(host.tree), /could not be validated/); assert.equal(field(host, "Editorial mission").props.value, "Keep this reviewed mission.");
  });
}

test("response validation counts Unicode code points consistently with the API", () => {
  const { checkedOnboardingDrafts } = helperExports(); const value = drafts();
  value.suggestions[0].name = "🌍".repeat(100); assert.equal(checkedOnboardingDrafts(value), value);
  value.suggestions[0].name += "🌍"; assert.throws(() => checkedOnboardingDrafts(value), /could not be validated/);
});
