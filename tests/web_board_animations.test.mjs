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
    this.focusCalls = [];
    this.scrollCalls = [];
    this.children = [];
    this.listeners = new Map();
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

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute() {}
  focus(options) { this.focusCalls.push(options); }
  scrollIntoView(options) { this.scrollCalls.push(options); }
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
    requestAnimationFrame(callback) {
      if (typeof callback === "function") callback();
      return 1;
    },
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
    currentTurnSeat,
    domesticTradeActorSeat,
    domesticTradePresentation,
    tradeOfferSignature,
    formatTradeBundle,
    buildTradeReceiveOperator,
    renderTradePromptActions,
    commandOptionsForView,
    showLiveSnapshot,
    sendGameCommand,
    developmentCardInventoryPresentation,
    createDevelopmentCardInventory,
    calculatePublicPoints,
    createResultVpBreakdown,
    resetRoomState,
    personalityModeForView,
    publicAIPersonalityLabel,
    lobbyAIMemberDescription,
    playerIdentityLabel,
    aiCommentaryHeading,
    elements,
    audioCalls: globalThis.__audioCalls,
  };`, sandbox, { filename: "web/app.js" });
  return sandbox.__animationTest;
}

const animation = loadAnimationFunctions();

test("live score stays public even for the viewer's own hidden victory cards", () => {
  animation.state.matchResult = null;
  animation.state.snapshot = {
    board_manifest: {
      nodes: [
        { building: { type: "settlement", owner_player_index: 0 } },
        { building: { type: "city", owner_player_index: 0 } },
      ],
    },
  };
  const points = animation.calculatePublicPoints({
    players: [{ victory_point_cards: 2 }, {}],
    phase: {
      name: "main",
      longest_road_owner: 0,
      largest_army_owner: 1,
    },
  });

  assert.deepEqual([...points], [5, 2]);
});

test("finished score and result breakdown reveal the complete authoritative total", () => {
  animation.state.matchResult = {
    standings: [{ seat: 1, victory_points: 10 }],
  };
  const points = animation.calculatePublicPoints({
    players: [{ victory_point_cards: 2 }],
    phase: { name: "finished" },
  });
  assert.deepEqual([...points], [10]);

  const breakdown = animation.createResultVpBreakdown({
    name: "Host",
    victory_points: 10,
    vp_breakdown: {
      settlements: { count: 4, points: 4 },
      cities: { count: 1, points: 2 },
      longest_road: { awarded: true, points: 2 },
      largest_army: { awarded: false, points: 0 },
      victory_point_cards: { count: 2, points: 2 },
      total: 10,
    },
  });
  assert.equal(breakdown.children.length, 5);
  assert.deepEqual(
    breakdown.children.map((child) => child.textContent),
    [
      "開拓地 4点（4軒）",
      "都市 2点（1軒）",
      "最長交易路 2点",
      "最大騎士力 0点",
      "勝利点カード 2点（2枚）",
    ],
  );
});

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
  animation.state.replayPlaying = false;
  animation.state.replayTimer = null;
  animation.state.replayRequestPending = false;
  animation.state.replayExpectedIndex = null;
  animation.state.replayRequestGeneration = 0;
  animation.state.replayManifest = null;
  animation.state.commandPending = false;
  animation.state.nextSequence = 0;
  animation.state.developmentInventoryOpen = null;
  animation.elements["board-shell"].focusCalls.length = 0;
  animation.elements["board-shell"].scrollCalls.length = 0;
  animation.clearPendingBoardAnimations();
  animation.audioCalls.length = 0;
}

function replayFrameEvent(index, revision = 70 + index) {
  return {
    type: "network_replay_frame",
    room_code: "ROOM01",
    snapshot: snapshot(revision),
    controls: {
      frame_index: index,
      revision,
      label: "frame " + index,
    },
  };
}

function armReplayFrame(index) {
  animation.state.welcome = { room_code: "ROOM01" };
  animation.state.replayRequestPending = true;
  animation.state.replayExpectedIndex = index;
}

function deliverReplayFrame(event, batch = [event]) {
  armReplayFrame(event.controls.frame_index);
  animation.processEvents(batch, { animateLive: false });
  animation.state.replayRequestPending = false;
  animation.state.replayExpectedIndex = null;
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
  const historicalSnapshot = animation.state.snapshot;
  animation.state.replayIndex = 0;
  animation.processEvents([next]);
  assert.equal(animation.state.pendingBuildAnimations.size, 0);
  assert.equal(animation.state.pendingDiceAnimation, null);
  assert.equal(animation.state.snapshot, historicalSnapshot);
  assert.equal(animation.state.liveSnapshot, next);

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

test("replay entry focuses once while later frames preserve the viewport", () => {
  resetAnimationState();
  animation.state.replayManifest = {
    frame_count: 3,
    frames: [null, null, null],
  };
  const board = animation.elements["board-shell"];
  const first = replayFrameEvent(0);
  deliverReplayFrame(first, [first, first]);
  assert.equal(board.focusCalls.length, 1);
  assert.equal(board.scrollCalls.length, 1);
  assert.equal(board.scrollCalls[0].block, "center");

  deliverReplayFrame(replayFrameEvent(1));
  animation.state.replayPlaying = true;
  deliverReplayFrame(replayFrameEvent(2));
  deliverReplayFrame(replayFrameEvent(2));
  assert.equal(board.focusCalls.length, 1);
  assert.equal(board.scrollCalls.length, 1);
});

test("explicit live return and a later replay entry may focus once again", () => {
  resetAnimationState();
  animation.state.replayManifest = {
    frame_count: 2,
    frames: [null, null],
  };
  const board = animation.elements["board-shell"];
  const historical = replayFrameEvent(0, 80);
  deliverReplayFrame(historical);
  assert.equal(board.scrollCalls.length, 1);

  const live = snapshot(99);
  animation.state.liveSnapshot = live;
  animation.state.replayPlaying = true;
  animation.showLiveSnapshot();
  assert.equal(animation.state.replayIndex, null);
  assert.equal(animation.state.snapshot, live);
  assert.equal(animation.state.replayPlaying, false);
  assert.equal(board.scrollCalls.length, 2);

  deliverReplayFrame(historical);
  assert.equal(board.scrollCalls.length, 3);
  deliverReplayFrame(replayFrameEvent(1, 81));
  assert.equal(board.scrollCalls.length, 3);
});

test("a late replay response cannot undo explicit live return", () => {
  resetAnimationState();
  animation.state.replayManifest = {
    frame_count: 2,
    frames: [null, null],
  };
  const board = animation.elements["board-shell"];
  deliverReplayFrame(replayFrameEvent(0, 80));
  const live = snapshot(99);
  animation.state.liveSnapshot = live;

  armReplayFrame(1);
  const late = replayFrameEvent(1, 81);
  animation.showLiveSnapshot();
  const scrollsAfterLive = board.scrollCalls.length;
  animation.processEvents([late], { animateLive: false });

  assert.equal(animation.state.replayIndex, null);
  assert.equal(animation.state.snapshot, live);
  assert.equal(animation.state.replayRequestPending, false);
  assert.equal(board.scrollCalls.length, scrollsAfterLive);
});

test("a replay response from a room that was reset is ignored", () => {
  resetAnimationState();
  animation.state.replayManifest = {
    frame_count: 2,
    frames: [null, null],
  };
  armReplayFrame(1);
  const late = replayFrameEvent(1, 81);

  animation.resetRoomState(false);
  animation.processEvents([late], { animateLive: false });

  assert.equal(animation.state.welcome, null);
  assert.equal(animation.state.snapshot, null);
  assert.equal(animation.state.replayIndex, null);
  assert.equal(animation.state.replayRequestPending, false);
  assert.equal(animation.elements["board-shell"].scrollCalls.length, 0);
});

test("replay suppresses options and stale command submission", async () => {
  resetAnimationState();
  const actionable = snapshot(90);
  actionable.command_options = [
    { command: "roll_dice", args: {} },
  ];
  assert.equal(animation.commandOptionsForView(actionable).length, 1);

  animation.state.snapshot = actionable;
  animation.state.replayIndex = 0;
  animation.state.nextSequence = 7;
  assert.deepEqual(
    Array.from(animation.commandOptionsForView(actionable)),
    [],
  );
  await animation.sendGameCommand(actionable.command_options[0]);
  assert.equal(animation.state.commandPending, false);
  assert.equal(animation.state.nextSequence, 7);
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

test("domestic trade presentation always uses the viewer's give and receive direction", () => {
  const gameState = {
    players: [{ name: "Host" }, { name: "Guest" }],
    phase: {
      name: "main",
      special_phase: "domestic_trade_response",
      turn_order: [0, 1],
      current_player_index: 0,
    },
    domestic_trade: {
      partner: 1,
      editor: 0,
      give: { WOOD: 2, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 0 },
      receive: { WOOD: 0, SHEEP: 1, WHEAT: 0, BRICK: 0, ORE: 1 },
      receive_operator: "or",
      is_counter: false,
      is_broadcast: false,
      broadcast_index: -1,
    },
  };

  const proposer = animation.domesticTradePresentation(gameState, 0);
  assert.equal(proposer.outgoingSide, "give");
  assert.equal(proposer.incomingSide, "receive");
  assert.equal(proposer.counterpartyName, "Guest");
  assert.equal(animation.formatTradeBundle(proposer.outgoing), "木 2");
  assert.equal(proposer.incomingOperator, "or");
  assert.equal(
    animation.formatTradeBundle(proposer.incoming, proposer.incomingOperator),
    "羊 1 または 鉄 1",
  );

  const responder = animation.domesticTradePresentation(gameState, 1);
  assert.equal(responder.outgoingSide, "receive");
  assert.equal(responder.incomingSide, "give");
  assert.equal(responder.counterpartyName, "Host");
  assert.equal(responder.outgoingOperator, "or");
  assert.equal(
    animation.formatTradeBundle(responder.outgoing, responder.outgoingOperator),
    "羊 1 または 鉄 1",
  );
  assert.equal(animation.domesticTradeActorSeat(gameState), 1);

  gameState.domestic_trade.is_counter = true;
  gameState.domestic_trade.editor = 1;
  gameState.phase.special_phase = "domestic_trade_edit";
  assert.equal(animation.domesticTradeActorSeat(gameState), 1);
  assert.equal(
    animation.domesticTradePresentation(gameState, 1).outgoingSide,
    "receive",
  );
  gameState.phase.special_phase = "domestic_trade_counter_response";
  assert.equal(animation.domesticTradeActorSeat(gameState), 0);
});

test("trade offer identity ignores handoff phase but changes for counter or next responder", () => {
  const gameState = {
    phase: {
      special_phase: "domestic_trade_handoff",
      turn_order: [0, 1, 2],
      current_player_index: 0,
    },
    domestic_trade: {
      partner: 1,
      give: { WOOD: 1 },
      receive: { ORE: 1 },
      is_counter: false,
      is_broadcast: true,
      broadcast_index: 0,
    },
  };
  const handoff = animation.tradeOfferSignature(gameState);
  gameState.phase.special_phase = "domestic_trade_response";
  assert.equal(animation.tradeOfferSignature(gameState), handoff);
  gameState.domestic_trade.partner = 2;
  gameState.domestic_trade.broadcast_index = 1;
  assert.notEqual(animation.tradeOfferSignature(gameState), handoff);
  const nextResponder = animation.tradeOfferSignature(gameState);
  gameState.domestic_trade.is_counter = true;
  assert.notEqual(animation.tradeOfferSignature(gameState), nextResponder);
  const counter = animation.tradeOfferSignature(gameState);
  gameState.domestic_trade.receive_operator = "or";
  assert.notEqual(animation.tradeOfferSignature(gameState), counter);
});

test("OR editor and response controls expose explicit alternatives in both directions", () => {
  const toggle = animation.buildTradeReceiveOperator("or", [
    { command: "trade_receive_operator", args: { operator: "and" } },
  ]);
  const choices = toggle.children[1];
  assert.deepEqual(
    choices.children.map((button) => button.textContent),
    ["すべて", "どれか1つ（OR）"],
  );
  assert.equal(choices.children[0].disabled, false);
  assert.equal(choices.children[1].disabled, true);

  const gameState = {
    players: [{ name: "Host" }, { name: "Guest" }],
    phase: {
      name: "main",
      special_phase: "domestic_trade_response",
      turn_order: [0, 1],
      current_player_index: 0,
    },
    domestic_trade: {
      partner: 1,
      give: { WOOD: 1 },
      receive: { ORE: 1, WHEAT: 1 },
      receive_operator: "or",
      is_counter: false,
      is_broadcast: true,
      broadcast_index: 0,
    },
  };
  const acceptOptions = [
    { command: "trade_accept", args: { resource: "ORE" } },
    { command: "trade_accept", args: { resource: "WHEAT" } },
    { command: "trade_counter", args: {} },
    { command: "trade_reject", args: {} },
  ];
  animation.renderTradePromptActions(
    acceptOptions,
    false,
    animation.domesticTradePresentation(gameState, 1),
  );
  assert.deepEqual(
    animation.elements["trade-prompt-actions"].children.map((button) => button.textContent),
    ["鉄1を渡して承諾", "麦1を渡して承諾", "条件を変更する", "今回は拒否"],
  );

  gameState.domestic_trade.is_counter = true;
  gameState.domestic_trade.is_broadcast = false;
  gameState.phase.special_phase = "domestic_trade_counter_response";
  animation.renderTradePromptActions(
    acceptOptions.filter((option) => option.command !== "trade_counter"),
    false,
    animation.domesticTradePresentation(gameState, 0),
  );
  assert.deepEqual(
    animation.elements["trade-prompt-actions"].children.map((button) => button.textContent),
    ["鉄1を受け取って承諾", "麦1を受け取って承諾", "今回は拒否"],
  );
});

function developmentPlayer(overrides = {}) {
  return {
    development_card_total: 0,
    development_cards: {
      KNIGHT: 0,
      ROAD_BUILDING: 0,
      YEAR_OF_PLENTY: 0,
      MONOPOLY: 0,
    },
    new_development_cards: {
      KNIGHT: 0,
      ROAD_BUILDING: 0,
      YEAR_OF_PLENTY: 0,
      MONOPOLY: 0,
    },
    victory_point_cards: 0,
    ...overrides,
  };
}

function descendantText(element) {
  if (!element || typeof element !== "object") return "";
  return [
    element.textContent || "",
    ...(element.children || []).map(descendantText),
  ].join(" ");
}

test("own development inventory groups usable, new, and private victory cards", () => {
  const player = developmentPlayer({
    development_card_total: 4,
    development_cards: {
      KNIGHT: 2,
      ROAD_BUILDING: 0,
      YEAR_OF_PLENTY: 0,
      MONOPOLY: 0,
    },
    new_development_cards: {
      KNIGHT: 0,
      ROAD_BUILDING: 0,
      YEAR_OF_PLENTY: 0,
      MONOPOLY: 1,
    },
    victory_point_cards: 1,
  });
  assert.deepEqual(
    JSON.parse(JSON.stringify(
      animation.developmentCardInventoryPresentation(player, true),
    )),
    {
      visible: true,
      total: 4,
      empty: false,
      usable: [{ key: "KNIGHT", label: "騎士", count: 2 }],
      newlyPurchased: [{ key: "MONOPOLY", label: "独占", count: 1 }],
      victoryPoints: 1,
    },
  );
});

test("development inventory renders privately and preserves its toggle", () => {
  resetAnimationState();
  const player = developmentPlayer({
    development_cards: {
      KNIGHT: 1,
      ROAD_BUILDING: 0,
      YEAR_OF_PLENTY: 0,
      MONOPOLY: 0,
    },
  });
  const own = animation.createDevelopmentCardInventory(player, true);
  assert.equal(own.id, "details");
  assert.equal(own.className, "development-inventory");
  assert.equal(own.open, true);
  assert.match(descendantText(own), /使用候補/);
  assert.match(descendantText(own), /騎士 ×1/);
  assert.match(descendantText(own), /あなたにだけ表示/);
  assert.equal(
    animation.createDevelopmentCardInventory(player, false),
    null,
  );

  own.open = false;
  for (const listener of own.listeners.get("toggle") || []) listener();
  const rerendered = animation.createDevelopmentCardInventory(player, true);
  assert.equal(rerendered.open, false);

  animation.resetRoomState(false);
  assert.equal(animation.state.developmentInventoryOpen, null);
});

test("own zero-card inventory remains available with a clear empty state", () => {
  assert.deepEqual(
    JSON.parse(JSON.stringify(
      animation.developmentCardInventoryPresentation(
        developmentPlayer(),
        true,
      ),
    )),
    {
      visible: true,
      total: 0,
      empty: true,
      usable: [],
      newlyPurchased: [],
      victoryPoints: 0,
    },
  );
  const rendered = animation.createDevelopmentCardInventory(
    developmentPlayer(),
    true,
  );
  assert.equal(rendered.open, false);
  assert.match(descendantText(rendered), /所持なし/);
});

test("development contents stay hidden from opponents and malformed viewers", () => {
  const privatePlayer = developmentPlayer({
    development_cards: {
      KNIGHT: 1,
      ROAD_BUILDING: 0,
      YEAR_OF_PLENTY: 0,
      MONOPOLY: 0,
    },
  });
  assert.equal(
    animation.developmentCardInventoryPresentation(
      privatePlayer,
      false,
    ).visible,
    false,
  );
  assert.equal(
    animation.developmentCardInventoryPresentation(
      {
        ...privatePlayer,
        development_cards: null,
        new_development_cards: null,
        victory_point_cards: null,
      },
      true,
    ).visible,
    false,
  );
  assert.equal(
    animation.developmentCardInventoryPresentation(
      {
        ...privatePlayer,
        development_cards: {
          ...privatePlayer.development_cards,
          KNIGHT: -1,
        },
      },
      true,
    ).visible,
    false,
  );
});

test("mixed AI identities stay hidden until result presentation", () => {
  assert.equal(
    animation.publicAIPersonalityLabel("mixed", "expansion"),
    null,
  );
  assert.equal(
    animation.lobbyAIMemberDescription(
      { ai_personality: null },
      "mixed",
    ),
    "AI · 性格は対局後に公開 · サーバー管理",
  );
  assert.equal(
    animation.playerIdentityLabel(
      {
        name: "CPU1",
        marker: "◆",
        is_ai: true,
        ai_personality: null,
      },
      "mixed",
    ),
    "◆ CPU1・AI",
  );
  assert.equal(
    animation.aiCommentaryHeading(
      {
        player_name: "CPU1",
        personality: null,
        title: "建設候補を評価",
      },
      "mixed",
    ),
    "CPU1: 建設候補を評価",
  );
  assert.equal(
    animation.publicAIPersonalityLabel("expansion", "expansion"),
    "拡大重視",
  );

  animation.state.lobby = {
    settings: { ai_personality_mode: "mixed" },
  };
  const legacyMode = animation.personalityModeForView({ ai: {} });
  assert.equal(legacyMode, "mixed");
  assert.equal(
    animation.playerIdentityLabel(
      {
        name: "CPU2",
        marker: "▲",
        is_ai: true,
        ai_personality: "trader",
      },
      legacyMode,
    ),
    "▲ CPU2・AI",
  );
  assert.equal(
    animation.aiCommentaryHeading(
      {
        player_name: "CPU2",
        personality: "trader",
        title: "交易候補を評価",
      },
      legacyMode,
    ),
    "CPU2: 交易候補を評価",
  );
  animation.state.lobby = null;
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

test("rules, trade prompt, two-column editor, and responsive audio controls are present", () => {
  assert.match(indexSource, /id="rules-toggle"/);
  assert.match(indexSource, /id="rules-drawer"[^>]*role="dialog"/);
  assert.match(indexSource, /id="trade-prompt"[^>]*role="dialog"/);
  assert.match(indexSource, /id="audio-volume"[^>]*type="range"/);
  assert.match(cssSource, /\.trade-editor-grid\s*\{[\s\S]*grid-template-columns:\s*repeat\(auto-fit/);
  assert.match(cssSource, /@media \(hover: none\), \(pointer: coarse\)[\s\S]*\.trade-adjust-button/);
  assert.match(appSource, /state\.replayIndex === null[\s\S]*viewer_player_index === ownSeat/);
});

test("development inventory is accessible, private, and responsive", () => {
  assert.match(appSource, /document\.createElement\("details"\)/);
  assert.match(
    appSource,
    /index === ownSeat && index === snapshotViewerSeat/,
  );
  assert.match(
    cssSource,
    /\.development-inventory > summary:focus-visible/,
  );
  assert.match(
    cssSource,
    /\.development-inventory-grid\s*\{[\s\S]*repeat\(auto-fit, minmax\(132px, 1fr\)\)/,
  );
  assert.match(
    cssSource,
    /@media \(max-width: 440px\)[\s\S]*\.development-inventory-grid\s*\{[\s\S]*grid-template-columns:\s*1fr/,
  );
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
