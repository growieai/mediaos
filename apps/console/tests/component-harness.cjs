// Minimal offline hook and JSX harness for exercising rendered event handlers.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

let rendering;
const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
const hooks = {
  useState(initial) {
    const host = rendering, index = host.index++;
    if (!(index in host.hooks)) host.hooks[index] = { value: typeof initial === "function" ? initial() : initial };
    return [host.hooks[index].value, value => {
      host.hooks[index].value = typeof value === "function" ? value(host.hooks[index].value) : value;
      host.dirty = true;
    }];
  },
  useRef(initial) {
    const host = rendering, index = host.index++;
    if (!(index in host.hooks)) host.hooks[index] = { current: initial };
    return host.hooks[index];
  },
  useMemo(create, deps) {
    const host = rendering, index = host.index++;
    if (!same(host.hooks[index]?.deps, deps)) host.hooks[index] = { deps, value: create() };
    return host.hooks[index].value;
  },
  useCallback(callback, deps) { return hooks.useMemo(() => callback, deps); },
  useEffect(effect, deps) {
    const host = rendering, index = host.index++;
    if (!same(host.hooks[index]?.deps, deps)) {
      const prior = host.hooks[index];
      host.hooks[index] = { deps, effect, cleanup: prior?.cleanup };
      host.effects.push(() => {
        prior?.cleanup?.();
        host.hooks[index].cleanup = effect();
      });
    }
  },
};
const jsx = (type, props, key) => ({ type, props: props || {}, key });
function load(name, dependencies = {}) {
  const source = fs.readFileSync(path.join(__dirname, "../app", name), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled)(name => {
    if (name === "react") return hooks;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name in dependencies) return dependencies[name];
    throw new Error("Unexpected component dependency");
  }, module, module.exports);
  return module.exports.default;
}

function mount(component, props, runEffects = true) {
  const host = { hooks: [], props, effects: [], dirty: false, index: 0, tree: null };
  host.render = next => {
    if (next) host.props = next;
    let remaining = 30;
    do {
      assert.ok(remaining-- > 0, "component render converges");
      host.dirty = false; host.index = 0; host.effects = []; rendering = host;
      try { host.tree = component(host.props); } finally { rendering = null; }
      if (runEffects) host.effects.forEach(effect => effect());
    } while (host.dirty);
    return host.tree;
  };
  // Drain complete promise chains (mutation -> response body -> parallel refresh),
  // rather than assuming a fixed number of microtasks represents a settled UI.
  host.settle = async () => { for (let i = 0; i < 5; i++) { await new Promise(setImmediate); host.render(); } };
  host.unmount = () => host.hooks.forEach(hook => hook?.cleanup?.());
  host.replayEffects = () => {
    host.hooks.forEach(hook => { if (hook?.effect) { hook.cleanup?.(); hook.cleanup = hook.effect(); } });
    host.render();
  };
  host.render();
  return host;
}
function elements(tree) {
  if (Array.isArray(tree)) return tree.flatMap(elements);
  return tree && typeof tree === "object" ? [tree, ...elements(tree.props.children)] : [];
}
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join("");
  if (tree && typeof tree === "object") return text(tree.props.children);
  return tree === null || tree === undefined || typeof tree === "boolean" ? "" : String(tree);
}
function field(host, label) {
  const node = elements(host.tree).find(node => node.type === "label" && text(node).trim().startsWith(label));
  assert.ok(node, `field exists: ${label}`);
  return elements(node).find(node => ["input", "select", "textarea"].includes(node.type));
}
function change(host, label, value) {
  const node = field(host, label);
  node.props.onChange({ target: node.props.type === "checkbox" ? { checked: value } : { value } });
  host.render();
}
function form(host, index) { return elements(host.tree).filter(node => node.type === "form")[index]; }
function submit(host, index) { form(host, index).props.onSubmit({ preventDefault() {} }); host.render(); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

module.exports = { load, mount, elements, text, field, change, form, submit, deferred };
