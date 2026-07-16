"use strict";

const PROTOCOL_VERSION = 1;
const POLL_INTERVAL_MS = 650;
const RESOURCE_LABELS = {
  WOOD: "木",
  SHEEP: "羊",
  WHEAT: "麦",
  BRICK: "土",
  ORE: "鉄",
  DESERT: "砂漠",
  UNKNOWN: "未探索",
};
const PIECE_LABELS = { road: "街道", settlement: "開拓地", city: "都市" };
const CARD_LABELS = {
  knight: "騎士",
  road_building: "街道建設",
  year_of_plenty: "収穫",
  monopoly: "独占",
};
const BOARD_RESOURCE_COLORS = {
  WOOD: [62, 129, 75],
  SHEEP: [126, 178, 75],
  WHEAT: [221, 174, 62],
  BRICK: [177, 91, 64],
  ORE: [126, 136, 149],
  DESERT: [214, 178, 111],
  UNKNOWN: [30, 78, 91],
};
const BOARD_TERRAIN_ASSETS = {
  WOOD: "/assets/board/terrain-wood.webp",
  SHEEP: "/assets/board/terrain-sheep.webp",
  WHEAT: "/assets/board/terrain-wheat.webp",
  BRICK: "/assets/board/terrain-brick.webp",
  ORE: "/assets/board/terrain-ore.webp",
  DESERT: "/assets/board/terrain-desert.webp",
  UNKNOWN: "/assets/board/frontier-fog.webp",
};
const TOKEN_PIP_COUNTS = { 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1 };
const DEFAULT_FORECAST_OPTIONS = {
  catalog: "core_v1",
  forecast_lead_turns: 2,
  event_interval_turns: 6,
};
const DEFAULT_FRONTIER_OPTIONS = {
  initial_radius: 1,
  reveal_rule: "road_adjacent_v1",
};
const FORECAST_EVENT_PRESENTATION = {
  wheat_harvest_v1: {
    title: "豊作",
    description: "次の麦生産で、銀行在庫に余裕があれば生産対象者へ麦を1枚追加します。",
    active: "豊作: 次の麦生産に+1",
  },
  sheep_drought_v1: {
    title: "大干ばつ",
    description: "発動から全員が1手番を終えるまで、羊タイルは生産しません。",
    active: "大干ばつ: 羊の生産停止",
  },
};

const state = {
  welcome: null,
  lobby: null,
  snapshot: null,
  liveSnapshot: null,
  matchResult: null,
  resultError: null,
  resultAnnounced: false,
  replayManifest: null,
  replayIndex: null,
  replayPlaying: false,
  replayTimer: null,
  replayRequestPending: false,
  socket: null,
  socketReady: false,
  socketConnecting: false,
  socketRequests: [],
  socketHeartbeat: null,
  socketReconnect: null,
  nextSequence: 0,
  commandPending: false,
  pollPending: false,
  reconnecting: false,
  targetOptions: new Map(),
  pendingBuildAnimations: new Set(),
  pendingRevealAnimations: new Set(),
  pendingDiceAnimation: null,
  pendingAnimationRevision: null,
  currentView: null,
};

const elements = Object.fromEntries(
  [
    "connection-status",
    "connection-label",
    "home-view",
    "lobby-view",
    "game-view",
    "create-form",
    "join-form",
    "random-seed",
    "ai-player-count",
    "ai-personality-mode",
    "lobby-room-code",
    "copy-room-code",
    "lobby-status-text",
    "lobby-phase",
    "member-list",
    "lobby-settings-list",
    "ready-button",
    "start-button",
    "leave-button",
    "lobby-action-hint",
    "game-room-label",
    "game-phase-title",
    "game-instruction",
    "game-leave-button",
    "board-svg",
    "board-shell",
    "board-layer",
    "board-legend",
    "revision-badge",
    "action-list",
    "action-hint",
    "victory-target-label",
    "player-list",
    "latest-event-title",
    "latest-event-detail",
    "forecast-event-card",
    "forecast-event-countdown",
    "forecast-event-title",
    "forecast-event-detail",
    "forecast-active-list",
    "frontier-status-card",
    "frontier-status-count",
    "frontier-status-detail",
    "ai-commentary",
    "ai-commentary-title",
    "ai-commentary-detail",
    "result-dashboard",
    "result-winner",
    "result-summary",
    "result-live-button",
    "result-standings",
    "result-chart",
    "result-chart-legend",
    "replay-position",
    "replay-slider",
    "replay-first",
    "replay-previous",
    "replay-play",
    "replay-next",
    "replay-last",
    "replay-speed",
    "replay-frame-label",
    "result-events",
    "toast",
  ].map((id) => [id, document.getElementById(id)]),
);

function wireMessage(type, payload = {}) {
  return { type, protocol_version: PROTOCOL_VERSION, ...payload };
}

function variantConfigDocument(kind) {
  if (kind === "forecast_events") {
    return {
      version: 1,
      kind,
      options: { ...DEFAULT_FORECAST_OPTIONS },
    };
  }
  if (kind === "frontier") {
    return {
      version: 1,
      kind,
      options: { ...DEFAULT_FRONTIER_OPTIONS },
    };
  }
  return { version: 1, kind: "standard", options: {} };
}

function variantLabel(variant) {
  if (variant?.kind === "forecast_events") return "予告イベント";
  if (variant?.kind === "frontier") return "フロンティア探索";
  return "通常ルール";
}

function frontierPresentation(variantState) {
  if (variantState?.kind !== "frontier") return { visible: false };
  const publicState = variantState.public || {};
  const revealed = Array.isArray(publicState.revealed_tiles)
    ? publicState.revealed_tiles.length
    : 0;
  const discoveries = Number.isInteger(publicState.discovery_count)
    ? publicState.discovery_count
    : 0;
  return {
    visible: true,
    count: `${revealed} / 19 公開`,
    detail: discoveries > 0
      ? `街道から${discoveries}タイルを発見。霧に接する街道で探索を続けられます。`
      : "外周は未探索です。霧に接する街道を建設すると資源・数字・港が公開されます。",
  };
}

function forecastEventPresentation(variantState) {
  if (variantState?.kind !== "forecast_events") return { visible: false };
  const publicState = variantState.public || {};
  const forecast = publicState.forecast || {};
  const event = FORECAST_EVENT_PRESENTATION[forecast.event_id] || {
    title: "未対応イベント",
    description: "表示を更新してください。",
    active: "未対応イベント",
  };
  const completed = Number.isInteger(publicState.completed_turns)
    ? publicState.completed_turns
    : 0;
  const resolveTurn = Number.isInteger(forecast.resolve_turn)
    ? forecast.resolve_turn
    : completed;
  const remaining = Math.max(0, resolveTurn - completed);
  const active = Array.isArray(publicState.active_effects)
    ? publicState.active_effects.map((effect) => {
      const definition = FORECAST_EVENT_PRESENTATION[effect?.event_id];
      return definition?.active || "未対応イベント";
    })
    : [];
  return {
    visible: true,
    title: event.title,
    description: event.description,
    countdown: remaining === 0 ? "発動処理中" : `あと${remaining}手番`,
    active,
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
  });
  const document = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(document.error?.message || `HTTP ${response.status}`);
    error.code = document.error?.code || "http_error";
    error.status = response.status;
    throw error;
  }
  return document;
}

async function startBrowserSession() {
  const document = await api("/api/session", { method: "POST" });
  processEvents(document.events || [], { animateLive: false });
  if (!state.welcome) {
    await reconnectFromStorage();
  }
  connectWebSocket();
  setConnection("online", "ローカルサーバー接続中");
}

async function sendMessage(message) {
  if (state.socketReady) return sendSocketMessage(message);
  const document = await api("/api/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message),
  });
  processEvents(document.events || [], {
    animateLive: message.type !== "reconnect_room",
  });
  return document;
}

