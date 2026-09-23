// Offline rendered-event tests. No browser, provider, credential or package addition.
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, mount, elements, text, field, change, form, submit, deferred } = require("./component-harness.cjs");

const MediaSetup = load("MediaSetup.tsx");
const MediaPanel = load("MediaPanel.tsx", { "./MediaSetup": { default: MediaSetup } });

const configurations = [
  { id: "visual-old", version: 1, payload: { display_name: "Old" } },
  { id: "visual-current", version: 2, payload: { display_name: "Current" } },
];
function setup(handler) {
  const calls = [], saved = [];
  const json = async (endpoint, method = "GET", body) => {
    calls.push({ endpoint, method, body });
    return handler ? handler(endpoint, method, body) : method === "GET" ? { configurations } : {};
  };
  const props = { workflowId: "workflow-a", influencerId: "influencer-a", json, onSaved: async () => { saved.push(true); } };
  return { calls, saved, props, host: mount(MediaSetup, props) };
}
function fillProfile(host) {
  change(host, "ElevenLabs voice ID", "chosen-voice-id");
  change(host, "Speech model", "eleven_v3");
  change(host, "Speech upper-bound", "0.250001");
  change(host, "HeyGen upper-bound", "0.100001");
  change(host, "Price reference", "Account rate checked; fixture");
  change(host, "Prices checked at", "2026-09-23T09:00");
}
function fillBudget(host) {
  change(host, "Maximum USD per video", "5.25");
  change(host, "Maximum USD per day", "12.50");
  change(host, "Expires at", "2026-09-24T09:00");
}

test("profile save posts only the exact selected voice, current visual and verified prices", async () => {
  const { host, calls, saved } = setup(); await host.settle(); fillProfile(host);
  submit(host, 0); assert.equal(calls.length, 1, "unacknowledged form makes no request");
  change(host, "I verified voice access", true); submit(host, 0); await host.settle();
  assert.deepEqual(calls, [
    { endpoint: "workflow-runs/workflow-a/renders", method: "GET", body: undefined },
    { endpoint: "influencers/influencer-a/media-profiles", method: "POST", body: {
      visual_config_version_id: "visual-current",
      payload: { schema_version: 1, voice_id: "chosen-voice-id", tts_model: "eleven_v3",
        tts_usd_per_1000_characters: "0.250001", avatar_usd_per_second: "0.100001",
        price_reference: "Account rate checked; fixture", price_checked_at: new Date("2026-09-23T09:00").toISOString(), presenter_provider: "heygen" },
    } },
  ]);
  assert.equal(saved.length, 1);
  assert.equal(field(host, "I verified voice access").props.checked, false);
});

test("budget defaults disabled and saving never executes a provider", async () => {
  const { host, calls } = setup(); await host.settle(); fillBudget(host);
  assert.equal(field(host, "Enable this spending policy").props.checked, false);
  change(host, "I authorize these exact limits", true); submit(host, 1); await host.settle();
  assert.deepEqual(calls[1], { endpoint: "media-spend-policy", method: "POST", body: {
    schema_version: 1, per_run_usd: "5.25", per_day_usd: "12.50", enabled: false,
    expires_at: new Date("2026-09-24T09:00").toISOString(),
  } });
  assert.equal(calls.length, 2);
  assert.equal(field(host, "I authorize these exact limits").props.checked, false);
});

test("profile and budget edits including reverted values require fresh acknowledgement", async () => {
  const { host, calls } = setup(); await host.settle(); fillProfile(host); fillBudget(host);
  change(host, "I verified voice access", true);
  change(host, "Speech model", "eleven_multilingual_v2"); change(host, "Speech model", "eleven_v3");
  assert.equal(field(host, "I verified voice access").props.checked, false);
  submit(host, 0);
  change(host, "I authorize these exact limits", true);
  change(host, "Enable this spending policy", true); change(host, "Enable this spending policy", false);
  assert.equal(field(host, "I authorize these exact limits").props.checked, false);
  submit(host, 1); assert.equal(calls.length, 1);
});

for (const identityChange of ["tenant", "token", "workflow", "influencer"]) {
  test(`${identityChange} scope change clears fields, enabled setting and authorizations`, async () => {
    const { host, props, calls } = setup(); await host.settle(); fillProfile(host); fillBudget(host);
    change(host, "Enable this spending policy", true);
    change(host, "I verified voice access", true); change(host, "I authorize these exact limits", true);
    const staleSubmit = form(host, 0).props.onSubmit;
    const freshJson = async (...args) => props.json(...args);
    // The parent binds tenant/token to json via useCallback. No credential enters the form.
    const next = { ...props, ...(identityChange === "workflow" ? { workflowId: "workflow-b" } : identityChange === "influencer" ? { influencerId: "influencer-b" } : { json: freshJson }) };
    host.render(next); await host.settle(); staleSubmit({ preventDefault() {} });
    assert.equal(field(host, "ElevenLabs voice ID").props.value, "");
    assert.equal(field(host, "Maximum USD per video").props.value, "");
    assert.equal(field(host, "Enable this spending policy").props.checked, false);
    assert.equal(field(host, "I verified voice access").props.checked, false);
    assert.equal(field(host, "I authorize these exact limits").props.checked, false);
    assert.ok(calls.every(call => call.method === "GET"), "stale submit cannot write to either scope");
  });
}

