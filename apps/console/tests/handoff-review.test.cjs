// Isolated event-handler coverage for human-authorized handoffs; never sends real HTTP.
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, mount, elements, text, field, change, deferred } = require("./component-harness.cjs");
const HandoffReview = load("HandoffReview.tsx");
const originalFetch = global.fetch;
test.afterEach(() => { global.fetch = originalFetch; });

const request = { id: "consent-a", content_hash: "consent-hash-a", business_reference: "business-ref-a", destination_version_id: "destination-a", attestations: [{ id: "attestation-a" }] };
const business = { id: "business-a", business_reference: "business-ref-a", revision: 1, status: "OPERATOR_ASSERTION_REVIEWED", content_hash: "business-hash-a", payload: { name: "Business fixture" } };
const transport = { id: "transport-a", destination_version_id: "destination-a", endpoint: "https://receiver.example.test/handoff", protocol: "SIGNED_RECEIPT_V1" };
const delivery = { id: "delivery-a", status: "AWAITING_AUTHORIZATION", operation: "SEND", business_identity_id: "business-a", transport_id: "transport-a", attestation_id: "attestation-a", original_delivery_id: null, payload_hash: "delivery-hash-a", payload: { exact_fixture: true }, expires_at: "2099-01-01T00:00:00Z", retention_due: false, transport, decisions: [], attempts: [] };
function response(value, ok = true) { return { ok, headers: { get: () => "application/json" }, json: async () => structuredClone(value) }; }
function setup({ deliveries = [delivery], businesses = [business], transports = [transport], live = true, props: changes = {}, intercept } = {}) {
  const calls = [];
  global.fetch = async (url, options = {}) => {
    const endpoint = url.replace("/api/internal/", "");
    const method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : undefined;
    const call = { endpoint, method, body };
    calls.push(call);
    if (intercept) {
      const handled = intercept(call, options);
      if (handled !== undefined) return handled;
    }
    if (method !== "GET") return response(delivery);
    if (endpoint === "business-identities") return response(businesses);
    if (endpoint === "conversion-transports") return response(transports);
    if (endpoint === "conversion-dependencies") return response({ live_enabled: live, credentials_configured: live });
    if (endpoint.endsWith("/deliveries")) return response(deliveries);
    throw new Error("Unexpected offline endpoint");
  };
  const props = { tenant: "tenant-a", token: "credential-fixture-a", request, operator: true, approver: true, ...changes };
  return { calls, props, host: mount(HandoffReview, props) };
}
function button(host, label) { return elements(host.tree).find(node => node.type === "button" && text(node) === label); }
function click(host, label) {
  const node = button(host, label); assert.ok(node, `button exists: ${label}`); assert.ok(!node.props.disabled, `button enabled: ${label}`);
  node.props.onClick(); host.render();
}
function acknowledge(host) { change(host, "I reviewed the exact payload", true); }

test("exact handoff authorization requires note and current payload acknowledgement", async () => {
  const { host, calls } = setup(); await host.settle();
  assert.ok(button(host, "Authorize exact handoff").props.disabled);
  change(host, "Review note", "I checked this exact destination and payload.");
  assert.ok(button(host, "Authorize exact handoff").props.disabled);
  acknowledge(host); click(host, "Authorize exact handoff"); await host.settle();
  assert.deepEqual(calls.filter(call => call.method === "POST"), [{
    endpoint: "conversion-deliveries/delivery-a/authorize", method: "POST",
    body: { payload_hash: "delivery-hash-a", decision: "AUTHORIZE", comment: "I checked this exact destination and payload." },
  }]);
  assert.equal(field(host, "I reviewed the exact payload").props.checked, false);
});

