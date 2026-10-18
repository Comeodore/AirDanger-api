import assert from "node:assert/strict";
import { mock, test } from "node:test";

const { Poller, default: worker } = await import("../dist/edge.test.mjs");

const CHANNELS = ["rocketskyiv", "war_monitor", "kpszsu"];
const MINE = "9843f66c" + "a".repeat(56);
const OTHER = "bcea8811" + "b".repeat(56);
const THIRD = "cccccccc" + "c".repeat(56);

const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign"]);
const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", pair.privateKey)).toString("base64");
const APNS_KEY_P8 = Buffer.from(`-----BEGIN PRIVATE KEY-----\n${pkcs8}\n-----END PRIVATE KEY-----\n`).toString("base64");

const logs = [];
for (const level of ["log", "warn", "error"]) {
  console[level] = (...args) => logs.push(`${level}: ${args.join(" ")}`);
}

function fakeCtx(store = new Map()) {
  const rows = [];
  let alarm = null;
  const ctx = {
    store,
    rows,
    storage: {
      async get(key) {
        return structuredClone(store.get(key));
      },
      async put(key, value) {
        const entries = typeof key === "string" ? [[key, value]] : Object.entries(key);
        for (const [k, v] of entries) store.set(k, structuredClone(v));
      },
      async getAlarm() {
        return alarm;
      },
      async setAlarm(at) {
        alarm = at;
      },
      sql: {
        exec(query, ...args) {
          if (query.startsWith("INSERT")) rows.push(args);
          return { one: () => ({ n: rows.length }), toArray: () => rows };
        },
      },
    },
    blockConcurrencyWhile(fn) {
      ctx.ready = fn();
      return ctx.ready;
    },
  };
  return ctx;
}

function fakeBrain({ throwOn = null, legacy = false } = {}) {
  const calls = { process: [], commit: [] };
  return {
    calls,
    throwOn,
    async process(json) {
      const req = JSON.parse(json);
      calls.process.push(req);
      const post = req.post;
      const text = post.text ?? (post.html.match(/js-message_text[^>]*>(.*?)<\/div>/) || [])[1] ?? "";
      if (this.throwOn && text.includes(this.throwOn)) throw new Error("brain exploded");
      const state = { ...req.state, seen: (req.state.seen || 0) + 1 };
      const ts = post.ts ?? new Date().toISOString();
      if (!text.startsWith("ALERT")) {
        return JSON.stringify({ state, result: { outcome: "no_match", ts, age: 1, text } });
      }
      const result = {
        outcome: "alert", ts, age: 1, text, label: "inbound/ballistic", type: "ballistic",
        severity: "inbound", push_text: text,
        send: [{ tokens: req.devices.map((d) => d.token), payload: { aps: { alert: { title: text } } }, priority: 10, ttl: 60 }],
        commit: { kind: "threat", type: "ballistic", severity: "inbound", ts },
      };
      const reply = { state, result };
      if (!legacy) reply.committed_state = { ...state, committed: "reply" };
      return JSON.stringify(reply);
    },
    async commit(json) {
      const req = JSON.parse(json);
      calls.commit.push(req);
      return JSON.stringify({ state: { ...req.state, committed: "rpc" } });
    },
  };
}

function page(channel, posts) {
  return posts.map(([id, text]) =>
    `<div class="tgme_widget_message" data-post="${channel}/${id}">` +
    `<div class="tgme_widget_message_text js-message_text" dir="auto">${text}</div>` +
    `<time datetime="${new Date().toISOString()}"></time></div>`).join("\n");
}

function network({ pages = {}, apns = {}, home = { up: true, devices: [] } } = {}) {
  const net = { pages, apns, home, tme: [], pushes: [], reports: [], deviceFetches: 0 };
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url);
    if (u.host === "t.me") {
      net.tme.push(init);
      const response = new Response(net.pages[u.pathname.split("/").pop()] ?? "");
      Object.defineProperty(response, "url", { value: url });
      return response;
    }
    if (u.host.endsWith("push.apple.com")) {
      const token = u.pathname.split("/").pop();
      net.pushes.push({ token, headers: init.headers, body: JSON.parse(init.body) });
      const [status, reason] = net.apns[token] ?? [200];
      return new Response(reason ? JSON.stringify({ reason }) : "", { status });
    }
    if (u.host === "home.test") {
      if (!net.home.up) throw new Error("connection refused");
      if (u.pathname === "/edge/devices") {
        net.deviceFetches += 1;
        return Response.json({ devices: net.home.devices });
      }
      const body = JSON.parse(init.body);
      net.reports.push(body);
      return Response.json({ stored: body.decisions.length, removed: body.dead_tokens.length });
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  return net;
}

