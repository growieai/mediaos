const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { load, mount, elements, text, change, submit, deferred } = require("./component-harness.cjs");

function exported(file) {
  const source = fs.readFileSync(path.join(__dirname, "../app", file), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const result = { exports: {} };
  new Function("require", "module", "exports", code)(() => ({}), result, result.exports);
  return result.exports;
}
const types = exported("studio/types.ts");
const proxy = exported("api/internal/[...path]/route.ts");
const identity = { email: "operator@example.test", tenant_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", tenant_name: "Test workspace", roles: ["OPERATOR"], expires_at: new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString() };
const session = types.accountSession(identity);
const data = { identity: { tenant_id: session.tenant, memberships: [{ roles: ["OPERATOR"] }] }, overview: { tenant_id: session.tenant, tenant_name: "Test workspace", counts: { influencers: 0, workflow_runs: 0, awaiting_approval: 0 }, workflows: [] }, influencers: [], creationEnabled: true, categories: [], accounts: { connections: [], revocations: [] }, dependencies: {} };
const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", ...headers } });
const stubs = Object.fromEntries(["Modal", "Avatar", "CreatorWizard", "SourceComposer", "ContentDetail", "Channels", "Discovery"].map(name => [`./${name}`, { default: () => null }]));
function studio(accountRequest, request) {
  return mount(load("studio/CreatorStudio.tsx", { "./Icons": { default: () => null, categoryIcon: {} }, ...stubs, "./types": { ...types, accountRequest, request: request || (async (_scope, route) => ({ context: data.identity, "studio/overview": data.overview, "studio/influencers": { influencers: [], creation_enabled: true }, "studio/categories": { categories: [] }, "social/connections": data.accounts, "social/dependencies": {} })[route]) } }), {});
}
function click(host, label) {
  const button = elements(host.tree).find(node => node.type === "button" && (text(node).trim() === label || node.props["aria-label"] === label));
  assert.ok(button, label); button.props.onClick(); host.render();
}
function loginChild(host) { return elements(host.tree).find(node => typeof node.type === "function" && node.type.name === "WorkspaceLogin"); }
function login(accountRequest, token = null, request) {
  const parent = studio(accountRequest, request); click(parent, "Open workspace"); const child = loginChild(parent); const connected = [];
  const form = mount(child.type, { ...child.props, setupToken: token, onConnected: (...args) => connected.push(args) });
  return { parent, form, connected, close() { form.unmount(); parent.unmount(); } };
}

test("email sign-in is primary and never asks an ordinary user for tenant ID or access key", () => {
  const view = login(async () => { throw new types.SignInRequired(); });
  try {
    assert.match(text(view.form.tree), /Welcome back/); assert.match(text(view.form.tree), /Email address/);
    assert.ok(!text(view.form.tree).includes("Workspace ID")); assert.ok(!text(view.form.tree).includes("Access key"));
    click(view.form, "Advanced access"); assert.match(text(view.form.tree), /Workspace ID/);
    click(view.form, "Back to email sign-in"); click(view.form, "Forgot password?"); assert.match(text(view.form.tree), /administrator for a new password setup link/);
  } finally { view.close(); }
});

test("sign-in submits email and masked password then loads the authorized cookie workspace", async () => {
  const calls = []; const view = login(async (...args) => { calls.push(args); return identity; });
  try {
    change(view.form, "Email address", " operator@example.test "); change(view.form, "Password", "private password fixture");
    submit(view.form, 0); await view.form.settle();
    const call = calls.find(call => call[0] === "login"); assert.deepEqual(call[1], { email: identity.email, password: "private password fixture" });
    assert.equal(view.connected.length, 1); assert.equal(view.connected[0][0].token, ""); assert.equal(view.connected[0][0].tenant, identity.tenant_id);
    const password = elements(view.form.tree).find(node => node.type === "input" && node.props.autoComplete === "current-password");
    assert.equal(password, undefined); assert.match(text(view.form.tree), /password has been cleared/);
  } finally { view.close(); }
});
test("workspace recovery after successful setup does not consume the one-use invitation again", async () => {
  const calls = []; let unavailable = true;
  const view = login(async (...args) => { calls.push(args); return identity; }, "a".repeat(40), async (_scope, route) => {
    if (unavailable) throw new Error("Workspace temporarily unavailable");
    return ({ context: data.identity, "studio/overview": data.overview, "studio/influencers": { influencers: [], creation_enabled: true }, "studio/categories": { categories: [] }, "social/connections": data.accounts, "social/dependencies": {} })[route];
  });
  try {
    change(view.form, "Create password", "A memorable passphrase"); change(view.form, "Confirm password", "A memorable passphrase"); submit(view.form, 0); await view.form.settle();
    assert.match(text(view.form.tree), /You are signed in, but the workspace could not load/); unavailable = false; submit(view.form, 0); await view.form.settle();
    assert.equal(calls.filter(call => call[0] === "setup").length, 1); assert.equal(view.connected.length, 1);
  } finally { view.close(); }
});

test("invalid login shows a safe error, clears the password and does not invent a session", async () => {
  const view = login(async () => { throw new Error("Email or password is incorrect."); });
  try {
    change(view.form, "Email address", identity.email); change(view.form, "Password", "private password fixture"); submit(view.form, 0); await view.form.settle();
    assert.match(text(view.form.tree), /Email or password is incorrect/); assert.equal(view.connected.length, 0);
    assert.equal(elements(view.form.tree).find(node => node.props.autoComplete === "current-password").props.value, "");
  } finally { view.close(); }
});

test("setup validates confirmation and UTF-8 password bounds before consuming an invitation", async () => {
  const calls = []; const view = login(async (...args) => { calls.push(args); return identity; }, "a".repeat(40));
  try {
    change(view.form, "Create password", "short"); change(view.form, "Confirm password", "short"); submit(view.form, 0); await view.form.settle(); assert.match(text(view.form.tree), /at least 15/);
    change(view.form, "Create password", "🌍".repeat(19)); change(view.form, "Confirm password", "🌍".repeat(19)); submit(view.form, 0); await view.form.settle(); assert.equal(calls.filter(call => call[0] === "setup").length, 0);
    change(view.form, "Create password", "A memorable passphrase"); change(view.form, "Confirm password", "does not match"); submit(view.form, 0); await view.form.settle(); assert.match(text(view.form.tree), /do not match/);
    change(view.form, "Confirm password", "A memorable passphrase"); submit(view.form, 0); await view.form.settle();
    assert.deepEqual(calls.find(call => call[0] === "setup")[1], { token: "a".repeat(40), password: "A memorable passphrase" });
  } finally { view.close(); }
});

test("late login completion after closing cannot reopen a workspace", async () => {
  const pending = deferred(); const view = login(async () => pending.promise);
  change(view.form, "Email address", identity.email); change(view.form, "Password", "fixture password"); submit(view.form, 0); view.close();
  pending.resolve(identity); await view.form.settle(); assert.equal(view.connected.length, 0);
});

test("session restoration fetches current workspace permissions and survives reload without localStorage", async () => {
  const scoped = []; const host = studio(async () => identity, async (current, route) => {
    scoped.push(current); return ({ context: data.identity, "studio/overview": data.overview, "studio/influencers": { influencers: [], creation_enabled: true }, "studio/categories": { categories: [] }, "social/connections": data.accounts, "social/dependencies": {} })[route];
  });
  try { await host.settle(); assert.match(text(host.tree), /Welcome to Test workspace/); assert.equal(scoped.length, 6); assert.ok(scoped.every(scope => scope.token === "" && scope.tenant === identity.tenant_id)); }
  finally { host.unmount(); }
});

test("a manual login invalidates a pending session restoration", async () => {
  const pending = deferred(); const host = studio(async () => pending.promise);
  click(host, "Open workspace"); const child = loginChild(host);
  const legacy = { tenant: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", token: "advanced" };
  child.props.onConnected(legacy, { ...data, overview: { ...data.overview, tenant_name: "Chosen workspace" } }); host.render();
  pending.resolve(identity); await host.settle(); assert.match(text(host.tree), /Welcome to Chosen workspace/); assert.ok(!text(host.tree).includes("Welcome to Test workspace")); host.unmount();
});

test("logout clears private state immediately, revokes the server session and offers retry on failure", async () => {
  let attempt = 0; const calls = []; const pending = deferred();
  const host = studio(async action => { calls.push(action); if (action === "logout") { attempt++; if (attempt === 1) return pending.promise; return null; } return identity; });
  try {
    await host.settle(); click(host, "Sign out / switch access"); assert.ok(!text(host.tree).includes("Welcome to Test workspace")); assert.equal(calls.filter(action => action === "logout").length, 1);
    pending.reject(new Error("Connection interrupted")); await host.settle(); assert.match(text(host.tree), /Could not confirm server sign-out/);
    click(host, "Retry sign out"); await host.settle(); assert.equal(calls.filter(action => action === "logout").length, 2); assert.ok(!text(host.tree).includes("Retry sign out"));
  } finally { host.unmount(); }
});

test("setup fragment is removed before any session request and never becomes an API URL", () => {
  const previous = global.window; const replaced = []; const calls = [];
  global.window = { location: { hash: `#setup=${"x".repeat(40)}`, pathname: "/", search: "" }, history: { replaceState: (...args) => replaced.push(args) }, scrollTo() {} };
  const host = studio(async (...args) => calls.push(args));
  try { assert.deepEqual(replaced[0], [null, "", "/"]); assert.equal(calls.length, 0); assert.equal(loginChild(host).props.setupToken, "x".repeat(40)); }
  finally { host.unmount(); global.window = previous; }
});
test("opening another setup fragment in an existing tab aborts restoration and clears the prior workspace", async () => {
  const previous = global.window; const listeners = new Map(); const pending = deferred();
  global.window = { location: { hash: "", pathname: "/", search: "" }, history: { replaceState() { global.window.location.hash = ""; } }, scrollTo() {}, addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: name => listeners.delete(name) };
  const host = studio(async () => pending.promise);
  try {
    global.window.location.hash = `#setup=${"a".repeat(40)}`; listeners.get("hashchange")(); host.render();
    assert.equal(loginChild(host).props.setupToken, "a".repeat(40)); assert.equal(global.window.location.hash, "");
    pending.resolve(identity); await host.settle(); assert.ok(!text(host.tree).includes("Welcome to Test workspace"));
    global.window.location.hash = `#setup=${"b".repeat(40)}`; listeners.get("hashchange")(); host.render(); assert.equal(loginChild(host).props.setupToken, "b".repeat(40));
    host.replayEffects(); await host.settle(); assert.equal(loginChild(host).props.setupToken, "b".repeat(40));
  } finally { host.unmount(); global.window = previous; }
  assert.equal(listeners.size, 0);
});

test("request helpers use only the HttpOnly cookie for account sessions", async () => {
  const previous = global.fetch; const calls = []; global.fetch = async (...args) => { calls.push(args); return json(calls.length === 1 ? identity : {}); };
  try {
    await types.accountRequest("login", { email: identity.email, password: "fixture only" }); await types.request(session, "studio/overview");
    assert.ok(calls.every(([, options]) => options.credentials === "same-origin" && !("Authorization" in options.headers)));
    assert.ok(!calls[0][0].includes(identity.email)); assert.ok(!calls[0][0].includes("fixture"));
  } finally { global.fetch = previous; }
});
test("private portrait and visual-review clients work with cookie-only sessions", async () => {
  const previous = global.fetch; const calls = []; const hosts = [];
  global.fetch = async (url, options) => { calls.push([url, options]); return url.endsWith("/portrait") ? new Response("portrait fixture", { headers: { "Content-Type": "image/png" } }) : json({ configurations: [], renders: [] }); };
  try {
    const avatar = mount(load("studio/Avatar.tsx", { "./types": types }), { session, creator: { id: "creator", name: "Private creator", visual_config_version_id: "visual", portrait_available: true } }); hosts.push(avatar);
    const visual = mount(load("VisualReview.tsx", { "./DeliveryPreflight": { default: () => null }, "./MetricsReview": { default: () => null } }), { token: "", tenant: session.tenant, run: { id: "run", state: "AWAITING_APPROVAL", asset_version_id: "asset", research_version_id: "research", qa_report_id: "qa" }, operator: true, approver: false, refresh: async () => {} }); hosts.push(visual);
    await avatar.settle(); await visual.settle(); assert.equal(calls.length, 2);
    assert.ok(calls.every(([, options]) => options.credentials === "same-origin" && options.headers["X-Tenant-ID"] === session.tenant && !("Authorization" in options.headers)));
    assert.ok(elements(avatar.tree).some(node => node.type === "img"));
  } finally { hosts.forEach(host => host.unmount()); global.fetch = previous; }
});

test("typed account response rejects missing, malformed and expired identities", async () => {
  const previous = global.fetch;
  try {
    for (const value of [null, {}, { ...identity, tenant_id: "wrong" }, { ...identity, roles: [1] }, { ...identity, expires_at: "2000-01-01T00:00:00Z" }]) {
      global.fetch = async () => json(value); await assert.rejects(() => types.accountRequest("session"), /invalid sign-in response/);
    }
  } finally { global.fetch = previous; }
});

function proxyRequest(route, method, headers = {}, body) {
  const request = new Request(`https://mediaos.example.test/api/internal/${route}`, { method, headers, body }); request.nextUrl = new URL(request.url);
  return proxy[method](request, { params: Promise.resolve({ path: route.split("/") }) });
}
async function withProxy(work) {
  const previous = { fetch: global.fetch, origin: process.env.AUTH_PUBLIC_ORIGIN }; const calls = [];
  process.env.AUTH_PUBLIC_ORIGIN = "https://mediaos.example.test";
  global.fetch = async (...args) => { calls.push(args); return json(identity, 200, { "Set-Cookie": "__Host-mediaos_session=" + "x".repeat(40) + "; Secure; HttpOnly; SameSite=Lax; Path=/" }); };
  try { await work(calls); } finally { global.fetch = previous.fetch; if (previous.origin === undefined) delete process.env.AUTH_PUBLIC_ORIGIN; else process.env.AUTH_PUBLIC_ORIGIN = previous.origin; }
}
test("proxy permits exact same-origin login and forwards only the session Set-Cookie", async () => withProxy(async calls => {
  const response = await proxyRequest("auth/login", "POST", { Origin: "https://mediaos.example.test" }, "{}");
  assert.equal(response.status, 200); assert.match(response.headers.get("set-cookie"), /HttpOnly/); assert.equal(calls[0][1].headers.Origin, "https://mediaos.example.test"); assert.ok(!("Authorization" in calls[0][1].headers));
}));
for (const origin of [undefined, "null", "https://evil.example.test", "https://mediaos.example.test.evil.test"]) {
  test(`proxy rejects cookie mutation CSRF from ${origin}`, async () => withProxy(async calls => {
    const response = await proxyRequest("workflow-runs", "POST", { Cookie: "__Host-mediaos_session=" + "x".repeat(40), Host: "evil.example.test", ...(origin ? { Origin: origin } : {}) }, "{}");
    assert.equal(response.status, 403); assert.equal(calls.length, 0);
  }));
}
test("proxy forwards only the configured session cookie and never forwards backend cookies on business responses", async () => withProxy(async calls => {
  const cookie = "__Host-mediaos_session=" + "x".repeat(40);
  const response = await proxyRequest("studio/overview", "GET", { Cookie: `unrelated=private; ${cookie}; mediaos_session=${"y".repeat(40)}` });
  assert.equal(response.status, 200); assert.equal(calls[0][1].headers.Cookie, cookie); assert.equal(response.headers.get("set-cookie"), null);
}));
test("proxy rejects duplicate cookies, unknown auth methods and unauthenticated business APIs", async () => withProxy(async calls => {
  const cookie = "__Host-mediaos_session=" + "x".repeat(40);
  assert.equal((await proxyRequest("auth/session", "GET", { Cookie: `${cookie}; ${cookie}` })).status, 401);
  assert.equal((await proxyRequest("auth/login", "GET")).status, 404);
  assert.equal((await proxyRequest("auth/admin", "POST", {}, "{}")).status, 404);
  assert.equal((await proxyRequest("studio/overview", "GET")).status, 401); assert.equal(calls.length, 0);
}));
test("advanced bearer clients remain supported and cookie requests preserve the tenant binding for backend enforcement", async () => withProxy(async calls => {
  const response = await proxyRequest("workflow-runs", "POST", { Authorization: "Bearer fixture-access", "X-Tenant-ID": identity.tenant_id }, "{}");
  assert.equal(response.status, 200); assert.equal(calls[0][1].headers.Authorization, "Bearer fixture-access");
  await proxyRequest("studio/overview", "GET", { Cookie: "__Host-mediaos_session=" + "x".repeat(40), "X-Tenant-ID": "attacker-chosen-tenant" });
  assert.equal(calls[1][1].headers["X-Tenant-ID"], "attacker-chosen-tenant", "the backend must reject mismatch rather than silently use a new tab's tenant");
}));
test("production Next builds allow explicitly configured loopback HTTP but never remote HTTP", async () => withProxy(async calls => {
  const prior = process.env.NODE_ENV; process.env.NODE_ENV = "production";
  try {
    process.env.AUTH_PUBLIC_ORIGIN = "http://127.0.0.1:3000";
    assert.equal((await proxyRequest("auth/login", "POST", { Origin: "http://127.0.0.1:3000" }, "{}")).status, 200);
    process.env.AUTH_PUBLIC_ORIGIN = "http://remote.example.test";
    assert.equal((await proxyRequest("auth/login", "POST", { Origin: "http://remote.example.test" }, "{}")).status, 503);
    delete process.env.AUTH_PUBLIC_ORIGIN;
    assert.equal((await proxyRequest("auth/session", "GET")).status, 503);
    assert.equal(calls.length, 1);
  } finally { if (prior === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = prior; }
}));
test("console pages and login cannot be framed even without the hosted ingress", async () => {
  const config = exported("../next.config.ts").default;
  const rules = await config.headers(); const headers = Object.fromEntries(rules[0].headers.map(row => [row.key, row.value]));
  assert.equal(rules[0].source, "/:path*"); assert.equal(headers["Content-Security-Policy"], "frame-ancestors 'none'"); assert.equal(headers["X-Frame-Options"], "DENY"); assert.equal(headers["Referrer-Policy"], "no-referrer");
});