function websocketURL() {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/api/socket`;
}

function connectWebSocket() {
  if (state.socketReady || state.socketConnecting || !window.WebSocket) return;
  state.socketConnecting = true;
  window.clearTimeout(state.socketReconnect);
  const socket = new WebSocket(websocketURL());
  state.socket = socket;
  socket.addEventListener("open", () => {
    if (state.socket !== socket) return;
    state.socketConnecting = false;
    state.socketReady = true;
    setConnection("online", "WebSocket接続中");
    window.clearInterval(state.socketHeartbeat);
    state.socketHeartbeat = window.setInterval(sendSocketHeartbeat, 350);
  });
  socket.addEventListener("message", (event) => {
    let document;
    try {
      document = JSON.parse(event.data);
    } catch (_error) {
      socket.close(1002, "invalid server message");
      return;
    }
    processEvents(document.events || [], {
      animateLive: document.kind !== "bootstrap",
    });
    const pending = document.kind === "bootstrap"
      ? null
      : state.socketRequests.shift();
    if (document.error) {
      const error = new Error(document.error.message || "WebSocket操作に失敗しました。");
      error.code = document.error.code || "socket_error";
      if (pending) pending.reject(error);
      else showToast(error.message, true);
    } else if (pending) {
      pending.resolve(document);
    }
  });
  socket.addEventListener("close", () => {
    if (state.socket !== socket) return;
    state.socket = null;
    state.socketReady = false;
    state.socketConnecting = false;
    window.clearInterval(state.socketHeartbeat);
    state.socketHeartbeat = null;
    for (const pending of state.socketRequests.splice(0)) {
      pending.reject(new Error("WebSocket接続が切れました。"));
    }
    setConnection("connecting", "HTTPで再接続中");
    state.socketReconnect = window.setTimeout(connectWebSocket, 1600);
  });
  socket.addEventListener("error", () => {
    // The close handler falls back to HTTP polling and retries the upgrade.
  });
}

function sendSocketMessage(message) {
  if (!state.socketReady || !state.socket) {
    return Promise.reject(new Error("WebSocketは接続されていません。"));
  }
  return new Promise((resolve, reject) => {
    const pending = {
      timer: null,
      resolve(value) {
        window.clearTimeout(pending.timer);
        resolve(value);
      },
      reject(error) {
        window.clearTimeout(pending.timer);
        reject(error);
      },
    };
    pending.timer = window.setTimeout(() => {
      const index = state.socketRequests.indexOf(pending);
      if (index >= 0) state.socketRequests.splice(index, 1);
      pending.reject(new Error("WebSocketの応答がないためHTTPへ切り替えます。"));
      const activeSocket = state.socket;
      if (activeSocket && activeSocket.readyState < WebSocket.CLOSING) {
        activeSocket.close(1011, "response timeout");
      }
    }, 6000);
    state.socketRequests.push(pending);
    try {
      state.socket.send(JSON.stringify(message));
    } catch (error) {
      state.socketRequests.pop();
      pending.reject(error);
    }
  });
}

function sendSocketHeartbeat() {
  if (!state.socketReady || state.socketRequests.length > 0) return;
  sendSocketMessage(
    wireMessage("ping", { nonce: `web-${Date.now()}` }),
  ).catch(() => {});
}

async function pollEvents() {
  if (state.pollPending || state.socketReady) return;
  state.pollPending = true;
  try {
    const document = await api("/api/events");
    processEvents(document.events || []);
    setConnection("online", "ローカルサーバー接続中");
  } catch (error) {
    if (error.status === 401 && !state.reconnecting) {
      state.reconnecting = true;
      try {
        await startBrowserSession();
      } finally {
        state.reconnecting = false;
      }
    } else {
      setConnection("error", "再接続を試しています");
    }
  } finally {
    state.pollPending = false;
  }
}

function processEvents(events, { animateLive = true } = {}) {
  let dirty = false;
  let focusBoardAfterRender = false;
  for (const event of events) {
    if (!event || typeof event !== "object") continue;
    switch (event.type) {
      case "session_welcome":
        state.welcome = event;
        state.nextSequence = Number.isInteger(event.next_sequence)
          ? event.next_sequence
          : 0;
        if (event.reconnect_token) {
          sessionStorage.setItem(
            "catan-reconnect",
            JSON.stringify({
              roomCode: event.room_code,
              token: event.reconnect_token,
            }),
          );
        }
        dirty = true;
        break;
      case "lobby_snapshot":
        if (
          !state.lobby
          || !Number.isInteger(state.lobby.revision)
          || !Number.isInteger(event.lobby?.revision)
          || event.lobby.revision >= state.lobby.revision
        ) {
          state.lobby = event.lobby;
          syncRoleFromLobby();
          dirty = true;
        }
        break;
      case "state_snapshot":
        {
          let changed = false;
          if (!state.liveSnapshot || event.revision >= state.liveSnapshot.revision) {
            const previousLive = state.liveSnapshot;
            changed = !previousLive || event.revision > previousLive.revision;
            if (
              animateLive
              && state.replayIndex === null
              && previousLive
              && event.revision > previousLive.revision
            ) {
              queueLiveBoardAnimations(previousLive, event);
            }
            state.liveSnapshot = event;
          }
          if (
            state.replayIndex === null
            && (!state.snapshot || event.revision >= state.snapshot.revision)
          ) {
            changed = changed || !state.snapshot || event.revision > state.snapshot.revision;
            state.snapshot = event;
          }
          dirty = dirty || changed;
        }
        break;
      case "match_result":
      case "network_match_result":
        state.matchResult = event.result || null;
        state.resultError = null;
        if (event.replay) {
          const count = Number(event.replay.frame_count) || 0;
          state.replayManifest = {
            ...event.replay,
            frames: Array.from({ length: count }, (_, index) =>
              state.replayManifest?.frames?.[index] || null,
            ),
          };
        }
        dirty = true;
        break;
      case "network_result_unavailable":
        state.resultError = event.message || "対局結果を読み込めませんでした。";
        dirty = true;
        break;
      case "replay_manifest":
        state.replayManifest = event.replay || null;
        dirty = true;
        break;
      case "replay_frame":
      case "network_replay_frame": {
        const index = Number.isInteger(event.index) ? event.index : event.controls?.frame_index;
        if (Number.isInteger(index) && event.snapshot) {
          state.replayIndex = index;
          state.snapshot = event.snapshot;
          const frames = replayFrameEntries();
          if (frames.length > index) {
            frames[index] = {
              revision: event.controls?.revision,
              elapsed_ms: event.controls?.elapsed_ms,
              label: event.controls?.label,
            };
          }
          dirty = true;
          focusBoardAfterRender = true;
        }
        break;
      }
      case "game_command_result":
        state.commandPending = false;
        reconcileSequence(event);
        if (!event.accepted) {
          showToast(event.message || "操作が受理されませんでした。", true);
        }
        dirty = true;
        break;
      case "request_error":
        state.commandPending = false;
        showToast(event.message || "操作を処理できませんでした。", true);
        dirty = true;
        break;
      case "room_closed":
        showToast(event.message || "部屋が終了しました。", true);
        resetRoomState(false);
        dirty = true;
        break;
      default:
        break;
    }
  }
  if (dirty) render();
  if (focusBoardAfterRender) focusGameBoard();
}

function reconcileSequence(event) {
  if (!Number.isInteger(event.sequence) || event.sequence < 0) return;
  const doesNotConsume = new Set([
    "sequence_conflict",
    "sequence_expired",
    "sequence_gap",
  ]);
  if (event.accepted || !doesNotConsume.has(event.code)) {
    state.nextSequence = Math.max(state.nextSequence, event.sequence + 1);
  }
}

function queueLiveBoardAnimations(previousSnapshot, nextSnapshot) {
  const builds = detectNewBoardPieces(
    previousSnapshot?.board_manifest,
    nextSnapshot?.board_manifest,
  );
  for (const build of builds) {
    state.pendingBuildAnimations.add(`${build.kind}:${build.targetId}`);
  }
  for (const targetId of detectRevealedTiles(
    previousSnapshot?.board_manifest,
    nextSnapshot?.board_manifest,
  )) {
    state.pendingRevealAnimations.add(targetId);
  }
  const dice = detectNewDiceRoll(previousSnapshot, nextSnapshot);
  if (dice) state.pendingDiceAnimation = dice;
  state.pendingAnimationRevision = nextSnapshot.revision;
  playLiveSnapshotAudio(previousSnapshot, nextSnapshot, builds, dice);
}

function playLiveSnapshotAudio(previousSnapshot, nextSnapshot, builds, dice) {
  const audio = window.CatanAudio;
  if (!audio) return;
  if (dice) audio.playDice(dice.values);

  const finishedNow = previousSnapshot?.state?.phase?.name !== "finished"
    && nextSnapshot?.state?.phase?.name === "finished";
  if (finishedNow) {
    audio.playVictory();
    return;
  }

  if (builds.length) {
    audio.playBuild(builds[builds.length - 1].kind);
  }
  if (tradeActivityTotal(nextSnapshot) > tradeActivityTotal(previousSnapshot)) {
    audio.playTrade();
  }
}

function tradeActivityTotal(snapshot) {
  const players = snapshot?.state?.match_metrics?.players;
  if (!Array.isArray(players)) return 0;
  return players.reduce((total, player) => {
    const domestic = Number(player?.domestic_trades);
    const bank = Number(player?.bank_trades);
    return total
      + (Number.isFinite(domestic) && domestic >= 0 ? domestic : 0)
      + (Number.isFinite(bank) && bank >= 0 ? bank : 0);
  }, 0);
}

function takePendingBoardAnimations(snapshot) {
  const isCurrentLive = state.replayIndex === null
    && state.liveSnapshot
    && snapshot?.revision === state.liveSnapshot.revision
    && snapshot?.revision === state.pendingAnimationRevision;
  if (!isCurrentLive) {
    if (state.pendingAnimationRevision !== null) clearPendingBoardAnimations();
    return { buildKeys: new Set(), revealIds: new Set(), dice: null };
  }
  const plan = {
    buildKeys: new Set(state.pendingBuildAnimations),
    revealIds: new Set(state.pendingRevealAnimations),
    dice: state.pendingDiceAnimation,
  };
  clearPendingBoardAnimations();
  return plan;
}

function clearPendingBoardAnimations() {
  state.pendingBuildAnimations.clear();
  state.pendingRevealAnimations.clear();
  state.pendingDiceAnimation = null;
  state.pendingAnimationRevision = null;
}

function detectNewBoardPieces(previousManifest, nextManifest) {
  if (!previousManifest || !nextManifest) return [];
  if (
    previousManifest.mode !== nextManifest.mode
    || previousManifest.seed !== nextManifest.seed
    || previousManifest.custom_map_fingerprint !== nextManifest.custom_map_fingerprint
  ) {
    return [];
  }
  const previousRoads = new Set(
    (previousManifest.edges || [])
      .filter((edge) => edge.road)
      .map((edge) => edge.id),
  );
  const previousBuildings = new Map(
    (previousManifest.nodes || []).map((node) => [node.id, node.building?.type || null]),
  );
  const builds = [];
  for (const edge of nextManifest.edges || []) {
    if (edge.road && !previousRoads.has(edge.id)) {
      builds.push({ kind: "road", targetId: edge.id });
    }
  }
  for (const node of nextManifest.nodes || []) {
    const buildingType = node.building?.type || null;
    const previousType = previousBuildings.get(node.id) || null;
    if (buildingType === "settlement" && previousType === null) {
      builds.push({ kind: "settlement", targetId: node.id });
    } else if (buildingType === "city" && previousType !== "city") {
      builds.push({ kind: "city", targetId: node.id });
    }
  }
  return builds;
}

function detectRevealedTiles(previousManifest, nextManifest) {
  if (!previousManifest || !nextManifest) return [];
  if (
    previousManifest.mode !== nextManifest.mode
    || previousManifest.seed !== nextManifest.seed
    || previousManifest.custom_map_fingerprint !== nextManifest.custom_map_fingerprint
  ) {
    return [];
  }
  const previousTiles = new Map(
    (previousManifest.tiles || []).map((tile) => [
      tile.id,
      tile.revealed !== false && tile.resource !== "UNKNOWN",
    ]),
  );
  return (nextManifest.tiles || [])
    .filter((tile) => (
      previousTiles.get(tile.id) === false
      && tile.revealed !== false
      && tile.resource !== "UNKNOWN"
    ))
    .map((tile) => tile.id);
}

function detectNewDiceRoll(previousSnapshot, nextSnapshot) {
  if (!previousSnapshot || !nextSnapshot) return null;
  if (!Number.isInteger(nextSnapshot.revision) || nextSnapshot.revision <= previousSnapshot.revision) {
    return null;
  }
  const initialTotal = newestInitialDiceTotal(previousSnapshot, nextSnapshot);
  const previousPair = publishedDicePair(previousSnapshot);
  const nextPair = publishedDicePair(nextSnapshot);
  const previousPhase = previousSnapshot.state?.phase || {};
  const nextPhase = nextSnapshot.state?.phase || {};
  const rolledNow = !previousPhase.dice_rolled && Boolean(nextPhase.dice_rolled);
  const pairChanged = nextPair !== null && !sameDicePair(previousPair, nextPair);
  const actorAdvanced = Boolean(nextPhase.dice_rolled)
    && activeSnapshotPlayer(previousSnapshot) !== activeSnapshotPlayer(nextSnapshot);
  if (initialTotal === null && !rolledNow && !pairChanged && !actorAdvanced) return null;

  const publishedTotal = nextPair ? nextPair[0] + nextPair[1] : null;
  const total = initialTotal ?? publishedTotal ?? latestDiceTotal(nextSnapshot);
  if (!Number.isInteger(total) || total < 2 || total > 12) return null;
  const values = nextPair && publishedTotal === total
    ? nextPair
    : deterministicDicePair(total, nextSnapshot.revision);
  return { values, total, revision: nextSnapshot.revision };
}

function publishedDicePair(snapshot) {
  const candidate = snapshot?.state?.phase?.last_dice_pair;
  if (!Array.isArray(candidate) || candidate.length !== 2) return null;
  const values = candidate.map(Number);
  return values.every((value) => Number.isInteger(value) && value >= 1 && value <= 6)
    ? values
    : null;
}

function sameDicePair(first, second) {
  if (first === null || second === null) return first === second;
  return first[0] === second[0] && first[1] === second[1];
}

function newestInitialDiceTotal(previousSnapshot, nextSnapshot) {
  const previous = previousSnapshot.state?.initial?.dice_histories;
  const next = nextSnapshot.state?.initial?.dice_histories;
  if (!previous || !next || typeof previous !== "object" || typeof next !== "object") {
    return null;
  }
  for (const [playerName, values] of Object.entries(next)) {
    if (!Array.isArray(values)) continue;
    const priorValues = Array.isArray(previous[playerName]) ? previous[playerName] : [];
    if (values.length > priorValues.length) {
      const total = Number(values[values.length - 1]);
      if (Number.isInteger(total) && total >= 2 && total <= 12) return total;
    }
  }
  return null;
}

function activeSnapshotPlayer(snapshot) {
  const phase = snapshot?.state?.phase || {};
  const order = Array.isArray(phase.turn_order) ? phase.turn_order : [];
  return order[phase.current_player_index] ?? phase.current_player_index ?? null;
}

function latestDiceTotal(snapshot) {
  const history = snapshot?.state?.history || {};
  const candidates = [
    history.latest_event?.title,
    history.latest_event?.detail,
    ...(Array.isArray(history.log_messages) ? [...history.log_messages].reverse() : []),
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== "string") continue;
    const match = candidate.match(/(?:ダイス(?:の目)?|出目)\s*[:：]?\s*(1[0-2]|[2-9])(?:\D|$)/);
    if (match) return Number(match[1]);
  }
  return null;
}

function deterministicDicePair(total, revision = 0) {
  const pairs = [];
  for (let first = 1; first <= 6; first += 1) {
    const second = total - first;
    if (second >= 1 && second <= 6) pairs.push([first, second]);
  }
  if (!pairs.length) return [1, 1];
  const index = Math.abs(Number(revision) || 0) % pairs.length;
  return pairs[index];
}

async function reconnectFromStorage() {
  const raw = sessionStorage.getItem("catan-reconnect");
  if (!raw) return;
  try {
    const saved = JSON.parse(raw);
    if (!saved.roomCode || !saved.token) return;
    const document = await sendMessage(
      wireMessage("reconnect_room", {
        room_code: saved.roomCode,
        reconnect_token: saved.token,
      }),
    );
    if (!(document.events || []).some((event) => event.type === "session_welcome")) {
      sessionStorage.removeItem("catan-reconnect");
    }
  } catch (_error) {
    sessionStorage.removeItem("catan-reconnect");
  }
}

function syncRoleFromLobby() {
  if (!state.welcome || !state.lobby || !Number.isInteger(state.welcome.seat_index)) {
    return;
  }
  const seat = state.welcome.seat_index + 1;
  const ownMember = state.lobby.members?.find((member) => member.seat === seat);
  if (ownMember && ["host", "player"].includes(ownMember.role)) {
    state.welcome.role = ownMember.role;
  }
}

function setConnection(value, label) {
  if (elements["connection-status"].dataset.state !== value) {
    elements["connection-status"].dataset.state = value;
  }
  if (elements["connection-label"].textContent !== label) {
    elements["connection-label"].textContent = label;
  }
}

function showToast(message, isError = false) {
  const toast = elements.toast;
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 3400);
}

function resetRoomState(renderNow = true) {
  state.welcome = null;
  state.lobby = null;
  state.snapshot = null;
  state.liveSnapshot = null;
  state.matchResult = null;
  state.resultError = null;
  state.resultAnnounced = false;
  state.replayManifest = null;
  state.replayIndex = null;
  stopReplay();
  state.nextSequence = 0;
  state.commandPending = false;
  state.targetOptions.clear();
  clearPendingBoardAnimations();
  sessionStorage.removeItem("catan-reconnect");
  if (renderNow) render();
}

function render() {
  const hasGame = Boolean(state.snapshot && state.lobby?.phase === "started");
  const hasLobby = Boolean(state.welcome && state.lobby && !hasGame);
  const nextView = hasGame ? "game" : hasLobby ? "lobby" : "home";
  elements["home-view"].hidden = hasLobby || hasGame;
  elements["lobby-view"].hidden = !hasLobby;
  elements["game-view"].hidden = !hasGame;
  if (hasLobby) renderLobby();
  if (hasGame) renderGame();
  if (state.currentView !== nextView) {
    state.currentView = nextView;
    window.requestAnimationFrame(() => window.scrollTo(0, 0));
  }
}

function renderLobby() {
  const lobby = state.lobby;
  elements["lobby-room-code"].textContent = lobby.room_code || "------";
  elements["lobby-phase"].textContent = lobby.phase === "started" ? "対局中" : "待機中";
  elements["lobby-status-text"].textContent = `${lobby.player_members}/${lobby.settings.player_count}席 · 観戦${lobby.spectators}人`;
  renderMembers(lobby);
  renderLobbySettings(lobby.settings);

  const isPlayer = Number.isInteger(state.welcome?.seat_index);
  const isHost = state.welcome?.role === "host";
  const ownSeat = isPlayer ? state.welcome.seat_index + 1 : null;
  const ownMember = lobby.members.find((member) => member.seat === ownSeat);
  elements["ready-button"].hidden = !isPlayer;
  elements["ready-button"].textContent = ownMember?.ready ? "準備を取り消す" : "準備OK";
  elements["ready-button"].dataset.ready = ownMember?.ready ? "true" : "false";
  elements["start-button"].hidden = !isHost;
  elements["start-button"].disabled = !lobby.can_start;
  elements["lobby-action-hint"].textContent = isHost
    ? lobby.can_start
      ? "全員の準備が整いました。対局を開始できます。"
      : "全席が埋まり、全員が準備すると開始できます。"
    : isPlayer
      ? "準備OKにして、ホストの開始を待ちます。"
      : "観戦者として公開情報だけを受け取ります。";
}

function renderMembers(lobby) {
  const list = elements["member-list"];
  list.replaceChildren();
  const bySeat = new Map(
    lobby.members.filter((member) => member.seat !== null).map((member) => [member.seat, member]),
  );
  for (let seat = 1; seat <= lobby.settings.player_count; seat += 1) {
    const member = bySeat.get(seat);
    const row = document.createElement("li");
    row.className = `member-row${member ? "" : " empty-seat"}`;
    row.append(textElement("span", `SEAT ${seat}`, "member-seat"));
    const name = document.createElement("div");
    name.className = "member-name";
    name.append(
      textElement("strong", member?.display_name || "空席"),
      textElement(
        "small",
        member
          ? member.is_ai
            ? `${aiPersonalityLabel(member.ai_personality)}AI · サーバー管理`
            : `${roleLabel(member.role)} · ${member.connected ? "接続中" : "再接続待ち"}`
          : "参加者を待っています",
      ),
    );
    row.append(name);
    row.append(
      textElement(
        "span",
        member?.is_ai ? "AI READY" : member?.ready ? "READY" : member ? "WAIT" : "OPEN",
        `ready-state${member?.ready ? " ready" : ""}`,
      ),
    );
    list.append(row);
  }
  const spectators = lobby.members.filter((member) => member.seat === null);
  for (const member of spectators) {
    const row = document.createElement("li");
    row.className = "member-row";
    row.append(textElement("span", "VIEW", "member-seat"));
    const name = document.createElement("div");
    name.className = "member-name";
    name.append(
      textElement("strong", member.display_name),
      textElement("small", member.connected ? "観戦中" : "再接続待ち"),
    );
    row.append(name, textElement("span", "SPECTATOR", "ready-state"));
    list.append(row);
  }
}

function renderLobbySettings(settings) {
  const list = elements["lobby-settings-list"];
  list.replaceChildren();
  const rows = [
    ["プレイヤー", `${settings.player_count}人`],
    ["AI", settings.ai_player_count ? `${settings.ai_player_count}人 · ${aiPersonalityLabel(settings.ai_personality_mode)}` : "なし"],
    ["勝利条件", `${settings.victory_target} VP`],
    ["盤面", boardModeLabel(settings.board_mode)],
    ["モード", variantLabel(settings.variant)],
    ["Seed", String(settings.board_seed)],
    ["追加ルール", houseRulesLabel(settings.house_rules)],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(textElement("dt", label), textElement("dd", value));
    list.append(row);
  }
}

function renderGame() {
  const snapshot = state.snapshot;
  const gameState = snapshot.state;
  const phase = gameState.phase || {};
  const activeSeat = activePlayerIndex(gameState);
  const ownSeat = state.welcome?.seat_index;
  const title = phaseTitle(gameState, activeSeat);
  elements["game-room-label"].textContent = `ROOM ${state.welcome?.room_code || "------"} · ${roleLabel(state.welcome?.role)}`;
  elements["game-phase-title"].textContent = title.title;
  elements["game-instruction"].textContent = title.detail;
  elements["revision-badge"].textContent = `rev. ${snapshot.revision}`;
  elements["victory-target-label"].textContent = `${gameState.rules?.victory_point_target || 10} VP`;

  const options = Array.isArray(snapshot.command_options) ? snapshot.command_options : [];
  state.targetOptions = new Map(
    options
      .filter((option) => typeof option?.args?.target === "string")
      .map((option) => [option.args.target, option]),
  );
  const animationPlan = takePendingBoardAnimations(snapshot);
  renderBoard(snapshot.board_manifest, gameState.players || [], animationPlan);
  renderActions(options);
  renderPlayers(gameState, activeSeat, ownSeat);
  const latest = gameState.history?.latest_event || {};
  elements["latest-event-title"].textContent = latest.title || "進行中";
  elements["latest-event-detail"].textContent = latest.detail || "次の操作を待っています。";
  renderForecastEvent(gameState.variant_state);
  renderFrontierStatus(gameState.variant_state);
  renderAICommentary(gameState.ai?.status);
  const finished = state.liveSnapshot?.state?.phase?.name === "finished";
  elements["result-dashboard"].hidden = !finished;
  if (finished) {
    renderMatchResult();
    if (!state.resultAnnounced) {
      state.resultAnnounced = true;
      window.requestAnimationFrame(() => focusAndScroll(elements["result-dashboard"], "start"));
    }
  }
}

function renderForecastEvent(variantState) {
  const presentation = forecastEventPresentation(variantState);
  const card = elements["forecast-event-card"];
  card.hidden = !presentation.visible;
  if (!presentation.visible) {
    elements["forecast-active-list"].replaceChildren();
    return;
  }
  elements["forecast-event-countdown"].textContent = presentation.countdown;
  elements["forecast-event-title"].textContent = presentation.title;
  elements["forecast-event-detail"].textContent = presentation.description;
  const activeList = elements["forecast-active-list"];
  activeList.replaceChildren();
  const activeLabels = presentation.active.length
    ? presentation.active
    : ["現在発動中の効果なし"];
  for (const label of activeLabels) {
    activeList.append(textElement("span", label, "forecast-active-chip"));
  }
}

function renderFrontierStatus(variantState) {
  const presentation = frontierPresentation(variantState);
  const card = elements["frontier-status-card"];
  card.hidden = !presentation.visible;
  if (!presentation.visible) return;
  elements["frontier-status-count"].textContent = presentation.count;
  elements["frontier-status-detail"].textContent = presentation.detail;
}

function renderAICommentary(status) {
  const visible = Boolean(status?.player_name && status?.title);
  elements["ai-commentary"].hidden = !visible;
  if (!visible) return;
  elements["ai-commentary-title"].textContent = `${status.player_name}（${aiPersonalityLabel(status.personality)}）: ${status.title}`;
  elements["ai-commentary-detail"].textContent = status.detail || "次の一手を評価しています。";
}

function renderActions(options) {
  const list = elements["action-list"];
  list.replaceChildren();
  const direct = options.filter((option) => !option?.args?.target);
  for (const option of direct) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "action-button";
    if (["roll_dice", "end_turn", "trade_accept", "trade_submit"].includes(option.command)) {
      button.classList.add("primary-action");
    }
    if (["cancel", "trade_reject"].includes(option.command)) {
      button.classList.add("danger-action");
    }
    button.textContent = commandLabel(option);
    button.disabled = state.commandPending;
    button.addEventListener("click", () => sendGameCommand(option));
    list.append(button);
  }
  const targetCount = state.targetOptions.size;
  elements["action-hint"].textContent = targetCount
    ? `盤面上で光っている候補を選べます（${targetCount}か所）。`
    : direct.length
      ? "行動を選ぶと権威サーバーが合法性を再確認します。"
      : state.welcome?.role === "spectator"
        ? "観戦中です。操作はプレイヤーだけに表示されます。"
        : "ほかのプレイヤーの操作を待っています。";
}

async function sendGameCommand(option) {
  if (state.commandPending || !state.snapshot) return;
  state.commandPending = true;
  renderActions(state.snapshot.command_options || []);
  const sequence = state.nextSequence;
  state.nextSequence += 1;
  try {
    await sendMessage(
      wireMessage("game_command", {
        sequence,
        expected_revision: state.snapshot.revision,
        command: option.command,
        args: option.args || {},
      }),
    );
  } catch (error) {
    state.commandPending = false;
    state.nextSequence = sequence;
    showToast(error.message, true);
    render();
  }
}

function renderBoard(
  manifest,
  players,
  animationPlan = { buildKeys: new Set(), revealIds: new Set(), dice: null },
) {
  if (!manifest) return;
  const layer = elements["board-layer"];
  layer.replaceChildren();
  const nodeById = new Map(manifest.nodes.map((node) => [node.id, node]));
  const bounds = manifest.coordinate_space.bounds;
  const boardCenter = {
    x: (bounds.min_x + bounds.max_x) / 2,
    y: (bounds.min_y + bounds.max_y) / 2,
  };
  const harborLayouts = layoutBoardHarbors(manifest, nodeById, boardCenter);
  const viewBox = boardVisualBounds(bounds, harborLayouts);
  elements["board-svg"].setAttribute(
    "viewBox",
    `${viewBox.x} ${viewBox.y} ${viewBox.width} ${viewBox.height}`,
  );

  const definitions = svg("defs");
  const tileLayer = svg("g");
  const harborLayer = svg("g");
  const edgeLayer = svg("g");
  const targetLayer = svg("g");
  const roadLayer = svg("g");
  const buildingLayer = svg("g");
  const robberLayer = svg("g");
  const diceLayer = svg("g");
  layer.append(
    definitions,
    tileLayer,
    harborLayer,
    edgeLayer,
    targetLayer,
    roadLayer,
    buildingLayer,
    robberLayer,
    diceLayer,
  );

  manifest.tiles.forEach((tile, index) => {
    const points = boardTilePoints(tile, nodeById);
    if (points.length < 3) return;
    drawBoardTile(
      definitions,
      tileLayer,
      tile,
      points,
      index,
      animationPlan.revealIds?.has(tile.id) || false,
    );
    if (state.targetOptions.has(tile.id)) {
      drawTileTarget(targetLayer, tile, points);
    }
    if (tile.robber) drawBoardRobber(robberLayer, tile.center);
  });

  for (const harborLayout of harborLayouts) {
    drawBoardHarbor(harborLayer, harborLayout);
  }

  for (const edge of manifest.edges) {
    const nodes = edge.node_ids.map((id) => nodeById.get(id)?.position).filter(Boolean);
    if (nodes.length !== 2) continue;
    edgeLayer.append(svg("line", { x1: nodes[0].x, y1: nodes[0].y, x2: nodes[1].x, y2: nodes[1].y, class: "edge" }));
    if (edge.road) {
      drawRoadPiece(
        roadLayer,
        nodes[0],
        nodes[1],
        players,
        edge.road.owner_player_index,
        edge.id,
        animationPlan,
      );
    }
    if (state.targetOptions.has(edge.id)) {
      drawEdgeTarget(targetLayer, edge.id, nodes[0], nodes[1]);
    }
  }

  for (const node of manifest.nodes) {
    if (state.targetOptions.has(node.id)) {
      drawNodeTarget(targetLayer, node);
    }
    if (node.building) {
      drawBuildingPiece(
        buildingLayer,
        node.position,
        players,
        node.building.owner_player_index,
        node.building.type,
        node.id,
        animationPlan,
      );
    }
  }
  if (animationPlan.dice) {
    drawDiceRollEffect(diceLayer, animationPlan.dice, boardCenter);
  }
  renderLegend(players);
}

function boardTilePoints(tile, nodeById) {
  return tile.corner_node_ids
    .map((id) => nodeById.get(id)?.position)
    .filter(Boolean);
}

function boardPointList(points) {
  return points.map((point) => `${point.x},${point.y}`).join(" ");
}

function appendSvgTitle(element, text) {
  const title = svg("title");
  title.textContent = text;
  element.append(title);
}

function drawBoardTile(definitions, tileLayer, tile, points, index, animateReveal = false) {
  const pointList = boardPointList(points);
  const fallbackColor = boardRgb(BOARD_RESOURCE_COLORS[tile.resource] || [130, 145, 125]);
  const fallback = svg("polygon", {
    points: pointList,
    class: `tile ${tile.resource}${animateReveal ? " frontier-reveal-enter" : ""}`,
    fill: fallbackColor,
  });
  appendSvgTitle(
    fallback,
    `${RESOURCE_LABELS[tile.resource] || tile.resource}${tile.number === null ? "" : ` ${tile.number}`}`,
  );
  tileLayer.append(fallback);

  const clipId = `board-tile-clip-${index}`;
  const clip = svg("clipPath", { id: clipId });
  clip.append(svg("polygon", { points: pointList }));
  definitions.append(clip);

  const minX = Math.min(...points.map((point) => point.x));
  const maxX = Math.max(...points.map((point) => point.x));
  const minY = Math.min(...points.map((point) => point.y));
  const maxY = Math.max(...points.map((point) => point.y));
  const asset = BOARD_TERRAIN_ASSETS[tile.resource];
  if (asset) {
    tileLayer.append(
      svg("image", {
        href: asset,
        x: minX,
        y: minY,
        width: maxX - minX,
        height: maxY - minY,
        preserveAspectRatio: "xMidYMid slice",
        "clip-path": `url(#${clipId})`,
        "pointer-events": "none",
        class: animateReveal ? "frontier-reveal-enter" : "",
      }),
    );
  }
  tileLayer.append(
    svg("polygon", {
      points: pointList,
      fill: "none",
      stroke: "#222322",
      "stroke-width": 3,
      "stroke-linejoin": "round",
      "pointer-events": "none",
    }),
    svg("polygon", {
      points: pointList,
      fill: "none",
      stroke: "#f0dda4",
      "stroke-width": 0.9,
      "stroke-opacity": 0.76,
      "stroke-linejoin": "round",
      "pointer-events": "none",
    }),
  );
  if (tile.revealed === false || tile.resource === "UNKNOWN") {
    const unknownGroup = svg("g", { "pointer-events": "none", class: "frontier-unknown-mark" });
    unknownGroup.append(
      svg("circle", {
        cx: tile.center.x,
        cy: tile.center.y,
        r: 20,
        fill: "rgba(8, 31, 43, 0.68)",
        stroke: "#9ed8d1",
        "stroke-width": 2,
      }),
      svg("text", {
        x: tile.center.x,
        y: tile.center.y + 9,
        "text-anchor": "middle",
        fill: "#e6f3e6",
        "font-size": 28,
        "font-weight": 900,
      }),
    );
    unknownGroup.lastChild.textContent = "?";
    tileLayer.append(unknownGroup);
    return;
  }
  if (tile.number !== null) drawNumberToken(tileLayer, tile.center, tile.number);
}

