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
};
const PIECE_LABELS = { road: "街道", settlement: "開拓地", city: "都市" };
const CARD_LABELS = {
  knight: "騎士",
  road_building: "街道建設",
  year_of_plenty: "収穫",
  monopoly: "独占",
};

const state = {
  welcome: null,
  lobby: null,
  snapshot: null,
  nextSequence: 0,
  commandPending: false,
  pollPending: false,
  reconnecting: false,
  targetOptions: new Map(),
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
    "board-layer",
    "board-legend",
    "revision-badge",
    "action-list",
    "action-hint",
    "victory-target-label",
    "player-list",
    "latest-event-title",
    "latest-event-detail",
    "toast",
  ].map((id) => [id, document.getElementById(id)]),
);

function wireMessage(type, payload = {}) {
  return { type, protocol_version: PROTOCOL_VERSION, ...payload };
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
  processEvents(document.events || []);
  setConnection("online", "ローカルサーバー接続中");
  if (!state.welcome) {
    await reconnectFromStorage();
  }
}

async function sendMessage(message) {
  const document = await api("/api/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message),
  });
  processEvents(document.events || []);
  return document;
}

async function pollEvents() {
  if (state.pollPending) return;
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

function processEvents(events) {
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
        break;
      case "lobby_snapshot":
        state.lobby = event.lobby;
        syncRoleFromLobby();
        break;
      case "state_snapshot":
        if (!state.snapshot || event.revision >= state.snapshot.revision) {
          state.snapshot = event;
        }
        break;
      case "game_command_result":
        state.commandPending = false;
        reconcileSequence(event);
        if (!event.accepted) {
          showToast(event.message || "操作が受理されませんでした。", true);
        }
        break;
      case "request_error":
        state.commandPending = false;
        showToast(event.message || "操作を処理できませんでした。", true);
        break;
      case "room_closed":
        showToast(event.message || "部屋が終了しました。", true);
        resetRoomState();
        break;
      default:
        break;
    }
  }
  render();
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
  elements["connection-status"].dataset.state = value;
  elements["connection-label"].textContent = label;
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

