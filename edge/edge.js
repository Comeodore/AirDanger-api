import { DurableObject } from "cloudflare:workers";
import { MemoryStorage, TelegramClient, WebCryptoProvider, WebSocketTransport } from "@mtcute/web";
import { convertFromTelethonSession } from "./node_modules/@mtcute/convert/telethon/convert.js";
import wasm from "./mtcute.wasm";

const CHANNELS = ["rocketskyiv", "war_monitor", "kpszsu"];
const POLL_MS = 2000;
const PULL_MS = 3000;
const PULL_LIMIT = 3;
const PULL_TIMEOUT_MS = 15 * 1000;
const TME_TIMEOUT_MS = 8 * 1000;
const STALL_MS = 30 * 1000;
const ONLY_PREFIX_MIN = 8;
const MTPROTO_RETRY_MS = 30 * 1000;
const MTPROTO_STARTUP_DELAY_MS = 180 * 1000;
const PULL_REPORT_MS = 60 * 1000;
const OBSERVED_LIMIT = 300;
const DEVICES_EVERY_MS = 60 * 1000;
const REPORT_EVERY_MS = 5 * 1000;
const HOME_TIMEOUT_MS = 5000;
const HOME_REPORT_MS = 10 * 60 * 1000;
const REPORT_LIMIT = 1000;
const APNS_CONCURRENCY = 6;
const JWT_TTL_MS = 50 * 60 * 1000;
const MISMATCH = new Set(["BadDeviceToken", "DeviceTokenNotForTopic"]);
const SOUNDS = ["alert.caf", "pulse.caf", "opovishchennia.caf"];
const MTPROTO_FATAL = /AUTH_KEY_UNREGISTERED|AUTH_KEY_DUPLICATED|AUTH_KEY_INVALID|SESSION_REVOKED|SESSION_EXPIRED|USER_DEACTIVATED/;
const SEEN_LIMIT = 200;
const KEEP_MS = 14 * 24 * 3600 * 1000;
const PRUNE_EVERY_MS = 3600 * 1000;
const BRIEF_LIMIT = 80;
const OUTAGE_WARN_MS = 30 * 1000;
const OUTAGE_ERROR_MS = 180 * 1000;
const OUTAGE_REMIND_MS = 600 * 1000;
const BLIND_WARN_MS = 60 * 1000;
const PULL_STALE_MS = 60 * 1000;
const MTPROTO_GRACE_MS = 120 * 1000;
const NOT_CONFIGURED = "not configured";
const USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36";
const POST = /data-post="[^"/]+\/(\d+)"/g;
const TIME = /<time datetime="([^"]+)"/;

function blocks(page) {
  const marks = [...page.matchAll(POST)];
  return marks.map((mark, i) => [
    Number(mark[1]),
    page.slice(mark.index, i + 1 < marks.length ? marks[i + 1].index : page.length),
  ]);
}

function flat(text) {
  return (text || "").split(/\s+/).filter(Boolean).join(" ");
}

function brief(text) {
  return flat(text).slice(0, BRIEF_LIMIT);
}

function seconds(ms) {
  return Math.round(ms / 1000);
}

function lagOf(ts) {
  const at = Date.parse(ts || "");
  return Number.isNaN(at) ? null : (Date.now() - at) / 1000;
}