function drawNumberToken(layer, center, number) {
  const hot = number === 6 || number === 8;
  const textColor = hot ? "#b42a2a" : "#1e1e1a";
  const ringColor = hot ? "#a63026" : "#3f392d";
  const group = svg("g", { "pointer-events": "none" });
  group.append(
    svg("circle", { cx: center.x + 2, cy: center.y + 3, r: 25, fill: "#23221d" }),
    svg("circle", { cx: center.x, cy: center.y, r: 24, fill: "#f9f5de" }),
    svg("circle", {
      cx: center.x,
      cy: center.y,
      r: 22.5,
      fill: "none",
      stroke: ringColor,
      "stroke-width": hot ? 3.5 : 3,
    }),
    svg("path", {
      d: boardArcPath(center, 19, 205, 330),
      fill: "none",
      stroke: "#fffff8",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-opacity": 0.9,
    }),
  );
  const label = svg("text", {
    x: center.x,
    y: center.y - 7,
    fill: textColor,
    "font-size": 27,
    "font-weight": 950,
    "text-anchor": "middle",
    "dominant-baseline": "middle",
  });
  label.textContent = String(number);
  group.append(label);

  const pipCount = TOKEN_PIP_COUNTS[number] || 0;
  const spacing = 7;
  const startX = center.x - ((pipCount - 1) * spacing) / 2;
  for (let index = 0; index < pipCount; index += 1) {
    group.append(
      svg("circle", {
        cx: startX + index * spacing,
        cy: center.y + 13,
        r: 2.7,
        fill: textColor,
      }),
    );
  }
  layer.append(group);
}

