const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "../..");
const recordingDir = path.join(root, "docs", "motion-recordings");
const state = { history: [], lifecycle: { drafts: [], approved: [] }, ticketNumber: 0, failApproval: false };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const json = (res, value, status = 200) => {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(value));
};

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", chunk => { body += chunk; });
    req.on("end", () => {
      try { resolve(body ? JSON.parse(body) : {}); }
      catch (err) { reject(err); }
    });
    req.on("error", reject);
  });
}

function mockTicket(ticket) {
  const suffix = ++state.ticketNumber;
  const ticketId = `TKT-MOTION-${suffix}`;
  const draftId = `draft-motion-${suffix}`;
  const createdAt = new Date().toISOString();
  const trace = [
    { t: 0, step: "Ticket received", detail: ticket, state: "completed" },
    { t: 0, step: "Orchestrator", detail: "Assigned to agent1-solver using least-used-agent routing.", state: "completed" },
    { t: 0.1, step: "Memory Agent", detail: "Searching Meko shared context.", state: "completed" },
    { t: 0.2, step: "Searched Meko", detail: "match quality: low; approved knowledge hits: 0; unapproved drafts: 0", state: "completed" },
    { t: 0.3, step: "Resolution Agent", detail: "Proposed solution generated.", state: "completed" },
    { t: 0.4, step: "Meko Memory", detail: "New fix stored as an unapproved draft.", state: "completed" },
  ];
  const result = {
    ticket_id: ticketId,
    ticket,
    status: "new_draft_saved",
    agent: "agent1-solver",
    reply: "Please restart the app and check whether the issue persists.",
    total_seconds: 0.4,
    prompt_tokens: 18,
    trace,
    created_at: createdAt,
    memory_id: draftId,
  };
  state.history.push(result);
  state.lifecycle.drafts.unshift({
    id: draftId,
    text: `[DRAFT - unverified, written by agent1-solver] Support problem: ${ticket}\nSuggested fix: Please restart the app.`,
    learned_by: "agent1-solver",
    learned_at: createdAt,
  });
  return result;
}

function createServer() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    if (req.method === "GET" && url.pathname === "/") {
      res.writeHead(200, {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
      });
      res.end(fs.readFileSync(path.join(root, "index.html")));
      return;
    }
    if (req.method === "GET" && url.pathname === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/health") {
      json(res, { meko: "connected", model: "motion-check", agents: 2 });
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/metrics") {
      json(res, state.history);
      return;
    }
    if (req.method === "GET" && url.pathname === "/api/memory/lifecycle") {
      json(res, state.lifecycle);
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/approve") {
      const body = await readBody(req);
      if (state.failApproval) {
        json(res, { detail: "Simulated approval failure" }, 502);
        return;
      }
      const draft = state.lifecycle.drafts.find(item => item.id === body.id);
      if (!draft) {
        json(res, { detail: "Draft not found" }, 404);
        return;
      }
      state.lifecycle.drafts = state.lifecycle.drafts.filter(item => item.id !== body.id);
      const entry = "Section: Approved fixes > Demo issue\n\nProblem: Demo issue. Fix: Restart the app. Escalate to: a human specialist.";
      state.lifecycle.approved.unshift({
        id: `promoted-${body.id}`,
        text: entry,
        approved_at: new Date().toISOString(),
        approved_by: "Demo reviewer",
      });
      await sleep(120);
      json(res, { entry });
      return;
    }
    if (req.method === "POST" && url.pathname === "/api/ticket/stream") {
      const body = await readBody(req);
      const result = mockTicket(String(body.ticket || "Demo issue"));
      const events = [
        { type: "step", ...result.trace[0] },
        { type: "step", ...result.trace[1] },
        { type: "step", t: 0.1, step: "Memory Agent", detail: "Searching Meko shared context.", state: "running" },
        { type: "step", ...result.trace[2] },
        { type: "step", ...result.trace[3] },
        { type: "step", t: 0.3, step: "Resolution Agent", detail: "Generating a cautious new solution.", state: "running" },
        { type: "step", ...result.trace[4] },
        { type: "step", t: 0.35, step: "Meko Memory", detail: "Saving proposed solution as an unapproved draft.", state: "running" },
        { type: "step", ...result.trace[5] },
      ];
      res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });
      for (const event of events) {
        if (res.destroyed) return;
        res.write(`data: ${JSON.stringify(event)}\n\n`);
        await sleep(70);
      }
      if (!res.destroyed) {
        res.write(`data: ${JSON.stringify({ type: "complete", result })}\n\n`);
        res.end();
      }
      return;
    }
    json(res, { detail: "Not found" }, 404);
  });
}