async function makePoller({ mode = "live", only = "", brain = fakeBrain(), devices, store } = {}) {
  const ctx = fakeCtx(store);
  const env = {
    MODE: mode, ONLY_TOKENS: only, BRAIN: brain, HOME_URL: "https://home.test", EDGE_KEY: "edge",
    APNS_KEY_P8, APNS_KEY_ID: "KEYID", APNS_TEAM_ID: "TEAM", APNS_TOPIC: "comeodore.airdanger",
    APNS_SANDBOX: "0", STATUS_KEY: "status-key",
  };
  const poller = new Poller(ctx, env);
  await ctx.ready;
  if (devices) poller.devices = devices.map((token) => ({ token, critical: true, critical_volume: 1 }));
  return { poller, ctx, brain };
}

function pages(extra = {}) {
  return Object.fromEntries(CHANNELS.map((c) => [c, page(c, extra[c] ?? [[100, "old"]])]));
}

function message(channel, id, text) {
  return { id, text, date: new Date(), chat: { username: channel } };
}

async function baselined(options) {
  const net = network({ pages: pages() });
  const made = await makePoller(options);
  await made.poller.tick();
  return { ...made, net };
}

test("a post seen by pull, update and t.me/s is decided and pushed once", async () => {
  const { poller, brain, net } = await baselined({ devices: [MINE] });
  assert.equal(brain.calls.process.length, 0);
  const post = message("kpszsu", 101, "ALERT Балістика на Київ");
  poller.mt.client = { getHistory: async (channel) => (channel === "kpszsu" ? [post] : []) };
  await poller.pullAll();
  await poller.fromMtproto("update", post);
  net.pages.kpszsu = page("kpszsu", [[100, "old"], [101, post.text]]);
  await poller.tick();
  assert.equal(brain.calls.process.length, 1);
  assert.equal(net.pushes.length, 1);
  assert.deepEqual(poller.firsts, { tme: 0, pull: 1, update: 0 });
  assert.equal(poller.reports.length, 1);
  assert.equal(poller.reports[0].pushed, true);
});

test("seen ids survive a restart and stay bounded", async () => {
  const { poller, ctx, brain } = await baselined({ devices: [MINE] });
  for (let id = 101; id <= 350; id++) {
    await poller.deliver("kpszsu", id, { channel: "kpszsu", text: "quiet", ts: new Date().toISOString() }, "pull");
  }
  const seen = ctx.store.get("seen").kpszsu;
  assert.equal(seen.length, 200);
  assert.equal(seen.at(-1), 350);
  const restarted = await makePoller({ store: ctx.store, brain });
  const before = brain.calls.process.length;
  await restarted.poller.deliver("kpszsu", 350, { channel: "kpszsu", text: "quiet" }, "update");
  assert.equal(brain.calls.process.length, before);
  assert.equal(restarted.poller.brain.seen, poller.brain.seen);
});

test("the cooldown is committed only after a delivery, from the same reply", async () => {
  const { poller, brain, net } = await baselined({ devices: [MINE] });
  net.apns[MINE] = [500, "InternalServerError"];
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  assert.equal(poller.brain.committed, undefined);
  net.apns[MINE] = [200];
  await poller.deliver("kpszsu", 102, { channel: "kpszsu", text: "ALERT two" }, "pull");
  assert.equal(poller.brain.committed, "reply");
  assert.equal(brain.calls.commit.length, 0);
});

test("an older brain without committed_state is committed over rpc", async () => {
  const { poller, brain } = await baselined({ devices: [MINE], brain: fakeBrain({ legacy: true }) });
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  assert.equal(brain.calls.commit.length, 1);
  assert.equal(poller.brain.committed, "rpc");
});