function boardArcPath(center, radius, startDegrees, endDegrees) {
  const point = (degrees) => {
    const radians = degrees * Math.PI / 180;
    return {
      x: center.x + Math.cos(radians) * radius,
      y: center.y + Math.sin(radians) * radius,
    };
  };
  const start = point(startDegrees);
  const end = point(endDegrees);
  const largeArc = Math.abs(endDegrees - startDegrees) > 180 ? 1 : 0;
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} 1 ${end.x} ${end.y}`;
}

function drawBoardRobber(layer, center) {
  const group = svg("g", { "pointer-events": "none" });
  appendSvgTitle(group, "盗賊");
  const bodyPoints = [
    { x: center.x - 11, y: center.y - 3 },
    { x: center.x + 11, y: center.y - 3 },
    { x: center.x + 15, y: center.y + 17 },
    { x: center.x - 15, y: center.y + 17 },
  ];
  group.append(
    svg("ellipse", {
      cx: center.x,
      cy: center.y + 20,
      rx: 19,
      ry: 6,
      fill: "#231d17",
      "fill-opacity": 0.9,
    }),
    svg("polygon", {
      points: boardPointList(bodyPoints),
      fill: "#2b2a28",
      stroke: "#171412",
      "stroke-width": 5,
      "stroke-linejoin": "round",
    }),
    svg("polygon", {
      points: boardPointList(bodyPoints),
      fill: "#2b2a28",
      stroke: "#edcf90",
      "stroke-width": 2,
      "stroke-linejoin": "round",
    }),
    svg("circle", { cx: center.x, cy: center.y - 10, r: 12, fill: "#171412" }),
    svg("circle", {
      cx: center.x,
      cy: center.y - 10,
      r: 10,
      fill: "#2b2a28",
      stroke: "#edcf90",
      "stroke-width": 2,
    }),
    svg("circle", { cx: center.x - 3, cy: center.y - 13, r: 3, fill: "#696864" }),
    svg("line", {
      x1: center.x - 7,
      y1: center.y + 1,
      x2: center.x - 9,
      y2: center.y + 12,
      stroke: "#5e5c58",
      "stroke-width": 2,
      "stroke-linecap": "round",
    }),
  );
  layer.append(group);
}

function boardRgb(color) {
  return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
}

function boardPlayerRgb(players, index) {
  const color = players?.[index]?.color;
  if (Array.isArray(color) && color.length >= 3) {
    return color.slice(0, 3).map((channel) => Math.max(0, Math.min(255, Number(channel) || 0)));
  }
  return [230, 237, 241];
}

function mixBoardColor(color, target, amount) {
  const ratio = Math.max(0, Math.min(1, amount));
  return color.map((channel, index) => Math.round(channel + (target[index] - channel) * ratio));
}

function layoutBoardHarbors(manifest, nodeById, boardCenter) {
  const layouts = [];
  const occupied = [];
  const buildingObstacles = manifest.nodes
    .filter((node) => node.building)
    .map((node) => ({
      x: node.position.x - 24,
      y: node.position.y - 23,
      width: 48,
      height: 46,
    }));
  const roadSegments = manifest.edges
    .filter((edge) => edge.perimeter && edge.road)
    .map((edge) => edge.node_ids.map((id) => nodeById.get(id)?.position).filter(Boolean))
    .filter((points) => points.length === 2);

  manifest.harbors.forEach((harbor, harborIndex) => {
    const nodes = harbor.node_ids.map((id) => nodeById.get(id)?.position).filter(Boolean);
    if (nodes.length !== 2) return;
    const geometry = harborEdgeGeometry(nodes[0], nodes[1], boardCenter);
    const labelWidth = Math.max(58, Math.min(88, Array.from(harbor.label || "3:1").length * 13 + 25));
    const labelHeight = 32;
    const tangentOffsets = [0, -44, 44, -72, 72, -100, 100];
    const outwardDistances = [78, 94, 110, 128, 146];
    let rect = null;

    for (const distance of outwardDistances) {
      for (const tangentOffset of tangentOffsets) {
        const center = {
          x: geometry.midpoint.x + geometry.outward.x * distance + geometry.axis.x * tangentOffset,
          y: geometry.midpoint.y + geometry.outward.y * distance + geometry.axis.y * tangentOffset,
        };
        const candidate = {
          x: center.x - labelWidth / 2,
          y: center.y - labelHeight / 2,
          width: labelWidth,
          height: labelHeight,
        };
        const visualCandidate = boardInflateRect(candidate, 7);
        const overlapsBadge = occupied.some((previous) => boardRectsOverlap(visualCandidate, previous));
        const overlapsBuilding = buildingObstacles.some((building) => boardRectsOverlap(visualCandidate, building));
        const overlapsRoad = roadSegments.some(([start, end]) => (
          boardSegmentIntersectsRect(start, end, boardInflateRect(candidate, 12))
        ));
        const overlapsTile = manifest.tiles.some((tile) => (
          boardRectOverlapsCircle(visualCandidate, tile.center, 54)
        ));
        if (!overlapsBadge && !overlapsBuilding && !overlapsRoad && !overlapsTile) {
          rect = candidate;
          break;
        }
      }
      if (rect) break;
    }

    if (!rect) {
      const distance = 154 + harborIndex * 3;
      const tangentOffset = (harborIndex % 2 ? -1 : 1) * 24;
      const center = {
        x: geometry.midpoint.x + geometry.outward.x * distance + geometry.axis.x * tangentOffset,
        y: geometry.midpoint.y + geometry.outward.y * distance + geometry.axis.y * tangentOffset,
      };
      rect = {
        x: center.x - labelWidth / 2,
        y: center.y - labelHeight / 2,
        width: labelWidth,
        height: labelHeight,
      };
    }

    const dock = harborDockGeometry(geometry);
    const badgeCenter = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
    const connectorLead = {
      x: dock.connectorStart.x + geometry.outward.x * 18,
      y: dock.connectorStart.y + geometry.outward.y * 18,
    };
    const connectorEnd = boardRectBoundaryPoint(rect, connectorLead);
    const visualBounds = boardBoundsForPoints(
      [
        dock.innerLeft,
        dock.innerRight,
        dock.outerLeft,
        dock.outerRight,
        dock.connectorStart,
        connectorLead,
        connectorEnd,
        badgeCenter,
      ],
      rect,
      8,
    );
    const layout = {
      harbor,
      geometry,
      dock,
      rect,
      connectorLead,
      connectorEnd,
      visualBounds,
    };
    layouts.push(layout);
    occupied.push(boardInflateRect(rect, 8));
  });
  return layouts;
}

function harborEdgeGeometry(start, end, boardCenter) {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const length = Math.max(1, Math.hypot(dx, dy));
  const axis = { x: dx / length, y: dy / length };
  const midpoint = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  let outward = { x: -axis.y, y: axis.x };
  const radial = { x: midpoint.x - boardCenter.x, y: midpoint.y - boardCenter.y };
  if (outward.x * radial.x + outward.y * radial.y < 0) {
    outward = { x: -outward.x, y: -outward.y };
  }
  return { start, end, midpoint, axis, outward, length };
}

function harborDockGeometry(geometry) {
  const halfSpan = Math.min(12, geometry.length * 0.24);
  const shoreGap = 7;
  const pierLength = 13;
  const local = (along, away) => ({
    x: geometry.midpoint.x + geometry.axis.x * along + geometry.outward.x * away,
    y: geometry.midpoint.y + geometry.axis.y * along + geometry.outward.y * away,
  });
  return {
    innerLeft: local(-halfSpan, shoreGap),
    innerRight: local(halfSpan, shoreGap),
    outerLeft: local(-halfSpan, shoreGap + pierLength),
    outerRight: local(halfSpan, shoreGap + pierLength),
    connectorStart: local(0, shoreGap + pierLength),
  };
}

function boardVisualBounds(bounds, harborLayouts) {
  let minX = bounds.min_x - 24;
  let minY = bounds.min_y - 24;
  let maxX = bounds.max_x + 24;
  let maxY = bounds.max_y + 24;
  for (const layout of harborLayouts) {
    minX = Math.min(minX, layout.visualBounds.x);
    minY = Math.min(minY, layout.visualBounds.y);
    maxX = Math.max(maxX, layout.visualBounds.x + layout.visualBounds.width);
    maxY = Math.max(maxY, layout.visualBounds.y + layout.visualBounds.height);
  }
  const margin = 28;
  const x = Math.floor(minX - margin);
  const y = Math.floor(minY - margin);
  return {
    x,
    y,
    width: Math.ceil(maxX + margin - x),
    height: Math.ceil(maxY + margin - y),
  };
}

function boardInflateRect(rect, amount) {
  return {
    x: rect.x - amount,
    y: rect.y - amount,
    width: rect.width + amount * 2,
    height: rect.height + amount * 2,
  };
}

function boardRectsOverlap(first, second) {
  return first.x < second.x + second.width
    && first.x + first.width > second.x
    && first.y < second.y + second.height
    && first.y + first.height > second.y;
}

function boardRectOverlapsCircle(rect, center, radius) {
  const nearestX = Math.max(rect.x, Math.min(center.x, rect.x + rect.width));
  const nearestY = Math.max(rect.y, Math.min(center.y, rect.y + rect.height));
  return Math.hypot(center.x - nearestX, center.y - nearestY) < radius;
}

function boardSegmentIntersectsRect(start, end, rect) {
  const inside = (point) => point.x >= rect.x
    && point.x <= rect.x + rect.width
    && point.y >= rect.y
    && point.y <= rect.y + rect.height;
  if (inside(start) || inside(end)) return true;
  const corners = [
    { x: rect.x, y: rect.y },
    { x: rect.x + rect.width, y: rect.y },
    { x: rect.x + rect.width, y: rect.y + rect.height },
    { x: rect.x, y: rect.y + rect.height },
  ];
  return corners.some((corner, index) => (
    boardSegmentsIntersect(start, end, corner, corners[(index + 1) % corners.length])
  ));
}

function boardSegmentsIntersect(firstStart, firstEnd, secondStart, secondEnd) {
  const orientation = (a, b, c) => (
    (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
  );
  const onSegment = (a, b, point) => point.x >= Math.min(a.x, b.x) - 0.001
    && point.x <= Math.max(a.x, b.x) + 0.001
    && point.y >= Math.min(a.y, b.y) - 0.001
    && point.y <= Math.max(a.y, b.y) + 0.001;
  const firstA = orientation(firstStart, firstEnd, secondStart);
  const firstB = orientation(firstStart, firstEnd, secondEnd);
  const secondA = orientation(secondStart, secondEnd, firstStart);
  const secondB = orientation(secondStart, secondEnd, firstEnd);
  if (((firstA > 0 && firstB < 0) || (firstA < 0 && firstB > 0))
    && ((secondA > 0 && secondB < 0) || (secondA < 0 && secondB > 0))) {
    return true;
  }
  if (Math.abs(firstA) < 0.001 && onSegment(firstStart, firstEnd, secondStart)) return true;
  if (Math.abs(firstB) < 0.001 && onSegment(firstStart, firstEnd, secondEnd)) return true;
  if (Math.abs(secondA) < 0.001 && onSegment(secondStart, secondEnd, firstStart)) return true;
  return Math.abs(secondB) < 0.001 && onSegment(secondStart, secondEnd, firstEnd);
}

function boardRectBoundaryPoint(rect, toward) {
  const center = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
  const dx = toward.x - center.x;
  const dy = toward.y - center.y;
  const ratio = Math.max(
    Math.abs(dx) / Math.max(1, rect.width / 2),
    Math.abs(dy) / Math.max(1, rect.height / 2),
  ) || 1;
  return { x: center.x + dx / ratio, y: center.y + dy / ratio };
}

function boardBoundsForPoints(points, rect, padding) {
  const xs = points.map((point) => point.x).concat(rect.x, rect.x + rect.width + 4);
  const ys = points.map((point) => point.y).concat(rect.y, rect.y + rect.height + 5);
  const minX = Math.min(...xs) - padding;
  const minY = Math.min(...ys) - padding;
  const maxX = Math.max(...xs) + padding;
  const maxY = Math.max(...ys) + padding;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function drawBoardHarbor(layer, layout) {
  const { harbor, geometry, dock, rect, connectorLead, connectorEnd } = layout;
  const group = svg("g", { "pointer-events": "none" });
  appendSvgTitle(group, `交換所 ${harbor.label}`);
  const shadowOffset = { x: geometry.axis.x + geometry.outward.x * 2, y: geometry.axis.y + geometry.outward.y * 2 };
  const shifted = (point, amount = 1) => ({
    x: point.x + shadowOffset.x * amount,
    y: point.y + shadowOffset.y * amount,
  });
  const drawPier = (start, end) => {
    const shadowStart = shifted(start);
    const shadowEnd = shifted(end);
    group.append(
      svg("line", { x1: shadowStart.x, y1: shadowStart.y, x2: shadowEnd.x, y2: shadowEnd.y, stroke: "#251f1b", "stroke-width": 9, "stroke-linecap": "round" }),
      svg("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, stroke: "#463121", "stroke-width": 8, "stroke-linecap": "round" }),
      svg("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, stroke: "#9e693b", "stroke-width": 5, "stroke-linecap": "round" }),
      svg("line", { x1: start.x - geometry.axis.x, y1: start.y - geometry.axis.y, x2: end.x - geometry.axis.x, y2: end.y - geometry.axis.y, stroke: "#e6b067", "stroke-width": 1.2, "stroke-linecap": "round" }),
    );
  };
  drawPier(dock.innerLeft, dock.outerLeft);
  drawPier(dock.innerRight, dock.outerRight);
  drawPier(dock.outerLeft, dock.outerRight);
  for (const point of [dock.outerLeft, dock.outerRight]) {
    const shadow = shifted(point, 0.7);
    group.append(
      svg("circle", { cx: shadow.x, cy: shadow.y, r: 5, fill: "#34271e" }),
      svg("circle", { cx: point.x, cy: point.y, r: 4, fill: "#b17642" }),
      svg("circle", { cx: point.x - 1, cy: point.y - 1, r: 2, fill: "#ebbb76" }),
    );
  }

  const connectorPoints = [dock.connectorStart, connectorLead, connectorEnd];
  const shadowPoints = connectorPoints.map((point) => `${point.x + 2},${point.y + 3}`).join(" ");
  group.append(
    svg("polyline", { points: shadowPoints, fill: "none", stroke: "#383027", "stroke-width": 4, "stroke-linejoin": "round", "stroke-linecap": "round" }),
    svg("polyline", { points: boardPointList(connectorPoints), fill: "none", stroke: "#dabc88", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }),
    svg("rect", { x: rect.x + 3, y: rect.y + 4, width: rect.width, height: rect.height, rx: 8, fill: "#2b231d" }),
    svg("rect", { x: rect.x, y: rect.y, width: rect.width, height: rect.height, rx: 8, fill: "#dab577" }),
    svg("rect", { x: rect.x + 2, y: rect.y + 2, width: rect.width - 4, height: rect.height - 4, rx: 6, fill: "#eed39f" }),
    svg("line", { x1: rect.x + 9, y1: rect.y + 5, x2: rect.x + rect.width - 9, y2: rect.y + 5, stroke: "#ffecc1", "stroke-width": 2, "stroke-linecap": "round" }),
    svg("rect", { x: rect.x, y: rect.y, width: rect.width, height: rect.height, rx: 8, fill: "none", stroke: "#533a27", "stroke-width": 2 }),
  );
  const resourceColor = BOARD_RESOURCE_COLORS[harbor.resource];
  const accent = resourceColor
    ? resourceColor.map((channel) => Math.round((channel * 2 + 170) / 3))
    : [95, 145, 178];
  group.append(
    svg("rect", { x: rect.x + 5, y: rect.y + 5, width: 7, height: rect.height - 10, rx: 3, fill: boardRgb(accent), stroke: "#49392b", "stroke-width": 1 }),
  );
  const label = svg("text", {
    x: rect.x + rect.width / 2 + 3,
    y: rect.y + rect.height / 2 + 1,
    fill: "#483527",
    "font-size": 12,
    "font-weight": 850,
    "text-anchor": "middle",
    "dominant-baseline": "middle",
  });
  label.textContent = harbor.label;
  group.append(label);
  layer.append(group);
}

function drawRoadPiece(
  layer,
  nodeStart,
  nodeEnd,
  players,
  ownerIndex,
  targetId,
  animationPlan,
) {
  const dx = nodeEnd.x - nodeStart.x;
  const dy = nodeEnd.y - nodeStart.y;
  const fullLength = Math.hypot(dx, dy);
  if (fullLength < 2) return;
  const axis = { x: dx / fullLength, y: dy / fullLength };
  const normal = { x: -axis.y, y: axis.x };
  const inset = Math.min(6, fullLength * 0.15);
  const start = {
    x: nodeStart.x + axis.x * inset,
    y: nodeStart.y + axis.y * inset,
  };
  const length = fullLength - inset * 2;
  const base = boardPlayerRgb(players, ownerIndex);
  const light = mixBoardColor(base, [255, 255, 246], 0.52);
  const shade = mixBoardColor(base, [24, 20, 17], 0.38);
  const grain = mixBoardColor(base, [44, 30, 21], 0.28);
  const animated = animationPlan.buildKeys.has(`road:${targetId}`);
  const group = svg("g", {
    class: animated ? "board-piece build-enter build-enter-road" : "board-piece",
    "data-piece-id": targetId,
    "pointer-events": "none",
  });
  appendSvgTitle(group, `${players?.[ownerIndex]?.name || `Player ${ownerIndex + 1}`}の街道`);

  const localPoint = (along, across, offset = { x: 0, y: 0 }) => ({
    x: start.x + axis.x * along + normal.x * across + offset.x,
    y: start.y + axis.y * along + normal.y * across + offset.y,
  });
  const plank = (width, capExtension, offset = { x: 0, y: 0 }) => {
    const half = width / 2;
    const bevel = Math.min(3.5, length * 0.12);
    return [
      localPoint(-capExtension, -half * 0.48, offset),
      localPoint(bevel, -half, offset),
      localPoint(length - bevel, -half, offset),
      localPoint(length + capExtension, -half * 0.48, offset),
      localPoint(length + capExtension, half * 0.48, offset),
      localPoint(length - bevel, half, offset),
      localPoint(bevel, half, offset),
      localPoint(-capExtension, half * 0.48, offset),
    ];
  };
  if (animated) {
    const haloStart = localPoint(-1, 0);
    const haloEnd = localPoint(length + 1, 0);
    group.append(svg("line", {
      x1: haloStart.x,
      y1: haloStart.y,
      x2: haloEnd.x,
      y2: haloEnd.y,
      class: "build-enter-halo build-enter-halo-road",
      stroke: "#ffe9a8",
      "stroke-width": 24,
      "stroke-linecap": "round",
    }));
  }
  group.append(
    svg("polygon", { points: boardPointList(plank(17, 2.2, { x: 2.5, y: 3.5 })), fill: "#181a1c", "stroke-linejoin": "round" }),
    svg("polygon", { points: boardPointList(plank(16, 2)), fill: "#191c20", "stroke-linejoin": "round" }),
    svg("polygon", { points: boardPointList(plank(11, 1.4)), fill: boardRgb(base), "stroke-linejoin": "round" }),
  );
  const addLocalLine = (fromAlong, fromAcross, toAlong, toAcross, color, width) => {
    const from = localPoint(fromAlong, fromAcross);
    const to = localPoint(toAlong, toAcross);
    group.append(svg("line", {
      x1: from.x,
      y1: from.y,
      x2: to.x,
      y2: to.y,
      stroke: boardRgb(color),
      "stroke-width": width,
      "stroke-linecap": "round",
    }));
  };
  addLocalLine(3.5, -3.5, length - 3.5, -3.5, light, 2);
  addLocalLine(3.5, 3.7, length - 3.5, 3.7, shade, 2);
  for (const [position, side] of [[0.30, -0.4], [0.64, 0.6]]) {
    const middle = length * position;
    addLocalLine(middle - 3, side, middle + 3, side, grain, 1);
  }

  const pattern = Math.abs(Number(players?.[ownerIndex]?.piece_pattern ?? ownerIndex) || 0) % 4;
  if (pattern < 2) {
    const positions = pattern === 0 ? [0.5] : [0.40, 0.60];
    for (const position of positions) {
      const point = localPoint(length * position, 0);
      group.append(
        svg("circle", { cx: point.x, cy: point.y, r: 2.2, fill: boardRgb(grain), stroke: boardRgb(light), "stroke-width": 1 }),
      );
    }
  } else {
    const positions = pattern === 2 ? [0.5] : [0.38, 0.62];
    for (const position of positions) {
      addLocalLine(length * position, -3, length * position, 3, light, 1.2);
    }
  }
  layer.append(group);
}

function drawBuildingPiece(
  layer,
  center,
  players,
  ownerIndex,
  buildingType,
  targetId,
  animationPlan,
) {
  const base = boardPlayerRgb(players, ownerIndex);
  const light = mixBoardColor(base, [255, 255, 244], 0.50);
  const roof = mixBoardColor(base, [48, 31, 22], 0.26);
  const shade = mixBoardColor(base, [22, 19, 18], 0.43);
  const detail = mixBoardColor(base, [23, 24, 27], 0.58);
  const animated = animationPlan.buildKeys.has(`${buildingType}:${targetId}`);
  const group = svg("g", {
    class: animated ? `board-piece build-enter build-enter-${buildingType}` : "board-piece",
    "data-piece-id": targetId,
    "pointer-events": "none",
  });
  appendSvgTitle(
    group,
    `${players?.[ownerIndex]?.name || `Player ${ownerIndex + 1}`}の${buildingType === "city" ? "都市" : "開拓地"}`,
  );
  const points = (values, offset = { x: 0, y: 0 }) => values.map(([x, y]) => ({
    x: center.x + x + offset.x,
    y: center.y + y + offset.y,
  }));
  let silhouette;
  let markCenter;
  if (animated) {
    group.append(svg("circle", {
      cx: center.x,
      cy: center.y,
      r: buildingType === "city" ? 29 : 24,
      class: "build-enter-halo",
      fill: "none",
      stroke: "#ffe9a8",
      "stroke-width": 5,
    }));
  }
  if (buildingType === "city") {
    silhouette = [[-16, 12], [-16, -1], [-8, -10], [0, -3], [0, -13], [13, -13], [13, 12]];
    group.append(
      svg("ellipse", { cx: center.x, cy: center.y + 12, rx: 17, ry: 5, fill: "#181a1c", "fill-opacity": 0.92 }),
      svg("polygon", { points: boardPointList(points(silhouette, { x: 2, y: 3 })), fill: "#181a1c", "stroke-linejoin": "round" }),
      svg("polygon", { points: boardPointList(points(silhouette)), fill: boardRgb(base), "stroke-linejoin": "round" }),
      svg("polygon", { points: boardPointList(points([[-16, -1], [-8, -10], [0, -3], [0, 1], [-8, -6], [-16, 2]])), fill: boardRgb(roof) }),
      svg("polygon", { points: boardPointList(points([[0, -13], [13, -13], [10, -9], [0, -9]])), fill: boardRgb(light) }),
      svg("polygon", { points: boardPointList(points([[10, -9], [13, -13], [13, 12], [9, 9]])), fill: boardRgb(shade) }),
      svg("line", { x1: center.x - 14, y1: center.y - 1, x2: center.x - 8, y2: center.y - 8, stroke: boardRgb(light), "stroke-width": 2, "stroke-linecap": "round" }),
      svg("line", { x1: center.x, y1: center.y - 8, x2: center.x, y2: center.y + 10, stroke: boardRgb(detail), "stroke-width": 1 }),
      svg("rect", { x: center.x + 4, y: center.y - 5, width: 4, height: 5, rx: 1, fill: boardRgb(detail) }),
      svg("line", { x1: center.x + 4, y1: center.y - 5, x2: center.x + 7, y2: center.y - 5, stroke: boardRgb(light), "stroke-width": 1 }),
      svg("rect", { x: center.x - 11, y: center.y + 4, width: 5, height: 8, rx: 1, fill: boardRgb(detail) }),
    );
    markCenter = { x: center.x + 5, y: center.y + 3 };
  } else {
    silhouette = [[-11, 10], [-11, -1], [0, -12], [11, -1], [11, 10]];
    group.append(
      svg("ellipse", { cx: center.x + 1.5, cy: center.y + 10.5, rx: 13.5, ry: 4.5, fill: "#181a1c", "fill-opacity": 0.92 }),
      svg("polygon", { points: boardPointList(points(silhouette, { x: 2, y: 3 })), fill: "#181a1c", "stroke-linejoin": "round" }),
      svg("polygon", { points: boardPointList(points(silhouette)), fill: boardRgb(base), "stroke-linejoin": "round" }),
      svg("polygon", { points: boardPointList(points([[-11, -1], [0, -12], [11, -1], [9, 2], [0, -7], [-9, 2]])), fill: boardRgb(roof) }),
      svg("polygon", { points: boardPointList(points([[7, 2], [11, -1], [11, 10], [7, 8]])), fill: boardRgb(shade) }),
      svg("rect", { x: center.x - 2, y: center.y + 3, width: 5, height: 7, rx: 1, fill: boardRgb(detail) }),
      svg("line", { x1: center.x - 9, y1: center.y - 1, x2: center.x, y2: center.y - 10, stroke: boardRgb(light), "stroke-width": 2, "stroke-linecap": "round" }),
      svg("line", { x1: center.x - 9, y1: center.y + 1, x2: center.x - 9, y2: center.y + 8, stroke: boardRgb(light), "stroke-width": 1 }),
    );
    markCenter = { x: center.x, y: center.y - 2 };
  }
  drawOwnerPattern(
    group,
    markCenter,
    Number(players?.[ownerIndex]?.piece_pattern ?? ownerIndex) || 0,
    boardRgb(light),
    3,
  );
  group.append(
    svg("polygon", {
      points: boardPointList(points(silhouette)),
      fill: "none",
      stroke: "#191c20",
      "stroke-width": 2,
      "stroke-linejoin": "round",
    }),
  );
  layer.append(group);
}

function drawOwnerPattern(layer, center, patternValue, color, radius) {
  const pattern = Math.abs(patternValue) % 4;
  if (pattern === 0) {
    layer.append(svg("circle", { cx: center.x, cy: center.y, r: Math.max(1, radius - 1), fill: color }));
  } else if (pattern === 1) {
    layer.append(svg("polygon", { points: boardPointList([
      { x: center.x, y: center.y - radius },
      { x: center.x + radius, y: center.y },
      { x: center.x, y: center.y + radius },
      { x: center.x - radius, y: center.y },
    ]), fill: color }));
  } else if (pattern === 2) {
    layer.append(svg("polygon", { points: boardPointList([
      { x: center.x, y: center.y - radius },
      { x: center.x + radius, y: center.y + radius },
      { x: center.x - radius, y: center.y + radius },
    ]), fill: color }));
  } else {
    layer.append(svg("rect", {
      x: center.x - radius + 1,
      y: center.y - radius + 1,
      width: radius * 2 - 1,
      height: radius * 2 - 1,
      rx: 0.7,
      fill: color,
    }));
  }
}

function drawDiceRollEffect(layer, dice, boardCenter) {
  const values = Array.isArray(dice.values) ? dice.values.map(Number) : [];
  if (
    values.length !== 2
    || values.some((value) => !Number.isInteger(value) || value < 1 || value > 6)
    || values[0] + values[1] !== dice.total
  ) {
    return;
  }
  const overlay = svg("g", {
    class: "board-dice-roll-overlay",
    "aria-hidden": "true",
    "data-dice-total": dice.total,
    "pointer-events": "none",
  });
  overlay.append(
    svg("ellipse", {
      cx: boardCenter.x,
      cy: boardCenter.y + 8,
      rx: 70,
      ry: 46,
      class: "board-dice-stage",
    }),
  );
  drawDieFace(overlay, { x: boardCenter.x - 28, y: boardCenter.y - 5 }, values[0], "first");
  drawDieFace(overlay, { x: boardCenter.x + 28, y: boardCenter.y - 5 }, values[1], "second");

  const totalGroup = svg("g", { class: "board-dice-total" });
  totalGroup.append(
    svg("rect", {
      x: boardCenter.x - 36,
      y: boardCenter.y + 32,
      width: 72,
      height: 27,
      rx: 13.5,
      class: "board-dice-total-badge",
    }),
  );
  const totalLabel = svg("text", {
    x: boardCenter.x,
    y: boardCenter.y + 46,
    class: "board-dice-total-label",
    "text-anchor": "middle",
    "dominant-baseline": "middle",
  });
  totalLabel.textContent = `合計 ${dice.total}`;
  totalGroup.append(totalLabel);
  overlay.append(totalGroup);
  layer.append(overlay);

  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  window.setTimeout(() => {
    if (overlay.isConnected) overlay.remove();
  }, reducedMotion ? 1250 : 1900);
}

function drawDieFace(layer, center, value, position) {
  const size = 44;
  const half = size / 2;
  const group = svg("g", {
    class: `board-die board-die-${position}`,
    "data-die-value": value,
  });
  group.append(
    svg("rect", {
      x: center.x - half + 3,
      y: center.y - half + 4,
      width: size,
      height: size,
      rx: 10,
      class: "board-die-shadow",
    }),
    svg("rect", {
      x: center.x - half,
      y: center.y - half,
      width: size,
      height: size,
      rx: 10,
      class: "board-die-face",
    }),
    svg("path", {
      d: `M ${center.x - 13} ${center.y - 17} H ${center.x + 10}`,
      class: "board-die-highlight",
    }),
  );
  for (const [column, row] of dicePipPositions(value)) {
    group.append(
      svg("circle", {
        cx: center.x + column * 12,
        cy: center.y + row * 12,
        r: 3.8,
        class: "board-die-pip",
      }),
    );
  }
  layer.append(group);
}

function dicePipPositions(value) {
  const patterns = {
    1: [[0, 0]],
    2: [[-1, -1], [1, 1]],
    3: [[-1, -1], [0, 0], [1, 1]],
    4: [[-1, -1], [1, -1], [-1, 1], [1, 1]],
    5: [[-1, -1], [1, -1], [0, 0], [-1, 1], [1, 1]],
    6: [[-1, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [1, 1]],
  };
  return patterns[value] || [];
}

function drawTileTarget(layer, tile, points) {
  const option = state.targetOptions.get(tile.id);
  const color = targetColor(option, "tile");
  const group = svg("g", { class: "board-target" });
  group.append(
    svg("polygon", {
      points: boardPointList(points),
      fill: "#ffffff",
      "fill-opacity": 0.001,
      stroke: "none",
      "pointer-events": "all",
    }),
    svg("polygon", {
      points: boardPointList(points),
      fill: "none",
      stroke: "#192630",
      "stroke-width": 8,
      "stroke-linejoin": "round",
      "pointer-events": "none",
    }),
    svg("polygon", {
      points: boardPointList(points),
      fill: "none",
      stroke: color,
      "stroke-width": 5,
      "stroke-linejoin": "round",
      "stroke-dasharray": "10 5",
      "pointer-events": "none",
    }),
    svg("polygon", {
      points: boardPointList(points),
      fill: "none",
      stroke: "#fffff5",
      "stroke-width": 1.2,
      "stroke-linejoin": "round",
      "pointer-events": "none",
    }),
  );
  addTargetBehavior(group, tile.id);
  layer.append(group);
}

function drawEdgeTarget(layer, targetId, start, end) {
  const option = state.targetOptions.get(targetId);
  const color = targetColor(option, "edge");
  const middle = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  const group = svg("g", { class: "board-target" });
  group.append(
    svg("line", {
      x1: start.x,
      y1: start.y,
      x2: end.x,
      y2: end.y,
      stroke: "#ffffff",
      "stroke-width": 28,
      "stroke-opacity": 0.001,
      "stroke-linecap": "round",
      "pointer-events": "stroke",
    }),
    svg("line", {
      x1: start.x,
      y1: start.y,
      x2: end.x,
      y2: end.y,
      stroke: "#1f2b36",
      "stroke-width": 13,
      "stroke-linecap": "round",
      "pointer-events": "none",
    }),
    svg("line", {
      x1: start.x,
      y1: start.y,
      x2: end.x,
      y2: end.y,
      stroke: color,
      "stroke-width": 7,
      "stroke-linecap": "round",
      "stroke-dasharray": "10 6",
      "pointer-events": "none",
    }),
    svg("line", {
      x1: start.x,
      y1: start.y,
      x2: end.x,
      y2: end.y,
      stroke: "#fffff5",
      "stroke-width": 1.5,
      "stroke-linecap": "round",
      "pointer-events": "none",
    }),
    svg("circle", { cx: middle.x, cy: middle.y, r: 8, fill: "#1f2b36", stroke: color, "stroke-width": 2, "pointer-events": "none" }),
    svg("line", { x1: middle.x - 3, y1: middle.y, x2: middle.x + 3, y2: middle.y, stroke: "#fffff5", "stroke-width": 1.5, "pointer-events": "none" }),
    svg("line", { x1: middle.x, y1: middle.y - 3, x2: middle.x, y2: middle.y + 3, stroke: "#fffff5", "stroke-width": 1.5, "pointer-events": "none" }),
  );
  addTargetBehavior(group, targetId);
  layer.append(group);
}

function drawNodeTarget(layer, node) {
  const option = state.targetOptions.get(node.id);
  const city = option?.args?.piece === "city";
  const color = targetColor(option, "node");
  const { x, y } = node.position;
  const group = svg("g", { class: "board-target" });
  group.append(
    svg("circle", {
      cx: x,
      cy: y,
      r: 23,
      fill: "#ffffff",
      "fill-opacity": 0.001,
      stroke: "none",
      "pointer-events": "all",
    }),
  );
  if (city) {
    group.append(
      svg("rect", { x: x - 20, y: y - 20, width: 40, height: 40, rx: 9, fill: "none", stroke: "#192630", "stroke-width": 7, "pointer-events": "none" }),
      svg("rect", { x: x - 20, y: y - 20, width: 40, height: 40, rx: 9, fill: "none", stroke: color, "stroke-width": 4, "pointer-events": "none" }),
      svg("rect", { x: x - 18, y: y - 18, width: 36, height: 36, rx: 7, fill: "none", stroke: "#fffff5", "stroke-width": 1, "pointer-events": "none" }),
    );
  } else {
    group.append(
      svg("circle", { cx: x, cy: y, r: 15, fill: "none", stroke: "#192630", "stroke-width": 7, "pointer-events": "none" }),
      svg("circle", { cx: x, cy: y, r: 15, fill: "none", stroke: color, "stroke-width": 4, "pointer-events": "none" }),
      svg("circle", { cx: x, cy: y, r: 3, fill: "#fffff5", "pointer-events": "none" }),
    );
  }
  addTargetBehavior(group, node.id);
  layer.append(group);
}

function targetColor(option, targetKind) {
  if (targetKind === "edge") return "#e1a0ff";
  if (targetKind === "tile") {
    return option?.command === "move_robber" ? "#ffaaa0" : "#ffe89a";
  }
  return option?.args?.piece === "city" ? "#ffe086" : "#84ffb2";
}

function addTargetBehavior(element, targetId) {
  const option = state.targetOptions.get(targetId);
  if (!option) return;
  element.setAttribute("tabindex", "0");
  element.setAttribute("focusable", "true");
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", commandLabel(option));
  element.addEventListener("click", () => sendGameCommand(option));
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      sendGameCommand(option);
    }
  });
}

function renderLegend(players) {
  const legend = elements["board-legend"];
  legend.replaceChildren();
  players.forEach((player, index) => {
    const item = document.createElement("span");
    item.className = "legend-item";
    const dot = document.createElement("span");
    dot.className = "legend-dot";
    dot.style.background = playerColor(players, index);
    item.append(
      dot,
      document.createTextNode(`${player.marker || ""} ${player.name}`.trim()),
    );
    legend.append(item);
  });
}

function renderPlayers(gameState, activeSeat, ownSeat) {
  const list = elements["player-list"];
  list.replaceChildren();
  const publicPoints = calculatePublicPoints(gameState);
  gameState.players.forEach((player, index) => {
    const card = document.createElement("article");
    card.className = `web-player-card${index === activeSeat ? " current" : ""}`;
    const color = document.createElement("span");
    color.className = "player-color";
    color.style.background = playerColor(gameState.players, index);
    const main = document.createElement("div");
    main.className = "player-main";
    const identity = player.is_ai
      ? `${player.marker || ""} ${player.name}・${aiPersonalityLabel(player.ai_personality)}AI`
      : `${player.marker || ""} ${player.name}`;
    main.append(
      textElement("strong", identity.trim()),
      textElement(
        "small",
        `${index === ownSeat ? "あなた · " : ""}手札${player.resource_total ?? resourceTotal(player.resources)}枚 · 発展${player.development_card_total ?? 0}枚`,
      ),
    );
    const gains = gameState.history?.public_gain_history?.[player.name];
    const latestGain = Array.isArray(gains) ? gains[gains.length - 1] : null;
    if (latestGain?.text) {
      main.append(
        textElement(
          "small",
          `直近公開: ${latestGain.text}${latestGain.source ? `（${latestGain.source}）` : ""}`,
          "public-gain",
        ),
      );
    }
    card.append(color, main, textElement("span", `VP ${publicPoints[index]}`, "player-vp"));
    if (player.resources && typeof player.resources === "object") {
      const strip = document.createElement("div");
      strip.className = "resource-strip";
      for (const resource of ["WOOD", "SHEEP", "WHEAT", "BRICK", "ORE"]) {
        strip.append(textElement("span", `${RESOURCE_LABELS[resource]} ${player.resources[resource] || 0}`, "resource-chip"));
      }
      card.append(strip);
    }
    list.append(card);
  });
}

function renderMatchResult() {
  const result = state.matchResult;
  if (!result) {
    elements["result-winner"].textContent = state.resultError ? "対局終了" : "対局結果を集計中";
    elements["result-summary"].textContent = state.resultError
      || "権威サーバーから最終集計を受け取っています。";
    elements["result-standings"].replaceChildren();
    elements["result-events"].replaceChildren();
    return;
  }
  const winner = result.winner?.name;
  elements["result-winner"].textContent = winner ? `${winner} の勝利` : "対局終了";
  const replayNotice = state.replayManifest?.truncated
    ? ` · 長期戦のためrev.${state.replayManifest.first_revision}以降を保存`
    : "";
  elements["result-summary"].textContent = `${result.victory_target || "—"} VP戦 · ${boardModeLabel(result.board?.mode)} · seed ${result.board?.seed ?? "—"}${replayNotice}`;
  renderResultStandings(result.standings || []);
  renderResultChart(result.vp_progression || [], result.standings || []);
  renderResultEvents(result.important_events || []);
  syncReplayControls();
}

function renderResultStandings(standings) {
  const container = elements["result-standings"];
  container.replaceChildren();
  for (const row of standings) {
    const item = document.createElement("article");
    item.className = `result-standing${row.winner ? " winner" : ""}`;
    const color = document.createElement("span");
    color.className = "legend-dot";
    color.style.background = arrayColor(row.color);
    const builds = row.builds || {};
    const trades = row.trades || {};
    const details = [
      `建設 道${builds.roads ?? row.roads ?? 0}・開${builds.settlements ?? row.settlements ?? 0}・都${builds.cities ?? row.cities ?? 0}`,
      `交易 国内${trades.domestic ?? 0}・銀行${trades.bank ?? 0}`,
      `運指数 ${formatLuck(row.luck_index)}`,
    ].join(" / ");
    const copy = document.createElement("div");
    copy.className = "result-player-copy";
    copy.append(
      textElement("strong", `${row.name}${row.is_ai ? `（${aiPersonalityLabel(row.personality)}AI）` : ""}`),
      textElement("small", details),
    );
    item.append(
      textElement("span", `#${row.rank || "—"}`, "result-rank"),
      color,
      copy,
      textElement("span", `${row.victory_points ?? 0} VP`, "result-score"),
    );
    container.append(item);
  }
}