test("late character response from an old scope cannot replace the current configuration", async () => {
  const old = deferred();
  const { host, props } = setup(() => old.promise);
  host.render({ ...props, json: async () => ({ configurations: [{ id: "new-visual", version: 1, payload: { display_name: "New tenant" } }] }) });
  await host.settle(); old.resolve({ configurations }); await host.settle();
  assert.ok(text(host.tree).includes("New tenant"));
  assert.ok(!text(host.tree).includes("Current · version"));
});

test("save submission is single-flight and completion after unmount cannot refresh another panel", async () => {
  const write = deferred();
  const { host, calls, saved } = setup((endpoint, method) => method === "GET" ? { configurations } : write.promise);
  await host.settle(); fillProfile(host); change(host, "I verified voice access", true);
  const submitTwice = form(host, 0).props.onSubmit;
  submitTwice({ preventDefault() {} }); submitTwice({ preventDefault() {} });
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  host.unmount(); write.resolve({}); await Promise.resolve(); await Promise.resolve();
  assert.equal(saved.length, 0);
});

test("late save completion after authentication changes does not signal success in the new scope", async () => {
  const write = deferred();
  const { host, props, saved } = setup((endpoint, method) => method === "GET" ? { configurations } : write.promise);
  await host.settle(); fillBudget(host); change(host, "I authorize these exact limits", true); submit(host, 1);
  host.render({ ...props, json: async () => ({ configurations }) }); await host.settle();
  write.resolve({}); await host.settle();
  assert.equal(saved.length, 0);
  assert.ok(!text(host.tree).includes("Saved a new"));
});

test("strict-mode effect replay still loads the current character configuration", async () => {
  const { host } = setup(); host.replayEffects(); await host.settle();
  assert.ok(text(host.tree).includes("Current · version 2"));
});

test("parent/child StrictMode replay does not reuse the parent's aborted fetch signal", async () => {
  const originalFetch = global.fetch;
  const first = deferred(), renderCalls = [], parentCalls = [];
  const response = value => ({ ok: true, headers: { get: () => "application/json" }, json: async () => value });
  global.fetch = async (endpoint, options) => {
    if (endpoint.endsWith("/renders")) {
      renderCalls.push(options.signal);
      if (options.signal?.aborted) throw new Error("aborted");
      return renderCalls.length === 1 ? first.promise : response({ configurations });
    }
    parentCalls.push(options.signal);
    return response({ profiles: [], spend_policies: [], dependencies: {}, runs: [] });
  };
  let parent, child;
  try {
    const props = { tenant: "tenant-fixture", token: "credential-placeholder", run: { id: "workflow", influencer_id: "influencer", state: "APPROVED" }, operator: true, approver: true, admin: true, choices: [] };
    parent = mount(MediaPanel, props, false);
    child = mount(MediaSetup, elements(parent.tree).find(node => node.type === MediaSetup).props);
    // React mounts child effects before the parent's and replays cleanup before
    // remount. Reproduce the child remount while the parent signal is still aborted.
    parent.replayEffects();
    parent.unmount(); child.unmount();
    assert.equal(parentCalls[0].aborted, true);
    child.replayEffects();
    parent.replayEffects();
    await child.settle(); await parent.settle();
    first.resolve(response({ configurations: [{ id: "stale", version: 99, payload: { display_name: "Disposed response" } }] }));
    await child.settle();
    assert.equal(renderCalls.length, 2);
    assert.ok(renderCalls.every(signal => signal instanceof AbortSignal));
    assert.notEqual(renderCalls[0], renderCalls[1]);
    assert.notEqual(renderCalls[1], parentCalls[0]);
    assert.equal(renderCalls[1].aborted, false);
    assert.ok(text(child.tree).includes("Current · version 2"));
    assert.ok(!text(child.tree).includes("Disposed response"));
    fillProfile(child); change(child, "I verified voice access", true);
    assert.equal(elements(child.tree).find(node => node.type === "button" && text(node) === "Save voice and price profile").props.disabled, false);
  } finally { child?.unmount(); parent?.unmount(); global.fetch = originalFetch; }
});

test("MediaPanel exposes setup only to administrators", () => {
  const props = { tenant: "tenant-fixture", token: "credential-placeholder", run: { id: "workflow", influencer_id: "influencer", state: "APPROVED" }, operator: true, approver: true, admin: false, choices: [] };
  const host = mount(MediaPanel, props, false);
  assert.ok(!elements(host.tree).some(node => node.type === MediaSetup));
  host.render({ ...props, admin: true });
  const setupNodes = elements(host.tree).filter(node => node.type === MediaSetup);
  assert.equal(setupNodes.length, 1);
  assert.ok(!setupNodes[0].key.includes(props.token), "credential must not enter the React key");
});