test("business review pins exact evidence revision and does not infer consent or send", async () => {
  const { host, calls } = setup({ businesses: [{ ...business, status: "AWAITING_REVIEW" }] }); await host.settle();
  change(host, "Business evidence", "business-a");
  assert.ok(button(host, "Review business identity").props.disabled);
  change(host, "Review note", "Registry evidence checked.");
  change(host, "I checked this exact business identity", true);
  click(host, "Review business identity"); await host.settle();
  assert.deepEqual(calls.filter(call => call.method === "POST"), [{ endpoint: "business-identities/business-a/review", method: "POST", body: { content_hash: "business-hash-a", identity_attested: true, comment: "Registry evidence checked." } }]);
});

test("saving/recovering an identical handoff uses the same key after an interrupted create", async () => {
  let creates = 0;
  const { host, calls } = setup({ intercept: call => {
    if (call.method === "POST" && call.endpoint === "conversion-requests/consent-a/deliveries" && creates++ === 0) return Promise.reject(new Error("Simulated local request interruption"));
  } });
  await host.settle(); change(host, "Business evidence", "business-a"); change(host, "Provisioned destination", "transport-a");
  click(host, "Prepare exact handoff for review"); await host.settle();
  click(host, "Prepare exact handoff for review"); await host.settle();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 2); assert.deepEqual(posts[0], posts[1]);
  assert.deepEqual({ ...posts[0].body, idempotency_key: "redacted-for-assertion" }, { business_identity_id: "business-a", transport_id: "transport-a", attestation_id: "attestation-a", request_hash: "consent-hash-a", idempotency_key: "redacted-for-assertion" });
  assert.match(posts[0].body.idempotency_key, /^[0-9a-f-]{36}$/);
});

test("known rejection permits an explicit replacement with a new key and fresh authorization", async () => {
  let saved = [], nextId = 0;
  const results = new Map();
  const { host, calls } = setup({ deliveries: [], intercept: call => {
    if (call.method === "GET" && call.endpoint.endsWith("/deliveries")) return response(saved);
    if (call.method === "POST" && call.endpoint.endsWith("/deliveries")) {
      if (!results.has(call.body.idempotency_key)) {
        const next = { ...delivery, id: `prepared-${++nextId}`, payload_hash: `prepared-hash-${nextId}` };
        results.set(call.body.idempotency_key, next); saved.push(next);
      }
      return response(results.get(call.body.idempotency_key));
    }
    if (call.method === "POST" && call.endpoint.endsWith("/authorize")) {
      assert.equal(call.body.decision, "REJECT");
      saved = saved.map(row => ({ ...row, status: "REJECTED" }));
      for (const [key, value] of results) results.set(key, { ...value, status: "REJECTED" });
      return response(saved[0]);
    }
  } });
  await host.settle(); change(host, "Business evidence", "business-a"); change(host, "Provisioned destination", "transport-a");
  click(host, "Prepare exact handoff for review"); await host.settle();
  change(host, "Review note", "Reject this request for correction."); acknowledge(host);
  click(host, "Reject handoff"); await host.settle();
  assert.ok(button(host, "Prepare replacement for fresh review").props.disabled);
  acknowledge(host); click(host, "Prepare replacement for fresh review"); await host.settle();
  const creates = calls.filter(call => call.method === "POST" && call.endpoint.endsWith("/deliveries"));
  assert.equal(creates.length, 2);
  assert.notEqual(creates[0].body.idempotency_key, creates[1].body.idempotency_key);
  assert.deepEqual({ ...creates[0].body, idempotency_key: undefined }, { ...creates[1].body, idempotency_key: undefined });
  assert.equal(field(host, "Saved handoff").props.value, "prepared-2");
  assert.ok(button(host, "Authorize exact handoff").props.disabled);
  assert.equal(button(host, "Send authorized handoff"), undefined);
});