function renderResultChart(timeline, standings) {
  const chart = elements["result-chart"];
  const legend = elements["result-chart-legend"];
  chart.replaceChildren();
  legend.replaceChildren();
  chart.setAttribute("viewBox", "0 0 520 230");
  const chartTitle = svg("title");
  chartTitle.textContent = "プレイヤー別の勝利点推移";
  const chartDescription = svg("desc");
  chartDescription.textContent = standings.length
    ? `最終得点: ${standings.map((row) => `${row.name} ${row.victory_points ?? 0}点`).join("、")}`
    : "勝利点推移データはありません。";
  chart.append(chartTitle, chartDescription);
  if (!timeline.length || !standings.length) {
    chart.append(svgText(260, 115, "勝利点の推移データがありません", "chart-axis-label"));
    return;
  }
  const maximum = Math.max(
    1,
    Number(state.matchResult?.victory_target) || 10,
    ...timeline.flatMap((entry) => (entry.scores || []).map((score) => Number(score.victory_points) || 0)),
  );
  const left = 32;
  const top = 12;
  const width = 472;
  const height = 188;
  for (let value = 0; value <= maximum; value += Math.max(1, Math.ceil(maximum / 5))) {
    const y = top + height - (value / maximum) * height;
    chart.append(svg("line", { x1: left, y1: y, x2: left + width, y2: y, class: "chart-grid-line" }));
    chart.append(svgText(left - 8, y + 3, String(value), "chart-axis-label"));
  }
  standings.forEach((player, playerIndex) => {
    const points = timeline.map((entry, index) => {
      const score = (entry.scores || []).find((candidate) => candidate.seat === player.seat);
      return {
        x: left + (index / Math.max(1, timeline.length - 1)) * width,
        y: top + height - ((Number(score?.victory_points) || 0) / maximum) * height,
      };
    });
    const color = arrayColor(player.color, playerIndex);
    chart.append(svg("polyline", {
      points: points.map((point) => `${point.x},${point.y}`).join(" "),
      stroke: color,
      class: "chart-player-line",
    }));
    for (const point of points) {
      chart.append(svg("circle", { cx: point.x, cy: point.y, r: 4, fill: color, class: "chart-player-point" }));
    }
    const item = document.createElement("span");
    item.className = "legend-item";
    const dot = document.createElement("span");
    dot.className = "legend-dot";
    dot.style.background = color;
    item.append(dot, document.createTextNode(player.name));
    legend.append(item);
  });
}

