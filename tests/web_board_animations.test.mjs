import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const appSource = readFileSync(new URL("../web/app.js", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("../web/app.css", import.meta.url), "utf8");
const indexSource = readFileSync(new URL("../web/index.html", import.meta.url), "utf8");

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.dataset = {};
    this.style = {};
    this.value = id === "ai-player-count" ? "0" : "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
    };
    const fields = new Map();
    this.elements = new Proxy({}, {
      get: (_target, name) => {
        if (!fields.has(name)) {
          const field = new FakeElement(String(name));
          field.value = name === "player_count" ? "2" : "0";
          fields.set(name, field);
        }
        return fields.get(name);
      },
    });
  }

  addEventListener() {}
  append() {}
  replaceChildren() {}
  setAttribute() {}
  focus() {}
  scrollIntoView() {}
  remove() {}
}

function loadAnimationFunctions() {
  const byId = new Map();
  const audioCalls = [];
  const fakeDocument = {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, new FakeElement(id));
      return byId.get(id);
    },
    createElement(tag) {
      return new FakeElement(tag);
    },
    createElementNS(_namespace, tag) {
      return new FakeElement(tag);
    },
  };
  const sandbox = {
    console,
    document: fakeDocument,
    fetch: () => new Promise(() => {}),
    FormData: class {},
    navigator: { clipboard: { writeText: async () => {} } },
    sessionStorage: {
      getItem: () => null,
      setItem() {},
      removeItem() {},
    },
    __audioCalls: audioCalls,
  };
  sandbox.window = {
    WebSocket: undefined,
    location: { protocol: "http:", host: "localhost" },
    addEventListener() {},
    clearInterval() {},
    clearTimeout() {},
    confirm: () => true,
    matchMedia: () => ({ matches: false }),
    requestAnimationFrame() {},
    scrollTo() {},
    setInterval: () => 0,
    setTimeout: () => 0,
    CatanAudio: {
      playDice(values) { audioCalls.push(`dice:${values.join("+")}`); },
      playBuild(kind) { audioCalls.push(`build:${kind}`); },
      playTrade() { audioCalls.push("trade"); },
      playVictory() { audioCalls.push("victory"); },
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(`${appSource}\n;globalThis.__animationTest = {
    state,
    processEvents,
    queueLiveBoardAnimations,
    takePendingBoardAnimations,
    clearPendingBoardAnimations,
    detectNewBoardPieces,
    detectRevealedTiles,
    detectNewDiceRoll,
    deterministicDicePair,
    dicePipPositions,
    playLiveSnapshotAudio,
    tradeActivityTotal,
    activePlayerIndex,
    phaseTitle,
    variantConfigDocument,
    variantLabel,
    forecastEventPresentation,
    frontierPresentation,
    audioCalls: globalThis.__audioCalls,
  };`, sandbox, { filename: "web/app.js" });
  return sandbox.__animationTest;
}

const animation = loadAnimationFunctions();

function manifest({ road = false, firstBuilding = null, secondBuilding = null, seed = 41 } = {}) {
  return {
    mode: "constrained",
    seed,
    edges: [{ id: "edge-1", road: road ? { owner_player_index: 0 } : null }],
    nodes: [
      { id: "node-1", building: firstBuilding ? { type: firstBuilding, owner_player_index: 0 } : null },
      { id: "node-2", building: secondBuilding ? { type: secondBuilding, owner_player_index: 1 } : null },
    ],
  };
}

function snapshot(
  revision,
  {
    board = manifest(),
    pair = null,
    diceRolled = false,
    actor = 0,
    histories = { Alice: [] },
    latestTitle = "",
  } = {},
) {
  return {
    type: "state_snapshot",
    revision,
    board_manifest: board,
    state: {
      phase: {
        dice_rolled: diceRolled,
        last_dice_pair: pair,
        current_player_index: actor,
        turn_order: [0, 1],
      },
      initial: { dice_histories: histories },
      history: { latest_event: { title: latestTitle }, log_messages: [] },
    },
  };
}

function resetAnimationState() {
  animation.state.liveSnapshot = null;
  animation.state.snapshot = null;
  animation.state.lobby = null;
  animation.state.welcome = null;
  animation.state.replayIndex = null;
  animation.state.currentView = null;
  animation.clearPendingBoardAnimations();
  animation.audioCalls.length = 0;
}

test("board diff finds only newly built roads, settlements, and city upgrades", () => {
  const previous = manifest({ secondBuilding: "settlement" });
  const next = manifest({ road: true, firstBuilding: "settlement", secondBuilding: "city" });
  assert.deepEqual(
    Array.from(animation.detectNewBoardPieces(previous, next), ({ kind, targetId }) => `${kind}:${targetId}`),
    ["road:edge-1", "settlement:node-1", "city:node-2"],
  );
  assert.deepEqual(Array.from(animation.detectNewBoardPieces(next, next)), []);
  assert.deepEqual(Array.from(animation.detectNewBoardPieces(previous, manifest({ seed: 99, road: true }))), []);
});

test("frontier diff identifies only newly revealed stable tile ids", () => {
  const previous = {
    mode: "constrained",
    seed: 0,
    tiles: [
      { id: "tile-1", revealed: false, resource: "UNKNOWN" },
      { id: "tile-2", revealed: true, resource: "WOOD" },
    ],
  };
  const next = {
    ...previous,
    tiles: [
      { id: "tile-1", revealed: true, resource: "ORE" },
      { id: "tile-2", revealed: true, resource: "WOOD" },
    ],
  };
  assert.deepEqual(Array.from(animation.detectRevealedTiles(previous, next)), ["tile-1"]);
  assert.deepEqual(Array.from(animation.detectRevealedTiles(next, next)), []);
});

test("published last_dice_pair is used exactly and agrees with its total", () => {
  const previous = snapshot(7);
  const next = snapshot(8, { pair: [2, 6], diceRolled: true });
  const result = animation.detectNewDiceRoll(previous, next);
  assert.equal(result.total, 8);
  assert.deepEqual(Array.from(result.values), [2, 6]);
});

test("legacy snapshots receive a deterministic legal pair for main and initial dice", () => {
  const mainResult = animation.detectNewDiceRoll(
    snapshot(10),
    snapshot(11, { diceRolled: true, latestTitle: "Aliceのダイス: 9" }),
  );
  assert.equal(mainResult.total, 9);
  assert.equal(mainResult.values[0] + mainResult.values[1], 9);
  assert.ok(mainResult.values.every((value) => value >= 1 && value <= 6));
  assert.deepEqual(
    Array.from(mainResult.values),
    Array.from(animation.deterministicDicePair(9, 11)),
  );

  const initialResult = animation.detectNewDiceRoll(
    snapshot(20, { histories: { Alice: [6] } }),
    snapshot(21, { histories: { Alice: [6, 7] } }),
  );
  assert.equal(initialResult.total, 7);
  assert.equal(initialResult.values[0] + initialResult.values[1], 7);
});

test("a coalesced next-player roll is detected even when the same pair repeats", () => {
  const previous = snapshot(30, { pair: [3, 4], diceRolled: true, actor: 0 });
  const next = snapshot(33, { pair: [3, 4], diceRolled: true, actor: 1 });
  const result = animation.detectNewDiceRoll(previous, next);
  assert.equal(result.total, 7);
  assert.deepEqual(Array.from(result.values), [3, 4]);
});

test("unchanged and same-revision dice states never retrigger", () => {
  const rolled = snapshot(40, { pair: [1, 5], diceRolled: true });
  assert.equal(animation.detectNewDiceRoll(rolled, snapshot(40, { pair: [1, 5], diceRolled: true })), null);
  assert.equal(animation.detectNewDiceRoll(rolled, snapshot(41, { pair: [1, 5], diceRolled: true })), null);
});

test("bootstrap and replay updates are baselines, while a live revision queues once", () => {
  const previous = snapshot(50);
  const next = snapshot(51, {
    board: manifest({ road: true }),
    pair: [4, 4],
    diceRolled: true,
  });

  resetAnimationState();
  animation.processEvents([previous], { animateLive: false });
  animation.processEvents([next], { animateLive: false });
  assert.equal(animation.state.pendingBuildAnimations.size, 0);
  assert.equal(animation.state.pendingDiceAnimation, null);

  resetAnimationState();
  animation.processEvents([previous], { animateLive: false });
  animation.state.replayIndex = 0;
  animation.processEvents([next]);
  assert.equal(animation.state.pendingBuildAnimations.size, 0);
  assert.equal(animation.state.pendingDiceAnimation, null);

  resetAnimationState();
  animation.processEvents([previous], { animateLive: false });
  animation.processEvents([next]);
  assert.deepEqual(Array.from(animation.state.pendingBuildAnimations), ["road:edge-1"]);
  assert.equal(animation.state.pendingDiceAnimation.total, 8);

  const firstPlan = animation.takePendingBoardAnimations(next);
  assert.deepEqual(Array.from(firstPlan.buildKeys), ["road:edge-1"]);
  assert.deepEqual(Array.from(firstPlan.dice.values), [4, 4]);
  const secondPlan = animation.takePendingBoardAnimations(next);
  assert.equal(secondPlan.buildKeys.size, 0);
  assert.equal(secondPlan.dice, null);
});

test("dice pip layouts contain exactly the displayed face value", () => {
  for (let value = 1; value <= 6; value += 1) {
    assert.equal(animation.dicePipPositions(value).length, value);
  }
});

test("live audio cues use public dice, build, trade, and victory changes", () => {
  const previous = snapshot(60);
  previous.state.phase.name = "main";
  previous.state.match_metrics = {
    players: [{ domestic_trades: 0, bank_trades: 0 }],
  };
  const next = snapshot(61);
  next.state.phase.name = "main";
  next.state.match_metrics = {
    players: [{ domestic_trades: 2, bank_trades: 0 }],
  };

  resetAnimationState();
  animation.playLiveSnapshotAudio(
    previous,
    next,
    [{ kind: "road", targetId: "edge-1" }],
    { values: [2, 5], total: 7 },
  );
  assert.deepEqual(Array.from(animation.audioCalls), [
    "dice:2+5",
    "build:road",
    "trade",
  ]);
  assert.equal(animation.tradeActivityTotal(next), 2);

  animation.audioCalls.length = 0;
  next.state.phase.name = "finished";
  animation.playLiveSnapshotAudio(
    previous,
    next,
    [{ kind: "city", targetId: "node-1" }],
    null,
  );
  assert.deepEqual(Array.from(animation.audioCalls), ["victory"]);
});

test("initial-phase heading follows dice and placement actors", () => {
  const players = [{ name: "Host" }, { name: "CPU1" }];
  const diceState = {
    players,
    phase: { name: "initial", turn_order: [1, 0], current_player_index: 0 },
    initial: {
      dice_phase: true,
      dice_contenders: [0, 1],
      placement_order: [],
      player_index: 1,
    },
  };
  const diceActor = animation.activePlayerIndex(diceState);
  assert.equal(diceActor, 1);
  assert.match(animation.phaseTitle(diceState, diceActor).title, /CPU1/);

  const placementState = {
    players,
    phase: { name: "initial", turn_order: [1, 0], current_player_index: 0 },
    initial: {
      dice_phase: false,
      dice_contenders: [],
      placement_order: [1, 0],
      player_index: 1,
      waiting_for_road: false,
    },
  };
  const placementActor = animation.activePlayerIndex(placementState);
  assert.equal(placementActor, 0);
  assert.match(animation.phaseTitle(placementState, placementActor).title, /Host/);
});

test("room creation sends canonical standard and forecast variant documents", () => {
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("standard"))),
    { version: 1, kind: "standard", options: {} },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("forecast_events"))),
    {
      version: 1,
      kind: "forecast_events",
      options: {
        catalog: "core_v1",
        forecast_lead_turns: 2,
        event_interval_turns: 6,
      },
    },
  );
  assert.equal(animation.variantLabel({ kind: "forecast_events" }), "予告イベント");
  assert.equal(animation.variantLabel({ kind: "standard" }), "通常ルール");
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("frontier"))),
    {
      version: 1,
      kind: "frontier",
      options: { initial_radius: 1, reveal_rule: "road_adjacent_v1" },
    },
  );
  assert.equal(animation.variantLabel({ kind: "frontier" }), "フロンティア探索");
});