async function openPage(browser, baseUrl, options = {}) {
  const contextOptions = {
    viewport: { width: 1440, height: 900 },
    reducedMotion: options.reducedMotion || "no-preference",
  };
  if (options.recordVideo) {
    contextOptions.recordVideo = { dir: recordingDir, size: { width: 1440, height: 900 } };
  }
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", error => consoleErrors.push(error.message));
  await page.route("https://fonts.googleapis.com/**", route =>
    route.fulfill({ status: 200, contentType: "text/css", body: "" }));
  await page.addInitScript(() => {
    window.__motionStarts = [];
    window.__waapiAnimations = [];
    window.__rafRequests = 0;
    window.__cls = 0;
    document.addEventListener("animationstart", event => {
      window.__motionStarts.push({ name: event.animationName, target: event.target.className?.toString() || "" });
    });
    const nativeAnimate = Element.prototype.animate;
    Element.prototype.animate = function (keyframes, options) {
      const animation = nativeAnimate.call(this, keyframes, options);
      window.__waapiAnimations.push({ duration: options?.duration, target: this.className?.toString() || "" });
      return animation;
    };
    const nativeRaf = window.requestAnimationFrame.bind(window);
    window.requestAnimationFrame = callback => {
      window.__rafRequests++;
      return nativeRaf(callback);
    };
    if ("PerformanceObserver" in window) {
      try {
        new PerformanceObserver(list => {
          for (const entry of list.getEntries()) {
            if (!entry.hadRecentInput) window.__cls += entry.value;
          }
        }).observe({ type: "layout-shift", buffered: true });
      } catch (_) {}
    }
  });
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("#stat-agent-fixes").textContent !== "—");
  return { context, page, consoleErrors };
}

async function closeAndSaveVideo(context, page, destination) {
  const video = page.video();
  await context.close();
  if (video) {
    await fs.promises.mkdir(path.dirname(destination), { recursive: true });
    const source = await video.path();
    await video.saveAs(destination);
    if (path.resolve(source) !== path.resolve(destination) && path.dirname(source) === recordingDir) {
      await fs.promises.unlink(source);
    }
  }
}