function within(promise, ms, what) {
  let timer;
  const expiry = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} timed out after ${ms / 1000}s`)), ms);
  });
  return Promise.race([promise, expiry]).finally(() => clearTimeout(timer));
}

function sameKey(given, expected) {
  if (!expected || !given) return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  let diff = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) diff |= (a[i] ?? 0) ^ b[i];
  return diff === 0;
}

function prefixesOf(raw) {
  return (raw || "").toLowerCase().split(",").map((t) => t.trim()).filter(Boolean);
}

function b64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

class FetchWebSocket extends EventTarget {
  constructor(url, protocols) {
    super();
    this.url = url;
    this.binaryType = "arraybuffer";
    this.socket = null;
    this.open(url, protocols);
  }

  async open(url, protocols) {
    try {
      const response = await fetch(url.replace(/^ws/, "http"), {
        headers: {
          Upgrade: "websocket",
          "Sec-WebSocket-Protocol": [].concat(protocols || []).join(", "),
        },
      });
      const socket = response.webSocket;
      if (!socket) throw new Error(`websocket upgrade failed: HTTP ${response.status}`);
      socket.binaryType = "arraybuffer";
      socket.accept();
      this.socket = socket;
      socket.addEventListener("message", (event) =>
        this.dispatchEvent(Object.assign(new Event("message"), { data: event.data })));
      socket.addEventListener("close", (event) =>
        this.dispatchEvent(Object.assign(new Event("close"), { code: event.code, reason: event.reason })));
      socket.addEventListener("error", (event) =>
        this.dispatchEvent(Object.assign(new Event("error"), {
          error: event.error || new Error(String(event.message || "websocket error")),
        })));
      this.dispatchEvent(new Event("open"));
    } catch (error) {
      this.dispatchEvent(Object.assign(new Event("error"), { error }));
    }
  }

  send(data) {
    this.socket.send(data);
  }

  close(code, reason) {
    if (this.socket) this.socket.close(code, reason);
  }
}

function decisionLines(channel, id, r, dryRun, verbose) {
  const short = verbose ? flat(r.text) : brief(r.text);
  const sending = dryRun ? "would be sent (dry run)" : "sending";
  switch (r.outcome) {
    case "no_text":
      return verbose ? [`${channel}: message ${id} has no text`] : [];
    case "no_match":
      return verbose ? [`${channel}: no match — ${short}`] : [];
    case "type_off":
      return verbose ? [`${channel}: ${r.type} not in PUSH_TYPES — ${short}`] : [];
    case "stale":
      return [`${channel}: message ${id} skipped, ${Math.round(r.age)}s old`];
    case "safety": {
      const lines = [r.was_live
        ? `${channel}: safety cleared ballistic context — ${short}`
        : `${channel}: safety, no live context — ${short}`];
      if (r.all_clear === "outside") lines.push(`${channel}: all clear outside an episode — ${short}`);
      if (r.all_clear === "repeat") lines.push(`${channel}: all clear already announced — ${short}`);
      if (r.all_clear === "due") lines.push(`${channel}: all clear ${sending} — ${short}`);
      return lines;
    }
    case "alert":
      if (r.suppressed) return [`${channel}: ${r.label} suppressed, cooldown ${r.cooldown_left}s left — ${short}`];
      return [`${channel}: ${r.label} ${sending} — ${short}`];
    case "cruise_leads":
      return [`${channel}: bare missile dropped, cruise in the air — ${short}`];
    case "warning_off":
      return [`${channel}: ${r.type} warning silent, PUSH_WARNINGS off — ${short}`];
    case "bare_no_context":
      return [`${channel}: bare target dropped, no ballistic context — ${short}`];
    case "bare_warning_off":
      return [`${channel}: bare warning silent, PUSH_WARNINGS off — ${short}`];
    default:
      return [];
  }
}

export class Poller extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(
      "CREATE TABLE IF NOT EXISTS decisions (at INTEGER, channel TEXT, msg_id INTEGER, source TEXT, " +
        "post_ts TEXT, age REAL, outcome TEXT, label TEXT, sent INTEGER, suppressed INTEGER, " +
        "all_clear TEXT, text TEXT)",
    );
    this.channels = {};
    this.lastTick = null;
    this.lock = Promise.resolve();
    this.observed = { tme: {}, pull: {}, update: {} };
    this.firsts = { tme: 0, pull: 0, update: 0 };
    this.mt = {
      client: null, starting: null, me: null, disabled: null,
      nextStartAt: Date.now() + MTPROTO_STARTUP_DELAY_MS, bootAt: Date.now(), everOk: false, pulls: {}, signedInAt: null,
      timer: null, pulling: false, lastPullOk: null, failingSince: null, reportedAt: 0, updates: 0,
    };
    this.prunedAt = 0;
    this.mode = ["dry", "test", "live"].includes(env.MODE) ? env.MODE : "dry";
    this.dryRun = this.mode === "dry";
    this.home = { busy: false, devicesAt: 0, reportAt: 0, okAt: null, failReportedAt: 0 };
    this.apns = { key: null, jwt: null, jwtAt: 0 };
    ctx.blockConcurrencyWhile(async () => {
      this.seen = (await ctx.storage.get("seen")) || {};
      this.brain = (await ctx.storage.get("brain")) || {};
      this.logLevel = (await ctx.storage.get("logLevel")) || env.LOG_LEVEL || "normal";
      this.devices = (await ctx.storage.get("devices")) || [];
      this.reports = (await ctx.storage.get("reports")) || [];
      this.deadTokens = (await ctx.storage.get("deadTokens")) || [];
      this.onlyTokens = (await ctx.storage.get("onlyTokens")) ?? env.ONLY_TOKENS ?? "";
      console.log(
        `poller up: channels=${CHANNELS.join(",")} poll=${POLL_MS / 1000}s ` +
          `mode=${this.mode} log=${this.logLevel} devices=${this.devices.length}`,
      );
    });
  }

  get verbose() {
    return this.logLevel === "verbose";
  }

  async setOnlyTokens(tokens) {
    const prefixes = prefixesOf(tokens);
    const bad = prefixes.filter((p) => !new RegExp(`^[0-9a-f]{${ONLY_PREFIX_MIN},}$`).test(p));
    if (bad.length) {
      return { error: `token prefixes need ${ONLY_PREFIX_MIN}+ hex chars: ${bad.join(",")}`, only: this.onlyTokens };
    }
    this.onlyTokens = prefixes.join(",");
    await this.ctx.storage.put("onlyTokens", this.onlyTokens);
    console.log(`test devices set to ${this.onlyTokens || "none"}`);
    return { only: this.onlyTokens, matches: this.sendDevices().map((d) => d.token.slice(0, 8)) };
  }

  async setLogLevel(level) {
    if (level !== "verbose" && level !== "normal") return `unknown level ${level}`;
    this.logLevel = level;
    await this.ctx.storage.put("logLevel", level);
    console.log(`log level set to ${level}`);
    return level;
  }

  async ensure() {
    if ((await this.ctx.storage.getAlarm()) !== null) return "running";
    if (this.lastTick && Date.now() - this.lastTick.at < STALL_MS) return "running";
    await this.ctx.storage.setAlarm(Date.now() + 1000);
    console.warn("polling was stopped, re-armed");
    return "armed";
  }

  async alarm() {
    try {
      this.ensureMtproto();
      this.syncHome();
      await this.tick();
    } finally {
      await this.ctx.storage.setAlarm(Date.now() + POLL_MS);
    }
  }

  async tick() {
    const started = Date.now();
    const pages = await Promise.all(CHANNELS.map((channel) => this.page(channel)));
    const now = Date.now();
    const summary = [];
    let newCount = 0;
    for (let i = 0; i < CHANNELS.length; i++) {
      const channel = CHANNELS[i];
      const page = pages[i];
      if (page === null) {
        summary.push(`${channel} failed`);
        continue;
      }
      const found = blocks(page);
      if (!found.length) {
        this.blind(channel, page.length, now);
        summary.push(`${channel} blind`);
        continue;
      }
      summary.push(`${channel} ${found[found.length - 1][0]}`);
      this.reachable(channel, now);
      const fresh = this.observe("tme", channel, found.map(([id]) => id));
      if (!this.seen[channel]) {
        await this.baseline(channel, found.map(([id]) => id), "tme");
        continue;
      }
      for (const [id, html] of found) {
        const unseen = !this.seen[channel].includes(id);
        if (!unseen && !fresh.has(id)) continue;
        if (unseen) newCount += 1;
        const stamp = html.match(TIME);
        try {
          await this.deliver(channel, id, { channel, html }, "tme", stamp ? stamp[1] : null);
        } catch (error) {
          console.error(`${channel}: message ${id} failed, leaving it to be retried: ${error}`);
          break;
        }
      }
    }
    if (started - this.prunedAt > PRUNE_EVERY_MS) {
      this.sql.exec("DELETE FROM decisions WHERE at < ?", started - KEEP_MS);
      this.prunedAt = started;
    }
    this.lastTick = { at: started };
    if (this.verbose) {
      console.log(`tick, ${newCount} new: ${summary.join(", ")}`);
    }
  }

  observe(source, channel, ids) {
    const known = this.observed[source][channel];
    if (!known) {
      this.observed[source][channel] = ids.slice(-OBSERVED_LIMIT);
      return new Set();
    }
    const fresh = new Set(ids.filter((id) => !known.includes(id)));
    for (const id of fresh) known.push(id);
    if (known.length > OBSERVED_LIMIT) known.splice(0, known.length - OBSERVED_LIMIT);
    return fresh;
  }

  async baseline(channel, ids, source) {
    if (this.seen[channel]) return;
    this.seen[channel] = [...ids].sort((a, b) => a - b).slice(-SEEN_LIMIT);
    console.log(`watching ${channel} via ${source} from message ${Math.max(...ids)}`);
    await this.ctx.storage.put({ seen: this.seen, brain: this.brain });
  }

  deliver(channel, id, post, source, ts) {
    const run = this.lock.then(() => this.deliverNow(channel, id, post, source, ts));
    this.lock = run.catch(() => {});
    return run;
  }

  async deliverNow(channel, id, post, source, ts) {
    const seen = this.seen[channel];
    if (!seen) {
      await this.baseline(channel, [id], source);
      return;
    }
    if (seen.includes(id)) {
      if (this.verbose) {
        const lag = lagOf(ts);
        console.log(`${channel}: message ${id} also via ${source}` + (lag === null ? "" : `, ${lag.toFixed(1)}s after post`));
      }
      return;
    }
    await this.handle(channel, id, post, source);
    seen.push(id);
    if (seen.length > SEEN_LIMIT) seen.shift();
    this.firsts[source] += 1;
    await this.ctx.storage.put({ seen: this.seen, brain: this.brain });
  }

  ensureMtproto() {
    const mt = this.mt;
    if (mt.client || mt.starting || mt.disabled || Date.now() < mt.nextStartAt) return;
    if (!this.env.TG_SESSION || !this.env.TG_API_ID || !this.env.TG_API_HASH) {
      mt.disabled = NOT_CONFIGURED;
      console.warn("mtproto not configured — polling t.me/s only");
      return;
    }
    mt.starting = this.startMtproto().finally(() => {
      mt.starting = null;
    });
  }

  async startMtproto() {
    const mt = this.mt;
    const client = new TelegramClient({
      apiId: Number(this.env.TG_API_ID),
      apiHash: this.env.TG_API_HASH,
      storage: new MemoryStorage(),
      crypto: new WebCryptoProvider({ wasmInput: wasm }),
      transport: new WebSocketTransport({ ws: FetchWebSocket }),
      logLevel: 2,
    });
    client.onNewMessage.add((message) => {
      this.fromMtproto("update", message).catch((error) =>
        console.error(`update ${message.id} failed: ${error}`));
    });
    try {
      const me = await client.start({ session: convertFromTelethonSession(this.env.TG_SESSION) });
      mt.client = client;
      mt.me = me.username;
      mt.signedInAt = Date.now();
      console.log(`mtproto signed in as @${me.username}, pull every ${PULL_MS / 1000}s plus updates`);
      mt.timer = setInterval(() => this.pullAll(), PULL_MS);
      await this.pullAll();
    } catch (error) {
      await this.dropMtproto(client, error);
    }
  }

  async dropMtproto(client, error) {
    const mt = this.mt;
    if (mt.timer) clearInterval(mt.timer);
    mt.timer = null;
    mt.client = null;
    try {
      await client.close();
    } catch {}
    const text = String(error);
    if (MTPROTO_FATAL.test(text)) {
      mt.disabled = text;
      const hint = /DUPLICATED/.test(text)
        ? " (the session was used from two places at once and Telegram invalidated it — log in again)"
        : "";
      console.error(`mtproto session rejected, polling t.me/s only: ${text}${hint}`);
    } else {
      mt.nextStartAt = Date.now() + MTPROTO_RETRY_MS;
      console.error(`mtproto down, restarting in ${MTPROTO_RETRY_MS / 1000}s: ${text}`);
    }
  }

  async pullAll() {
    const mt = this.mt;
    const client = mt.client;
    if (!client || mt.pulling) return;
    mt.pulling = true;
    try {
      const batches = await Promise.allSettled(
        CHANNELS.map((channel) =>
          within(client.getHistory(channel, { limit: PULL_LIMIT }), PULL_TIMEOUT_MS, "pull")),
      );
      const failed = batches.find((b) => b.status === "rejected");
      if (failed && MTPROTO_FATAL.test(String(failed.reason))) {
        await this.dropMtproto(client, failed.reason);
        return;
      }
      this.notePull(failed ? failed.reason : null);
      this.notePulls(batches);
      for (let i = 0; i < CHANNELS.length; i++) {
        if (batches[i].status !== "fulfilled") continue;
        try {
          await this.pulled(CHANNELS[i], batches[i].value);
        } catch (error) {
          console.error(`${CHANNELS[i]}: pulled message failed, leaving it to be retried: ${error}`);
        }
      }
    } finally {
      mt.pulling = false;
    }
  }

  notePulls(batches) {
    const mt = this.mt;
    const now = Date.now();
    CHANNELS.forEach((channel, i) => {
      const pull = (mt.pulls[channel] ??= { lastOk: null, error: null });
      if (batches[i].status === "fulfilled") {
        pull.lastOk = now;
        pull.error = null;
        mt.everOk = true;
      } else {
        pull.error = String(batches[i].reason);
      }
    });
  }

  notePull(error) {
    const mt = this.mt;
    const now = Date.now();
    if (!error) {
      mt.lastPullOk = now;
      if (mt.failingSince !== null) {
        console.log(`mtproto pull back after ${seconds(now - mt.failingSince)}s`);
        mt.failingSince = null;
      }
      return;
    }
    if (mt.failingSince === null || now - mt.reportedAt >= PULL_REPORT_MS) {
      mt.failingSince ??= now;
      mt.reportedAt = now;
      console.warn(`mtproto pull failing for ${seconds(now - mt.failingSince)}s: ${error}`);
    }
  }

  async pulled(channel, batch) {
    const messages = [...batch].sort((a, b) => a.id - b.id);
    const fresh = this.observe("pull", channel, messages.map((m) => m.id));
    if (!this.seen[channel]) {
      await this.baseline(channel, messages.map((m) => m.id), "pull");
      return;
    }
    for (const message of messages) {
      if (this.seen[channel].includes(message.id) && !fresh.has(message.id)) continue;
      await this.deliver(channel, message.id, this.mtprotoPost(channel, message), "pull",
        message.date.toISOString());
    }
  }

  mtprotoPost(channel, message) {
    return { channel, text: message.text || "", ts: message.date.toISOString() };
  }

  async fromMtproto(source, message) {
    const channel = ((message.chat && message.chat.username) || "").toLowerCase();
    if (!CHANNELS.includes(channel)) return;
    this.mt.updates += 1;
    await this.deliver(channel, message.id, this.mtprotoPost(channel, message), source,
      message.date.toISOString());
  }

  sendDevices() {
    if (this.mode === "dry") return [];
    if (this.mode === "test") {
      const only = prefixesOf(this.onlyTokens).filter((p) => p.length >= ONLY_PREFIX_MIN);
      return this.devices.filter((d) => only.some((prefix) => d.token.startsWith(prefix)));
    }
    return this.devices;
  }

  async handle(channel, id, post, source) {
    const devices = this.sendDevices();
    const reply = JSON.parse(
      await this.env.BRAIN.process(
        JSON.stringify({
          state: this.brain,
          post,
          now: new Date().toISOString(),
          devices,
          push_warnings: true,
          critical_alerts: true,
          assume_delivered: this.dryRun,
        }),
      ),
    );
    this.brain = reply.state;
    const r = reply.result;
    const text = this.verbose ? flat(r.text) : brief(r.text);
    console.log(`${channel}: message ${id} via ${source}, ${r.age ?? "?"}s after post — ${text}`);
    for (const line of decisionLines(channel, id, r, this.dryRun, this.verbose)) console.log(line);
    let delivered = 0;
    if (r.send && !this.dryRun) {
      const label = r.outcome === "safety" ? "all clear" : r.label;
      delivered = await this.sendBatches(channel, r.send, label);
      if (delivered > 0 && r.commit) {
        this.brain = reply.committed_state ?? JSON.parse(
          await this.env.BRAIN.commit(JSON.stringify({ state: this.brain, commit: r.commit })),
        ).state;
      }
    }
    this.sql.exec(
      "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
      Date.now(), channel, id, source, r.ts ?? null, r.age ?? null, r.outcome, r.label ?? "",
      this.dryRun ? (r.send ? 1 : 0) : delivered, r.suppressed ? 1 : 0, r.all_clear ?? null,
      r.text ?? null,
    );
    if (this.mode === "live") await this.queueReport(channel, r, delivered);
  }

  async queueReport(channel, r, delivered) {
    let row = null;
    if (r.outcome === "alert") {
      row = {
        channel, type: r.type, severity: r.severity, text: r.text,
        push_text: r.push_text || r.text, ts: r.ts, pushed: !r.suppressed && delivered > 0,
      };
    } else if (r.outcome === "safety" && r.all_clear === "due") {
      row = {
        channel, type: "all_clear", severity: "clear", text: r.text, push_text: "",
        ts: r.ts, pushed: delivered > 0,
      };
    }
    if (!row) return;
    this.reports.push(row);
    if (this.reports.length > REPORT_LIMIT) this.reports.splice(0, this.reports.length - REPORT_LIMIT);
    await this.ctx.storage.put("reports", this.reports);
    this.home.reportAt = 0;
  }

  async apnsJwt() {
    const now = Date.now();
    if (this.apns.jwt && now - this.apns.jwtAt < JWT_TTL_MS) return this.apns.jwt;
    if (!this.apns.key) {
      const pem = atob(this.env.APNS_KEY_P8);
      const body = pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, "");
      const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
      this.apns.key = await crypto.subtle.importKey(
        "pkcs8", der, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"],
      );
    }
    const encode = (value) => b64url(new TextEncoder().encode(JSON.stringify(value)));
    const unsigned = `${encode({ alg: "ES256", kid: this.env.APNS_KEY_ID })}.` +
      encode({ iss: this.env.APNS_TEAM_ID, iat: Math.floor(now / 1000) });
    const signature = await crypto.subtle.sign(
      { name: "ECDSA", hash: "SHA-256" }, this.apns.key, new TextEncoder().encode(unsigned),
    );
    this.apns.jwt = `${unsigned}.${b64url(new Uint8Array(signature))}`;
    this.apns.jwtAt = now;
    return this.apns.jwt;
  }

  async apnsSend(token, payload, priority, ttl, jwt) {
    const host = this.env.APNS_SANDBOX === "1" ? "api.sandbox.push.apple.com" : "api.push.apple.com";
    try {
      const response = await fetch(`https://${host}/3/device/${token}`, {
        method: "POST",
        headers: {
          authorization: `bearer ${jwt}`,
          "apns-topic": this.env.APNS_TOPIC,
          "apns-push-type": "alert",
          "apns-priority": String(priority),
          "apns-expiration": String(Math.floor(Date.now() / 1000) + ttl),
          "content-type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      if (response.status === 200) return { token, ok: true };
      let reason = `HTTP ${response.status}`;
      try {
        reason = (await response.json()).reason || reason;
      } catch {}
      return { token, ok: false, reason };
    } catch (error) {
      return { token, ok: false, reason: String(error) };
    }
  }

  async sendBatches(channel, batches, label) {
    const jobs = batches.flatMap((b) => b.tokens.map((token) => [token, b.payload, b.priority, b.ttl]));
    if (!jobs.length) {
      console.warn(`${channel}: ${label} dropped, no devices to send to`);
      return 0;
    }
    const started = Date.now();
    let jwt;
    try {
      jwt = await this.apnsJwt();
    } catch (error) {
      console.error(`${channel}: ${label} not sent, APNs key unusable: ${error}`);
      return 0;
    }
    const results = [];
    let next = 0;
    const lane = async () => {
      while (next < jobs.length) {
        const [token, payload, priority, ttl] = jobs[next++];
        results.push(await this.apnsSend(token, payload, priority, ttl, jwt));
      }
    };
    await Promise.all(Array.from({ length: Math.min(APNS_CONCURRENCY, jobs.length) }, lane));
    const took = Date.now() - started;
    const delivered = results.filter((r) => r.ok).length;
    const mismatched = results.filter((r) => MISMATCH.has(r.reason));
    if (mismatched.length && mismatched.length === results.length) {
      console.error(`every push failed with ${mismatched[0].reason} — check APNS_SANDBOX and APNS_TOPIC`);
    }
    for (const r of results) {
      if (r.ok) continue;
      if (r.reason === "Unregistered") await this.dropDevice(r.token);
      else console.warn(`push to ${r.token.slice(0, 8)}… failed: ${r.reason}`);
    }
    if (!delivered) {
      console.error(`${channel}: ${label} DELIVERED TO NONE of ${jobs.length} devices in ${took}ms`);
    } else if (delivered < jobs.length) {
      console.warn(`${channel}: ${label} delivered to ${delivered} of ${jobs.length} devices in ${took}ms`);
    } else {
      console.log(`${channel}: ${label} delivered to ${delivered}/${jobs.length} devices in ${took}ms`);
    }
    return delivered;
  }

  async dropDevice(token) {
    this.devices = this.devices.filter((d) => d.token !== token);
    if (!this.deadTokens.includes(token)) this.deadTokens.push(token);
    await this.ctx.storage.put({ devices: this.devices, deadTokens: this.deadTokens });
    console.log(`device ${token.slice(0, 8)}… removed: Unregistered`);
    this.home.reportAt = 0;
  }

  syncHome() {
    if (this.dryRun || this.home.busy || !this.env.HOME_URL || !this.env.EDGE_KEY) return;
    const now = Date.now();
    const wantDevices = now - this.home.devicesAt >= DEVICES_EVERY_MS;
    const wantReport = (this.reports.length || this.deadTokens.length) && now - this.home.reportAt >= REPORT_EVERY_MS;
    if (!wantDevices && !wantReport) return;
    this.home.busy = true;
    this.syncHomeNow(wantDevices, wantReport).finally(() => {
      this.home.busy = false;
    });
  }

  async homeFetch(path, init = {}) {
    const response = await fetch(`${this.env.HOME_URL}${path}`, {
      ...init,
      headers: { "x-edge-key": this.env.EDGE_KEY, "content-type": "application/json" },
      signal: AbortSignal.timeout(HOME_TIMEOUT_MS),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async syncHomeNow(wantDevices, wantReport) {
    const now = Date.now();
    try {
      if (wantDevices) {
        this.home.devicesAt = now;
        const { devices } = await this.homeFetch("/edge/devices");
        const fresh = devices.filter((d) => !this.deadTokens.includes(d.token));
        if (fresh.length !== this.devices.length) {
          console.log(`devices synced from home: ${this.devices.length} → ${fresh.length}`);
        }
        this.devices = fresh;
        await this.ctx.storage.put("devices", this.devices);
      }
      if (wantReport) {
        this.home.reportAt = now;
        const decisions = this.reports.slice(0, 200);
        const deadTokens = [...this.deadTokens];
        const result = await this.homeFetch("/edge/report", {
          method: "POST",
          body: JSON.stringify({ decisions, dead_tokens: deadTokens }),
        });
        this.reports.splice(0, decisions.length);
        this.deadTokens = this.deadTokens.filter((t) => !deadTokens.includes(t));
        await this.ctx.storage.put({ reports: this.reports, deadTokens: this.deadTokens });
        if (this.verbose) console.log(`home report: ${result.stored} stored, ${result.removed} removed`);
      }
      if (wantDevices) {
        await this.homeFetch("/edge/health", { method: "POST", body: JSON.stringify(this.sourceHealth()) });
      }
      if (this.home.okAt === null || this.home.failReportedAt) {
        if (this.home.failReportedAt) console.log("home API reachable again");
        this.home.failReportedAt = 0;
      }
      this.home.okAt = Date.now();
    } catch (error) {
      if (!this.home.failReportedAt || Date.now() - this.home.failReportedAt >= HOME_REPORT_MS) {
        this.home.failReportedAt = Date.now();
        console.warn(
          `home API unreachable (${error}); using ${this.devices.length} cached devices, ` +
            `${this.reports.length} feed rows queued`,
        );
      }
    }
  }

  tmeState(channel, now) {
    const s = this.channels[channel];
    if (!s) return "starting";
    if (s.warnedNoPreview) return "no_preview";
    if (s.blindSince !== null && now - s.blindSince >= BLIND_WARN_MS) return "blind";
    if (s.failures && now - s.failingSince >= OUTAGE_WARN_MS) return "unreachable";
    return s.lastOk === null ? "starting" : "ok";
  }

  mtprotoState(now) {
    const mt = this.mt;
    if (mt.disabled) return mt.disabled === NOT_CONFIGURED ? "off" : "rejected";
    const fresh = (at) => at !== null && now - at < PULL_STALE_MS;
    const pulled = fresh(mt.signedInAt) || Object.values(mt.pulls).some((p) => fresh(p.lastOk));
    if (mt.client && pulled) return "ok";
    if (!mt.everOk && now - mt.bootAt < MTPROTO_STARTUP_DELAY_MS + MTPROTO_GRACE_MS) return "starting";
    return "down";
  }

  pullState(channel, overall, now) {
    if (overall !== "ok") return overall;
    const pull = this.mt.pulls[channel];
    const since = pull && pull.lastOk !== null ? pull.lastOk : this.mt.signedInAt;
    return since !== null && now - since < PULL_STALE_MS ? "ok" : "failing";
  }

  sourceHealth() {
    const now = Date.now();
    const mtproto = this.mtprotoState(now);
    const channels = Object.fromEntries(CHANNELS.map((channel) => [
      channel, { tme: this.tmeState(channel, now), mtproto: this.pullState(channel, mtproto, now) },
    ]));
    return { mtproto, channels };
  }

  async page(channel) {
    try {
      const response = await fetch(`https://t.me/s/${channel}`, {
        headers: { "User-Agent": USER_AGENT },
        cf: { cacheTtl: 0, cacheEverything: false },
        signal: AbortSignal.timeout(TME_TIMEOUT_MS),
      });
      if (!response.ok) {
        this.failure(channel, `HTTP ${response.status}`, Date.now());
        return null;
      }
      if (!response.url.includes("/s/")) {
        this.noPreview(channel, response.url, Date.now());
        return null;
      }
      return await response.text();
    } catch (error) {
      this.failure(channel, String(error), Date.now());
      return null;
    }
  }

  state(channel) {
    this.channels[channel] ??= {
      failures: 0, failingSince: 0, level: 0, reportedAt: 0,
      blindSince: null, warnedBlind: false, warnedNoPreview: false,
      lastOk: null, lastError: null,
    };
    return this.channels[channel];
  }

  endOutage(channel, s, now) {
    if (!s.failures) return;
    if (s.level) {
      console.log(
        `${channel}: reachable again after ${seconds(now - s.failingSince)}s (${s.failures} polls failed)`,
      );
    }
    s.failures = 0;
    s.level = 0;
  }

  reachable(channel, now) {
    const s = this.state(channel);
    this.endOutage(channel, s, now);
    if (s.warnedBlind) console.log(`${channel}: messages parse again, markup is fine`);
    if (s.warnedNoPreview) console.log(`${channel}: the channel preview is back`);
    s.blindSince = null;
    s.warnedBlind = false;
    s.warnedNoPreview = false;
    s.lastOk = now;
  }

  failure(channel, detail, now) {
    const s = this.state(channel);
    s.failures += 1;
    s.lastError = detail;
    if (s.failures === 1) s.failingSince = now;
    const down = now - s.failingSince;
    const still = () =>
      `${channel}: STILL unreachable for ${(down / 60000).toFixed(1)} min ` +
      `(${s.failures} polls failed), last: ${detail}`;
    if (s.level === 0) {
      if (down >= OUTAGE_WARN_MS) {
        s.level = 1;
        s.reportedAt = now;
        console.warn(
          `${channel}: unreachable for ${seconds(down)}s (${s.failures} polls failed), last: ${detail}`,
        );
      }
    } else if (s.level === 1) {
      if (down >= OUTAGE_ERROR_MS) {
        s.level = 2;
        s.reportedAt = now;
        console.error(still());
      }
    } else if (now - s.reportedAt >= OUTAGE_REMIND_MS) {
      s.reportedAt = now;
      console.error(still());
    }
  }

  blind(channel, bytes, now) {
    const s = this.state(channel);
    this.endOutage(channel, s, now);
    if (s.blindSince === null) {
      s.blindSince = now;
      return;
    }
    if (!s.warnedBlind && now - s.blindSince >= BLIND_WARN_MS) {
      s.warnedBlind = true;
      console.error(
        `${channel}: t.me/s answered ${bytes} bytes but no messages parsed for ` +
          `${seconds(now - s.blindSince)}s — the page markup has probably changed`,
      );
    }
  }

  noPreview(channel, url, now) {
    const s = this.state(channel);
    this.endOutage(channel, s, now);
    if (!s.warnedNoPreview) {
      s.warnedNoPreview = true;
      console.error(`${channel}: t.me redirected to ${url} — the channel preview is gone or revoked`);
    }
  }

  async status() {
    return {
      mode: this.mode,
      dry_run: this.dryRun,
      log_level: this.logLevel,
      devices: this.devices.length,
      only_tokens: this.onlyTokens,
      send_devices: this.sendDevices().map((d) => d.token.slice(0, 8)),
      home: { ok_ms_ago: this.home.okAt === null ? null : Date.now() - this.home.okAt,
        queued_reports: this.reports.length, queued_dead_tokens: this.deadTokens.length },
      mtproto: {
        me: this.mt.me,
        connected: Boolean(this.mt.client),
        disabled: this.mt.disabled,
        last_pull_ok_ms_ago: this.mt.lastPullOk === null ? null : Date.now() - this.mt.lastPullOk,
        failing_since: this.mt.failingSince,
        updates: this.mt.updates,
      },
      sources: this.sourceHealth(),
      first_seen_by: this.firsts,
      last_tick: this.lastTick,
      alarm: await this.ctx.storage.getAlarm(),
      channels: this.channels,
      newest: Object.fromEntries(
        Object.entries(this.seen).map(([channel, ids]) => [channel, ids[ids.length - 1]]),
      ),
      brain: this.brain,
      decisions: this.sql.exec("SELECT COUNT(*) AS n, MAX(at) AS last FROM decisions").one(),
    };
  }

  async testPush(critical) {
    if (this.mode !== "test") return { error: `test pushes need MODE=test, now ${this.mode}` };
    const devices = this.sendDevices();
    const batches = devices.map((d) => {
      const loud = critical && d.critical;
      const sound = SOUNDS.includes(d.sound) ? d.sound : "alert.caf";
      const volume = Math.round(Math.min(1, Math.max(0, d.critical_volume ?? 1)) * 100) / 100;
      const aps = {
        alert: loud
          ? { title: "Тест критикала", body: "Перевірка критичного сповіщення з Cloudflare" }
          : { title: "Тест воркера", body: "Перевірка доставки з Cloudflare" },
        sound: loud ? { critical: 1, name: sound, volume } : "silent.caf",
        "interruption-level": loud ? "critical" : "time-sensitive",
      };
      return { tokens: [d.token], payload: { aps, kind: "test" }, priority: 10, ttl: 60 };
    });
    const delivered = await this.sendBatches(
      "edge", batches, critical ? "critical test push" : "test push",
    );
    return {
      devices: devices.map((d) => d.token.slice(0, 8) + (critical && d.critical ? "/critical" : "")),
      delivered,
    };
  }

  async decisions(since) {
    return this.sql.exec("SELECT * FROM decisions WHERE at >= ? ORDER BY at", since).toArray();
  }
}

export default {
  async fetch(request, env) {
    if (!sameKey(request.headers.get("x-key"), env.STATUS_KEY)) {
      return new Response("forbidden", { status: 403 });
    }
    const url = new URL(request.url);
    const stub = env.POLLER.getByName("main");
    if (url.pathname === "/status") return Response.json(await stub.status());
    if (url.pathname === "/decisions") {
      return Response.json(await stub.decisions(Number(url.searchParams.get("since") || 0)));
    }
    if (url.pathname === "/start") return new Response(await stub.ensure());
    if (url.pathname === "/only") {
      return Response.json(await stub.setOnlyTokens(url.searchParams.get("tokens") || ""));
    }
    if (url.pathname === "/test-push") {
      return Response.json(await stub.testPush(url.searchParams.get("critical") === "1"));
    }
    if (url.pathname === "/log") {
      return new Response(await stub.setLogLevel(url.searchParams.get("level") || ""));
    }
    return new Response("not found", { status: 404 });
  },

  async scheduled(controller, env) {
    await env.POLLER.getByName("main").ensure();
  },
};