test("frontier status presents only public reveal progress", () => {
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.frontierPresentation({
      kind: "frontier",
      public: {
        revealed_tiles: ["-1,0", "0,0", "1,0"],
        discovery_count: 2,
      },
    }))),
    {
      visible: true,
      count: "3 / 19 公開",
      detail: "街道から2タイルを発見。霧に接する街道で探索を続けられます。",
    },
  );
  assert.equal(animation.frontierPresentation({ kind: "standard" }).visible, false);
});

test("forecast card presents countdown and active effects from public state only", () => {
  const presentation = animation.forecastEventPresentation({
    kind: "forecast_events",
    public: {
      completed_turns: 5,
      forecast: {
        event_id: "sheep_drought_v1",
        announced_turn: 2,
        resolve_turn: 8,
      },
      active_effects: [{
        event_id: "wheat_harvest_v1",
        started_turn: 2,
        expires_turn: null,
      }],
      resolved_count: 1,
    },
  });
  assert.equal(presentation.visible, true);
  assert.equal(presentation.title, "大干ばつ");
  assert.equal(presentation.countdown, "あと3手番");
  assert.deepEqual(Array.from(presentation.active), ["豊作: 次の麦生産に+1"]);
  assert.equal(
    animation.forecastEventPresentation({ kind: "standard", public: {} }).visible,
    false,
  );
});