function renderResultEvents(events) {
  const container = elements["result-events"];
  container.replaceChildren();
  if (!events.length) {
    container.append(textElement("p", "重要イベントは記録されませんでした。", "context-hint"));
    return;
  }
  for (const event of events) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "result-event-button";
    button.disabled = !Number.isInteger(event.replay_frame_index);
    if (event.replay_frame_index === state.replayIndex) {
      button.setAttribute("aria-current", "true");
    }
    button.append(
      textElement("strong", event.title || "イベント"),
      textElement("small", event.detail || "この時点の盤面を表示します。"),
    );
    if (Number.isInteger(event.replay_frame_index)) {
      button.addEventListener("click", () => requestReplayFrame(event.replay_frame_index));
    }
    container.append(button);
  }
}

function replayFrameEntries() {
  return Array.isArray(state.replayManifest?.frames) ? state.replayManifest.frames : [];
}

function replayFrameCount() {
  return Number.isInteger(state.replayManifest?.frame_count)
    ? state.replayManifest.frame_count
    : replayFrameEntries().length;
}

function syncReplayControls() {
  const count = replayFrameCount();
  const available = count > 0;
  const index = state.replayIndex === null ? Math.max(0, count - 1) : state.replayIndex;
  elements["replay-slider"].max = String(Math.max(0, count - 1));
  elements["replay-slider"].value = String(index);
  elements["replay-slider"].setAttribute(
    "aria-valuetext",
    available ? `${index + 1} / ${count}` : "リプレイなし",
  );
  elements["replay-slider"].disabled = !available;
  const atStart = index <= 0;
  const atEnd = state.replayIndex === null || index >= count - 1;
  elements["replay-first"].disabled = !available || atStart;
  elements["replay-previous"].disabled = !available || atStart;
  elements["replay-play"].disabled = count < 2;
  elements["replay-next"].disabled = !available || atEnd;
  elements["replay-last"].disabled = !available || atEnd;
  elements["result-live-button"].disabled = state.replayIndex === null;
  elements["replay-position"].textContent = state.replayIndex === null
    ? "LIVE"
    : `${index + 1} / ${count}`;
  const metadata = replayFrameEntries()[index];
  elements["replay-frame-label"].textContent = metadata
    ? `${metadata.label || "盤面更新"} · rev. ${metadata.revision ?? metadata.sequence ?? index}`
    : available
      ? "リプレイ位置を選択できます。"
      : "対局終了後に操作できます。";
  elements["replay-play"].textContent = state.replayPlaying ? "一時停止" : "再生";
  elements["replay-play"].setAttribute("aria-pressed", String(state.replayPlaying));
}