async function checkReducedMotion(browser, baseUrl) {
  state.history = [];
  state.lifecycle = { drafts: [], approved: [] };
  state.ticketNumber = 0;
  const { context, page, consoleErrors } = await openPage(browser, baseUrl, { reducedMotion: "reduce" });
  await page.waitForTimeout(100);
  const animations = await page.evaluate(() => document.getAnimations().map(animation => ({
    name: animation.animationName,
    playState: animation.playState,
    target: animation.effect?.target?.className?.toString() || "",
  })));
  assert.equal(animations.length, 0, `Reduced-motion mode should have no active animations after load: ${JSON.stringify(animations)}`);
  await page.getByRole("button", { name: "Agents", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#pg-agents").classList.contains("show"));
  await page.getByRole("button", { name: "Support desk" }).click();
  await page.locator("#ticket").fill("Reduced motion test ticket");
  await page.getByRole("button", { name: "Send to agents" }).click();
  await page.locator("#result .tag").filter({ hasText: "New fix saved" }).waitFor();
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(() => document.getAnimations().length), 0);
  assert.deepEqual(consoleErrors, []);
  await context.close();
  return { animationsAfterLoad: animations.length, pageNavigation: "passed", ticketFlow: "passed" };
}

async function checkTicketFlow(browser, baseUrl) {
  state.history = [];
  state.lifecycle = { drafts: [], approved: [] };
  state.ticketNumber = 0;
  const { context, page, consoleErrors } = await openPage(browser, baseUrl, { recordVideo: true });
  await page.locator("#ticket").fill("The demo app is showing a temporary issue.");
  await page.getByRole("button", { name: "Send to agents" }).click();
  await page.locator("#result .tag").filter({ hasText: "New fix saved" }).waitFor();
  await page.waitForFunction(() => window.__motionStarts.some(item => item.name === "stamp-land"));
  await page.waitForFunction(() => window.__motionStarts.some(item => item.name === "step-enter"));
  await page.waitForTimeout(650);
  const first = await page.evaluate(() => ({
    steps: window.__motionStarts.filter(item => item.name === "step-enter").length,
    stamps: window.__motionStarts.filter(item => item.name === "stamp-land").length,
    raf: window.__rafRequests,
    count: document.querySelector("#stat-agent-fixes").textContent,
  }));
  assert(first.steps > 0, "Streamed steps should enter once");
  assert.equal(first.stamps, 1, "The ticket outcome stamp should animate once");
  assert.equal(first.count, "1", "The live KPI should reach the actual ticket count");
  assert(first.raf > 0, "The KPI count-up should use animation frames");
  await page.evaluate(() => refresh());
  await page.waitForTimeout(60);
  const afterRefresh = await page.evaluate(() => ({
    steps: window.__motionStarts.filter(item => item.name === "step-enter").length,
    stamps: window.__motionStarts.filter(item => item.name === "stamp-land").length,
    raf: window.__rafRequests,
  }));
  assert.deepEqual(afterRefresh, { steps: first.steps, stamps: 1, raf: first.raf }, "Refreshing unchanged data must not replay motion");
  assert.deepEqual(consoleErrors, []);
  await closeAndSaveVideo(context, page, path.join(recordingDir, "ticket-stream.webm"));
  return { stepEntrances: first.steps, outcomeStamps: first.stamps, countUpFrames: first.raf, refreshReplayed: false };
}

async function checkApprovalFlow(browser, baseUrl) {
  state.history = [];
  state.lifecycle = {
    drafts: [{
      id: "draft-approval-demo",
      text: "[DRAFT - unverified, written by agent1-solver] Support problem: Demo issue\nSuggested fix: Restart the app.",
      learned_by: "agent1-solver",
      learned_at: new Date().toISOString(),
    }],
    approved: [],
  };
  state.ticketNumber = 0;
  const { context, page, consoleErrors } = await openPage(browser, baseUrl, { recordVideo: true });
  const session = await context.newCDPSession(page);
  await session.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  await page.getByRole("button", { name: "Memory & approval" }).click();
  await page.getByRole("button", { name: "Approve" }).waitFor();
  await page.waitForTimeout(550);
  await page.evaluate(() => {
    window.__frameGaps = [];
    window.__lastFrame = null;
    window.__frameStart = performance.now();
    const sample = now => {
      if (window.__lastFrame !== null) window.__frameGaps.push(now - window.__lastFrame);
      window.__lastFrame = now;
      if (now - window.__frameStart < 1000) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  page.on("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByText("Approved", { exact: true }).waitFor();
  await page.waitForFunction(() => window.__waapiAnimations.some(item => item.duration === 480));
  await page.waitForTimeout(700);
  const beforeRefresh = await page.evaluate(() => ({
    flips: window.__waapiAnimations.filter(item => item.duration === 480).length,
    cls: window.__cls,
    frameGaps: window.__frameGaps.slice(),
    approvalCount: document.querySelector("#stat-human-approvals").textContent,
  }));
  assert.equal(beforeRefresh.flips, 1, "One approval should cause one FLIP animation");
  assert.equal(beforeRefresh.approvalCount, "1");
  assert(beforeRefresh.frameGaps.length > 10, "CPU-throttled frame sampling should run");
  const maxFrameGap = Math.max(...beforeRefresh.frameGaps);
  assert(maxFrameGap < 100, `Approval animation should stay smooth at 4x CPU throttling (max frame gap ${maxFrameGap.toFixed(1)}ms)`);
  await page.evaluate(() => refresh());
  await page.waitForTimeout(60);
  const afterRefresh = await page.evaluate(() => window.__waapiAnimations.filter(item => item.duration === 480).length);
  assert.equal(afterRefresh, 1, "Refreshing unchanged approval data must not replay FLIP");
  assert.deepEqual(consoleErrors, []);
  await session.detach();
  await closeAndSaveVideo(context, page, path.join(recordingDir, "human-approval.webm"));
  return { flipCount: beforeRefresh.flips, approvalCount: beforeRefresh.approvalCount, cpuThrottle: "4x", maxFrameGapMs: Number(maxFrameGap.toFixed(1)), refreshReplayed: false };
}

async function checkApprovalFailure(browser, baseUrl) {
  state.history = [];
  state.lifecycle = {
    drafts: [{
      id: "draft-approval-failure",
      text: "[DRAFT - unverified, written by agent1-solver] Support problem: Demo issue\nSuggested fix: Restart the app.",
      learned_by: "agent1-solver",
      learned_at: new Date().toISOString(),
    }],
    approved: [],
  };
  state.failApproval = true;
  const { context, page, consoleErrors } = await openPage(browser, baseUrl);
  await page.getByRole("button", { name: "Memory & approval" }).click();
  await page.getByRole("button", { name: "Approve" }).waitFor();
  page.on("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByText("Approval failed: Simulated approval failure", { exact: true }).waitFor();
  await page.waitForFunction(() => window.__motionStarts.some(item => item.name === "button-shake"));
  assert(consoleErrors.every(message => message.includes("502 (Bad Gateway)")), `Unexpected console errors: ${consoleErrors.join("; ")}`);
  await context.close();
  state.failApproval = false;
  return { failureToast: "shown", buttonShake: "played" };
}

async function checkMemoryExpansion(browser, baseUrl) {
  state.history = [];
  state.lifecycle = {
    drafts: Array.from({ length: 7 }, (_, index) => ({
      id: `draft-expansion-${index}`,
      text: `[DRAFT - unverified, written by agent1-solver] Support problem: Demo issue ${index}\nSuggested fix: Restart the app.`,
      learned_by: "agent1-solver",
      learned_at: new Date().toISOString(),
    })),
    approved: [],
  };
  const { context, page, consoleErrors } = await openPage(browser, baseUrl);
  await page.getByRole("button", { name: "Memory & approval" }).click();
  await page.getByRole("button", { name: "Read more (2 more)" }).click();
  const expanded = await page.locator("#drafts .fix.fresh-expanded").evaluateAll(cards =>
    cards.map(card => Number.parseInt(card.style.animationDelay, 10)));
  assert.deepEqual(expanded, [0, 40], "Expanded draft cards should enter with a 40ms stagger");
  await page.getByRole("button", { name: "Show less" }).click();
  await page.waitForFunction(() => document.querySelectorAll("#drafts .fix").length === 5);
  assert.deepEqual(consoleErrors, []);
  await context.close();
  return { expandedCards: expanded.length, staggerMs: expanded, collapse: "passed" };
}

async function checkLayout(browser, baseUrl) {
  state.history = [];
  state.lifecycle = { drafts: [], approved: [] };
  const { context, page, consoleErrors } = await openPage(browser, baseUrl);
  await page.waitForTimeout(550);
  const clsByPage = {};
  await page.evaluate(() => { window.__cls = 0; });
  for (const [pageName, buttonName] of [
    ["desk", "Support desk"],
    ["memory", "Memory & approval"],
    ["agents", "Agents"],
  ]) {
    await page.getByRole("button", { name: buttonName, exact: true }).click();
    await page.waitForTimeout(500);
    clsByPage[pageName] = await page.evaluate(() => window.__cls);
    await page.evaluate(() => { window.__cls = 0; });
  }
  const overflow = {};
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    overflow[width] = {};
    for (const [pageName, buttonName] of [
      ["desk", "Support desk"],
      ["memory", "Memory & approval"],
      ["agents", "Agents"],
    ]) {
      await page.getByRole("button", { name: buttonName, exact: true }).click();
      await page.waitForTimeout(250);
      overflow[width][pageName] = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        document: document.documentElement.scrollWidth,
      }));
      assert(
        overflow[width][pageName].document <= overflow[width][pageName].viewport,
        `Horizontal overflow at ${width}px on ${pageName}: ${JSON.stringify(overflow[width][pageName])}`,
      );
    }
  }
  Object.entries(clsByPage).forEach(([name, value]) => {
    assert(value < 0.02, `CLS on ${name} should stay under 0.02 (observed ${value})`);
  });
  assert.deepEqual(consoleErrors, []);
  await context.close();
  return { clsByPage, overflow, consoleErrors };
}

async function main() {
  await fs.promises.mkdir(recordingDir, { recursive: true });
  const server = createServer();
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const baseUrl = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch({ headless: true });
  try {
    const results = {
      reducedMotion: await checkReducedMotion(browser, baseUrl),
      ticketFlow: await checkTicketFlow(browser, baseUrl),
      approvalFlow: await checkApprovalFlow(browser, baseUrl),
      approvalFailure: await checkApprovalFailure(browser, baseUrl),
      memoryExpansion: await checkMemoryExpansion(browser, baseUrl),
      layout: await checkLayout(browser, baseUrl),
      recordings: [
        path.relative(root, path.join(recordingDir, "ticket-stream.webm")),
        path.relative(root, path.join(recordingDir, "human-approval.webm")),
      ],
    };
    console.log(JSON.stringify(results, null, 2));
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