test("an interrupted replacement preserves its new key until the create result is recovered", async () => {
  let creates = 0;
  const rejected = { ...delivery, status: "REJECTED" };
  const replacement = { ...delivery, id: "replacement-a" };
  const { host, calls } = setup({ deliveries: [rejected], intercept: call => {
    if (call.method === "POST" && call.endpoint.endsWith("/deliveries")) {
      if (creates++ === 0) return Promise.reject(new Error("Simulated interrupted replacement"));
      return response(replacement);
    }
    if (call.method === "GET" && call.endpoint.endsWith("/deliveries") && creates > 1) return response([rejected, replacement]);
  } });
  await host.settle(); acknowledge(host); click(host, "Prepare replacement for fresh review"); await host.settle();
  acknowledge(host); click(host, "Prepare replacement for fresh review"); await host.settle();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 2); assert.deepEqual(posts[0], posts[1]);
  assert.equal(field(host, "Saved handoff").props.value, "replacement-a");
});

test("replacement never creates a new key for unresolved or already received handoffs", async () => {
  for (const status of ["AWAITING_AUTHORIZATION", "AUTHORIZED", "UNKNOWN_OUTCOME", "RECEIVED", "REVOKED"]) {
    const { host, calls } = setup({ deliveries: [{ ...delivery, status }] }); await host.settle();
    assert.equal(button(host, "Prepare replacement for fresh review"), undefined);
    assert.ok(calls.every(call => call.method === "GET")); host.unmount();
  }
  const { host, calls } = setup({ deliveries: [{ ...delivery, status: "REJECTED" }, { ...delivery, id: "held-replacement", status: "UNKNOWN_OUTCOME" }] });
  await host.settle(); change(host, "Saved handoff", "delivery-a"); acknowledge(host);
  assert.ok(button(host, "Prepare replacement for fresh review").props.disabled);
  assert.ok(calls.every(call => call.method === "GET"));
});

test("a rejected revocation replacement pins its original delivery and only prepares review", async () => {
  const { host, calls } = setup({ deliveries: [{ ...delivery, status: "REJECTED", operation: "REVOKE", original_delivery_id: "original-send" }] });
  await host.settle(); acknowledge(host); click(host, "Prepare replacement for fresh review"); await host.settle();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1); assert.equal(posts[0].endpoint, "conversion-deliveries/original-send/revocation");
  assert.deepEqual(Object.keys(posts[0].body), ["idempotency_key"]);
});

test("live-disabled configuration never enables dispatch or reconciliation controls", async () => {
  for (const status of ["AUTHORIZED", "UNKNOWN_OUTCOME"]) {
    const { host, calls } = setup({ deliveries: [{ ...delivery, status }], live: false }); await host.settle(); acknowledge(host);
    const control = button(host, status === "AUTHORIZED" ? "Send authorized handoff" : "Check destination receipt");
    assert.ok(control.props.disabled);
    assert.ok(calls.every(call => call.method === "GET"));
    host.unmount();
  }
});

test("authorized dispatch requires fresh exact acknowledgement and is single-flight", async () => {
  const wait = deferred();
  const { host, calls } = setup({ deliveries: [{ ...delivery, status: "AUTHORIZED" }], intercept: call => call.endpoint.endsWith("/dispatch") ? wait.promise : undefined });
  await host.settle(); assert.ok(button(host, "Send authorized handoff").props.disabled);
  acknowledge(host); const send = button(host, "Send authorized handoff").props.onClick;
  send(); send(); assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(calls.at(-1).endpoint, "conversion-deliveries/delivery-a/dispatch");
  assert.equal(calls.at(-1).body, undefined);
  wait.resolve(response({ ...delivery, status: "RECEIVED" })); await host.settle();
  assert.equal(field(host, "I reviewed the exact payload").props.checked, false);
});

test("unknown outcome offers receipt lookup rather than resending the original payload", async () => {
  const { host, calls } = setup({ deliveries: [{ ...delivery, status: "UNKNOWN_OUTCOME" }] }); await host.settle();
  assert.equal(button(host, "Send authorized handoff"), undefined);
  acknowledge(host); click(host, "Check destination receipt"); await host.settle();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1); assert.equal(posts[0].endpoint, "conversion-deliveries/delivery-a/reconcile");
  assert.equal(posts[0].body, undefined);
});