test("only 410 Unregistered removes a device", async () => {
  const { poller, net, ctx } = await baselined({ devices: [MINE, OTHER, THIRD] });
  net.apns[MINE] = [410, "Unregistered"];
  net.apns[OTHER] = [400, "BadDeviceToken"];
  net.apns[THIRD] = [400, "DeviceTokenNotForTopic"];
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  assert.deepEqual(poller.devices.map((d) => d.token), [OTHER, THIRD]);
  assert.deepEqual(ctx.store.get("deadTokens"), [MINE]);
  net.home.devices = [MINE, OTHER, THIRD].map((token) => ({ token }));
  await poller.syncHomeNow(true, true);
  assert.deepEqual(poller.devices.map((d) => d.token), [OTHER, THIRD]);
  assert.deepEqual(net.reports.at(-1).dead_tokens, [MINE]);
  assert.deepEqual(poller.deadTokens, []);
});

test("every push failing as a mismatch keeps every device", async () => {
  const { poller, net } = await baselined({ devices: [MINE, OTHER] });
  net.apns[MINE] = [400, "BadDeviceToken"];
  net.apns[OTHER] = [400, "BadDeviceToken"];
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  assert.equal(poller.devices.length, 2);
  assert.ok(logs.some((l) => l.includes("check APNS_SANDBOX and APNS_TOPIC")));
});

test("a post the brain chokes on does not hold back the other channels", async () => {
  const brain = fakeBrain({ throwOn: "BOOM" });
  const { poller } = await baselined({ devices: [MINE], brain });
  const histories = {
    rocketskyiv: [message("rocketskyiv", 101, "BOOM")],
    war_monitor: [message("war_monitor", 101, "ALERT war")],
    kpszsu: [message("kpszsu", 101, "ALERT kpszsu")],
  };
  poller.mt.client = { getHistory: async (channel) => histories[channel] };
  await poller.pullAll();
  assert.ok(poller.seen.war_monitor.includes(101));
  assert.ok(poller.seen.kpszsu.includes(101));
  assert.ok(!poller.seen.rocketskyiv.includes(101));
  assert.equal(poller.mt.lastPullOk !== null, true);
  brain.throwOn = null;
  await poller.pullAll();
  assert.ok(poller.seen.rocketskyiv.includes(101));
});

test("one channel failing to pull does not stop the others", async () => {
  const { poller, brain } = await baselined({ devices: [MINE] });
  poller.mt.client = {
    getHistory: async (channel) => {
      if (channel === "rocketskyiv") throw new Error("RPC_CALL_FAIL");
      return [message(channel, 101, "quiet")];
    },
  };
  await poller.pullAll();
  assert.equal(brain.calls.process.length, 2);
  assert.notEqual(poller.mt.failingSince, null);
  assert.equal(poller.mt.client !== null, true);
});

test("a hanging pull times out instead of stalling the loop", async () => {
  const { poller } = await baselined({ devices: [MINE] });
  poller.mt.client = { getHistory: () => new Promise(() => {}) };
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    const pulling = poller.pullAll();
    await Promise.resolve();
    mock.timers.tick(15 * 1000);
    await pulling;
  } finally {
    mock.timers.reset();
  }
  assert.equal(poller.mt.pulling, false);
  assert.notEqual(poller.mt.failingSince, null);
});

test("a revoked session falls back to t.me/s only", async () => {
  const { poller } = await baselined({ devices: [MINE] });
  let closed = false;
  poller.mt.client = {
    getHistory: async () => { throw new Error("Telegram API error 401: AUTH_KEY_UNREGISTERED"); },
    close: async () => { closed = true; },
  };
  await poller.pullAll();
  assert.equal(poller.mt.client, null);
  assert.match(poller.mt.disabled, /AUTH_KEY_UNREGISTERED/);
  assert.equal(closed, true);
  poller.ensureMtproto();
  assert.equal(poller.mt.starting, null);
});

test("test mode reaches only the listed devices and refuses short prefixes", async () => {
  const { poller, net } = await baselined({ mode: "test", only: "9843f66c", devices: [MINE, OTHER] });
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  assert.deepEqual(net.pushes.map((p) => p.token), [MINE]);
  assert.equal(poller.reports.length, 0);
  const refused = await poller.setOnlyTokens("bc");
  assert.match(refused.error, /8\+ hex/);
  assert.equal(poller.onlyTokens, "9843f66c");
  const pushed = await poller.testPush(true);
  assert.deepEqual(pushed.devices, ["9843f66c/critical"]);
  assert.deepEqual(net.pushes.at(-1).body.aps.sound, { critical: 1, name: "alert.caf", volume: 1 });
  await poller.setOnlyTokens("");
  assert.deepEqual(poller.sendDevices(), []);
});