async function requestReplayFrame(index) {
  const count = replayFrameCount();
  if (state.replayRequestPending || !Number.isInteger(index) || index < 0 || index >= count) return;
  state.replayRequestPending = true;
  try {
    await sendMessage(wireMessage("replay_frame_request", { index }));
  } catch (error) {
    stopReplay();
    showToast(error.message, true);
  } finally {
    state.replayRequestPending = false;
    syncReplayControls();
  }
}

function stopReplay() {
  state.replayPlaying = false;
  window.clearTimeout(state.replayTimer);
  state.replayTimer = null;
  if (elements["replay-play"]) elements["replay-play"].textContent = "再生";
}

function scheduleReplay() {
  window.clearTimeout(state.replayTimer);
  if (!state.replayPlaying) return;
  const delay = Number(elements["replay-speed"].value) || 800;
  state.replayTimer = window.setTimeout(async () => {
    const count = replayFrameCount();
    const current = state.replayIndex === null ? -1 : state.replayIndex;
    if (current >= count - 1) {
      stopReplay();
      syncReplayControls();
      return;
    }
    await requestReplayFrame(current + 1);
    scheduleReplay();
  }, delay);
}

function showLiveSnapshot() {
  stopReplay();
  state.replayIndex = null;
  if (state.liveSnapshot) state.snapshot = state.liveSnapshot;
  render();
  focusGameBoard();
}