test("revocation only creates a new review request and never sends it", async () => {
  const { host, calls } = setup({ deliveries: [{ ...delivery, status: "RECEIVED" }] }); await host.settle();
  acknowledge(host); click(host, "Prepare revocation notice for separate review"); await host.settle();
  const posts = calls.filter(call => call.method === "POST");
  assert.equal(posts.length, 1); assert.equal(posts[0].endpoint, "conversion-deliveries/delivery-a/revocation");
  assert.deepEqual(Object.keys(posts[0].body), ["idempotency_key"]);
});

test("switching reviewed handoff revision away and back clears acknowledgement", async () => {
  const other = { ...delivery, id: "delivery-b", payload_hash: "delivery-hash-b" };
  const { host } = setup({ deliveries: [delivery, other] }); await host.settle();
  change(host, "Saved handoff", "delivery-a"); acknowledge(host);
  change(host, "Saved handoff", "delivery-b"); change(host, "Saved handoff", "delivery-a");
  assert.equal(field(host, "I reviewed the exact payload").props.checked, false);
  assert.ok(button(host, "Authorize exact handoff").props.disabled);
});

for (const changed of ["tenant", "token", "request", "role"]) {
  test(`${changed} changes clear prior review and reject stale action handlers`, async () => {
    const { host, props, calls } = setup({ deliveries: [{ ...delivery, status: "AUTHORIZED" }] }); await host.settle();
    change(host, "Review note", "Checked prior context."); acknowledge(host);
    const staleSend = button(host, "Send authorized handoff").props.onClick;
    const next = { ...props, ...(changed === "tenant" ? { tenant: "tenant-b" } : changed === "token" ? { token: "credential-fixture-b" } : changed === "role" ? { operator: false } : { request: { ...request, id: "consent-b", content_hash: "consent-hash-b" } }) };
    host.render(next); await host.settle(); staleSend(); await host.settle();
    assert.equal(field(host, "Review note").props.value, "");
    assert.equal(field(host, "I reviewed the exact payload").props.checked, false);
    assert.ok(calls.every(call => call.method === "GET"));
  });
}

test("late GET responses cannot replace a newly authenticated context", async () => {
  const wait = deferred(); let old = true;
  const { host, props } = setup({ intercept: call => call.endpoint.endsWith("/deliveries") && old ? wait.promise : undefined });
  old = false; host.render({ ...props, token: "credential-fixture-b" }); await host.settle();
  wait.resolve(response([{ ...delivery, id: "stale-delivery", payload: { stale_only: true } }])); await host.settle();
  assert.ok(!text(host.tree).includes("stale_only"));
  assert.equal(field(host, "Saved handoff").props.value, "delivery-a");
});

test("late mutation completion after context change performs no follow-up request", async () => {
  const wait = deferred();
  const { host, props, calls } = setup({ deliveries: [{ ...delivery, status: "AUTHORIZED" }], intercept: call => call.endpoint.endsWith("/dispatch") ? wait.promise : undefined });
  await host.settle(); acknowledge(host); click(host, "Send authorized handoff");
  host.render({ ...props, token: "credential-fixture-b" }); await host.settle();
  const count = calls.length; wait.resolve(response(delivery)); await host.settle();
  assert.equal(calls.length, count);
  assert.equal(field(host, "I reviewed the exact payload").props.checked, false);
});

test("read-only caller cannot prepare, authorize or send and mismatched destinations are filtered", async () => {
  const { host } = setup({ props: { operator: false, approver: false }, businesses: [business, { ...business, id: "other-business", business_reference: "unrelated" }], transports: [transport, { ...transport, id: "other-destination", endpoint: "https://other.example.test", destination_version_id: "unrelated" }] });
  await host.settle();
  for (const label of ["Prepare exact handoff for review", "Authorize exact handoff", "Send authorized handoff"]) assert.equal(button(host, label), undefined);
  assert.ok(!elements(host.tree).some(node => node.type === "option" && ["other-business", "other-destination"].includes(node.props.value)));
});