function resetRoomState() {
  state.welcome = null;
  state.lobby = null;
  state.snapshot = null;
  state.nextSequence = 0;
  state.commandPending = false;
  state.targetOptions.clear();
  sessionStorage.removeItem("catan-reconnect");
  render();
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
          ? `${roleLabel(member.role)} · ${member.connected ? "接続中" : "再接続待ち"}`
          : "参加者を待っています",
      ),
    );
    row.append(name);
    row.append(
      textElement(
        "span",
        member?.ready ? "READY" : member ? "WAIT" : "OPEN",
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
    ["勝利条件", `${settings.victory_target} VP`],
    ["盤面", boardModeLabel(settings.board_mode)],
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
  renderBoard(snapshot.board_manifest, gameState.players || []);
  renderActions(options);
  renderPlayers(gameState, activeSeat, ownSeat);
  const latest = gameState.history?.latest_event || {};
  elements["latest-event-title"].textContent = latest.title || "進行中";
  elements["latest-event-detail"].textContent = latest.detail || "次の操作を待っています。";
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

function renderBoard(manifest, players) {
  if (!manifest) return;
  const layer = elements["board-layer"];
  layer.replaceChildren();
  const nodeById = new Map(manifest.nodes.map((node) => [node.id, node]));
  const bounds = manifest.coordinate_space.bounds;
  const padding = 120;
  elements["board-svg"].setAttribute(
    "viewBox",
    `${bounds.min_x - padding} ${bounds.min_y - padding} ${bounds.max_x - bounds.min_x + padding * 2} ${bounds.max_y - bounds.min_y + padding * 2}`,
  );
  const boardCenter = {
    x: (bounds.min_x + bounds.max_x) / 2,
    y: (bounds.min_y + bounds.max_y) / 2,
  };

  const tileLayer = svg("g");
  const harborLayer = svg("g");
  const edgeLayer = svg("g");
  const pieceLayer = svg("g");
  const targetLayer = svg("g");
  layer.append(tileLayer, harborLayer, edgeLayer, pieceLayer, targetLayer);

  for (const tile of manifest.tiles) {
    const center = tile.center;
    const points = tile.corner_node_ids
      .map((id) => nodeById.get(id)?.position)
      .filter(Boolean);
    const polygon = svg("polygon", {
      points: points.map((point) => `${point.x},${point.y}`).join(" "),
      class: `tile ${tile.resource}${state.targetOptions.has(tile.id) ? " board-target" : ""}`,
    });
    addTargetBehavior(polygon, tile.id);
    tileLayer.append(polygon);
    tileLayer.append(
      svgText(center.x, center.y - 29, RESOURCE_LABELS[tile.resource] || tile.resource, "tile-text"),
    );
    if (tile.number !== null) {
      tileLayer.append(svg("circle", { cx: center.x, cy: center.y + 5, r: 25, class: "number-token" }));
      tileLayer.append(
        svgText(
          center.x,
          center.y + 7,
          String(tile.number),
          `number-text${[6, 8].includes(tile.number) ? " hot" : ""}`,
        ),
      );
    }
    if (tile.robber) {
      tileLayer.append(svg("circle", { cx: center.x + 29, cy: center.y - 18, r: 12, class: "robber" }));
      tileLayer.append(svg("rect", { x: center.x + 20, y: center.y - 14, width: 18, height: 26, rx: 7, class: "robber" }));
    }
  }

  const harborById = new Map(manifest.harbors.map((harbor) => [harbor.id, harbor]));
  for (const harbor of manifest.harbors) {
    const nodes = harbor.node_ids.map((id) => nodeById.get(id)?.position).filter(Boolean);
    if (nodes.length !== 2) continue;
    const middle = { x: (nodes[0].x + nodes[1].x) / 2, y: (nodes[0].y + nodes[1].y) / 2 };
    const dx = middle.x - boardCenter.x;
    const dy = middle.y - boardCenter.y;
    const length = Math.hypot(dx, dy) || 1;
    const badge = { x: middle.x + (dx / length) * 54, y: middle.y + (dy / length) * 54 };
    harborLayer.append(svg("line", { x1: middle.x, y1: middle.y, x2: badge.x, y2: badge.y, class: "harbor-line" }));
    harborLayer.append(svg("rect", { x: badge.x - 31, y: badge.y - 14, width: 62, height: 28, rx: 8, class: "harbor-badge" }));
    harborLayer.append(svgText(badge.x, badge.y + 1, harbor.label, "harbor-text"));
  }

  for (const edge of manifest.edges) {
    const nodes = edge.node_ids.map((id) => nodeById.get(id)?.position).filter(Boolean);
    if (nodes.length !== 2) continue;
    edgeLayer.append(svg("line", { x1: nodes[0].x, y1: nodes[0].y, x2: nodes[1].x, y2: nodes[1].y, class: "edge" }));
    if (edge.road) {
      pieceLayer.append(svg("line", { x1: nodes[0].x, y1: nodes[0].y, x2: nodes[1].x, y2: nodes[1].y, class: "road-shadow" }));
      pieceLayer.append(
        svg("line", {
          x1: nodes[0].x,
          y1: nodes[0].y,
          x2: nodes[1].x,
          y2: nodes[1].y,
          class: "road",
          stroke: playerColor(players, edge.road.owner_player_index),
        }),
      );
    }
    if (state.targetOptions.has(edge.id)) {
      const target = svg("line", {
        x1: nodes[0].x,
        y1: nodes[0].y,
        x2: nodes[1].x,
        y2: nodes[1].y,
        stroke: "#ffdb7a",
        "stroke-width": 16,
        "stroke-opacity": 0.7,
        "stroke-linecap": "round",
        class: "board-target",
      });
      addTargetBehavior(target, edge.id);
      targetLayer.append(target);
    }
    if (edge.harbor_id && !harborById.has(edge.harbor_id)) continue;
  }

  for (const node of manifest.nodes) {
    if (node.building) {
      const color = playerColor(players, node.building.owner_player_index);
      if (node.building.type === "city") {
        pieceLayer.append(svg("rect", { x: node.position.x - 13, y: node.position.y - 13, width: 26, height: 26, rx: 4, fill: color, class: "building" }));
        pieceLayer.append(svg("rect", { x: node.position.x - 5, y: node.position.y - 22, width: 18, height: 18, rx: 3, fill: color, class: "building" }));
      } else {
        pieceLayer.append(
          svg("polygon", {
            points: `${node.position.x - 13},${node.position.y + 11} ${node.position.x - 13},${node.position.y - 4} ${node.position.x},${node.position.y - 16} ${node.position.x + 13},${node.position.y - 4} ${node.position.x + 13},${node.position.y + 11}`,
            fill: color,
            class: "building",
          }),
        );
      }
    }
    if (state.targetOptions.has(node.id)) {
      const target = svg("circle", {
        cx: node.position.x,
        cy: node.position.y,
        r: 14,
        fill: "#ffdb7a",
        "fill-opacity": 0.82,
        stroke: "#fff1bd",
        "stroke-width": 3,
        class: "board-target",
      });
      addTargetBehavior(target, node.id);
      targetLayer.append(target);
    }
  }
  renderLegend(players);
}

function addTargetBehavior(element, targetId) {
  const option = state.targetOptions.get(targetId);
  if (!option) return;
  element.setAttribute("tabindex", "0");
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
    item.append(dot, document.createTextNode(player.name));
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
    main.append(
      textElement("strong", `${player.marker || ""} ${player.name}`.trim()),
      textElement(
        "small",
        `${index === ownSeat ? "あなた · " : ""}手札${player.resource_total ?? resourceTotal(player.resources)}枚 · 発展${player.development_card_total ?? 0}枚`,
      ),
    );
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

function calculatePublicPoints(gameState) {
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
  return { host: "ホスト", player: "プレイヤー", spectator: "観戦" }[role] || role || "未参加";
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
          victory_target: Number(form.get("victory_target")),
          board_mode: String(form.get("board_mode")),
          board_seed: Number(form.get("board_seed")),
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

window.addEventListener("beforeunload", () => {
  // The HttpOnly browser session survives a refresh.  Explicit disconnect is
  // handled by the UI because keepalive requests during unload are unreliable.
});

async function initialise() {
  setConnection("connecting", "接続準備中");
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