function focusGameBoard() {
  window.requestAnimationFrame(() => focusAndScroll(elements["board-shell"], "center"));
}

function focusAndScroll(element, block) {
  if (!element) return;
  element.focus({ preventScroll: true });
  element.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    block,
  });
}

function arrayColor(color, fallbackIndex = 0) {
  if (Array.isArray(color) && color.length >= 3) {
    return `rgb(${color[0]}, ${color[1]}, ${color[2]})`;
  }
  return ["#ff6565", "#6478ff", "#efb444", "#63d7df"][fallbackIndex % 4];
}

function formatLuck(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(0)}` : "—";
}

function calculatePublicPoints(gameState) {
  if (gameState.phase?.name === "finished" && state.matchResult?.standings) {
    return gameState.players.map((_player, index) => {
      const standing = state.matchResult.standings.find((row) => row.seat === index + 1);
      return Number(standing?.victory_points) || 0;
    });
  }
  const points = gameState.players.map(() => 0);
  const manifest = state.snapshot?.board_manifest;
  for (const node of manifest?.nodes || []) {
    if (!node.building) continue;
    points[node.building.owner_player_index] += node.building.type === "city" ? 2 : 1;
  }
  const phase = gameState.phase || {};
  if (Number.isInteger(phase.longest_road_owner)) points[phase.longest_road_owner] += 2;
  if (Number.isInteger(phase.largest_army_owner)) points[phase.largest_army_owner] += 2;
  gameState.players.forEach((player, index) => {
    if (Number.isInteger(player.victory_point_cards)) points[index] += player.victory_point_cards;
  });
  return points;
}

function activePlayerIndex(gameState) {
  const phase = gameState.phase || {};
  const initial = gameState.initial || {};
  const special = gameState.special || {};
  const trade = gameState.domestic_trade || {};
  if (phase.special_phase === "discard" && Number.isInteger(special.discard_player)) {
    return special.discard_player;
  }
  if (phase.special_phase === "player_handoff" && Number.isInteger(special.handoff_player)) {
    return special.handoff_player;
  }
  if (
    typeof phase.special_phase === "string" &&
    phase.special_phase.startsWith("domestic_trade_")
  ) {
    if (Number.isInteger(trade.broadcast_viewer)) return trade.broadcast_viewer;
    if (Number.isInteger(trade.editor)) return trade.editor;
  }
  if (phase.name === "initial") {
    const initialOrder = initial.dice_phase
      ? initial.dice_contenders
      : initial.placement_order;
    if (Array.isArray(initialOrder)) {
      const initialActor = initialOrder[initial.player_index];
      if (Number.isInteger(initialActor)) return initialActor;
    }
  }
  const order = Array.isArray(phase.turn_order) ? phase.turn_order : [];
  return order[phase.current_player_index] ?? phase.current_player_index ?? null;
}

function phaseTitle(gameState, activeSeat) {
  const phase = gameState.phase || {};
  const playerName = Number.isInteger(activeSeat)
    ? gameState.players?.[activeSeat]?.name || "プレイヤー"
    : "プレイヤー";
  if (phase.name === "finished") {
    const winner = Number.isInteger(phase.winner) ? gameState.players?.[phase.winner]?.name : playerName;
    return { title: `${winner || "対局"}の勝利`, detail: "対局が終了しました。" };
  }
  if (phase.name === "initial") {
    if (gameState.initial?.dice_phase) {
      return { title: `配置順を決定 — ${playerName}`, detail: "手番のプレイヤーが初期ダイスを振ります。" };
    }
    return {
      title: `初期配置 — ${playerName}`,
      detail: gameState.initial?.waiting_for_road ? "開拓地につながる街道を選びます。" : "光っている交差点に開拓地を置きます。",
    };
  }
  const specialLabels = {
    discard: "捨て札を選択",
    move_robber: "盗賊を移動",
    steal: "略奪する相手を選択",
    bank_trade_give: "銀行へ渡す資源を選択",
    bank_trade_receive: "銀行から受け取る資源を選択",
    year_of_plenty: "収穫する資源を選択",
    monopoly: "独占する資源を選択",
    road_building: "街道建設",
  };
  if (phase.special_phase) {
    const label = phase.special_phase.startsWith("domestic_trade_")
      ? "国内交易"
      : specialLabels[phase.special_phase] || "特殊処理";
    return { title: `${label} — ${playerName}`, detail: "表示された候補から次の操作を選びます。" };
  }
  return phase.dice_rolled
    ? { title: `行動中 — ${playerName}`, detail: "建設・交易を行うか、手番を終了します。" }
    : { title: `ダイス前 — ${playerName}`, detail: "ダイスを振って資源を生産します。" };
}

function commandLabel(option) {
  const args = option.args || {};
  const fixed = {
    roll_dice: "ダイスを振る",
    end_turn: "手番終了",
    cancel: "キャンセル",
    buy_development: "発展カードを購入",
    start_bank_trade: "銀行交易",
    start_domestic_trade: "国内交易",
    trade_broadcast: "全員に募集",
    trade_submit: "この条件で提案",
    trade_reveal: "提案を確認",
    trade_accept: "承諾する",
    trade_counter: "条件を変更",
    trade_reject: "拒否する",
    finish_road_building: "街道建設を終了",
  };
  if (fixed[option.command]) return fixed[option.command];
  if (option.command === "build") return `${PIECE_LABELS[args.piece] || args.piece}を建設`;
  if (option.command === "initial_place") return args.target?.startsWith("edge") ? "初期街道を配置" : "初期開拓地を配置";
  if (option.command === "move_robber") return "盗賊の移動先";
  if (option.command === "select_resource") return `${RESOURCE_LABELS[args.resource] || args.resource}を選択`;
  if (option.command === "steal") return `席${Number(args.seat_index) + 1}から略奪`;
  if (option.command === "trade_partner") return `席${Number(args.seat_index) + 1}と交渉`;
  if (option.command === "trade_edit_side") return args.side === "give" ? "渡す資源を編集" : "受け取る資源を編集";
  if (option.command === "trade_adjust") {
    const direction = Number(args.delta) > 0 ? "+" : "−";
    return `${args.side === "give" ? "渡す" : "受取"} ${RESOURCE_LABELS[args.resource] || args.resource} ${direction}1`;
  }
  if (option.command === "use_development") return `${CARD_LABELS[args.card] || args.card}を使用`;
  return option.command.replaceAll("_", " ");
}

function boardModeLabel(mode) {
  return { constrained: "制約付き", fully_random: "公式ランダム", custom: "カスタム" }[mode] || mode;
}

function houseRulesLabel(rules) {
  if (!rules) return "なし（標準）";
  const labels = [];
  if (rules.bank_trade_3_to_1) labels.push("銀行3:1");
  if (rules.skip_discard_on_seven) labels.push("7捨て札なし");
  if (rules.disabled_development_cards?.length) labels.push(`発展${rules.disabled_development_cards.length}種禁止`);
  return labels.join(" / ") || "なし（標準）";
}

function roleLabel(role) {
  return { host: "ホスト", player: "プレイヤー", ai: "AI", spectator: "観戦" }[role] || role || "未参加";
}

function aiPersonalityLabel(mode) {
  return {
    standard: "標準",
    mixed: "混合",
    expansion: "拡大重視",
    trader: "交渉重視",
    disruptor: "妨害重視",
  }[mode] || mode || "標準";
}

function playerColor(players, index) {
  const color = players?.[index]?.color;
  return Array.isArray(color) && color.length >= 3
    ? `rgb(${color[0]}, ${color[1]}, ${color[2]})`
    : "#e6edf1";
}

function resourceTotal(resources) {
  if (!resources || typeof resources !== "object") return 0;
  return Object.values(resources).reduce((total, value) => total + (Number(value) || 0), 0);
}

function textElement(tag, text, className = "") {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function svg(tag, attributes = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function svgText(x, y, text, className) {
  const element = svg("text", { x, y, class: className });
  element.textContent = text;
  return element;
}

elements["create-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await sendMessage(
      wireMessage("create_room", {
        display_name: String(form.get("display_name") || "").trim(),
        settings: {
          player_count: Number(form.get("player_count")),
          ai_player_count: Number(form.get("ai_player_count")),
          ai_personality_mode: String(form.get("ai_personality_mode")),
          victory_target: Number(form.get("victory_target")),
          board_mode: String(form.get("board_mode")),
          board_seed: Number(form.get("board_seed")),
          variant: variantConfigDocument(String(form.get("variant_kind"))),
        },
      }),
    );
  } catch (error) {
    showToast(error.message, true);
  }
});

elements["join-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await sendMessage(
      wireMessage("join_room", {
        room_code: String(form.get("room_code") || "").trim().toUpperCase(),
        display_name: String(form.get("display_name") || "").trim(),
        role: form.get("role") === "spectator" ? "spectator" : "player",
      }),
    );
  } catch (error) {
    showToast(error.message, true);
  }
});

elements["random-seed"].addEventListener("click", () => {
  const input = elements["create-form"].elements.board_seed;
  input.value = String(Math.floor(10000 + Math.random() * 99989999));
});

function syncAIOptions() {
  const total = Number(elements["create-form"].elements.player_count.value);
  const select = elements["ai-player-count"];
  const previous = Math.min(Number(select.value) || 0, Math.max(0, total - 1));
  select.replaceChildren();
  for (let count = 0; count < total; count += 1) {
    const option = document.createElement("option");
    option.value = String(count);
    option.textContent = count === 0 ? "なし" : `${count}人`;
    option.selected = count === previous;
    select.append(option);
  }
  elements["ai-personality-mode"].disabled = previous === 0;
}

elements["create-form"].elements.player_count.addEventListener("change", syncAIOptions);
elements["ai-player-count"].addEventListener("change", () => {
  elements["ai-personality-mode"].disabled = Number(elements["ai-player-count"].value) === 0;
});

elements["ready-button"].addEventListener("click", async () => {
  const isReady = elements["ready-button"].dataset.ready === "true";
  try {
    await sendMessage(wireMessage("set_ready", { ready: !isReady }));
  } catch (error) {
    showToast(error.message, true);
  }
});

elements["start-button"].addEventListener("click", async () => {
  try {
    await sendMessage(wireMessage("start_game"));
  } catch (error) {
    showToast(error.message, true);
  }
});

async function leaveRoom() {
  if (state.lobby?.phase === "started" && state.welcome?.role !== "spectator") {
    const confirmed = window.confirm("プレイヤーが退出すると対局は終了します。退出しますか？");
    if (!confirmed) return;
  }
  try {
    await sendMessage(wireMessage("leave_room"));
  } catch (error) {
    showToast(error.message, true);
  }
  resetRoomState();
}

elements["leave-button"].addEventListener("click", leaveRoom);
elements["game-leave-button"].addEventListener("click", leaveRoom);
elements["copy-room-code"].addEventListener("click", async () => {
  const code = state.lobby?.room_code;
  if (!code) return;
  try {
    await navigator.clipboard.writeText(code);
    showToast("参加コードをコピーしました。");
  } catch (_error) {
    showToast(`参加コード: ${code}`);
  }
});

elements["result-live-button"].addEventListener("click", showLiveSnapshot);
elements["replay-first"].addEventListener("click", () => requestReplayFrame(0));
elements["replay-previous"].addEventListener("click", () => {
  const index = state.replayIndex === null ? replayFrameCount() - 1 : state.replayIndex;
  requestReplayFrame(Math.max(0, index - 1));
});
elements["replay-next"].addEventListener("click", () => {
  const index = state.replayIndex === null ? replayFrameCount() - 1 : state.replayIndex;
  requestReplayFrame(Math.min(replayFrameCount() - 1, index + 1));
});
elements["replay-last"].addEventListener("click", () => requestReplayFrame(replayFrameCount() - 1));
elements["replay-play"].addEventListener("click", () => {
  state.replayPlaying = !state.replayPlaying;
  if (state.replayPlaying && (state.replayIndex === null || state.replayIndex >= replayFrameCount() - 1)) {
    state.replayIndex = null;
  }
  syncReplayControls();
  scheduleReplay();
});
elements["replay-slider"].addEventListener("change", (event) => {
  stopReplay();
  requestReplayFrame(Number(event.currentTarget.value));
});
elements["replay-speed"].addEventListener("change", () => {
  if (state.replayPlaying) scheduleReplay();
});

window.addEventListener("beforeunload", () => {
  // The HttpOnly browser session survives a refresh.  Explicit disconnect is
  // handled by the UI because keepalive requests during unload are unreliable.
});

async function initialise() {
  setConnection("connecting", "接続準備中");
  syncAIOptions();
  try {
    await startBrowserSession();
    render();
  } catch (error) {
    setConnection("error", "サーバーに接続できません");
    showToast(error.message, true);
  }
  window.setInterval(pollEvents, POLL_INTERVAL_MS);
}

initialise();