test("forecast mode controls and persistent event card are present and styled", () => {
  assert.match(indexSource, /<select name="variant_kind">[\s\S]*value="forecast_events"/);
  assert.match(indexSource, /id="forecast-event-card" hidden/);
  assert.match(indexSource, /id="forecast-active-list"/);
  assert.match(cssSource, /\.forecast-event-card\s*\{/);
  assert.match(cssSource, /\.forecast-event-card\[hidden\]\s*\{[\s\S]*display:\s*none/);
});

test("frontier mode includes fog status and generated terrain asset", () => {
  assert.match(indexSource, /<select name="variant_kind">[\s\S]*value="frontier"/);
  assert.match(indexSource, /id="frontier-status-card" hidden/);
  assert.match(cssSource, /\.frontier-status-card\s*\{/);
  assert.match(appSource, /UNKNOWN:\s*"\/assets\/board\/frontier-fog\.webp"/);
});

test("animation CSS includes bounce, halo, dice landing, and reduced-motion overrides", () => {
  assert.match(cssSource, /@keyframes board-build-enter/);
  assert.match(cssSource, /@keyframes board-build-halo/);
  assert.match(cssSource, /@keyframes board-die-roll-first/);
  assert.match(cssSource, /@keyframes board-die-roll-second/);
  assert.match(cssSource, /@keyframes board-dice-total-enter/);
  assert.match(cssSource, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.board-dice-roll-overlay[\s\S]*animation: none !important/);
  assert.match(appSource, /class: "board-dice-roll-overlay"[\s\S]*"aria-hidden": "true"[\s\S]*"pointer-events": "none"/);
});