test("a short ONLY_TOKENS binding matches nobody", async () => {
  const { poller } = await makePoller({ mode: "test", only: "b", devices: [MINE, OTHER] });
  assert.deepEqual(poller.sendDevices(), []);
});

test("test pushes are refused outside test mode", async () => {
  const { poller, net } = await baselined({ mode: "live", devices: [MINE, OTHER] });
  const before = net.pushes.length;
  assert.match((await poller.testPush(true)).error, /MODE=test/);
  assert.equal(net.pushes.length, before);
});

test("the feed queue is bounded while home is down and drains oldest first", async () => {
  const { poller, net } = await baselined({ devices: [MINE] });
  net.home.up = false;
  for (let i = 0; i < 1005; i++) {
    await poller.queueReport("kpszsu", { outcome: "alert", type: "ballistic", severity: "inbound", text: String(i), ts: new Date().toISOString() }, 1);
  }
  await poller.syncHomeNow(false, true);
  assert.equal(poller.reports.length, 1000);
  assert.equal(poller.reports[0].text, "5");
  net.home.up = true;
  for (let i = 0; i < 5; i++) await poller.syncHomeNow(false, true);
  assert.equal(poller.reports.length, 0);
  assert.deepEqual(net.reports.map((r) => r.decisions.length), [200, 200, 200, 200, 200]);
  assert.equal(net.reports[0].decisions[0].text, "5");
  assert.equal(net.reports[4].decisions.at(-1).text, "1004");
});

test("cached devices keep pushes going while home is down", async () => {
  const { poller, net } = await baselined({ devices: [MINE] });
  net.home.up = false;
  await poller.syncHomeNow(true, false);
  assert.equal(poller.devices.length, 1);
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  assert.equal(net.pushes.length, 1);
});

test("the cron does not re-arm a tick that is running", async () => {
  const { poller, ctx } = await baselined({ devices: [MINE] });
  await ctx.storage.setAlarm(null);
  assert.equal(await poller.ensure(), "running");
  poller.lastTick = { at: Date.now() - 60 * 1000 };
  assert.equal(await poller.ensure(), "armed");
  assert.notEqual(await ctx.storage.getAlarm(), null);
});

test("t.me/s is fetched with a timeout", async () => {
  const { net } = await baselined({ devices: [MINE] });
  assert.ok(net.tme.length >= 3);
  assert.ok(net.tme.every((init) => init.signal instanceof AbortSignal));
});

test("APNs requests carry the expected headers and an ES256 token", async () => {
  const { poller, net } = await baselined({ devices: [MINE] });
  await poller.deliver("kpszsu", 101, { channel: "kpszsu", text: "ALERT one" }, "pull");
  const { headers } = net.pushes[0];
  assert.equal(headers["apns-topic"], "comeodore.airdanger");
  assert.equal(headers["apns-push-type"], "alert");
  assert.equal(headers["apns-priority"], "10");
  const expiry = Number(headers["apns-expiration"]) - Math.floor(Date.now() / 1000);
  assert.ok(expiry > 55 && expiry <= 60);
  const [head, claims] = headers.authorization.replace("bearer ", "").split(".").slice(0, 2)
    .map((part) => JSON.parse(Buffer.from(part, "base64url").toString()));
  assert.deepEqual(head, { alg: "ES256", kid: "KEYID" });
  assert.equal(claims.iss, "TEAM");
});

test("every route needs the status key", async () => {
  const calls = [];
  const env = {
    STATUS_KEY: "status-key",
    POLLER: { getByName: () => ({ status: async () => { calls.push("status"); return {}; } }) },
  };
  const ask = (key) => worker.fetch(new Request("https://edge.test/status", { headers: key ? { "x-key": key } : {} }), env);
  assert.equal((await ask(null)).status, 403);
  assert.equal((await ask("status-kez")).status, 403);
  assert.equal((await ask("status-key-longer")).status, 403);
  assert.equal((await ask("status-key")).status, 200);
  assert.deepEqual(calls, ["status"]);
  const open = await worker.fetch(new Request("https://edge.test/status", { headers: { "x-key": "" } }), { ...env, STATUS_KEY: "" });
  assert.equal(open.status, 403);
});
