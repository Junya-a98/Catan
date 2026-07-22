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
const TRADE_RESOURCE_KEYS = Object.freeze(["WOOD", "SHEEP", "WHEAT", "BRICK", "ORE"]);
const PIECE_LABELS = { road: "街道", settlement: "開拓地", city: "都市" };
const CARD_LABELS = {
  knight: "騎士",
  road_building: "街道建設",
  year_of_plenty: "収穫",
  monopoly: "独占",
};
const DEVELOPMENT_CARD_KEYS = Object.freeze([
  "KNIGHT",
  "ROAD_BUILDING",
  "YEAR_OF_PLENTY",
  "MONOPOLY",
]);
const DEVELOPMENT_CARD_LABELS = Object.freeze({
  KNIGHT: "騎士",
  ROAD_BUILDING: "街道建設",
  YEAR_OF_PLENTY: "収穫",
  MONOPOLY: "独占",
});
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
  catalog: "core_v2",
  forecast_lead_turns: 2,
  event_interval_turns: 6,
};
const DEFAULT_FRONTIER_OPTIONS = {
  initial_radius: 1,
  reveal_rule: "road_adjacent_v1",
};
const EXPANDED_FRONTIER_OPTIONS = {
  catalog: "outer_ring_37_v1",
  initial_radius: 1,
  reveal_rule: "road_adjacent_v1",
};
const DEFAULT_TRADE2_OPTIONS = {
  catalog: "market_auction_v1",
  order_ttl_turns: 4,
  auction_ttl_turns: 4,
};
const DEFAULT_CREDIT_OPTIONS = Object.freeze({ catalog: "bank_loan_v1" });
const COMPOSITE_EVENTS_ECONOMY_CATALOG = "events_economy_v1";
const COMPOSITE_GRAND_CAMPAIGN_CATALOG = "grand_campaign_v1";
const CAMPAIGN_FORECAST_CATALOG = "campaign_v1";
const COMPOSITE_COMPONENTS = Object.freeze({
  [COMPOSITE_EVENTS_ECONOMY_CATALOG]: Object.freeze([
    "forecast_events",
    "trade2",
    "credit",
  ]),
  [COMPOSITE_GRAND_CAMPAIGN_CATALOG]: Object.freeze([
    "forecast_events",
    "frontier",
    "trade2",
    "credit",
  ]),
});
const COMPOSITE_COMPONENT_CATALOGS = Object.freeze({
  [COMPOSITE_EVENTS_ECONOMY_CATALOG]: Object.freeze({
    forecast_events: "core_v2",
    trade2: "market_auction_v1",
    credit: "bank_loan_v1",
  }),
  [COMPOSITE_GRAND_CAMPAIGN_CATALOG]: Object.freeze({
    forecast_events: CAMPAIGN_FORECAST_CATALOG,
    frontier: "outer_ring_37_v1",
    trade2: "market_auction_v1",
    credit: "bank_loan_v1",
  }),
});
const CAMPAIGN_BLOCKADE_SKIP_LABEL = "公開済み交換所なし・今回は発動なし";
const MARKET_RESOURCE_LIMIT = 19;
const INVITATION_ROOM_CODE_PATTERN = /^[A-Z0-9]{6}$/;
const INVITATION_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const INVITATION_ID_PATTERN = /^[A-Za-z0-9_-]{22}$/;
const INVITATION_ROLES = new Set(["player", "spectator"]);
const ROOM_ACCESS_AUTHENTICATION_ERROR = "authentication_failed";
const ROOM_ACCESS_SECURE_TRANSPORT_ERROR = "secure_transport_required";
const HTTP_ONLY_ROOM_MESSAGE_TYPES = new Set([
  "create_room",
  "join_room",
  "leave_room",
  "reconnect_room",
]);
const ROOM_RESUME_COOKIE_MESSAGE_TYPES = new Set([
  "create_room",
  "join_room",
]);
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
  harbor_blockade_v1: {
    title: "港湾封鎖",
    description: "予告された交換所が発動から2手番使用できなくなります。",
    active: "港湾封鎖: 指定交換所を使用不可",
  },
  construction_boom_v1: {
    title: "建設ブーム",
    description: "次に有料で建設される街道1本は、木か土のどちらか1枚が不要です。",
    active: "建設ブーム: 次の有料街道を1枚割引",
  },
  merchant_festival_v1: {
    title: "商人祭",
    description: "1ラウンドの間、国内交易が成立した双方へ銀行から資源を1枚支給します。",
    active: "商人祭: 国内交易の双方へ資源1枚",
  },
  bandit_raid_v1: {
    title: "山賊襲来",
    description: "発動時、盗賊が予告数字の高生産タイルへ移動します。捨て札と略奪はありません。",
    active: "山賊襲来を解決中",
  },
  earthquake_v1: {
    title: "地震",
    description: "1ラウンドの間、予告された方角の街道が接続と最長交易路に使えません。",
    active: "地震: 指定方角の街道を通行不能",
  },
};
const FORECAST_SECTOR_LABELS = ["東側", "南東側", "南西側", "西側", "北西側", "北東側"];

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
  replayExpectedIndex: null,
  replayRequestGeneration: 0,
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
  tradePromptSignature: null,
  tradePromptDismissed: new Set(),
  tradePromptNotified: new Set(),
  developmentInventoryOpen: null,
  forecastActiveSignature: null,
  marketEditorOpen: false,
  marketDraft: null,
  auctionEditorOpen: false,
  auctionDraft: null,
  creditEditorOpen: false,
  creditDraft: null,
  pendingRoomAccessAttempt: null,
  roomAccessPromptOpen: false,
  roomAccessCooldownUntil: null,
  roomAccessCooldownTimer: null,
  claimedInvitation: null,
  invitationCopyPending: false,
  activeInvitations: [],
  invitationListRoomCode: null,
  invitationListSignature: null,
  invitationListLoading: false,
  invitationListError: null,
  invitationListRequestId: 0,
  invitationMutationPending: false,
};

const elements = Object.fromEntries(
  [
    "connection-status",
    "connection-label",
    "rules-toggle",
    "rules-drawer",
    "rules-close",
    "rules-variant-note",
    "rules-campaign-note",
    "rules-forecast-note",
    "rules-frontier-note",
    "rules-market-note",
    "rules-credit-note",
    "audio-volume",
    "home-view",
    "lobby-view",
    "game-view",
    "create-form",
    "join-form",
    "invite-prefill-note",
    "decline-claimed-invitation",
    "create-room-protection",
    "invite-only-room",
    "open-room",
    "protect-room",
    "create-passphrase-fields",
    "create-room-passphrase",
    "create-passphrase-toggle",
    "room-protection-transport-note",
    "random-seed",
    "ai-player-count",
    "ai-personality-mode",
    "lobby-room-code",
    "copy-room-code",
    "copy-player-invite-link",
    "copy-spectator-invite-link",
    "invitation-guidance",
    "invite-link-fallback",
    "invite-link-value",
    "invitation-manager",
    "invitation-manager-status",
    "invitation-count",
    "invitation-list",
    "refresh-invitations",
    "revoke-all-invitations",
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
    "market-panel",
    "market-order-count",
    "market-order-list",
    "market-create-button",
    "market-hint",
    "auction-panel",
    "auction-count",
    "auction-list",
    "auction-create-button",
    "auction-hint",
    "auction-editor",
    "auction-editor-kicker",
    "auction-editor-title",
    "auction-editor-description",
    "auction-editor-summary",
    "auction-editor-body",
    "auction-editor-submit",
    "auction-editor-cancel",
    "auction-editor-close",
    "auction-editor-footnote",
    "credit-panel",
    "credit-loan-count",
    "credit-availability",
    "credit-loan-list",
    "credit-open-button",
    "credit-hint",
    "credit-editor",
    "credit-editor-kicker",
    "credit-editor-title",
    "credit-editor-description",
    "credit-editor-summary",
    "credit-editor-body",
    "credit-editor-submit",
    "credit-editor-cancel",
    "credit-editor-close",
    "credit-editor-footnote",
    "room-access-prompt",
    "room-access-form",
    "room-access-close",
    "room-access-target",
    "join-room-passphrase",
    "join-passphrase-toggle",
    "room-access-error",
    "room-access-submit",
    "room-access-cancel",
    "join-room-code",
    "join-player-role",
    "join-spectator-role",
    "market-editor",
    "market-editor-grid",
    "market-editor-summary",
    "market-editor-submit",
    "market-editor-cancel",
    "market-editor-close",
    "trade-prompt",
    "trade-prompt-kicker",
    "trade-prompt-title",
    "trade-prompt-description",
    "trade-prompt-terms",
    "trade-prompt-actions",
    "trade-prompt-close",
    "victory-target-label",
    "player-list",
    "latest-event-title",
    "latest-event-detail",
    "forecast-event-card",
    "forecast-event-countdown",
    "forecast-event-title",
    "forecast-event-detail",
    "forecast-active-list",
    "forecast-compact-strip",
    "forecast-compact-title",
    "forecast-compact-active",
    "forecast-live-status",
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

function browserHostname(location = window.location) {
  if (typeof location?.hostname === "string" && location.hostname) {
    return location.hostname.toLowerCase().replace(/\.$/, "");
  }
  const host = typeof location?.host === "string" ? location.host : "";
  if (host.startsWith("[")) {
    const closing = host.indexOf("]");
    return closing > 0 ? host.slice(1, closing).toLowerCase() : "";
  }
  return host.split(":", 1)[0].toLowerCase().replace(/\.$/, "");
}

function isLoopbackBrowserLocation(location = window.location) {
  const hostname = browserHostname(location);
  return hostname === "localhost"
    || hostname === "::1"
    || /^127(?:\.[0-9]{1,3}){3}$/.test(hostname);
}

function roomAccessTransportAllowed(location = window.location) {
  return location?.protocol === "https:" || isLoopbackBrowserLocation(location);
}

function setPasswordVisibility(input, button, visible) {
  if (!input || !button) return;
  input.type = visible ? "text" : "password";
  button.textContent = visible ? "隠す" : "表示";
  button.setAttribute("aria-pressed", visible ? "true" : "false");
  button.setAttribute(
    "aria-label",
    visible ? "部屋パスフレーズを隠す" : "部屋パスフレーズを表示",
  );
}

function togglePasswordVisibility(input, button) {
  setPasswordVisibility(input, button, input?.type === "password");
}

function clearPassphraseInput(input, button = null) {
  if (input) input.value = "";
  if (input && button) setPasswordVisibility(input, button, false);
}

function syncCreateRoomProtection() {
  const inviteOnly = elements["invite-only-room"];
  const openRoom = elements["open-room"];
  const passphraseRoom = elements["protect-room"];
  const fieldset = elements["create-room-protection"];
  const fields = elements["create-passphrase-fields"];
  const input = elements["create-room-passphrase"];
  const note = elements["room-protection-transport-note"];
  if (
    !inviteOnly
    || !openRoom
    || !passphraseRoom
    || !fieldset
    || !fields
    || !input
    || !note
  ) return;
  const transportAllowed = roomAccessTransportAllowed();
  inviteOnly.disabled = !transportAllowed;
  passphraseRoom.disabled = !transportAllowed;
  if (!transportAllowed && (inviteOnly.checked || passphraseRoom.checked)) {
    openRoom.checked = true;
    inviteOnly.checked = false;
    passphraseRoom.checked = false;
  }
  fieldset.classList.toggle("transport-blocked", !transportAllowed);
  const passphraseEnabled = transportAllowed && passphraseRoom.checked;
  fields.hidden = !passphraseEnabled;
  input.required = passphraseEnabled;
  note.textContent = !transportAllowed
    ? "期限付き招待とパスフレーズは平文HTTPでは利用できません。HTTPS/WSSで接続してください。"
    : inviteOnly.checked
      ? "入室にはホストが発行する期限付き・1回限りの招待リンクが必要です。"
      : passphraseEnabled
        ? "パスフレーズは作成時だけ送信し、このアプリには保存しません。"
        : "参加コードを知っている人は、追加の認証なしで参加できます。";
  if (!passphraseEnabled) {
    clearPassphraseInput(input, elements["create-passphrase-toggle"]);
  }
}

function roomAccessAttempt(document, claimedInvitation = null) {
  const roomCode = normalizeInvitationRoomCode(claimedInvitation?.room_code)
    || String(document.get("room_code") || "").trim().toUpperCase();
  const attempt = {
    room_code: roomCode,
    display_name: String(document.get("display_name") || "").trim(),
  };
  // A claimed invitation owns its role on the server.  Omitting the role from
  // this wire payload prevents a modified DOM from overriding the claim.
  if (!claimedInvitation) {
    attempt.role = document.get("role") === "spectator" ? "spectator" : "player";
  }
  return Object.freeze(attempt);
}

function roomAccessAttemptLabel(attempt) {
  const role = attempt?.role === "spectator" ? "観戦" : "参加";
  const code = normalizeInvitationRoomCode(attempt?.room_code) || "------";
  return `部屋 ${code} に${role}`;
}

function roomPassphraseClientError(value) {
  if (typeof value !== "string") return "パスフレーズを入力してください。";
  let normalized;
  try {
    normalized = value.normalize("NFC");
  } catch (_error) {
    return "パスフレーズを確認してください。";
  }
  const characterCount = Array.from(normalized).length;
  if (characterCount < 15 || characterCount > 64) {
    return "パスフレーズは15〜64文字で入力してください。";
  }
  if (!normalized.trim()) return "空白だけのパスフレーズは使用できません。";
  if (/[\u0000-\u001f\u007f-\u009f]/u.test(normalized)) {
    return "制御文字を含むパスフレーズは使用できません。";
  }
  if (typeof TextEncoder === "function" && new TextEncoder().encode(normalized).length > 256) {
    return "パスフレーズの文字数を減らしてください。";
  }
  return null;
}

function setRoomAccessError(message = "", retryAfterSeconds = null) {
  const error = elements["room-access-error"];
  const submit = elements["room-access-submit"];
  if (!error || !submit) return;
  window.clearInterval(state.roomAccessCooldownTimer);
  state.roomAccessCooldownTimer = null;
  state.roomAccessCooldownUntil = null;
  error.textContent = String(message || "");
  error.hidden = !message;
  submit.disabled = false;
  submit.textContent = "入室する";
  if (!Number.isInteger(retryAfterSeconds) || retryAfterSeconds <= 0) return;
  state.roomAccessCooldownUntil = Date.now() + retryAfterSeconds * 1000;
  const update = () => {
    const remaining = Math.max(
      0,
      Math.ceil((state.roomAccessCooldownUntil - Date.now()) / 1000),
    );
    submit.disabled = remaining > 0;
    submit.textContent = remaining > 0 ? `再試行まで ${remaining}秒` : "入室する";
    if (remaining === 0) {
      window.clearInterval(state.roomAccessCooldownTimer);
      state.roomAccessCooldownTimer = null;
      state.roomAccessCooldownUntil = null;
    }
  };
  update();
  state.roomAccessCooldownTimer = window.setInterval(update, 250);
}

function openRoomAccessPrompt(attempt, message = "") {
  if (!attempt || !elements["room-access-prompt"]) return;
  state.pendingRoomAccessAttempt = attempt;
  state.roomAccessPromptOpen = true;
  elements["room-access-target"].textContent = roomAccessAttemptLabel(attempt);
  clearPassphraseInput(
    elements["join-room-passphrase"],
    elements["join-passphrase-toggle"],
  );
  setRoomAccessError(message);
  elements["room-access-prompt"].hidden = false;
  document.body?.classList?.add("modal-open");
  window.requestAnimationFrame(() => elements["join-room-passphrase"].focus?.());
}

function closeRoomAccessPrompt({ clearAttempt = true, restoreFocus = true } = {}) {
  const wasOpen = state.roomAccessPromptOpen;
  state.roomAccessPromptOpen = false;
  window.clearInterval(state.roomAccessCooldownTimer);
  state.roomAccessCooldownTimer = null;
  state.roomAccessCooldownUntil = null;
  if (elements["room-access-prompt"]) elements["room-access-prompt"].hidden = true;
  clearPassphraseInput(
    elements["join-room-passphrase"],
    elements["join-passphrase-toggle"],
  );
  if (elements["room-access-error"]) {
    elements["room-access-error"].textContent = "";
    elements["room-access-error"].hidden = true;
  }
  if (elements["room-access-submit"]) {
    elements["room-access-submit"].disabled = false;
    elements["room-access-submit"].textContent = "入室する";
  }
  if (clearAttempt) state.pendingRoomAccessAttempt = null;
  if (!document.querySelector?.('.modal-backdrop:not([hidden])')) {
    document.body?.classList?.remove("modal-open");
  }
  if (wasOpen && restoreFocus) {
    elements["join-form"]?.elements?.room_code?.focus?.();
  }
}

function sendEphemeralPassphraseMessage(message, input = null, button = null) {
  let request;
  try {
    request = sendMessage(message);
  } finally {
    if (Object.prototype.hasOwnProperty.call(message, "passphrase")) {
      delete message.passphrase;
    }
    clearPassphraseInput(input, button);
  }
  return request;
}

function submitRoomAccessAttempt(attempt, passphrase = null) {
  const message = wireMessage("join_room", { ...attempt });
  if (typeof passphrase === "string" && passphrase) message.passphrase = passphrase;
  return sendEphemeralPassphraseMessage(
    message,
    elements["join-room-passphrase"],
    elements["join-passphrase-toggle"],
  );
}

function retryAfterSecondsFromEvent(event) {
  const value = event?.retry_after_seconds;
  return Number.isInteger(value) && value > 0 && value <= 3600 ? value : null;
}

function handleRoomAccessRequestError(event) {
  if (!state.pendingRoomAccessAttempt) return false;
  if (event.code === ROOM_ACCESS_AUTHENTICATION_ERROR) {
    if (state.claimedInvitation) {
      clearClaimedInvitation({ preserveRoomCode: true });
      closeRoomAccessPrompt({ restoreFocus: false });
      return false;
    }
    openRoomAccessPrompt(
      state.pendingRoomAccessAttempt,
      event.message || "パスフレーズを確認してください。",
    );
    return true;
  }
  if (event.code === ROOM_ACCESS_SECURE_TRANSPORT_ERROR) {
    closeRoomAccessPrompt({ restoreFocus: false });
    return false;
  }
  if (state.roomAccessPromptOpen && /_rate_limited$/.test(event.code || "")) {
    setRoomAccessError(
      event.message || "試行回数が多すぎます。しばらく待ってください。",
      retryAfterSecondsFromEvent(event),
    );
    return true;
  }
  closeRoomAccessPrompt({ restoreFocus: false });
  return false;
}

function handleRoomAccessThrownError(error) {
  if (!state.pendingRoomAccessAttempt) return false;
  if (error?.code === ROOM_ACCESS_AUTHENTICATION_ERROR) {
    if (state.claimedInvitation) {
      clearClaimedInvitation({ preserveRoomCode: true });
      closeRoomAccessPrompt({ restoreFocus: false });
      return false;
    }
    openRoomAccessPrompt(state.pendingRoomAccessAttempt, error.message);
    return true;
  }
  if (error?.code === ROOM_ACCESS_SECURE_TRANSPORT_ERROR) {
    closeRoomAccessPrompt({ restoreFocus: false });
    return false;
  }
  if (state.roomAccessPromptOpen && /_rate_limited$/.test(error?.code || "")) {
    setRoomAccessError(error.message, error.retryAfterSeconds);
    return true;
  }
  closeRoomAccessPrompt({ restoreFocus: false });
  return false;
}

function normalizeInvitationRoomCode(value) {
  if (typeof value !== "string") return null;
  if (!/^[A-Za-z0-9]{6}$/.test(value)) return null;
  const normalized = value.toUpperCase();
  return INVITATION_ROOM_CODE_PATTERN.test(normalized) ? normalized : null;
}

function parseInvitationRoomQuery(search) {
  const params = new URLSearchParams(typeof search === "string" ? search : "");
  const values = params.getAll("room");
  if (!values.length) return { present: false, code: null, canonicalSearch: null };
  if (values.length !== 1) return { present: true, code: null, canonicalSearch: null };
  const code = normalizeInvitationRoomCode(values[0]);
  return {
    present: true,
    code,
    canonicalSearch: code ? `?room=${code}` : null,
  };
}

function parseInvitationTokenFragment(hash) {
  if (typeof hash !== "string" || !hash) {
    return { present: false, token: null };
  }
  const match = /^#invite=([A-Za-z0-9_-]{43})$/.exec(hash);
  return {
    present: true,
    token: match && INVITATION_TOKEN_PATTERN.test(match[1]) ? match[1] : null,
  };
}

function currentBrowserOrigin(location = window.location) {
  if (
    typeof location?.origin === "string"
    && /^https?:\/\/[^/?#]+$/i.test(location.origin)
  ) return location.origin;
  const fallback = `${location?.protocol || ""}//${location?.host || ""}`;
  return /^https?:\/\/[^/?#]+$/i.test(fallback) ? fallback : null;
}

function invitationURL(origin, roomCode) {
  const code = normalizeInvitationRoomCode(roomCode);
  if (!code || typeof origin !== "string" || !/^https?:\/\/[^/?#]+$/i.test(origin)) {
    return null;
  }
  return `${origin}/?room=${code}`;
}

function invitationGrantURL(origin, roomCode, token) {
  const base = invitationURL(origin, roomCode);
  if (!base || typeof token !== "string" || !INVITATION_TOKEN_PATTERN.test(token)) {
    return null;
  }
  return `${base}#invite=${token}`;
}

function replaceInvitationLocation(location, history, search = "") {
  if (typeof history?.replaceState !== "function") return;
  const pathname = typeof location?.pathname === "string" && location.pathname.startsWith("/")
    ? location.pathname
    : "/";
  history.replaceState(null, "", `${pathname}${search}`);
}

function captureInvitationTokenFromLocation(
  location = window.location,
  history = window.history,
) {
  const parsedFragment = parseInvitationTokenFragment(location?.hash);
  if (!parsedFragment.present) {
    return { present: false, room_code: null, token: null };
  }
  const parsedRoom = parseInvitationRoomQuery(location?.search);
  const roomCode = parsedRoom.code;
  // Remove every fragment, including malformed credentials, before starting a
  // browser session or issuing any request.  A valid non-secret room query is
  // retained so an expired link can gracefully fall back to manual entry.
  replaceInvitationLocation(
    location,
    history,
    roomCode ? parsedRoom.canonicalSearch : "",
  );
  return {
    present: true,
    room_code: roomCode,
    token: roomCode ? parsedFragment.token : null,
  };
}

function applyInvitationFromLocation(
  location = window.location,
  history = window.history,
) {
  const parsed = parseInvitationRoomQuery(location?.search);
  if (!parsed.present) return null;
  const form = elements["join-form"];
  const note = elements["invite-prefill-note"];
  if (!parsed.code) {
    replaceInvitationLocation(location, history);
    if (note) note.hidden = true;
    form?.classList?.remove("invitation-target");
    return null;
  }
  if (location?.search !== parsed.canonicalSearch || location?.hash) {
    replaceInvitationLocation(location, history, parsed.canonicalSearch);
  }
  if (!form) return parsed.code;
  form.elements.room_code.value = parsed.code;
  form.classList?.add("invitation-target");
  if (note) note.hidden = false;
  window.requestAnimationFrame(() => {
    form.scrollIntoView?.({ behavior: "auto", block: "center" });
    form.elements.display_name.focus?.({ preventScroll: true });
  });
  return parsed.code;
}

function hideInvitationFallback() {
  if (elements["invite-link-fallback"]) {
    elements["invite-link-fallback"].hidden = true;
  }
  if (elements["invite-link-value"]) elements["invite-link-value"].value = "";
}

function showInvitationFallback(url) {
  const container = elements["invite-link-fallback"];
  const input = elements["invite-link-value"];
  if (!container || !input || typeof url !== "string") return;
  input.value = url;
  container.hidden = false;
  input.focus?.();
  input.select?.();
}

function currentInvitationURL() {
  return invitationURL(
    currentBrowserOrigin(),
    state.lobby?.room_code,
  );
}

async function copyInvitationLink() {
  const url = currentInvitationURL();
  if (!url) return false;
  try {
    await navigator.clipboard.writeText(url);
    hideInvitationFallback();
    showToast("招待リンクをコピーしました。");
    return true;
  } catch (_error) {
    showInvitationFallback(url);
    showToast("招待リンクを表示しました。手動でコピーしてください。", true);
    return false;
  }
}

function normalizeInvitationExpiry(value) {
  return Number.isSafeInteger(value) && value > 0 ? value : null;
}

function invitationPublicMetadata(
  value,
  { requireToken = false, requireInvitationId = false } = {},
) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowedKeys = new Set([
    "room_code",
    "role",
    "issued_at_ms",
    "expires_at_ms",
    ...(requireToken ? ["token"] : []),
    ...(requireInvitationId ? ["invitation_id"] : []),
  ]);
  if (!Object.keys(value).every((key) => allowedKeys.has(key))) return null;
  const roomCode = normalizeInvitationRoomCode(value.room_code);
  const role = INVITATION_ROLES.has(value.role) ? value.role : null;
  const expiresAtMs = normalizeInvitationExpiry(value.expires_at_ms);
  const token = typeof value.token === "string" && INVITATION_TOKEN_PATTERN.test(value.token)
    ? value.token
    : null;
  const invitationId = normalizeInvitationId(value.invitation_id);
  if (
    !roomCode
    || !role
    || !expiresAtMs
    || (requireToken && !token)
    || (requireInvitationId && !invitationId)
  ) return null;
  return {
    room_code: roomCode,
    role,
    expires_at_ms: expiresAtMs,
    ...(requireInvitationId ? { invitation_id: invitationId } : {}),
    ...(requireToken ? { token } : {}),
  };
}

function invitationExpiryLabel(expiresAtMs) {
  const timestamp = normalizeInvitationExpiry(expiresAtMs);
  if (!timestamp) return "1時間・1回限り";
  try {
    const formatted = new Intl.DateTimeFormat("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(timestamp));
    return `${formatted}まで・1回限り`;
  } catch (_error) {
    return "1時間・1回限り";
  }
}

function normalizeInvitationId(value) {
  return typeof value === "string" && INVITATION_ID_PATTERN.test(value) ? value : null;
}

function activeInvitationMetadata(value, roomCode) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowedKeys = new Set([
    "invitation_id",
    "room_code",
    "role",
    "issued_at_ms",
    "expires_at_ms",
  ]);
  if (!Object.keys(value).every((key) => allowedKeys.has(key))) return null;
  const invitationId = normalizeInvitationId(value.invitation_id);
  const normalizedRoomCode = normalizeInvitationRoomCode(value.room_code);
  const normalizedExpectedRoomCode = normalizeInvitationRoomCode(roomCode);
  const role = INVITATION_ROLES.has(value.role) ? value.role : null;
  const issuedAtMs = normalizeInvitationExpiry(value.issued_at_ms);
  const expiresAtMs = normalizeInvitationExpiry(value.expires_at_ms);
  if (
    !invitationId
    || !normalizedRoomCode
    || normalizedRoomCode !== normalizedExpectedRoomCode
    || !role
    || !issuedAtMs
    || !expiresAtMs
    || expiresAtMs <= issuedAtMs
  ) return null;
  // The active-list state deliberately keeps only non-secret fields needed by
  // the controls.  Tokens and digests are never accepted by this adapter.
  return Object.freeze({
    invitation_id: invitationId,
    role,
    expires_at_ms: expiresAtMs,
  });
}

function activeInvitationList(document, roomCode) {
  if (!document || typeof document !== "object" || Array.isArray(document)) return null;
  const allowedKeys = new Set(["api_version", "revoked_count", "invitations"]);
  if (
    !Object.keys(document).every((key) => allowedKeys.has(key))
    || document.api_version !== 1
    || (
      Object.prototype.hasOwnProperty.call(document, "revoked_count")
      && (!Number.isSafeInteger(document.revoked_count) || document.revoked_count < 0)
    )
  ) return null;
  if (!Array.isArray(document.invitations) || document.invitations.length > 32) return null;
  const invitations = [];
  const invitationIds = new Set();
  for (const value of document.invitations) {
    const invitation = activeInvitationMetadata(value, roomCode);
    if (!invitation || invitationIds.has(invitation.invitation_id)) return null;
    invitationIds.add(invitation.invitation_id);
    invitations.push(invitation);
  }
  return Object.freeze(invitations.sort((left, right) => (
    left.expires_at_ms - right.expires_at_ms
    || left.invitation_id.localeCompare(right.invitation_id)
  )));
}

function activeInvitationExpiryLabel(expiresAtMs) {
  const timestamp = normalizeInvitationExpiry(expiresAtMs);
  if (!timestamp) return "有効期限不明";
  try {
    const formatted = new Intl.DateTimeFormat("ja-JP", {
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(timestamp));
    return `${formatted}まで`;
  } catch (_error) {
    return "有効期限不明";
  }
}

function currentInvitationListSignature(lobby = state.lobby) {
  const roomCode = normalizeInvitationRoomCode(lobby?.room_code);
  if (!roomCode) return null;
  return [
    roomCode,
    lobby?.phase || "unknown",
    Number.isInteger(lobby?.player_members) ? lobby.player_members : "?",
    Number.isInteger(lobby?.spectators) ? lobby.spectators : "?",
  ].join(":");
}

function clearActiveInvitationState({ roomCode = null } = {}) {
  state.invitationListRequestId += 1;
  state.activeInvitations = [];
  state.invitationListRoomCode = normalizeInvitationRoomCode(roomCode);
  state.invitationListSignature = null;
  state.invitationListLoading = false;
  state.invitationListError = null;
  state.invitationMutationPending = false;
}

function renderActiveInvitationManager() {
  const manager = elements["invitation-manager"];
  const list = elements["invitation-list"];
  const status = elements["invitation-manager-status"];
  const count = elements["invitation-count"];
  const refreshButton = elements["refresh-invitations"];
  const revokeAllButton = elements["revoke-all-invitations"];
  if (!manager || !list || !status || !count || !refreshButton || !revokeAllButton) return;
  const isHost = state.welcome?.role === "host";
  const canManage = isHost && roomAccessTransportAllowed();
  manager.hidden = !canManage;
  if (!canManage) {
    list.replaceChildren();
    return;
  }
  const canIssue = Boolean(
    normalizeInvitationRoomCode(state.lobby?.room_code)
    && state.lobby?.phase === "waiting"
    && !state.invitationListLoading
    && !state.invitationMutationPending
    && !state.invitationCopyPending
  );
  elements["copy-player-invite-link"].disabled = !canIssue || Boolean(state.lobby?.full);
  elements["copy-spectator-invite-link"].disabled = !canIssue;

  list.replaceChildren();
  for (const invitation of state.activeInvitations) {
    const item = document.createElement("li");
    item.className = "invitation-list-item";
    const summary = document.createElement("div");
    summary.className = "invitation-list-summary";
    const role = document.createElement("strong");
    const isSpectator = invitation.role === "spectator";
    role.textContent = isSpectator ? "観戦者用" : "プレイヤー用";
    const expiry = document.createElement("span");
    const expiryLabel = activeInvitationExpiryLabel(invitation.expires_at_ms);
    expiry.textContent = `未使用・${expiryLabel}`;
    summary.append(role, expiry);
    const revokeButton = document.createElement("button");
    revokeButton.type = "button";
    revokeButton.textContent = "取り消す";
    revokeButton.setAttribute(
      "aria-label",
      `${isSpectator ? "観戦者" : "プレイヤー"}用招待（${expiryLabel}）を取り消す`,
    );
    revokeButton.disabled = (
      state.invitationListLoading
      || state.invitationMutationPending
      || state.invitationCopyPending
    );
    revokeButton.addEventListener("click", () => {
      revokeActiveInvitations({ invitationId: invitation.invitation_id });
    });
    item.append(summary, revokeButton);
    list.append(item);
  }

  count.textContent = `${state.activeInvitations.length}件`;
  if (state.invitationListLoading) {
    status.textContent = "未使用の招待を更新しています。";
  } else if (state.invitationListError) {
    status.textContent = state.invitationListError;
  } else if (state.activeInvitations.length) {
    status.textContent = "共有前のリンクや不要になった招待は、ここから無効にできます。";
  } else {
    status.textContent = "有効な未使用招待はありません。";
  }
  manager.setAttribute("aria-busy", state.invitationListLoading ? "true" : "false");
  refreshButton.disabled = state.invitationListLoading || state.invitationMutationPending;
  revokeAllButton.disabled = (
    state.activeInvitations.length === 0
    || state.invitationListLoading
    || state.invitationMutationPending
  );
}

async function loadActiveInvitations({ request = api, force = false, signature = null } = {}) {
  const roomCode = normalizeInvitationRoomCode(state.lobby?.room_code);
  const isHost = state.welcome?.role === "host";
  if (!roomCode || !isHost) return false;
  const currentSignature = signature || currentInvitationListSignature();
  if (
    !force
    && (state.invitationListLoading || state.invitationListSignature === currentSignature)
  ) return false;
  const requestId = state.invitationListRequestId + 1;
  state.invitationListRequestId = requestId;
  state.invitationListLoading = true;
  state.invitationListError = null;
  renderActiveInvitationManager();
  try {
    const response = await request("/api/invitations/list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const invitations = activeInvitationList(response, roomCode);
    if (!invitations) throw new Error("invalid invitation list response");
    if (
      requestId !== state.invitationListRequestId
      || roomCode !== normalizeInvitationRoomCode(state.lobby?.room_code)
      || state.welcome?.role !== "host"
    ) return false;
    state.activeInvitations = invitations;
    state.invitationListSignature = currentSignature;
    state.invitationListError = null;
    return true;
  } catch (_error) {
    if (requestId !== state.invitationListRequestId) return false;
    state.invitationListSignature = currentSignature;
    state.invitationListError = "招待一覧を更新できませんでした。「更新」でもう一度お試しください。";
    return false;
  } finally {
    if (requestId === state.invitationListRequestId) {
      state.invitationListLoading = false;
      renderActiveInvitationManager();
    }
  }
}

function ensureActiveInvitationList() {
  const roomCode = normalizeInvitationRoomCode(state.lobby?.room_code);
  if (state.welcome?.role !== "host" || !roomCode || !roomAccessTransportAllowed()) {
    if (state.invitationListRoomCode !== null || state.activeInvitations.length) {
      clearActiveInvitationState();
    }
    renderActiveInvitationManager();
    return;
  }
  if (state.invitationListRoomCode !== roomCode) {
    clearActiveInvitationState({ roomCode });
  }
  renderActiveInvitationManager();
  const signature = currentInvitationListSignature();
  if (!state.invitationListLoading && state.invitationListSignature !== signature) {
    void loadActiveInvitations({ signature });
  }
}

async function revokeActiveInvitations({
  invitationId = null,
  all = false,
  request = api,
  announce = true,
} = {}) {
  const normalizedId = normalizeInvitationId(invitationId);
  if ((all && invitationId !== null) || (!all && !normalizedId)) return false;
  if (state.invitationListLoading || state.invitationMutationPending) return false;
  state.invitationMutationPending = true;
  renderActiveInvitationManager();
  let response = null;
  try {
    response = await request("/api/invitations", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(all ? { all: true } : { invitation_id: normalizedId }),
    });
    const roomCode = normalizeInvitationRoomCode(state.lobby?.room_code);
    const invitations = roomCode ? activeInvitationList(response, roomCode) : null;
    if (roomCode && !invitations) throw new Error("invalid invitation revoke response");
    if (invitations) state.activeInvitations = invitations;
    state.invitationListSignature = currentInvitationListSignature();
    state.invitationListError = null;
    if (announce) {
      const count = Number.isSafeInteger(response?.revoked_count) ? response.revoked_count : 0;
      showToast(count > 0 ? `${count}件の招待を取り消しました。` : "招待はすでに使用済みか期限切れです。");
    }
    return true;
  } catch (_error) {
    if (announce) showToast("招待を取り消せませんでした。更新して状態を確認してください。", true);
    state.invitationListError = "取り消し結果を確認できませんでした。「更新」でもう一度お試しください。";
    return false;
  } finally {
    response = null;
    state.invitationMutationPending = false;
    renderActiveInvitationManager();
  }
}

function syncClaimedInvitationForm() {
  const form = elements["join-form"];
  const roomCodeInput = elements["join-room-code"];
  const playerRole = elements["join-player-role"];
  const spectatorRole = elements["join-spectator-role"];
  const note = elements["invite-prefill-note"];
  const decline = elements["decline-claimed-invitation"];
  if (!form || !roomCodeInput || !playerRole || !spectatorRole || !note) return;
  const invitation = state.claimedInvitation;
  const claimed = Boolean(invitation);
  roomCodeInput.readOnly = claimed;
  playerRole.disabled = claimed;
  spectatorRole.disabled = claimed;
  form.classList.toggle("invitation-claimed", claimed);
  if (decline) decline.hidden = !claimed;
  if (!claimed) return;
  roomCodeInput.value = invitation.room_code;
  playerRole.checked = invitation.role === "player";
  spectatorRole.checked = invitation.role === "spectator";
  const roleLabel = invitation.role === "spectator" ? "観戦者" : "プレイヤー";
  note.textContent = `${roleLabel}用の期限付き招待を確認しました（${invitationExpiryLabel(invitation.expires_at_ms)}）。表示名を確認して参加してください。`;
  note.hidden = false;
  form.classList.add("invitation-target");
}

function applyClaimedInvitation(value) {
  const invitation = invitationPublicMetadata(value);
  if (!invitation) return false;
  state.claimedInvitation = Object.freeze(invitation);
  syncClaimedInvitationForm();
  window.requestAnimationFrame(() => {
    elements["join-form"]?.scrollIntoView?.({ behavior: "auto", block: "center" });
    elements["join-form"]?.elements?.display_name?.focus?.({ preventScroll: true });
  });
  return true;
}

function clearClaimedInvitation({ preserveRoomCode = true } = {}) {
  const previous = state.claimedInvitation;
  state.claimedInvitation = null;
  const form = elements["join-form"];
  const roomCodeInput = elements["join-room-code"];
  const playerRole = elements["join-player-role"];
  const spectatorRole = elements["join-spectator-role"];
  const note = elements["invite-prefill-note"];
  if (roomCodeInput) {
    roomCodeInput.readOnly = false;
    if (!preserveRoomCode && previous?.room_code === roomCodeInput.value) {
      roomCodeInput.value = "";
    }
  }
  if (playerRole) playerRole.disabled = false;
  if (spectatorRole) spectatorRole.disabled = false;
  form?.classList?.remove("invitation-claimed");
  if (note && previous) {
    note.textContent = "招待コードを入力しました。表示名と参加方法を確認してから参加してください。";
    note.hidden = !normalizeInvitationRoomCode(roomCodeInput?.value);
  }
  if (elements["decline-claimed-invitation"]) {
    elements["decline-claimed-invitation"].hidden = true;
  }
}

async function claimCapturedInvitation(captured, request = api) {
  if (
    !captured
    || !normalizeInvitationRoomCode(captured.room_code)
    || typeof captured.token !== "string"
    || !INVITATION_TOKEN_PATTERN.test(captured.token)
  ) return null;
  let token = captured.token;
  let body = JSON.stringify({ room_code: captured.room_code, token });
  // The captured object is the only caller-owned reference.  Clear it before
  // awaiting network I/O; the request body is discarded in finally below.
  captured.token = null;
  try {
    const document = await request("/api/invitations/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
    });
    return invitationPublicMetadata(document?.invitation);
  } finally {
    token = null;
    body = null;
  }
}

async function resumeFriendInvitationFromCookie(request = api) {
  const document = await request("/api/invitations/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  return invitationPublicMetadata(document?.invitation);
}

async function declineClaimedInvitation(request = api) {
  if (!state.claimedInvitation) return false;
  const button = elements["decline-claimed-invitation"];
  if (button) button.disabled = true;
  try {
    await request("/api/invitations/claim", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    clearClaimedInvitation({ preserveRoomCode: true });
    showToast("招待を使わず、通常の参加方法へ戻りました。");
    return true;
  } catch (_error) {
    showToast("招待を破棄できませんでした。通信を確認してもう一度お試しください。", true);
    return false;
  } finally {
    if (button) button.disabled = false;
  }
}

async function deliverInvitationURL(url, navigatorObject = navigator) {
  if (typeof url !== "string") return null;
  if (typeof navigatorObject?.clipboard?.writeText === "function") {
    try {
      await navigatorObject.clipboard.writeText(url);
      return "clipboard";
    } catch (_error) {
      // Web Share is the safe fallback: unlike a manual field, it does not
      // leave the bearer URL rendered in the document.
    }
  }
  if (typeof navigatorObject?.share !== "function") return null;
  let sharePayload = {
    title: "カタン風ゲームへの招待",
    text: "1回限りの期限付き招待です。",
    url,
  };
  try {
    await navigatorObject.share(sharePayload);
    return "share";
  } catch (_error) {
    // User cancellation is intentionally treated like every other failed
    // hand-off so the freshly issued bearer is revoked immediately.
    return null;
  } finally {
    sharePayload = null;
  }
}

async function copyRoleInvitationLink(role, request = api) {
  if (
    !INVITATION_ROLES.has(role)
    || state.invitationCopyPending
    || state.invitationListLoading
    || state.invitationMutationPending
  ) return false;
  if (!roomAccessTransportAllowed()) {
    showToast("期限付き招待はHTTPS/WSSで接続して発行してください。", true);
    return false;
  }
  state.invitationCopyPending = true;
  let document = null;
  let invitationId = null;
  let token = null;
  let url = null;
  let delivery = null;
  try {
    document = await request("/api/invitations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    invitationId = normalizeInvitationId(document?.invitation?.invitation_id);
    const invitation = invitationPublicMetadata(document?.invitation, {
      requireToken: true,
      requireInvitationId: true,
    });
    if (!invitation || invitation.role !== role) {
      throw new Error("招待リンクを安全に発行できませんでした。");
    }
    token = invitation.token;
    url = invitationGrantURL(currentBrowserOrigin(), invitation.room_code, token);
    if (!url) throw new Error("招待リンクを安全に作成できませんでした。");
    delivery = await deliverInvitationURL(url);
    if (!delivery) {
      const revoked = await revokeActiveInvitations({
        invitationId,
        request,
        announce: false,
      });
      hideInvitationFallback();
      showToast(
        revoked
          ? "共有できなかったため、発行した招待を自動で取り消しました。"
          : "共有できませんでした。招待一覧を更新し、発行された招待を取り消してください。",
        true,
      );
      return false;
    }
    hideInvitationFallback();
    const label = role === "spectator" ? "観戦招待" : "プレイヤー招待";
    showToast(
      `${label}を${delivery === "share" ? "共有しました" : "コピーしました"}（${invitationExpiryLabel(invitation.expires_at_ms)}）。`,
    );
    await loadActiveInvitations({ request, force: true });
    return true;
  } catch (_error) {
    if (invitationId && !delivery) {
      await revokeActiveInvitations({ invitationId, request, announce: false });
    }
    hideInvitationFallback();
    showToast("招待リンクを発行またはコピーできませんでした。もう一度お試しください。", true);
    return false;
  } finally {
    if (document?.invitation && Object.prototype.hasOwnProperty.call(document.invitation, "token")) {
      document.invitation.token = null;
    }
    invitationId = null;
    token = null;
    url = null;
    delivery = null;
    document = null;
    state.invitationCopyPending = false;
    renderActiveInvitationManager();
  }
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
      kind: "frontier",
      options: { ...EXPANDED_FRONTIER_OPTIONS },
    };
  }
  if (kind === "frontier_legacy") {
    return {
      version: 1,
      kind: "frontier",
      options: { ...DEFAULT_FRONTIER_OPTIONS },
    };
  }
  if (kind === "trade2") {
    return {
      version: 1,
      kind: "trade2",
      options: { ...DEFAULT_TRADE2_OPTIONS },
    };
  }
  if (kind === "credit") {
    return {
      version: 1,
      kind: "credit",
      options: { ...DEFAULT_CREDIT_OPTIONS },
    };
  }
  if (kind === "composite") {
    return {
      version: 1,
      kind: "composite",
      options: { catalog: COMPOSITE_EVENTS_ECONOMY_CATALOG },
    };
  }
  if (kind === "grand_campaign") {
    return {
      version: 1,
      kind: "composite",
      options: { catalog: COMPOSITE_GRAND_CAMPAIGN_CATALOG },
    };
  }
  return { version: 1, kind: "standard", options: {} };
}

function variantLabel(variant) {
  if (variant?.kind === "forecast_events") return "予告イベント";
  if (variant?.kind === "trade2") {
    return variant.options?.catalog === "market_auction_v1"
      ? "交易2.0・市場と公開競売"
      : "交易2.0・常設市場";
  }
  if (variant?.kind === "credit") return "資源信用・借入と返済";
  if (variant?.kind === "composite") {
    const catalog = variant.options?.catalog ?? variant.public?.catalog;
    if (catalog === COMPOSITE_GRAND_CAMPAIGN_CATALOG) {
      return "グランドキャンペーン（全部入り）";
    }
    return catalog === COMPOSITE_EVENTS_ECONOMY_CATALOG
      ? "イベント＆経済（複合）"
      : "複合モード";
  }
  if (variant?.kind === "frontier") {
    if (variant.options?.catalog === "outer_ring_37_v1") {
      return "フロンティア探索・37タイル";
    }
    if (variant.options && !("catalog" in variant.options)) {
      return "フロンティア探索・19タイル";
    }
    return "フロンティア探索";
  }
  return "通常ルール";
}

function variantIncludesComponent(variant, kind) {
  if (!variant || typeof variant !== "object" || typeof kind !== "string") return false;
  if (variant.kind === kind) return true;
  if (variant.kind !== "composite") return false;
  const catalog = variant.public?.catalog ?? variant.options?.catalog;
  return COMPOSITE_COMPONENTS[catalog]?.includes(kind) === true;
}

function isCoreV2ForecastPublicState(component) {
  if (!component || typeof component !== "object" || Array.isArray(component)) return false;
  if (Object.prototype.hasOwnProperty.call(component, "catalog")) return false;
  const keys = Object.keys(component).sort();
  if (
    keys.length !== 4
    || keys[0] !== "active_effects"
    || keys[1] !== "completed_turns"
    || keys[2] !== "forecast"
    || keys[3] !== "resolved_count"
  ) return false;
  const forecast = component.forecast;
  return Boolean(
    forecast
    && typeof forecast === "object"
    && !Array.isArray(forecast)
    && Object.prototype.hasOwnProperty.call(forecast, "parameters"),
  );
}

function variantComponentPublic(variantState, kind) {
  if (!variantIncludesComponent(variantState, kind)) return null;
  const publicState = variantState?.public;
  if (!publicState || typeof publicState !== "object" || Array.isArray(publicState)) return null;
  if (variantState.kind === kind) return publicState;
  const catalog = publicState.catalog;
  const expectedCatalog = COMPOSITE_COMPONENT_CATALOGS[catalog]?.[kind];
  if (!expectedCatalog) return null;
  const component = publicState.components?.[kind];
  if (!component || typeof component !== "object" || Array.isArray(component)) return null;
  // core_v2 intentionally has no root catalog field; its forecast document
  // shape is the catalog discriminator.  Other composite children publish an
  // explicit catalog and continue to require an exact match.
  if (expectedCatalog === "core_v2") {
    return isCoreV2ForecastPublicState(component) ? component : null;
  }
  return component.catalog === expectedCatalog ? component : null;
}

function frontierPresentation(variantState, totalTiles = null) {
  const publicState = variantComponentPublic(variantState, "frontier");
  if (!publicState) return { visible: false };
  const revealed = Array.isArray(publicState.revealed_tiles)
    ? publicState.revealed_tiles.length
    : 0;
  const discoveries = Number.isInteger(publicState.discovery_count)
    ? publicState.discovery_count
    : 0;
  const catalogTotal = publicState.catalog === "outer_ring_37_v1" ? 37 : 19;
  const total = Number.isInteger(totalTiles) && totalTiles >= revealed
    ? totalTiles
    : Math.max(catalogTotal, revealed);
  return {
    visible: true,
    count: `${revealed} / ${total} 公開`,
    detail: discoveries > 0
      ? `街道から${discoveries}タイルを発見。霧に接する街道で探索を続けられます。`
      : "外周は未探索です。霧に接する街道を建設すると資源・数字・港が公開されます。",
  };
}

function campaignHarborBlockadePublicPlan(parameters) {
  const plan = parameters?.campaign_plan;
  if (
    !plan
    || typeof plan !== "object"
    || Array.isArray(plan)
    || plan.format !== "catan-grand-campaign-plan"
    || plan.version !== 1
    || plan.catalog !== COMPOSITE_GRAND_CAMPAIGN_CATALOG
    || plan.event_id !== "harbor_blockade_v1"
    || !Number.isInteger(plan.resolution_number)
    || plan.resolution_number < 0
    || !Array.isArray(plan.eligible_harbor_ids)
  ) return null;
  const eligible = plan.eligible_harbor_ids;
  if (!eligible.every((harborId) => /^harbor-(0|[1-9][0-9]?)$/.test(harborId))) {
    return null;
  }
  const outcome = plan.outcome;
  if (!outcome || typeof outcome !== "object" || Array.isArray(outcome)) return null;
  return { eligible, outcome };
}

function campaignHarborBlockadeTargetId(parameters) {
  const plan = campaignHarborBlockadePublicPlan(parameters);
  if (!plan) return null;
  const { eligible, outcome } = plan;
  if (
    outcome.kind === "target"
    && /^harbor-(0|[1-9][0-9]?)$/.test(outcome.harbor_id || "")
    && eligible.includes(outcome.harbor_id)
  ) {
    return outcome.harbor_id;
  }
  return null;
}

function campaignHarborBlockadeLabel(parameters) {
  const targetId = campaignHarborBlockadeTargetId(parameters);
  if (targetId) {
    return `対象: 交換所 #${Number(targetId.split("-")[1]) + 1}`;
  }
  const plan = campaignHarborBlockadePublicPlan(parameters);
  if (!plan) return "";
  const { eligible, outcome } = plan;
  if (
    outcome.kind === "skip"
    && outcome.reason === "no_revealed_harbors"
    && eligible.length === 0
  ) return CAMPAIGN_BLOCKADE_SKIP_LABEL;
  return "";
}

function forecastHarborTargetId(eventId, parameters = {}, catalog = null) {
  if (eventId !== "harbor_blockade_v1") return null;
  if (catalog === CAMPAIGN_FORECAST_CATALOG) {
    return campaignHarborBlockadeTargetId(parameters);
  }
  if (catalog !== null) return null;
  return /^harbor-[0-8]$/.test(parameters?.harbor_id || "")
    ? parameters.harbor_id
    : null;
}

function forecastAnnouncedHarborId(variantState) {
  const publicState = variantComponentPublic(variantState, "forecast_events");
  const forecast = publicState?.forecast;
  if (!forecast || typeof forecast !== "object" || Array.isArray(forecast)) return null;
  return forecastHarborTargetId(
    forecast.event_id,
    forecast.parameters,
    publicState.catalog || null,
  );
}

function forecastParameterLabel(eventId, parameters = {}, catalog = null) {
  if (eventId === "harbor_blockade_v1") {
    if (catalog === CAMPAIGN_FORECAST_CATALOG) {
      return campaignHarborBlockadeLabel(parameters);
    }
    const targetId = forecastHarborTargetId(eventId, parameters, catalog);
    if (targetId) {
      return `対象: 交換所 #${Number(targetId.split("-")[1]) + 1}`;
    }
  }
  if (eventId === "bandit_raid_v1" && Number.isInteger(parameters.target_number)) {
    return `対象数字: ${parameters.target_number}`;
  }
  if (eventId === "earthquake_v1" && Number.isInteger(parameters.sector)) {
    return `対象: ${FORECAST_SECTOR_LABELS[parameters.sector] || "不明な方角"}`;
  }
  return "";
}

function forecastActiveTiming(effect, completed) {
  if (Number.isInteger(effect?.expires_turn)) {
    return `残り${Math.max(0, effect.expires_turn - completed)}手番`;
  }
  if (effect?.event_id === "construction_boom_v1") return "次の有料街道まで";
  if (effect?.event_id === "wheat_harvest_v1") return "次の麦生産まで";
  return "解決中";
}

function forecastEventPresentation(variantState) {
  const publicState = variantComponentPublic(variantState, "forecast_events");
  if (!publicState) return { visible: false };
  const catalog = publicState.catalog || null;
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
  const parameterLabel = forecastParameterLabel(
    forecast.event_id,
    forecast.parameters,
    catalog,
  );
  const active = Array.isArray(publicState.active_effects)
    ? publicState.active_effects.map((effect) => {
      const definition = FORECAST_EVENT_PRESENTATION[effect?.event_id];
      const parts = [definition?.active || "未対応イベント"];
      const target = forecastParameterLabel(effect?.event_id, effect?.parameters, catalog);
      if (target) parts.push(target);
      parts.push(forecastActiveTiming(effect, completed));
      return parts.join("・");
    })
    : [];
  return {
    visible: true,
    title: event.title,
    description: parameterLabel === CAMPAIGN_BLOCKADE_SKIP_LABEL
      ? `${parameterLabel}。`
      : parameterLabel
      ? `${parameterLabel}。${event.description}`
      : event.description,
    countdown: remaining === 0 ? "発動処理中" : `あと${remaining}手番`,
    active,
    compact: [
      event.title,
      remaining === 0 ? "発動処理中" : `あと${remaining}手番`,
      parameterLabel,
    ].filter(Boolean).join("・"),
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
    error.retryAfterSeconds = retryAfterSecondsFromEvent(document.error);
    throw error;
  }
  return document;
}

function hasSessionWelcome(document) {
  return Array.isArray(document?.events)
    && document.events.some((event) => event?.type === "session_welcome");
}

let resumeConfirmationInFlight = null;

async function confirmRoomResumeAfterWelcome(document, request = api) {
  if (!hasSessionWelcome(document)) return false;
  if (resumeConfirmationInFlight) return resumeConfirmationInFlight;

  const confirmation = (async () => {
    try {
      const result = await request("/api/resume/confirm", { method: "POST" });
      processEvents(result?.events || [], { animateLive: false });
      return result?.confirmed === true;
    } catch (_error) {
      // The replacement cookie has already been applied to the response that
      // delivered session_welcome. Confirmation is best-effort: retaining a
      // restored game is safer than resetting it after a transient failure.
      return false;
    }
  })();
  resumeConfirmationInFlight = confirmation;
  try {
    return await confirmation;
  } finally {
    if (resumeConfirmationInFlight === confirmation) {
      resumeConfirmationInFlight = null;
    }
  }
}

async function startBrowserSession({
  allowInvitationResume = true,
  allowRoomResume = true,
  resetStaleRoom = false,
} = {}) {
  if (resetStaleRoom) resetRoomState(false);
  const document = await api("/api/session", { method: "POST" });
  processEvents(document.events || [], { animateLive: false });
  await confirmRoomResumeAfterWelcome(document);
  let restoredInvitation = null;
  if (
    !state.welcome
    && allowInvitationResume
    && roomAccessTransportAllowed()
  ) {
    restoredInvitation = await resumeFriendInvitationFromCookie();
    if (restoredInvitation) applyClaimedInvitation(restoredInvitation);
  }
  if (!state.welcome && !restoredInvitation && allowRoomResume) {
    await resumeRoomFromCookie();
  }
  connectWebSocket();
  setConnection("online", "ローカルサーバー接続中");
}

function messageRequiresHttpTransport(message) {
  return HTTP_ONLY_ROOM_MESSAGE_TYPES.has(message?.type);
}

async function sendMessage(message) {
  if (state.socketReady && !messageRequiresHttpTransport(message)) {
    return sendSocketMessage(message);
  }
  const document = await api("/api/message", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message),
  });
  processEvents(document.events || [], {
    animateLive: message.type !== "reconnect_room",
  });
  if (ROOM_RESUME_COOKIE_MESSAGE_TYPES.has(message.type)) {
    await confirmRoomResumeAfterWelcome(document);
  }
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
    const pending = document.kind === "response"
      ? state.socketRequests.shift()
      : null;
    if (document.error) {
      const error = new Error(document.error.message || "WebSocket操作に失敗しました。");
      error.code = document.error.code || "socket_error";
      error.retryAfterSeconds = retryAfterSecondsFromEvent(document.error);
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
        await startBrowserSession({ resetStaleRoom: true });
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
        state.welcome = { ...event };
        delete state.welcome.reconnect_token;
        clearClaimedInvitation({ preserveRoomCode: true });
        state.nextSequence = Number.isInteger(event.next_sequence)
          ? event.next_sequence
          : 0;
        if (state.pendingRoomAccessAttempt || state.roomAccessPromptOpen) {
          closeRoomAccessPrompt({ restoreFocus: false });
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
        if (
          Number.isInteger(index)
          && event.snapshot
          && isExpectedReplayFrame(event, index)
        ) {
          const enteringReplay = state.replayIndex === null;
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
          focusBoardAfterRender = focusBoardAfterRender || enteringReplay;
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
        if (!handleRoomAccessRequestError(event)) {
          showToast(event.message || "操作を処理できませんでした。", true);
        }
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
  if (focusBoardAfterRender && state.replayIndex !== null) focusGameBoard();
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

async function resumeRoomFromCookie() {
  try {
    const document = await api("/api/resume", { method: "POST" });
    const events = document.events || [];
    processEvents(events, { animateLive: false });
    await confirmRoomResumeAfterWelcome(document);
    return events.some((event) => event.type === "session_welcome");
  } catch (_error) {
    return false;
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
  invalidateReplayRequest();
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
  state.tradePromptSignature = null;
  state.tradePromptDismissed.clear();
  state.tradePromptNotified.clear();
  state.developmentInventoryOpen = null;
  state.forecastActiveSignature = null;
  state.marketDraft = null;
  state.auctionDraft = null;
  state.creditDraft = null;
  clearActiveInvitationState();
  clearClaimedInvitation({ preserveRoomCode: false });
  hideTradePrompt();
  closeMarketEditor({ restoreFocus: false });
  closeAuctionEditor({ restoreFocus: false });
  closeCreditEditor({ restoreFocus: false });
  closeRoomAccessPrompt({ restoreFocus: false });
  clearPendingBoardAnimations();
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
  if (!hasGame) {
    hideTradePrompt();
    closeMarketEditor({ restoreFocus: false });
    closeAuctionEditor({ restoreFocus: false });
    closeCreditEditor({ restoreFocus: false });
  }
  syncAudioScene(nextView);
  if (state.currentView !== nextView) {
    state.currentView = nextView;
    window.requestAnimationFrame(() => window.scrollTo(0, 0));
  }
}

function renderLobby() {
  const lobby = state.lobby;
  const roomCode = normalizeInvitationRoomCode(lobby.room_code);
  const isPlayer = Number.isInteger(state.welcome?.seat_index);
  const isHost = state.welcome?.role === "host";
  const secureInvitations = roomAccessTransportAllowed();
  const invitationAvailable = Boolean(
    isHost
    && secureInvitations
    && roomCode
    && !state.invitationCopyPending
    && !state.invitationListLoading
    && !state.invitationMutationPending
    && lobby.phase === "waiting",
  );
  elements["lobby-room-code"].textContent = roomCode || "------";
  elements["copy-room-code"].disabled = !roomCode;
  elements["copy-player-invite-link"].hidden = !isHost;
  elements["copy-spectator-invite-link"].hidden = !isHost;
  elements["copy-player-invite-link"].disabled = !invitationAvailable || lobby.full;
  elements["copy-spectator-invite-link"].disabled = !invitationAvailable;
  elements["invitation-guidance"].hidden = !isHost;
  elements["invitation-guidance"].classList.toggle(
    "transport-warning",
    isHost && !secureInvitations,
  );
  elements["invitation-guidance"].textContent = secureInvitations
    ? "招待リンクは1時間有効・1回限りです。相手ごとに新しいリンクを発行してください。"
    : "期限付き招待の発行にはHTTPS/WSS接続が必要です。";
  if (elements["invite-link-fallback"].dataset.roomCode !== roomCode) {
    hideInvitationFallback();
    elements["invite-link-fallback"].dataset.roomCode = roomCode || "";
  }
  ensureActiveInvitationList();
  elements["lobby-phase"].textContent = lobby.phase === "started" ? "対局中" : "待機中";
  elements["lobby-status-text"].textContent = `${lobby.player_members}/${lobby.settings.player_count}席 · 観戦${lobby.spectators}人`;
  renderMembers(lobby);
  renderLobbySettings(lobby.settings, lobby.access);

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
            ? lobbyAIMemberDescription(
              member,
              lobby.settings?.ai_personality_mode,
            )
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

function roomAccessPublicPresentation(access) {
  if (
    !access
    || typeof access !== "object"
    || Array.isArray(access)
    || typeof access.passphrase_required !== "boolean"
  ) return null;
  const keys = Object.keys(access);
  if (!keys.every((key) => ["passphrase_required", "invite_only"].includes(key))) {
    return null;
  }
  if (
    Object.prototype.hasOwnProperty.call(access, "invite_only")
    && typeof access.invite_only !== "boolean"
  ) return null;
  if (access.invite_only && access.passphrase_required) return null;
  if (access.invite_only) return "期限付き招待のみ";
  // Invite-only rooms deliberately reuse the persisted passphrase gate with
  // a server-owned secret.  The public lobby does not reveal which protected
  // mechanism is in use, so use an accurate non-secret umbrella label.
  return access.passphrase_required ? "招待／パスフレーズ" : "参加コード";
}

function renderLobbySettings(settings, access) {
  const list = elements["lobby-settings-list"];
  list.replaceChildren();
  const accessLabel = roomAccessPublicPresentation(access);
  const rows = [
    ["プレイヤー", `${settings.player_count}人`],
    ["AI", settings.ai_player_count ? `${settings.ai_player_count}人 · ${aiPersonalityLabel(settings.ai_personality_mode)}` : "なし"],
    ["勝利条件", `${settings.victory_target} VP`],
    ["盤面", boardModeLabel(settings.board_mode)],
    ["モード", variantLabel(settings.variant)],
    ["Seed", String(settings.board_seed)],
    ["入室保護", accessLabel || "確認不可"],
    ["追加ルール", houseRulesLabel(settings.house_rules)],
  ];
  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.append(textElement("dt", label), textElement("dd", value));
    list.append(row);
  }
}

function commandOptionsForView(snapshot) {
  if (state.replayIndex !== null) return [];
  return Array.isArray(snapshot?.command_options)
    ? snapshot.command_options
    : [];
}

function isExpectedReplayFrame(event, index) {
  return Boolean(
    state.replayRequestPending
    && state.replayExpectedIndex === index
    && typeof state.welcome?.room_code === "string"
    && event?.room_code === state.welcome.room_code
  );
}

function personalityModeForView(gameState) {
  return gameState?.ai?.personality_mode
    ?? state.lobby?.settings?.ai_personality_mode;
}

function renderGame() {
  const snapshot = state.snapshot;
  const gameState = snapshot.state;
  const phase = gameState.phase || {};
  const activeSeat = activePlayerIndex(gameState);
  const ownSeat = state.welcome?.seat_index;
  const personalityMode = personalityModeForView(gameState);
  const title = phaseTitle(gameState, activeSeat);
  const variantState = gameState.variant_state;
  elements["game-view"].dataset.variantKind = variantState?.kind || "standard";
  elements["game-view"].dataset.variantCatalog = variantState?.public?.catalog || "";
  elements["game-room-label"].textContent = `ROOM ${state.welcome?.room_code || "------"} · ${roleLabel(state.welcome?.role)}`;
  elements["game-phase-title"].textContent = title.title;
  elements["game-instruction"].textContent = title.detail;
  elements["revision-badge"].textContent = `rev. ${snapshot.revision}`;
  elements["victory-target-label"].textContent = `${gameState.rules?.victory_point_target || 10} VP`;

  const options = commandOptionsForView(snapshot);
  state.targetOptions = new Map(
    options
      .filter((option) => typeof option?.args?.target === "string")
      .map((option) => [option.args.target, option]),
  );
  const animationPlan = takePendingBoardAnimations(snapshot);
  renderBoard(
    snapshot.board_manifest,
    gameState.players || [],
    animationPlan,
    variantState,
  );
  renderActions(options);
  renderTradeMarket(gameState, options, ownSeat);
  renderTradeAuction(gameState, options, ownSeat);
  renderResourceCredit(gameState, options, ownSeat);
  renderIncomingTradePrompt(gameState, options, ownSeat);
  renderPlayers(
    gameState,
    activeSeat,
    ownSeat,
    snapshot.viewer_player_index,
    personalityMode,
  );
  const latest = gameState.history?.latest_event || {};
  elements["latest-event-title"].textContent = latest.title || "進行中";
  elements["latest-event-detail"].textContent = latest.detail || "次の操作を待っています。";
  renderForecastEvent(gameState.variant_state);
  renderFrontierStatus(
    gameState.variant_state,
    snapshot.board_manifest?.tiles?.length,
  );
  renderAICommentary(
    gameState.ai?.status,
    personalityMode,
    Boolean(gameState.players?.[activeSeat]?.is_ai && phase.name !== "finished"),
  );
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
  const compactStrip = elements["forecast-compact-strip"];
  card.hidden = !presentation.visible;
  compactStrip.hidden = !presentation.visible;
  if (!presentation.visible) {
    elements["forecast-active-list"].replaceChildren();
    state.forecastActiveSignature = null;
    return;
  }
  elements["forecast-event-countdown"].textContent = presentation.countdown;
  elements["forecast-event-title"].textContent = presentation.title;
  elements["forecast-event-detail"].textContent = presentation.description;
  elements["forecast-compact-title"].textContent = presentation.compact;
  elements["forecast-compact-active"].textContent = presentation.active.length
    ? `発動中: ${presentation.active.join(" / ")}`
    : "発動中の効果なし";
  const activeList = elements["forecast-active-list"];
  activeList.replaceChildren();
  const activeLabels = presentation.active.length
    ? presentation.active
    : ["現在発動中の効果なし"];
  for (const label of activeLabels) {
    activeList.append(textElement("li", label, "forecast-active-chip"));
  }

  const publicState = variantComponentPublic(variantState, "forecast_events") || {};
  const activeEffects = Array.isArray(publicState.active_effects)
    ? publicState.active_effects
    : [];
  const signature = activeEffects
    .map((effect) => `${effect.event_id}:${effect.started_turn}`)
    .sort()
    .join("|");
  if (state.replayIndex === null) {
    if (
      state.forecastActiveSignature !== null
      && signature !== state.forecastActiveSignature
      && presentation.active.length
    ) {
      elements["forecast-live-status"].textContent = `イベント発動。${presentation.active.join("、")}`;
    }
    state.forecastActiveSignature = signature;
  }
}

function renderFrontierStatus(variantState, totalTiles) {
  const presentation = frontierPresentation(variantState, totalTiles);
  const card = elements["frontier-status-card"];
  card.hidden = !presentation.visible;
  if (!presentation.visible) return;
  elements["frontier-status-count"].textContent = presentation.count;
  elements["frontier-status-detail"].textContent = presentation.detail;
}

function renderAICommentary(status, personalityMode, isActiveAI = true) {
  const visible = Boolean(status?.player_name && status?.title);
  elements["ai-commentary"].hidden = !visible;
  if (!visible) return;
  elements["ai-commentary-title"].textContent = aiCommentaryHeading(
    status,
    personalityMode,
    isActiveAI,
  );
  elements["ai-commentary-detail"].textContent = status.detail || "次の一手を評価しています。";
}

function renderActions(options) {
  const list = elements["action-list"];
  list.replaceChildren();
  const hasVariantOptions = options.some((option) => (
    isMarketCommand(option.command)
      || isAuctionCommand(option.command)
      || isCreditCommand(option.command)
  ));
  const direct = options.filter(
    (option) => (
      !option?.args?.target
      && !isMarketCommand(option.command)
      && !isAuctionCommand(option.command)
      && !isCreditCommand(option.command)
    ),
  );
  const gameState = state.snapshot?.state || {};
  const isTradeEditor =
    gameState.phase?.special_phase === "domestic_trade_edit"
    && gameState.domestic_trade?.editor === state.welcome?.seat_index
    && direct.some((option) => option.command === "trade_adjust");
  if (isTradeEditor) {
    renderTradeEditor(list, gameState, direct);
  } else {
    for (const option of direct) {
      list.append(createActionButton(option));
    }
  }
  const targetCount = state.targetOptions.size;
  const hint = targetCount
    ? `盤面上で光っている候補を選べます（${targetCount}か所）。`
    : isTradeEditor
      ? "「− / ＋」で枚数を調整します。ORにした欄は、承諾側が候補から1種類を選びます。"
    : direct.length || hasVariantOptions
      ? ""
      : state.welcome?.role === "spectator"
        ? "観戦中です。操作はプレイヤーだけに表示されます。"
        : "ほかのプレイヤーの操作を待っています。";
  elements["action-hint"].textContent = hint;
  elements["action-hint"].hidden = !hint;
}

function isMarketCommand(command) {
  return typeof command === "string" && command.startsWith("market_");
}

function isAuctionCommand(command) {
  return typeof command === "string" && command.startsWith("auction_");
}

function isCreditCommand(command) {
  return typeof command === "string" && command.startsWith("credit_");
}

function tradeMarketPresentation(
  variantState,
  players = [],
  options = [],
  ownSeat = null,
) {
  const publicState = variantComponentPublic(variantState, "trade2");
  if (!publicState) return { visible: false };
  const completedTurns = Number.isInteger(publicState.completed_turns)
    ? publicState.completed_turns
    : 0;
  const createOption = options.find((option) => option.command === "market_create") || null;
  const ownResources = Number.isInteger(ownSeat)
    ? players?.[ownSeat]?.resources
    : null;
  const orders = (Array.isArray(publicState.orders) ? publicState.orders : []).map(
    (order) => {
      const sellerIndex = Number(order?.seller_index);
      const sellerName = Number.isInteger(sellerIndex)
        ? players?.[sellerIndex]?.name || `プレイヤー${sellerIndex + 1}`
        : "不明なプレイヤー";
      const remainingTurns = Math.max(
        0,
        (Number.isInteger(order?.expires_turn) ? order.expires_turn : completedTurns)
          - completedTurns,
      );
      return {
        ...order,
        sellerIndex,
        sellerName,
        remainingTurns,
        isOwn: Number.isInteger(ownSeat) && sellerIndex === ownSeat,
        canAfford: canAffordMarketBundle(ownResources, order?.wanted),
        fillOption: findMarketOrderOption(options, "market_fill", order),
        cancelOption: findMarketOrderOption(options, "market_cancel", order),
      };
    },
  );
  return {
    visible: true,
    completedTurns,
    orders,
    createOption,
    ownResourceTotal: TRADE_RESOURCE_KEYS.reduce(
      (total, resource) => total + Math.max(0, Number(ownResources?.[resource]) || 0),
      0,
    ),
    ownOrderCount: orders.filter((order) => order.isOwn).length,
    countLabel: `${orders.length} / 16`,
  };
}

function marketCreateUnavailableLabel(
  presentation,
  { replaying = false, role = "player" } = {},
) {
  if (replaying) return "リプレイ中は出品できません";
  if (role === "spectator") return "観戦者は出品できません";
  if (presentation.orders.length >= 16) return "市場の注文枠が満杯です";
  if (presentation.ownOrderCount >= 4) return "自分の注文枠は4件までです";
  if (presentation.ownResourceTotal <= 0) return "出品できる資源がありません";
  return "自分の行動手番に出品できます";
}

function findMarketOrderOption(options, command, order) {
  return options.find((option) => (
    option.command === command
    && option.args?.order_id === order?.order_id
    && Number(option.args?.revision) === Number(order?.revision)
  )) || null;
}

function canAffordMarketBundle(resources, bundle) {
  if (!resources || !bundle || typeof bundle !== "object") return false;
  return Object.entries(bundle).every(([resource, count]) => (
    TRADE_RESOURCE_KEYS.includes(resource)
    && Number.isInteger(Number(count))
    && Number(count) > 0
    && Number(resources[resource] || 0) >= Number(count)
  ));
}

function formatMarketBundle(bundle) {
  const labels = TRADE_RESOURCE_KEYS
    .filter((resource) => Number(bundle?.[resource]) > 0)
    .map((resource) => `${RESOURCE_LABELS[resource]}${Number(bundle[resource])}`);
  return labels.length ? labels.join(" + ") : "未指定";
}

function renderTradeMarket(gameState, options, ownSeat) {
  const panel = elements["market-panel"];
  const presentation = tradeMarketPresentation(
    gameState?.variant_state,
    gameState?.players || [],
    options,
    ownSeat,
  );
  panel.hidden = !presentation.visible;
  if (!presentation.visible) {
    elements["market-order-list"].replaceChildren();
    closeMarketEditor({ restoreFocus: false });
    return;
  }

  elements["market-order-count"].textContent = presentation.countLabel;
  const list = elements["market-order-list"];
  list.replaceChildren();
  if (!presentation.orders.length) {
    list.append(
      textElement(
        "p",
        "まだ注文はありません。自分の手番に最初の条件を公開できます。",
        "market-empty",
      ),
    );
  }
  for (const order of presentation.orders) {
    const card = document.createElement("article");
    card.className = `market-order-card${order.isOwn ? " own-order" : ""}`;
    const heading = document.createElement("div");
    heading.className = "market-order-heading";
    heading.append(
      textElement("strong", `${order.sellerName}${order.isOwn ? "（あなた）" : ""}`),
      textElement("span", `残り${order.remainingTurns}手番`),
    );
    const terms = document.createElement("div");
    terms.className = "market-order-terms";
    terms.append(
      createMarketOrderTerm("出品", order.offer, "offer"),
      textElement("span", "→", "market-order-arrow"),
      createMarketOrderTerm("希望", order.wanted, "wanted"),
    );
    card.append(heading, terms, createMarketOrderButton(order, ownSeat));
    list.append(card);
  }

  const createButton = elements["market-create-button"];
  createButton.disabled = !presentation.createOption || state.commandPending;
  createButton.textContent = presentation.createOption
    ? "新しい注文を出す"
    : marketCreateUnavailableLabel(presentation, {
      replaying: state.replayIndex !== null,
      role: state.welcome?.role,
    });
  const hint = state.welcome?.role === "spectator"
    ? "観戦者は公開注文と残り期限を確認できます。"
    : "購入・取消は、自分の行動手番に実行できます。";
  elements["market-hint"].textContent = hint;
  elements["market-hint"].hidden = !hint;

  if (state.marketEditorOpen && !presentation.createOption) {
    closeMarketEditor({ restoreFocus: false });
  }
}

function createMarketOrderTerm(label, bundle, className) {
  const term = document.createElement("div");
  term.className = `market-order-term ${className}`;
  term.append(
    textElement("span", label),
    textElement("strong", formatMarketBundle(bundle)),
  );
  return term;
}

function createMarketOrderButton(order, ownSeat) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `${
    order.isOwn ? "ghost-button" : "secondary-button"
  } market-order-button`;
  const option = order.isOwn ? order.cancelOption : order.fillOption;
  if (option) {
    button.textContent = order.isOwn ? "この注文を取り消す" : "この条件で購入";
    button.disabled = state.commandPending;
    button.addEventListener("click", () => sendGameCommand(option));
    return button;
  }
  button.disabled = true;
  if (!Number.isInteger(ownSeat)) {
    button.textContent = "観戦中";
  } else if (order.isOwn) {
    button.textContent = "自分の行動手番に取消";
  } else if (!order.canAfford) {
    button.textContent = "希望資源が不足";
  } else {
    button.textContent = "自分の行動手番に購入";
  }
  return button;
}

function tradeAuctionPresentation(
  variantState,
  players = [],
  options = [],
  ownSeat = null,
) {
  const publicState = variantComponentPublic(variantState, "trade2");
  if (
    !publicState
    || publicState.catalog !== "market_auction_v1"
    || !Array.isArray(publicState.auctions)
  ) return { visible: false };
  const completedTurns = Number.isInteger(publicState.completed_turns)
    ? publicState.completed_turns
    : 0;
  const ownResources = Number.isInteger(ownSeat)
    ? players?.[ownSeat]?.resources
    : null;
  const auctions = publicState.auctions.map((auction) => {
    const sellerIndex = Number(auction?.seller_index);
    const bids = (Array.isArray(auction?.bids) ? auction.bids : []).map((bid) => {
      const bidderIndex = Number(bid?.bidder_index);
      return {
        ...bid,
        bidderIndex,
        bidderName: Number.isInteger(bidderIndex)
          ? players?.[bidderIndex]?.name || `プレイヤー${bidderIndex + 1}`
          : "不明なプレイヤー",
        isOwn: Number.isInteger(ownSeat) && bidderIndex === ownSeat,
        acceptOption: findAuctionOption(
          options,
          "auction_accept",
          auction,
          bidderIndex,
        ),
      };
    });
    const ownBid = bids.find((bid) => bid.isOwn) || null;
    return {
      ...auction,
      sellerIndex,
      sellerName: Number.isInteger(sellerIndex)
        ? players?.[sellerIndex]?.name || `プレイヤー${sellerIndex + 1}`
        : "不明なプレイヤー",
      remainingTurns: Math.max(
        0,
        (Number.isInteger(auction?.expires_turn)
          ? auction.expires_turn
          : completedTurns) - completedTurns,
      ),
      isOwn: Number.isInteger(ownSeat) && sellerIndex === ownSeat,
      bids,
      ownBid,
      bidOption: findAuctionOption(options, "auction_bid", auction),
      cancelBidOption: findAuctionOption(
        options,
        "auction_cancel_bid",
        auction,
      ),
      cancelOption: findAuctionOption(options, "auction_cancel", auction),
    };
  });
  return {
    visible: true,
    completedTurns,
    auctions,
    createOption: options.find((option) => option.command === "auction_create") || null,
    ownResources,
    ownAuctionCount: auctions.filter((auction) => auction.isOwn).length,
    countLabel: `${auctions.length} / 8`,
  };
}

function findAuctionOption(options, command, auction, bidderIndex = null) {
  return options.find((option) => (
    option.command === command
    && option.args?.auction_id === auction?.auction_id
    && Number(option.args?.revision) === Number(auction?.revision)
    && (
      bidderIndex === null
      || Number(option.args?.bidder_index) === Number(bidderIndex)
    )
  )) || null;
}

function renderTradeAuction(gameState, options, ownSeat) {
  const panel = elements["auction-panel"];
  const presentation = tradeAuctionPresentation(
    gameState?.variant_state,
    gameState?.players || [],
    options,
    ownSeat,
  );
  panel.hidden = !presentation.visible;
  if (!presentation.visible) {
    elements["auction-list"].replaceChildren();
    closeAuctionEditor({ restoreFocus: false });
    return;
  }
  elements["auction-count"].textContent = presentation.countLabel;
  const list = elements["auction-list"];
  list.replaceChildren();
  if (!presentation.auctions.length) {
    list.append(
      textElement(
        "p",
        "まだ競売はありません。品物を公開すると、相手が自由な組み合わせで入札できます。",
        "market-empty",
      ),
    );
  }
  for (const auction of presentation.auctions) {
    list.append(createAuctionCard(auction, ownSeat));
  }

  const createButton = elements["auction-create-button"];
  createButton.disabled = !presentation.createOption || state.commandPending;
  createButton.textContent = presentation.createOption
    ? "新しい競売を開く"
    : state.replayIndex !== null
      ? "リプレイ中は競売を開けません"
      : state.welcome?.role === "spectator"
        ? "観戦者は競売を開けません"
        : presentation.auctions.length >= 8
          ? "競売枠が満杯です"
          : presentation.ownAuctionCount >= 2
            ? "自分の競売枠は2件までです"
            : "自分の行動手番に競売を開けます";
  elements["auction-hint"].textContent = state.welcome?.role === "spectator"
    ? "観戦者は公開された出品・入札・残り期限を確認できます。"
    : "入札と入札取消は、ほかのプレイヤーの手番中でも実行できます。";

  if (state.auctionEditorOpen) {
    const draftAuction = presentation.auctions.find(
      (auction) => auction.auction_id === state.auctionDraft?.auctionId,
    );
    const validContext = state.auctionDraft?.mode === "create"
      ? presentation.createOption
      : draftAuction?.bidOption;
    if (!validContext) closeAuctionEditor({ restoreFocus: false });
  }
}

function createAuctionCard(auction, ownSeat) {
  const card = document.createElement("article");
  card.className = `auction-card${auction.isOwn ? " own-auction" : ""}`;
  const heading = document.createElement("div");
  heading.className = "auction-heading";
  heading.append(
    textElement(
      "strong",
      `${auction.sellerName}${auction.isOwn ? "（あなた）" : ""}`,
    ),
    textElement("span", `残り${auction.remainingTurns}手番`),
  );
  const lot = document.createElement("div");
  lot.className = "auction-lot";
  lot.append(
    textElement("span", "出品されている資源"),
    textElement("strong", formatMarketBundle(auction.offer)),
  );
  card.append(
    heading,
    lot,
    textElement(
      "p",
      `最低入札: 合計${Number(auction.minimum_bid_cards) || 1}枚`,
      "auction-minimum",
    ),
  );

  const bidList = document.createElement("div");
  bidList.className = "auction-bid-list";
  if (!auction.bids.length) {
    bidList.append(textElement("p", "まだ入札はありません。", "market-empty"));
  }
  for (const bid of auction.bids) {
    const bidCard = document.createElement("section");
    bidCard.className = `auction-bid${bid.isOwn ? " own-bid" : ""}`;
    const bidHeading = document.createElement("div");
    bidHeading.className = "auction-bid-heading";
    bidHeading.append(
      textElement(
        "strong",
        `${bid.bidderName}${bid.isOwn ? "（あなた）" : ""}`,
      ),
      textElement("span", `入札 ${formatMarketBundle(bid.offer)}`),
    );
    bidCard.append(bidHeading);
    if (auction.isOwn && bid.acceptOption) {
      const accept = document.createElement("button");
      accept.type = "button";
      accept.className = "primary-button";
      accept.textContent = "この入札を選んで落札確定";
      accept.disabled = state.commandPending;
      accept.addEventListener("click", () => sendGameCommand(bid.acceptOption));
      bidCard.append(accept);
    }
    bidList.append(bidCard);
  }
  card.append(bidList);

  const actions = document.createElement("div");
  actions.className = "auction-card-actions";
  if (!auction.isOwn && auction.bidOption) {
    const bidButton = document.createElement("button");
    bidButton.type = "button";
    bidButton.className = "secondary-button";
    bidButton.textContent = auction.ownBid ? "入札内容を変更" : "この競売へ入札";
    bidButton.disabled = state.commandPending;
    bidButton.addEventListener("click", () => openAuctionBidEditor(auction));
    actions.append(bidButton);
  }
  const cancelOption = auction.isOwn
    ? auction.cancelOption
    : auction.cancelBidOption;
  if (cancelOption) {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "ghost-button";
    cancel.textContent = auction.isOwn ? "競売を取り消す" : "自分の入札を取り消す";
    cancel.disabled = state.commandPending;
    cancel.addEventListener("click", () => sendGameCommand(cancelOption));
    actions.append(cancel);
  }
  if (actions.childElementCount) card.append(actions);
  if (!Number.isInteger(ownSeat)) {
    card.append(textElement("p", "観戦中", "context-hint"));
  }
  return card;
}

function resourceCreditPresentation(
  variantState,
  players = [],
  options = [],
  ownSeat = null,
) {
  const publicState = variantComponentPublic(variantState, "credit");
  if (
    !publicState
    || publicState.catalog !== "bank_loan_v1"
    || !Array.isArray(publicState.loans)
  ) return { visible: false };
  const completedTurns = Number.isInteger(publicState.completed_turns)
    ? publicState.completed_turns
    : 0;
  const loans = publicState.loans.map((loan) => {
    const borrowerIndex = Number(loan?.borrower_index);
    const delinquent = loan?.status === "delinquent";
    return {
      ...loan,
      borrowerIndex,
      borrowerName: Number.isInteger(borrowerIndex)
        ? players?.[borrowerIndex]?.name || `プレイヤー${borrowerIndex + 1}`
        : "不明なプレイヤー",
      isOwn: Number.isInteger(ownSeat) && borrowerIndex === ownSeat,
      delinquent,
      remainingTurns: delinquent
        ? 0
        : Math.max(
          0,
          (Number.isInteger(loan?.due_turn) ? loan.due_turn : completedTurns)
            - completedTurns,
        ),
      publicVpPenalty: delinquent ? 2 : 1,
      repayOption: options.find((option) => (
        option.command === "credit_repay"
        && option.args?.loan_id === loan?.loan_id
        && Number(option.args?.revision) === Number(loan?.revision)
      )) || null,
    };
  });
  const borrowOptions = options.filter((option) => (
    option.command === "credit_borrow"
    && TRADE_RESOURCE_KEYS.includes(option.args?.resource)
  ));
  const ownLoan = loans.find((loan) => loan.isOwn) || null;
  return {
    visible: true,
    completedTurns,
    loans,
    ownLoan,
    borrowOptions,
    ownResources: Number.isInteger(ownSeat)
      ? players?.[ownSeat]?.resources || null
      : null,
    countLabel: `債務 ${loans.length}件`,
  };
}

function renderResourceCredit(gameState, options, ownSeat) {
  const panel = elements["credit-panel"];
  const presentation = resourceCreditPresentation(
    gameState?.variant_state,
    gameState?.players || [],
    options,
    ownSeat,
  );
  panel.hidden = !presentation.visible;
  if (!presentation.visible) {
    elements["credit-loan-list"].replaceChildren();
    closeCreditEditor({ restoreFocus: false });
    return;
  }

  elements["credit-loan-count"].textContent = presentation.countLabel;
  elements["credit-availability"].textContent = presentation.ownLoan
    ? "あなたは借入枠を使用中です。完済すると再び借りられます。"
    : presentation.borrowOptions.length
      ? `現在借入可能: ${presentation.borrowOptions
        .map((option) => RESOURCE_LABELS[option.args.resource])
        .join("・")}`
      : "借入可能な資源は自分の行動手番に確認できます。";
  const list = elements["credit-loan-list"];
  list.replaceChildren();
  if (!presentation.loans.length) {
    list.append(textElement(
      "p",
      "現在、未返済の借入はありません。借入内容は全員へ公開されます。",
      "credit-empty",
    ));
  }
  for (const loan of presentation.loans) {
    list.append(createCreditLoanCard(loan));
  }

  const button = elements["credit-open-button"];
  const canOpen = presentation.ownLoan
    ? Boolean(presentation.ownLoan.repayOption)
    : presentation.borrowOptions.length > 0;
  button.disabled = !canOpen || state.commandPending;
  if (presentation.ownLoan) {
    button.textContent = presentation.ownLoan.repayOption
      ? presentation.ownLoan.delinquent
        ? `延滞債務を返済（残り${presentation.ownLoan.remaining_cards}枚）`
        : "返済内容を選ぶ"
      : "自分の行動手番に返済できます";
  } else if (presentation.borrowOptions.length) {
    button.textContent = "銀行から資源を1枚借りる";
  } else if (state.replayIndex !== null) {
    button.textContent = "リプレイ中は借入できません";
  } else if (state.welcome?.role === "spectator") {
    button.textContent = "観戦者は借入できません";
  } else {
    button.textContent = "自分の行動手番に借入できます";
  }
  elements["credit-hint"].textContent = state.welcome?.role === "spectator"
    ? "観戦者にも、債務状態・返済期限・公開VP減点が表示されます。"
    : presentation.ownLoan?.delinquent
      ? "延滞債務は任意資源で分割返済できます。対局進行は止まりません。"
      : "通常債務は期限までに、借りた資源1枚＋任意資源1枚をまとめて返します。";

  if (state.creditEditorOpen) {
    const validContext = state.creditDraft?.mode === "borrow"
      ? presentation.borrowOptions.length > 0 && !presentation.ownLoan
      : presentation.ownLoan?.repayOption;
    if (!validContext) closeCreditEditor({ restoreFocus: false });
  }
}

function createCreditLoanCard(loan) {
  const card = document.createElement("article");
  card.className = `credit-loan-card${loan.isOwn ? " own-loan" : ""}${loan.delinquent ? " delinquent" : ""}`;
  const heading = document.createElement("div");
  heading.className = "credit-loan-heading";
  heading.append(
    textElement("strong", `${loan.borrowerName}${loan.isOwn ? "（あなた）" : ""}`),
    textElement(
      "span",
      creditDeadlineLabel(loan),
      `credit-status${loan.delinquent ? " delinquent" : ""}`,
    ),
  );
  const terms = document.createElement("div");
  terms.className = "credit-loan-terms";
  terms.append(
    textElement(
      "span",
      loan.delinquent
        ? `残債 任意資源${Number(loan.remaining_cards) || 0}枚`
        : `借入 ${RESOURCE_LABELS[loan.borrowed_resource] || "不明な資源"}1枚`,
    ),
    textElement("strong", `公開VP −${loan.publicVpPenalty}`),
  );
  card.append(heading, terms);
  if (loan.isOwn && loan.repayOption) {
    const repay = document.createElement("button");
    repay.type = "button";
    repay.className = "secondary-button credit-repay-button";
    repay.textContent = loan.delinquent ? "返済する枚数を選ぶ" : "返済内容を選ぶ";
    repay.disabled = state.commandPending;
    repay.addEventListener("click", () => openCreditRepayEditor(loan));
    card.append(repay);
  }
  return card;
}

function creditDeadlineLabel(loan) {
  if (loan?.delinquent) return "延滞中";
  if (Number(loan?.remainingTurns) === 0) return "この手番終了まで";
  return `返済まで${Math.max(0, Number(loan?.remainingTurns) || 0)}手番`;
}

function emptyMarketBundle() {
  return Object.fromEntries(TRADE_RESOURCE_KEYS.map((resource) => [resource, 0]));
}

function availableMarketResources() {
  const ownSeat = state.welcome?.seat_index;
  const resources = Number.isInteger(ownSeat)
    ? state.snapshot?.state?.players?.[ownSeat]?.resources
    : null;
  return Object.fromEntries(
    TRADE_RESOURCE_KEYS.map((resource) => [
      resource,
      Math.max(0, Math.min(MARKET_RESOURCE_LIMIT, Number(resources?.[resource]) || 0)),
    ]),
  );
}

function compactMarketBundle(bundle) {
  return Object.fromEntries(
    TRADE_RESOURCE_KEYS
      .filter((resource) => Number(bundle?.[resource]) > 0)
      .map((resource) => [resource, Number(bundle[resource])]),
  );
}

function marketDraftValidation(draft, available = availableMarketResources()) {
  const offer = compactMarketBundle(draft?.offer);
  const wanted = compactMarketBundle(draft?.wanted);
  if (!Object.keys(offer).length || !Object.keys(wanted).length) {
    return {
      valid: false,
      offer,
      wanted,
      message: "出品と希望を1枚以上ずつ選んでください。",
    };
  }
  if (Object.keys(offer).some((resource) => Number(wanted[resource]) > 0)) {
    return {
      valid: false,
      offer,
      wanted,
      message: "同じ資源を出品側と希望側の両方には指定できません。",
    };
  }
  const withinLimits = Object.entries(offer).every(([resource, count]) => (
    Number.isInteger(count)
    && count >= 1
    && count <= MARKET_RESOURCE_LIMIT
    && count <= Number(available?.[resource] || 0)
  )) && Object.values(wanted).every((count) => (
    Number.isInteger(count) && count >= 1 && count <= MARKET_RESOURCE_LIMIT
  ));
  if (!withinLimits) {
    return {
      valid: false,
      offer,
      wanted,
      message: "出品枚数が手札を超えているか、指定枚数が上限を超えています。",
    };
  }
  return {
    valid: true,
    offer,
    wanted,
    message: `${formatMarketBundle(offer)} を出品 → ${formatMarketBundle(wanted)} を希望`,
  };
}

function adjustMarketDraft(side, resource, delta) {
  if (
    !state.marketDraft
    || !["offer", "wanted"].includes(side)
    || !TRADE_RESOURCE_KEYS.includes(resource)
    || ![-1, 1].includes(Number(delta))
  ) return;
  const bundle = state.marketDraft[side];
  const opposite = state.marketDraft[side === "offer" ? "wanted" : "offer"];
  const current = Number(bundle[resource]) || 0;
  const maximum = side === "offer"
    ? Number(availableMarketResources()[resource]) || 0
    : MARKET_RESOURCE_LIMIT;
  const next = Math.max(0, Math.min(maximum, current + Number(delta)));
  if (next > 0 && Number(opposite[resource]) > 0) return;
  bundle[resource] = next;
  renderMarketEditor();
}

function renderMarketEditor() {
  if (!state.marketEditorOpen || !state.marketDraft) return;
  const available = availableMarketResources();
  const grid = elements["market-editor-grid"];
  grid.replaceChildren(
    createMarketEditorSide("offer", "出品する", "現在使える手札まで指定できます。", available),
    createMarketEditorSide("wanted", "希望する", "購入者から受け取りたい資源です。", available),
  );
  const validation = marketDraftValidation(state.marketDraft, available);
  elements["market-editor-summary"].textContent = validation.message;
  elements["market-editor-submit"].disabled = !validation.valid || state.commandPending;
}

function createMarketEditorSide(side, title, subtitle, available) {
  const section = document.createElement("section");
  section.className = `market-editor-side ${side}`;
  const heading = document.createElement("div");
  heading.className = "market-editor-side-heading";
  heading.append(textElement("strong", title), textElement("small", subtitle));
  const list = document.createElement("div");
  list.className = "market-resource-list";
  for (const resource of TRADE_RESOURCE_KEYS) {
    const current = Number(state.marketDraft?.[side]?.[resource]) || 0;
    const oppositeSide = side === "offer" ? "wanted" : "offer";
    const opposite = Number(state.marketDraft?.[oppositeSide]?.[resource]) || 0;
    const maximum = side === "offer"
      ? Number(available[resource]) || 0
      : MARKET_RESOURCE_LIMIT;
    const row = document.createElement("div");
    row.className = "market-resource-row";
    row.append(
      textElement(
        "span",
        side === "offer"
          ? `${RESOURCE_LABELS[resource]}（手札${available[resource]}）`
          : RESOURCE_LABELS[resource],
        "market-resource-name",
      ),
      createMarketAdjustButton(side, resource, -1, current <= 0),
      textElement("span", String(current), "market-resource-count"),
      createMarketAdjustButton(
        side,
        resource,
        1,
        current >= maximum || opposite > 0,
      ),
    );
    list.append(row);
  }
  section.append(heading, list);
  return section;
}

function createMarketAdjustButton(side, resource, delta, disabled) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "market-adjust-button";
  button.textContent = delta > 0 ? "+" : "−";
  button.disabled = disabled || state.commandPending;
  button.setAttribute(
    "aria-label",
    `${side === "offer" ? "出品" : "希望"}する${RESOURCE_LABELS[resource]}を${delta > 0 ? "増やす" : "減らす"}`,
  );
  button.addEventListener("click", () => adjustMarketDraft(side, resource, delta));
  return button;
}

function createActionButton(option, label = commandLabel(option)) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "action-button";
  if (["roll_dice", "end_turn", "trade_accept", "trade_submit", "trade_reveal"].includes(option.command)) {
    button.classList.add("primary-action");
  }
  if (["cancel", "trade_reject"].includes(option.command)) {
    button.classList.add("danger-action");
  }
  button.textContent = label;
  button.disabled = state.commandPending;
  button.addEventListener("click", () => sendGameCommand(option));
  return button;
}

function renderTradeEditor(list, gameState, options) {
  const viewerSeat = state.welcome?.seat_index;
  const presentation = domesticTradePresentation(gameState, viewerSeat);
  const editor = document.createElement("section");
  editor.className = "trade-editor";
  editor.setAttribute("aria-label", "交易条件の編集");

  const heading = document.createElement("div");
  heading.className = "trade-editor-heading";
  const headingTitle = presentation.isCounter
    ? presentation.counterpartyName + "への条件変更"
    : presentation.isBroadcast
      ? "全員へ募集する条件"
      : presentation.counterpartyName + "への提案";
  heading.append(
    textElement("strong", headingTitle),
    textElement(
      "span",
      "赤枠の「あなたが渡す」と緑枠の「あなたが受け取る」を確認してください。相手の手札内訳は公開されません。",
    ),
    textElement(
      "div",
      "あなた: "
        + formatTradeBundle(presentation.outgoing, presentation.outgoingOperator)
        + " → "
        + formatTradeBundle(presentation.incoming, presentation.incomingOperator),
      "trade-live-summary",
    ),
  );
  editor.append(heading);

  const grid = document.createElement("div");
  grid.className = "trade-editor-grid";
  grid.append(
    buildTradeSideEditor({
      className: "outgoing",
      title: "あなたが渡す",
      subtitle: ownResourceSummary(gameState, viewerSeat),
      side: presentation.outgoingSide,
      bundle: presentation.outgoing,
      operator: presentation.outgoingOperator,
      options,
    }),
    buildTradeSideEditor({
      className: "incoming",
      title: "あなたが受け取る",
      subtitle: presentation.counterpartyName + "の手札内訳は非公開",
      side: presentation.incomingSide,
      bundle: presentation.incoming,
      operator: presentation.incomingOperator,
      options,
    }),
  );
  editor.append(grid);

  const actions = document.createElement("div");
  actions.className = "trade-editor-actions";
  const submit = options.find((option) => option.command === "trade_submit");
  if (submit) {
    const submitLabel = presentation.isCounter
      ? "この条件で再提案"
      : presentation.isBroadcast
        ? "この条件で全員に募集"
        : "この条件で提案";
    actions.append(createActionButton(submit, submitLabel));
  } else {
    const disabledSubmit = document.createElement("button");
    disabledSubmit.type = "button";
    disabledSubmit.className = "action-button primary-action";
    const receiveChoiceCount = TRADE_RESOURCE_KEYS.filter(
      (resource) => Number(gameState.domestic_trade?.receive?.[resource]) > 0,
    ).length;
    disabledSubmit.textContent =
      presentation.receiveOperator === "or" && receiveChoiceCount < 2
        ? "OR候補を2種類以上選択"
        : "双方1枚以上で提案できます";
    disabledSubmit.disabled = true;
    actions.append(disabledSubmit);
  }
  const cancel = options.find((option) => option.command === "cancel");
  if (cancel) actions.append(createActionButton(cancel, "交渉をやめる"));
  editor.append(actions);
  editor.append(
    textElement(
      "p",
      "「＋」が押せない資源は、手札不足か反対側ですでに指定されています。",
      "trade-editor-help",
    ),
  );
  list.append(editor);
}

function buildTradeSideEditor({
  className,
  title,
  subtitle,
  side,
  bundle,
  operator,
  options,
}) {
  const card = document.createElement("section");
  card.className = "trade-side-card " + className;
  const heading = document.createElement("div");
  heading.className = "trade-side-title";
  heading.append(textElement("strong", title), textElement("small", subtitle));
  card.append(heading);

  if (side === "receive") {
    card.append(buildTradeReceiveOperator(operator, options));
  }

  const rows = document.createElement("div");
  rows.className = "trade-resource-list";
  for (const resource of TRADE_RESOURCE_KEYS) {
    const row = document.createElement("div");
    row.className = "trade-resource-row";
    const minus = findTradeAdjustment(options, side, resource, -1);
    const plus = findTradeAdjustment(options, side, resource, 1);
    row.append(
      textElement("span", RESOURCE_LABELS[resource], "trade-resource-name"),
      createTradeAdjustmentButton(
        minus,
        "−",
        title + " " + RESOURCE_LABELS[resource] + "を1枚減らす",
      ),
      textElement(
        "strong",
        String(Number(bundle?.[resource]) || 0),
        "trade-resource-count",
      ),
      createTradeAdjustmentButton(
        plus,
        "＋",
        title + " " + RESOURCE_LABELS[resource] + "を1枚増やす",
      ),
    );
    rows.append(row);
  }
  card.append(rows);
  return card;
}

function buildTradeReceiveOperator(operator, options) {
  const wrapper = document.createElement("div");
  wrapper.className = "trade-operator-control";
  wrapper.append(textElement("span", "候補の扱い", "trade-operator-label"));
  const choices = document.createElement("div");
  choices.className = "trade-operator-choices";
  for (const [value, label] of [["and", "すべて"], ["or", "どれか1つ（OR）"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `trade-operator-button${operator === value ? " active" : ""}`;
    button.textContent = label;
    button.setAttribute("aria-pressed", operator === value ? "true" : "false");
    const option = options.find(
      (candidate) =>
        candidate.command === "trade_receive_operator"
        && candidate.args?.operator === value,
    );
    button.disabled = operator === value || !option || state.commandPending;
    if (option) button.addEventListener("click", () => sendGameCommand(option));
    choices.append(button);
  }
  wrapper.append(
    choices,
    textElement(
      "small",
      operator === "or"
        ? "承諾する側が、この欄の候補から1種類を選びます。"
        : "この欄に指定した資源をすべて交換します。",
    ),
  );
  return wrapper;
}

function findTradeAdjustment(options, side, resource, delta) {
  return options.find(
    (option) =>
      option.command === "trade_adjust"
      && option.args?.side === side
      && option.args?.resource === resource
      && Number(option.args?.delta) === delta,
  );
}

function createTradeAdjustmentButton(option, label, ariaLabel) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "trade-adjust-button";
  button.textContent = label;
  button.setAttribute("aria-label", ariaLabel);
  button.disabled = !option || state.commandPending;
  if (option) button.addEventListener("click", () => sendGameCommand(option));
  return button;
}

function ownResourceSummary(gameState, viewerSeat) {
  const player = Number.isInteger(viewerSeat) ? gameState.players?.[viewerSeat] : null;
  const total = Number(player?.resource_total);
  if (!Number.isFinite(total)) return "あなたの手札から支払います";
  const available = player?.resources && typeof player.resources === "object"
    ? resourceTotal(player.resources)
    : total;
  const reserved = Math.max(0, total - available);
  return reserved > 0
    ? `使用可能 ${available}枚 / 手札${total}枚（市場・競売で取り置き${reserved}枚）`
    : `あなたの手札 ${total}枚`;
}

function currentTurnSeat(gameState) {
  const phase = gameState?.phase || {};
  const order = Array.isArray(phase.turn_order) ? phase.turn_order : [];
  const indexed = order[phase.current_player_index];
  if (Number.isInteger(indexed)) return indexed;
  return Number.isInteger(phase.current_player_index)
    ? phase.current_player_index
    : null;
}

function domesticTradeActorSeat(gameState) {
  const phase = gameState?.phase?.special_phase;
  const trade = gameState?.domestic_trade || {};
  if (phase === "domestic_trade_handoff" || phase === "domestic_trade_response") {
    return Number.isInteger(trade.partner) ? trade.partner : null;
  }
  if (phase === "domestic_trade_edit" && trade.is_counter) {
    return Number.isInteger(trade.partner) ? trade.partner : null;
  }
  return currentTurnSeat(gameState);
}

function domesticTradePresentation(gameState, viewerSeat) {
  const trade = gameState?.domestic_trade || {};
  const turnSeat = currentTurnSeat(gameState);
  const viewerIsTurnPlayer =
    Number.isInteger(viewerSeat) && viewerSeat === turnSeat;
  const outgoingSide = viewerIsTurnPlayer ? "give" : "receive";
  const incomingSide = viewerIsTurnPlayer ? "receive" : "give";
  const counterpartySeat = viewerIsTurnPlayer ? trade.partner : turnSeat;
  const counterpartyName = Number.isInteger(counterpartySeat)
    ? gameState.players?.[counterpartySeat]?.name || "相手"
    : trade.is_broadcast
      ? "全員"
      : "相手";
  const proposerSeat = trade.is_counter ? trade.partner : turnSeat;
  const receiveOperator = trade.receive_operator === "or" ? "or" : "and";
  return {
    turnSeat,
    partnerSeat: Number.isInteger(trade.partner) ? trade.partner : null,
    proposerSeat: Number.isInteger(proposerSeat) ? proposerSeat : null,
    proposerName: Number.isInteger(proposerSeat)
      ? gameState.players?.[proposerSeat]?.name || "プレイヤー"
      : "プレイヤー",
    counterpartySeat,
    counterpartyName,
    outgoingSide,
    incomingSide,
    outgoing: trade[outgoingSide] || {},
    incoming: trade[incomingSide] || {},
    outgoingOperator: outgoingSide === "receive" ? receiveOperator : "and",
    incomingOperator: incomingSide === "receive" ? receiveOperator : "and",
    receiveOperator,
    isCounter: Boolean(trade.is_counter),
    isBroadcast: Boolean(trade.is_broadcast),
    broadcastIndex: Number.isInteger(trade.broadcast_index)
      ? trade.broadcast_index
      : -1,
  };
}

function formatTradeBundle(bundle, operator = "and") {
  const parts = TRADE_RESOURCE_KEYS.flatMap((resource) => {
    const count = Number(bundle?.[resource]);
    return Number.isInteger(count) && count > 0
      ? [RESOURCE_LABELS[resource] + " " + count]
      : [];
  });
  if (!parts.length) return "なし";
  return parts.join(operator === "or" ? " または " : "・");
}

function canonicalTradeBundle(bundle) {
  return TRADE_RESOURCE_KEYS
    .map((resource) => resource + ":" + Math.max(0, Number(bundle?.[resource]) || 0))
    .join(",");
}

function tradeOfferSignature(gameState) {
  const trade = gameState?.domestic_trade || {};
  return [
    currentTurnSeat(gameState),
    trade.partner,
    trade.is_counter ? "counter" : "offer",
    trade.is_broadcast ? "broadcast" : "direct",
    Number.isInteger(trade.broadcast_index) ? trade.broadcast_index : -1,
    canonicalTradeBundle(trade.give),
    canonicalTradeBundle(trade.receive),
    trade.receive_operator === "or" ? "or" : "and",
  ].join("|");
}

function renderIncomingTradePrompt(gameState, options, ownSeat) {
  const phase = gameState?.phase?.special_phase;
  const expectedCommands = {
    domestic_trade_handoff: "trade_reveal",
    domestic_trade_response: "trade_reject",
    domestic_trade_counter_handoff: "trade_reveal",
    domestic_trade_counter_response: "trade_reject",
  };
  const requiredCommand = expectedCommands[phase];
  const isLive =
    state.replayIndex === null
    && state.snapshot?.revision === state.liveSnapshot?.revision;
  const isViewer =
    Number.isInteger(ownSeat)
    && state.snapshot?.viewer_player_index === ownSeat;
  const isActor = domesticTradeActorSeat(gameState) === ownSeat;
  const hasAuthority = options.some(
    (option) => option.command === requiredCommand,
  );
  if (!isLive || !isViewer) {
    hideTradePrompt();
    return;
  }
  if (!requiredCommand || !isActor || !hasAuthority) {
    if (state.tradePromptSignature) {
      state.tradePromptDismissed.delete(state.tradePromptSignature);
      state.tradePromptNotified.delete(state.tradePromptSignature);
    }
    state.tradePromptSignature = null;
    hideTradePrompt();
    return;
  }

  const signature = tradeOfferSignature(gameState);
  if (
    state.tradePromptSignature
    && state.tradePromptSignature !== signature
  ) {
    state.tradePromptDismissed.clear();
    state.tradePromptNotified.clear();
  }
  state.tradePromptSignature = signature;
  const firstNotice = !state.tradePromptNotified.has(signature);
  if (firstNotice) {
    rememberTradePrompt(state.tradePromptNotified, signature);
    const audio = window.CatanAudio;
    if (typeof audio?.playTradeInvite === "function") audio.playTradeInvite();
  }

  const presentation = domesticTradePresentation(gameState, ownSeat);
  const isHandoff = phase.endsWith("_handoff");
  elements["trade-prompt-kicker"].textContent = presentation.isCounter
    ? "COUNTER OFFER"
    : presentation.isBroadcast
      ? "OPEN TRADE REQUEST"
      : "TRADE REQUEST";
  elements["trade-prompt-title"].textContent = presentation.isCounter
    ? presentation.proposerName + "から条件変更"
    : presentation.proposerName + "から交易の申し込み";
  elements["trade-prompt-description"].textContent = isHandoff
    ? "あなた宛ての提案です。「提案を見る」で条件と回答操作を表示します。"
    : presentation.receiveOperator === "or"
      ? "OR条件です。実行する資源候補を1つ選んで承諾するか、拒否・条件変更を選んでください。"
      : "交換する向きを確認して、承諾・拒否・条件変更を選んでください。";
  renderTradePromptTerms(presentation, isHandoff);
  renderTradePromptActions(options, isHandoff, presentation);

  const blockingModalOpen = !elements["rules-drawer"]?.hidden
    || state.marketEditorOpen
    || state.auctionEditorOpen
    || state.creditEditorOpen;
  if (!state.tradePromptDismissed.has(signature) && !blockingModalOpen) {
    showTradePrompt(firstNotice);
  } else {
    hideTradePrompt();
  }
}

function rememberTradePrompt(collection, signature) {
  collection.add(signature);
  while (collection.size > 40) {
    collection.delete(collection.values().next().value);
  }
}

function renderTradePromptTerms(presentation, hiddenForHandoff) {
  const terms = elements["trade-prompt-terms"];
  terms.replaceChildren();
  if (hiddenForHandoff) {
    const notice = document.createElement("div");
    notice.className = "trade-term-card incoming trade-terms-sealed";
    notice.append(
      textElement("span", "PRIVATE HANDOFF"),
      textElement("strong", "条件は確認操作のあとに表示されます"),
    );
    terms.append(notice);
    return;
  }
  terms.append(
    buildTradeTermCard(
      "outgoing",
      "あなたが渡す",
      formatTradeBundle(presentation.outgoing, presentation.outgoingOperator),
    ),
    buildTradeTermCard(
      "incoming",
      "あなたが受け取る",
      formatTradeBundle(presentation.incoming, presentation.incomingOperator),
    ),
  );
}

function buildTradeTermCard(className, label, value) {
  const card = document.createElement("article");
  card.className = "trade-term-card " + className;
  card.append(textElement("span", label), textElement("strong", value));
  return card;
}

function renderTradePromptActions(options, isHandoff, presentation) {
  const container = elements["trade-prompt-actions"];
  container.replaceChildren();
  const allowed = isHandoff
    ? ["trade_reveal"]
    : ["trade_accept", "trade_counter", "trade_reject"];
  const labels = {
    trade_reveal: "提案を見る",
    trade_accept: "この条件を承諾",
    trade_counter: "条件を変更する",
    trade_reject: "今回は拒否",
  };
  for (const command of allowed) {
    const matching = options.filter((candidate) => candidate.command === command);
    for (const option of matching) {
      let label = labels[command];
      if (command === "trade_accept" && option.args?.resource) {
        const resource = option.args.resource;
        const bundle = presentation.outgoingSide === "receive"
          ? presentation.outgoing
          : presentation.incoming;
        const count = Number(bundle?.[resource]) || 1;
        const direction = presentation.outgoingSide === "receive" ? "渡して" : "受け取って";
        label = `${RESOURCE_LABELS[resource] || resource}${count}を${direction}承諾`;
      }
      const button = createActionButton(option, label);
      button.addEventListener("click", () => {
        setTradePromptButtonsDisabled(true);
        if (command !== "trade_reveal") dismissTradePrompt();
      });
      container.append(button);
    }
  }
}

function setTradePromptButtonsDisabled(disabled) {
  const buttons = elements["trade-prompt-actions"]?.querySelectorAll?.("button") || [];
  for (const button of buttons) button.disabled = disabled;
}

function showTradePrompt(shouldFocus = false) {
  const prompt = elements["trade-prompt"];
  if (!prompt) return;
  const wasHidden = prompt.hidden;
  if (wasHidden) {
    tradePromptReturnFocus = document.activeElement || null;
  }
  prompt.hidden = false;
  syncModalBodyState();
  if (shouldFocus || wasHidden) {
    window.requestAnimationFrame(() => {
      prompt.querySelector?.(".trade-prompt-card")?.focus();
    });
  }
}

function hideTradePrompt({ restoreFocus = true } = {}) {
  const prompt = elements["trade-prompt"];
  const wasOpen = Boolean(prompt && !prompt.hidden);
  if (prompt) prompt.hidden = true;
  syncModalBodyState();
  if (wasOpen && restoreFocus) tradePromptReturnFocus?.focus?.();
  if (wasOpen) tradePromptReturnFocus = null;
}

function dismissTradePrompt() {
  if (state.tradePromptSignature) {
    rememberTradePrompt(
      state.tradePromptDismissed,
      state.tradePromptSignature,
    );
  }
  hideTradePrompt();
}

function syncAudioScene(view) {
  const audio = window.CatanAudio;
  if (typeof audio?.setScene !== "function") return;
  let scene = view;
  const specialPhase = state.snapshot?.state?.phase?.special_phase;
  if (
    view === "game"
    && typeof specialPhase === "string"
    && (specialPhase.startsWith("domestic_trade_")
      || specialPhase.startsWith("bank_trade_"))
  ) {
    scene = "trade";
  }
  audio.setScene(scene);
}

async function sendGameCommand(option) {
  if (state.replayIndex !== null || state.commandPending || !state.snapshot) return;
  state.commandPending = true;
  renderActions(state.snapshot.command_options || []);
  renderTradeMarket(
    state.snapshot.state,
    state.snapshot.command_options || [],
    state.welcome?.seat_index,
  );
  renderTradeAuction(
    state.snapshot.state,
    state.snapshot.command_options || [],
    state.welcome?.seat_index,
  );
  renderResourceCredit(
    state.snapshot.state,
    state.snapshot.command_options || [],
    state.welcome?.seat_index,
  );
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
  variantState = null,
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
  const announcedHarborId = forecastAnnouncedHarborId(variantState);
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
    drawBoardHarbor(
      harborLayer,
      harborLayout,
      announcedHarborId === harborLayout.harbor.id,
    );
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
        Boolean(edge.forecast_blocked),
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

function harborForecastPresentation(harbor, forecastAnnounced = false) {
  const blocked = harbor?.forecast_blocked === true;
  const announced = forecastAnnounced === true;
  const harborId = typeof harbor?.id === "string" ? harbor.id : "";
  const match = /^harbor-(0|[1-9][0-9]?)$/.exec(harborId);
  const ordinal = match ? Number(match[1]) + 1 : null;
  const classNames = ["board-harbor"];
  if (blocked) classNames.push("forecast-harbor-blocked");
  if (announced) classNames.push("forecast-harbor-announced");
  const titleStatus = [
    blocked ? "港湾封鎖中" : "",
    announced ? "港湾封鎖を予告中" : "",
  ].filter(Boolean).join("・");
  let statusLabel = "";
  if (blocked && announced) {
    statusLabel = `🔒${ordinal === null ? "" : ` #${ordinal}`} 封鎖・次回予告`;
  } else if (blocked) {
    statusLabel = `🔒${ordinal === null ? "" : ` #${ordinal}`} 封鎖`;
  } else if (announced) {
    statusLabel = `⚠${ordinal === null ? "" : ` #${ordinal}`} 予告`;
  }
  return {
    announced,
    blocked,
    className: classNames.join(" "),
    title: `交換所 ${harbor?.label || ""}${titleStatus ? `・${titleStatus}` : ""}`,
    statusClass: announced
      ? "forecast-harbor-notice forecast-harbor-notice-announced"
      : "forecast-harbor-notice forecast-harbor-notice-blocked",
    statusLabel,
  };
}

function drawBoardHarbor(layer, layout, forecastAnnounced = false) {
  const { harbor, geometry, dock, rect, connectorLead, connectorEnd } = layout;
  const presentation = harborForecastPresentation(harbor, forecastAnnounced);
  const group = svg("g", {
    class: presentation.className,
    "data-harbor-id": harbor.id,
    "data-forecast-announced": presentation.announced ? "true" : "false",
    "pointer-events": "none",
  });
  appendSvgTitle(group, presentation.title);
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
  if (presentation.statusLabel) {
    const status = svg("text", {
      x: rect.x + rect.width / 2,
      y: rect.y - 7,
      class: presentation.statusClass,
      "text-anchor": "middle",
    });
    status.textContent = presentation.statusLabel;
    group.append(status);
  }
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
  forecastBlocked = false,
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
    class: [
      "board-piece",
      animated ? "build-enter build-enter-road" : "",
      forecastBlocked ? "forecast-road-blocked" : "",
    ].filter(Boolean).join(" "),
    "data-piece-id": targetId,
    "pointer-events": "none",
  });
  appendSvgTitle(
    group,
    `${players?.[ownerIndex]?.name || `Player ${ownerIndex + 1}`}の街道${forecastBlocked ? "・地震で通行不能" : ""}`,
  );

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
  if (forecastBlocked) {
    const middle = localPoint(length * 0.5, 0);
    group.append(
      svg("path", {
        d: `M ${middle.x - 9} ${middle.y - 9} l 5 5 l -4 5 l 8 7 l -2 6`,
        class: "forecast-road-crack",
      }),
    );
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

function developmentCardInventoryPresentation(player, isOwnPlayer) {
  const hidden = {
    visible: false,
    total: 0,
    empty: true,
    usable: [],
    newlyPurchased: [],
    victoryPoints: 0,
  };
  if (
    !isOwnPlayer
    || !isDevelopmentCardCountMap(player?.development_cards)
    || !isDevelopmentCardCountMap(player?.new_development_cards)
    || !Number.isInteger(player?.victory_point_cards)
    || player.victory_point_cards < 0
  ) {
    return hidden;
  }
  const usable = developmentCardEntries(player.development_cards);
  const newlyPurchased = developmentCardEntries(
    player.new_development_cards,
  );
  const victoryPoints = player.victory_point_cards;
  const total = usable.reduce((sum, item) => sum + item.count, 0)
    + newlyPurchased.reduce((sum, item) => sum + item.count, 0)
    + victoryPoints;
  return {
    visible: true,
    total,
    empty: total === 0,
    usable,
    newlyPurchased,
    victoryPoints,
  };
}

function isDevelopmentCardCountMap(value) {
  return Boolean(
    value
    && typeof value === "object"
    && !Array.isArray(value)
    && DEVELOPMENT_CARD_KEYS.every(
      (key) => Number.isInteger(value[key]) && value[key] >= 0,
    ),
  );
}

function developmentCardEntries(counts) {
  return DEVELOPMENT_CARD_KEYS.flatMap((key) => {
    const count = counts[key];
    return count > 0
      ? [{ key, label: DEVELOPMENT_CARD_LABELS[key], count }]
      : [];
  });
}

function createDevelopmentCardInventory(player, isOwnPlayer) {
  const inventory = developmentCardInventoryPresentation(
    player,
    isOwnPlayer,
  );
  if (!inventory.visible) return null;

  const details = document.createElement("details");
  details.className = "development-inventory";
  details.open = state.developmentInventoryOpen === null
    ? inventory.total > 0
    : state.developmentInventoryOpen;
  details.addEventListener("toggle", () => {
    state.developmentInventoryOpen = details.open;
  });

  const summary = document.createElement("summary");
  summary.append(
    textElement("span", "発展カードの内訳"),
    textElement(
      "span",
      inventory.empty ? "所持なし" : `${inventory.total}枚`,
      "development-inventory-count",
    ),
  );
  details.append(summary);

  const body = document.createElement("div");
  body.className = "development-inventory-body";
  const groups = document.createElement("div");
  groups.className = "development-inventory-grid";
  groups.append(
    createDevelopmentCardGroup({
      title: "使用候補",
      description: "前の手番までに購入（状況により使用不可）",
      entries: inventory.usable,
      emptyLabel: "使用候補のカードなし",
      className: "usable",
    }),
    createDevelopmentCardGroup({
      title: "今手番に購入",
      description: "次の自分の手番から使用できます",
      entries: inventory.newlyPurchased,
      emptyLabel: "新しく購入したカードなし",
      className: "new",
    }),
    createVictoryPointCardGroup(inventory.victoryPoints),
  );
  body.append(
    groups,
    textElement(
      "p",
      "この内訳はあなたにだけ表示されています。",
      "development-inventory-privacy",
    ),
  );
  details.append(body);
  return details;
}

function createDevelopmentCardGroup({
  title,
  description,
  entries,
  emptyLabel,
  className,
}) {
  const group = document.createElement("section");
  group.className = `development-inventory-group ${className}`;
  group.setAttribute("aria-label", title);
  group.append(
    textElement("strong", title),
    textElement("small", description),
    createDevelopmentCardChipList(entries, emptyLabel),
  );
  return group;
}

function createDevelopmentCardChipList(entries, emptyLabel) {
  const list = document.createElement("div");
  list.className = "development-card-chips";
  if (!entries.length) {
    list.append(
      textElement("span", emptyLabel, "development-card-empty"),
    );
    return list;
  }
  for (const entry of entries) {
    list.append(
      textElement(
        "span",
        `${entry.label} ×${entry.count}`,
        "development-card-chip",
      ),
    );
  }
  return list;
}

function createVictoryPointCardGroup(count) {
  const group = document.createElement("section");
  group.className = "development-inventory-group private";
  group.setAttribute("aria-label", "勝利点カード（非公開）");
  group.append(
    textElement("strong", "勝利点カード（非公開）"),
    textElement("small", "勝利条件へ加算される秘密得点です"),
    createDevelopmentCardChipList(
      count > 0 ? [{ label: "勝利点", count }] : [],
      "勝利点カードなし",
    ),
  );
  return group;
}

function renderPlayers(
  gameState,
  activeSeat,
  ownSeat,
  snapshotViewerSeat,
  personalityMode,
) {
  const list = elements["player-list"];
  list.replaceChildren();
  const publicPoints = calculatePublicPoints(gameState);
  const finished = gameState.phase?.name === "finished";
  const finalScoresAvailable = finished && Array.isArray(state.matchResult?.standings);
  gameState.players.forEach((player, index) => {
    const card = document.createElement("article");
    card.className = `web-player-card${index === activeSeat ? " current" : ""}`;
    const color = document.createElement("span");
    color.className = "player-color";
    color.style.background = playerColor(gameState.players, index);
    const main = document.createElement("div");
    main.className = "player-main";
    const identity = playerIdentityLabel(
      player,
      personalityMode,
    );
    main.append(
      textElement("strong", identity.trim()),
      textElement(
        "small",
        `${index === ownSeat ? "あなた · " : ""}手札${player.resource_total ?? resourceTotal(player.resources)}枚 · 発展${player.development_card_total ?? 0}枚`,
      ),
    );
    if (player.resources && typeof player.resources === "object") {
      const reservedCount = Math.max(
        0,
        Number(player.resource_total || 0) - resourceTotal(player.resources),
      );
      if (reservedCount > 0) {
        main.append(
          textElement(
            "small",
            `市場・競売に${reservedCount}枚取り置き中`,
            "market-escrow",
          ),
        );
      }
    }
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
    card.append(
      color,
      main,
      textElement(
        "span",
        `${finalScoresAvailable ? "VP" : "公開VP"} ${publicPoints[index]}`,
        "player-vp",
      ),
    );
    const awards = [];
    if (gameState.phase?.longest_road_owner === index) {
      const length = Number(gameState.phase?.longest_road_length) || 0;
      awards.push(`最長交易路 +2${length ? `（${length}本）` : ""}`);
    }
    if (gameState.phase?.largest_army_owner === index) {
      const size = Number(gameState.phase?.largest_army_size) || Number(player.played_knights) || 0;
      awards.push(`最大騎士力 +2${size ? `（騎士${size}枚使用）` : ""}`);
    }
    if (awards.length) {
      const awardList = document.createElement("div");
      awardList.className = "player-awards";
      awardList.setAttribute("aria-label", "勝利点ボーナス");
      awards.forEach((label) => awardList.append(textElement("span", label, "player-award-chip")));
      card.append(awardList);
    }
    if (player.resources && typeof player.resources === "object") {
      const strip = document.createElement("div");
      strip.className = "resource-strip";
      for (const resource of ["WOOD", "SHEEP", "WHEAT", "BRICK", "ORE"]) {
        strip.append(textElement("span", `${RESOURCE_LABELS[resource]} ${player.resources[resource] || 0}`, "resource-chip"));
      }
      card.append(strip);
    }
    const inventory = createDevelopmentCardInventory(
      player,
      index === ownSeat && index === snapshotViewerSeat,
    );
    if (inventory) card.append(inventory);
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
    copy.append(createResultVpBreakdown(row));
    item.append(
      textElement("span", `#${row.rank || "—"}`, "result-rank"),
      color,
      copy,
      textElement("span", `${row.victory_points ?? 0} VP`, "result-score"),
    );
    container.append(item);
  }
}

function createResultVpBreakdown(row) {
  const breakdown = row.vp_breakdown || {};
  const settlements = breakdown.settlements || {
    count: Number(row.settlements) || 0,
    points: Number(row.settlements) || 0,
  };
  const cities = breakdown.cities || {
    count: Number(row.cities) || 0,
    points: (Number(row.cities) || 0) * 2,
  };
  const longestRoad = breakdown.longest_road || {
    awarded: Boolean(row.longest_road),
    points: row.longest_road ? 2 : 0,
  };
  const largestArmy = breakdown.largest_army || {
    awarded: Boolean(row.largest_army),
    points: row.largest_army ? 2 : 0,
  };
  const debtPenalty = breakdown.debt_penalty || null;
  const visiblePoints =
    Number(settlements.points || 0)
    + Number(cities.points || 0)
    + Number(longestRoad.points || 0)
    + Number(largestArmy.points || 0)
    + Number(debtPenalty?.points || 0);
  const victoryPointCards = breakdown.victory_point_cards || {
    count: Math.max(0, Number(row.victory_points || 0) - visiblePoints),
    points: Math.max(0, Number(row.victory_points || 0) - visiblePoints),
  };
  const components = [
    {
      label: `開拓地 ${Number(settlements.points) || 0}点（${Number(settlements.count) || 0}軒）`,
      awarded: Number(settlements.points) > 0,
    },
    {
      label: `都市 ${Number(cities.points) || 0}点（${Number(cities.count) || 0}軒）`,
      awarded: Number(cities.points) > 0,
    },
    {
      label: `最長交易路 ${Number(longestRoad.points) || 0}点`,
      awarded: Boolean(longestRoad.awarded),
      bonus: true,
    },
    {
      label: `最大騎士力 ${Number(largestArmy.points) || 0}点`,
      awarded: Boolean(largestArmy.awarded),
      bonus: true,
    },
    ...(debtPenalty && Number(debtPenalty.points) < 0
      ? [{
        label: `資源信用（${debtPenalty.status === "delinquent" ? "延滞" : "返済中"}） ${Number(debtPenalty.points)}点`,
        awarded: true,
        penalty: true,
      }]
      : []),
    {
      label: `勝利点カード ${Number(victoryPointCards.points) || 0}点（${Number(victoryPointCards.count) || 0}枚）`,
      awarded: Number(victoryPointCards.points) > 0,
      private: true,
    },
  ];
  const container = document.createElement("div");
  container.className = "result-vp-breakdown";
  container.setAttribute("aria-label", `${row.name}の勝利点内訳`);
  for (const component of components) {
    const classNames = ["result-vp-chip"];
    if (component.bonus && component.awarded) classNames.push("award");
    if (component.private && component.awarded) classNames.push("private");
    if (component.penalty) classNames.push("penalty");
    if (!component.awarded) classNames.push("zero");
    container.append(textElement("span", component.label, classNames.join(" ")));
  }
  return container;
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
  elements["replay-slider"].disabled = !available || state.replayRequestPending;
  const atStart = index <= 0;
  const atEnd = state.replayIndex === null || index >= count - 1;
  elements["replay-first"].disabled = !available || atStart || state.replayRequestPending;
  elements["replay-previous"].disabled = !available || atStart || state.replayRequestPending;
  elements["replay-play"].disabled = count < 2;
  elements["replay-next"].disabled = !available || atEnd || state.replayRequestPending;
  elements["replay-last"].disabled = !available || atEnd || state.replayRequestPending;
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
  const generation = state.replayRequestGeneration;
  state.replayRequestPending = true;
  state.replayExpectedIndex = index;
  syncReplayControls();
  try {
    await sendMessage(wireMessage("replay_frame_request", { index }));
  } catch (error) {
    if (state.replayRequestGeneration === generation) {
      stopReplay();
      showToast(error.message, true);
    }
  } finally {
    if (state.replayRequestGeneration === generation) {
      state.replayRequestPending = false;
      state.replayExpectedIndex = null;
      syncReplayControls();
    }
  }
}

function invalidateReplayRequest() {
  state.replayRequestGeneration += 1;
  state.replayRequestPending = false;
  state.replayExpectedIndex = null;
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
  invalidateReplayRequest();
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
  const variantState = gameState.variant_state || state.snapshot?.state?.variant_state;
  const creditPublic = variantComponentPublic(variantState, "credit");
  if (Array.isArray(creditPublic?.loans)) {
    for (const loan of creditPublic.loans) {
      const borrower = Number(loan?.borrower_index);
      if (!Number.isInteger(borrower) || borrower < 0 || borrower >= points.length) continue;
      points[borrower] += loan?.status === "delinquent" ? -2 : -1;
    }
  }
  return points.map((value) => Math.max(0, value));
}

function activePlayerIndex(gameState) {
  const phase = gameState.phase || {};
  const initial = gameState.initial || {};
  const special = gameState.special || {};
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
    return domesticTradeActorSeat(gameState);
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

function commandLabel(option, gameState = state.snapshot?.state) {
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
    trade_accept: args.resource
      ? `${RESOURCE_LABELS[args.resource] || args.resource}を選んで承諾`
      : "承諾する",
    trade_counter: "条件を変更",
    trade_reject: "拒否する",
    market_create: "市場へ注文を出す",
    market_fill: "市場の注文を購入",
    market_cancel: "市場の注文を取消",
    auction_create: "公開競売を開く",
    auction_bid: "公開競売へ入札",
    auction_cancel_bid: "競売の入札を取消",
    auction_accept: "競売の落札者を決定",
    auction_cancel: "公開競売を取消",
    credit_borrow: `${RESOURCE_LABELS[args.resource] || "資源"}を銀行から借りる`,
    credit_repay: "資源信用の債務を返済",
    finish_road_building: "街道建設を終了",
  };
  if (fixed[option.command]) return fixed[option.command];
  if (option.command === "build") return `${PIECE_LABELS[args.piece] || args.piece}を建設`;
  if (option.command === "initial_place") return args.target?.startsWith("edge") ? "初期街道を配置" : "初期開拓地を配置";
  if (option.command === "move_robber") return "盗賊の移動先";
  if (option.command === "select_resource") return `${RESOURCE_LABELS[args.resource] || args.resource}を選択`;
  if (option.command === "steal" || option.command === "trade_partner") {
    const seatIndex = Number(args.seat_index);
    const playerName = Number.isInteger(seatIndex)
      ? gameState?.players?.[seatIndex]?.name
      : null;
    const targetName = typeof playerName === "string" && playerName.trim()
      ? playerName.trim()
      : `プレイヤー${seatIndex + 1}`;
    return option.command === "steal"
      ? `${targetName}から資源を1枚奪う`
      : `${targetName}と交渉する`;
  }
  if (option.command === "trade_edit_side") return args.side === "give" ? "渡す資源を編集" : "受け取る資源を編集";
  if (option.command === "trade_receive_operator") {
    return args.operator === "or" ? "この欄をORにする" : "この欄をすべてにする";
  }
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

function publicAIPersonalityLabel(personalityMode, assignedPersonality) {
  if (personalityMode === "mixed") return null;
  return aiPersonalityLabel(assignedPersonality || personalityMode);
}

function lobbyAIMemberDescription(member, personalityMode) {
  const personality = publicAIPersonalityLabel(
    personalityMode,
    member?.ai_personality,
  );
  return personality
    ? `${personality}AI · サーバー管理`
    : "AI · 性格は対局後に公開 · サーバー管理";
}

function playerIdentityLabel(player, personalityMode) {
  const base = `${player?.marker || ""} ${player?.name || ""}`.trim();
  if (!player?.is_ai) return base;
  const personality = publicAIPersonalityLabel(
    personalityMode,
    player.ai_personality,
  );
  return personality
    ? `${base}・${personality}AI`
    : `${base}・AI`;
}

function aiCommentaryHeading(status, personalityMode, isActiveAI = true) {
  const personality = publicAIPersonalityLabel(
    personalityMode,
    status?.personality,
  );
  const speaker = personality
    ? `${status?.player_name || "AI"}（${personality}）`
    : status?.player_name || "AI";
  const timing = isActiveAI ? "判断中" : "直前のAI判断";
  return `${timing} — ${speaker}: ${status?.title || "判断中"}`;
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

let rulesReturnFocus = null;
let tradePromptReturnFocus = null;
let marketEditorReturnFocus = null;
let auctionEditorReturnFocus = null;
let creditEditorReturnFocus = null;

function openMarketEditor() {
  if (
    state.marketEditorOpen
    || state.auctionEditorOpen
    || state.creditEditorOpen
    || state.replayIndex !== null
  ) return;
  const createOption = (state.snapshot?.command_options || []).find(
    (option) => option.command === "market_create",
  );
  if (!createOption) {
    showToast("注文は自分の行動手番に作成できます。", true);
    return;
  }
  marketEditorReturnFocus = document.activeElement || elements["market-create-button"];
  hideTradePrompt({ restoreFocus: false });
  state.marketDraft = {
    offer: emptyMarketBundle(),
    wanted: emptyMarketBundle(),
  };
  state.marketEditorOpen = true;
  elements["market-editor"].hidden = false;
  renderMarketEditor();
  syncModalBodyState();
  window.requestAnimationFrame(() => {
    elements["market-editor"]?.querySelector?.(".market-editor-card")?.focus();
  });
}

function auctionFromSnapshot(auctionId) {
  const auctions = variantComponentPublic(
    state.snapshot?.state?.variant_state,
    "trade2",
  )?.auctions;
  return Array.isArray(auctions)
    ? auctions.find((auction) => auction.auction_id === auctionId) || null
    : null;
}

function availableAuctionResources(auction = null) {
  const available = availableMarketResources();
  const ownSeat = state.welcome?.seat_index;
  const ownBid = Number.isInteger(ownSeat)
    ? auction?.bids?.find((bid) => Number(bid.bidder_index) === ownSeat)
    : null;
  for (const [resource, amount] of Object.entries(ownBid?.offer || {})) {
    if (TRADE_RESOURCE_KEYS.includes(resource)) {
      available[resource] = Math.min(
        MARKET_RESOURCE_LIMIT,
        Number(available[resource] || 0) + Number(amount || 0),
      );
    }
  }
  return available;
}

function auctionDraftValidation(draft, available, auction = null) {
  const offer = compactMarketBundle(draft?.offer);
  if (!Object.keys(offer).length) {
    return { valid: false, offer, message: "資源を1枚以上選んでください。" };
  }
  if (Object.entries(offer).some(([resource, count]) => (
    !Number.isInteger(count)
    || count < 1
    || count > Number(available?.[resource] || 0)
  ))) {
    return { valid: false, offer, message: "使える手札を超えて指定しています。" };
  }
  if (draft?.mode === "create") {
    const minimum = Number(draft.minimumBidCards);
    if (!Number.isInteger(minimum) || minimum < 1 || minimum > MARKET_RESOURCE_LIMIT) {
      return { valid: false, offer, message: "最低入札枚数は1〜19枚です。" };
    }
    return {
      valid: true,
      offer,
      minimumBidCards: minimum,
      message: `${formatMarketBundle(offer)} を出品 / 最低入札 ${minimum}枚`,
    };
  }
  const minimum = Number(auction?.minimum_bid_cards) || 1;
  if (Object.keys(offer).some((resource) => Number(auction?.offer?.[resource]) > 0)) {
    return {
      valid: false,
      offer,
      message: "出品物と同じ資源は、この競売の入札に使えません。",
    };
  }
  if (Object.values(offer).reduce((total, count) => total + count, 0) < minimum) {
    return {
      valid: false,
      offer,
      message: `入札は合計${minimum}枚以上にしてください。`,
    };
  }
  const ownSeat = state.welcome?.seat_index;
  const previous = auction?.bids?.find(
    (bid) => Number(bid.bidder_index) === Number(ownSeat),
  );
  if (
    previous
    && JSON.stringify(compactMarketBundle(previous.offer)) === JSON.stringify(offer)
  ) {
    return { valid: false, offer, message: "現在と異なる入札内容を指定してください。" };
  }
  return {
    valid: true,
    offer,
    message: `${formatMarketBundle(offer)} で入札します`,
  };
}

function openAuctionCreateEditor() {
  if (
    state.auctionEditorOpen
    || state.marketEditorOpen
    || state.creditEditorOpen
    || state.replayIndex !== null
  ) return;
  const createOption = (state.snapshot?.command_options || []).find(
    (option) => option.command === "auction_create",
  );
  if (!createOption) {
    showToast("競売は自分の行動手番に開始できます。", true);
    return;
  }
  auctionEditorReturnFocus = document.activeElement || elements["auction-create-button"];
  hideTradePrompt({ restoreFocus: false });
  state.auctionDraft = {
    mode: "create",
    offer: emptyMarketBundle(),
    minimumBidCards: 1,
  };
  state.auctionEditorOpen = true;
  elements["auction-editor"].hidden = false;
  renderAuctionEditor();
  syncModalBodyState();
  window.requestAnimationFrame(() => {
    elements["auction-editor"]?.querySelector?.(".auction-editor-card")?.focus();
  });
}

function openAuctionBidEditor(auction) {
  if (
    state.auctionEditorOpen
    || state.marketEditorOpen
    || state.creditEditorOpen
    || state.replayIndex !== null
  ) return;
  const bidOption = findAuctionOption(
    state.snapshot?.command_options || [],
    "auction_bid",
    auction,
  );
  if (!bidOption) {
    showToast("現在はこの競売へ入札できません。", true);
    return;
  }
  const ownSeat = state.welcome?.seat_index;
  const previous = auction.bids?.find(
    (bid) => Number(bid.bidder_index) === Number(ownSeat),
  );
  auctionEditorReturnFocus = document.activeElement || elements["auction-panel"];
  hideTradePrompt({ restoreFocus: false });
  state.auctionDraft = {
    mode: "bid",
    auctionId: auction.auction_id,
    revision: auction.revision,
    offer: {
      ...emptyMarketBundle(),
      ...(previous?.offer || {}),
    },
  };
  state.auctionEditorOpen = true;
  elements["auction-editor"].hidden = false;
  renderAuctionEditor();
  syncModalBodyState();
  window.requestAnimationFrame(() => {
    elements["auction-editor"]?.querySelector?.(".auction-editor-card")?.focus();
  });
}

function closeAuctionEditor({ restoreFocus = true } = {}) {
  const wasOpen = state.auctionEditorOpen;
  state.auctionEditorOpen = false;
  state.auctionDraft = null;
  if (elements["auction-editor"]) elements["auction-editor"].hidden = true;
  if (!wasOpen) return;
  syncModalBodyState();
  if (restoreFocus) auctionEditorReturnFocus?.focus?.();
  auctionEditorReturnFocus = null;
  if (state.snapshot?.state) {
    renderIncomingTradePrompt(
      state.snapshot.state,
      state.snapshot.command_options || [],
      state.welcome?.seat_index,
    );
  }
}

function adjustAuctionDraft(resource, delta) {
  if (
    !state.auctionDraft
    || !TRADE_RESOURCE_KEYS.includes(resource)
    || ![-1, 1].includes(Number(delta))
  ) return;
  const auction = state.auctionDraft.mode === "bid"
    ? auctionFromSnapshot(state.auctionDraft.auctionId)
    : null;
  const available = availableAuctionResources(auction);
  const current = Number(state.auctionDraft.offer[resource]) || 0;
  const maximum = Number(available[resource]) || 0;
  state.auctionDraft.offer[resource] = Math.max(
    0,
    Math.min(maximum, current + Number(delta)),
  );
  renderAuctionEditor();
}

function adjustAuctionMinimum(delta) {
  if (state.auctionDraft?.mode !== "create") return;
  state.auctionDraft.minimumBidCards = Math.max(
    1,
    Math.min(
      MARKET_RESOURCE_LIMIT,
      Number(state.auctionDraft.minimumBidCards) + Number(delta),
    ),
  );
  renderAuctionEditor();
}

function renderAuctionEditor() {
  if (!state.auctionEditorOpen || !state.auctionDraft) return;
  const isCreate = state.auctionDraft.mode === "create";
  const auction = isCreate ? null : auctionFromSnapshot(state.auctionDraft.auctionId);
  const available = availableAuctionResources(auction);
  elements["auction-editor-kicker"].textContent = isCreate
    ? "CREATE AUCTION"
    : "PLACE OR UPDATE BID";
  elements["auction-editor-title"].textContent = isCreate
    ? "公開競売を開く"
    : "公開競売へ入札";
  elements["auction-editor-description"].textContent = isCreate
    ? "出品する資源と、受け付ける最低入札枚数を指定します。"
    : `${formatMarketBundle(auction?.offer)} に対する入札資源を指定します。`;
  const body = elements["auction-editor-body"];
  body.replaceChildren(createAuctionResourceEditor(available, auction));
  if (isCreate) body.append(createAuctionMinimumEditor());
  const validation = auctionDraftValidation(state.auctionDraft, available, auction);
  elements["auction-editor-summary"].textContent = validation.message;
  elements["auction-editor-submit"].textContent = isCreate
    ? "この内容で競売を開く"
    : "この内容で入札する";
  elements["auction-editor-submit"].disabled = !validation.valid || state.commandPending;
  elements["auction-editor-footnote"].textContent = isCreate
    ? "出品資源は競売の取消・期限切れ・落札まで取り置かれます。"
    : `最低入札は合計${Number(auction?.minimum_bid_cards) || 1}枚です。入札資源は取消・期限切れ・落札まで取り置かれます。`;
}

function createAuctionResourceEditor(available, auction) {
  const section = document.createElement("section");
  section.className = "market-editor-side offer";
  const heading = document.createElement("div");
  heading.className = "market-editor-side-heading";
  heading.append(
    textElement(
      "strong",
      state.auctionDraft.mode === "create" ? "出品する資源" : "入札する資源",
    ),
    textElement("small", "現在使える手札の範囲で指定します。"),
  );
  const list = document.createElement("div");
  list.className = "market-resource-list";
  for (const resource of TRADE_RESOURCE_KEYS) {
    const current = Number(state.auctionDraft.offer[resource]) || 0;
    const blocked = state.auctionDraft.mode === "bid"
      && Number(auction?.offer?.[resource]) > 0;
    const row = document.createElement("div");
    row.className = "market-resource-row";
    row.append(
      textElement(
        "span",
        `${RESOURCE_LABELS[resource]}（手札${available[resource]}）${blocked ? "・入札不可" : ""}`,
        "market-resource-name",
      ),
      createAuctionAdjustButton(resource, -1, current <= 0),
      textElement("span", String(current), "market-resource-count"),
      createAuctionAdjustButton(
        resource,
        1,
        blocked || current >= Number(available[resource] || 0),
      ),
    );
    list.append(row);
  }
  section.append(heading, list);
  return section;
}

function createAuctionAdjustButton(resource, delta, disabled) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "market-adjust-button";
  button.textContent = delta > 0 ? "+" : "−";
  button.disabled = disabled || state.commandPending;
  button.setAttribute(
    "aria-label",
    `${RESOURCE_LABELS[resource]}を${delta > 0 ? "増やす" : "減らす"}`,
  );
  button.addEventListener("click", () => adjustAuctionDraft(resource, delta));
  return button;
}

function createAuctionMinimumEditor() {
  const editor = document.createElement("div");
  editor.className = "auction-minimum-editor";
  const label = document.createElement("div");
  label.append(
    textElement("strong", "最低入札枚数"),
    textElement("small", "入札する資源の種類は相手が自由に選べます。"),
  );
  editor.append(
    label,
    createAuctionMinimumButton(-1),
    textElement(
      "span",
      String(state.auctionDraft.minimumBidCards),
      "market-resource-count",
    ),
    createAuctionMinimumButton(1),
  );
  return editor;
}

function createAuctionMinimumButton(delta) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "market-adjust-button";
  button.textContent = delta > 0 ? "+" : "−";
  button.setAttribute(
    "aria-label",
    `最低入札枚数を${delta > 0 ? "増やす" : "減らす"}`,
  );
  const current = Number(state.auctionDraft.minimumBidCards);
  button.disabled = state.commandPending
    || (delta < 0 && current <= 1)
    || (delta > 0 && current >= MARKET_RESOURCE_LIMIT);
  button.addEventListener("click", () => adjustAuctionMinimum(delta));
  return button;
}

function submitAuctionDraft() {
  if (!state.auctionEditorOpen || !state.auctionDraft) return;
  const isCreate = state.auctionDraft.mode === "create";
  const auction = isCreate ? null : auctionFromSnapshot(state.auctionDraft.auctionId);
  const validation = auctionDraftValidation(
    state.auctionDraft,
    availableAuctionResources(auction),
    auction,
  );
  if (!validation.valid) {
    renderAuctionEditor();
    return;
  }
  const option = isCreate
    ? (state.snapshot?.command_options || []).find(
      (candidate) => candidate.command === "auction_create",
    )
    : findAuctionOption(
      state.snapshot?.command_options || [],
      "auction_bid",
      auction,
    );
  if (!option) {
    closeAuctionEditor();
    showToast("競売状態が更新されました。内容を確認してください。", true);
    return;
  }
  const command = isCreate
    ? {
      command: "auction_create",
      args: {
        offer: validation.offer,
        minimum_bid_cards: validation.minimumBidCards,
      },
    }
    : {
      command: "auction_bid",
      args: {
        auction_id: option.args.auction_id,
        revision: option.args.revision,
        offer: validation.offer,
      },
    };
  closeAuctionEditor();
  sendGameCommand(command);
}

function creditLoanFromSnapshot(loanId) {
  const loans = variantComponentPublic(
    state.snapshot?.state?.variant_state,
    "credit",
  )?.loans;
  return Array.isArray(loans)
    ? loans.find((loan) => loan.loan_id === loanId) || null
    : null;
}

function creditRepayOption(loan) {
  return (state.snapshot?.command_options || []).find((option) => (
    option.command === "credit_repay"
    && option.args?.loan_id === loan?.loan_id
    && Number(option.args?.revision) === Number(loan?.revision)
  )) || null;
}

function creditDraftValidation(draft, available = availableMarketResources(), loan = null) {
  if (draft?.mode === "borrow") {
    const resource = draft.resource;
    if (!TRADE_RESOURCE_KEYS.includes(resource)) {
      return { valid: false, message: "借りる資源を1種類選んでください。" };
    }
    return {
      valid: true,
      resource,
      message: `${RESOURCE_LABELS[resource]}を1枚借ります。期限内の返済は${RESOURCE_LABELS[resource]}1枚＋任意資源1枚です。`,
    };
  }
  const payment = compactMarketBundle(draft?.payment);
  const paymentTotal = Object.values(payment).reduce(
    (total, count) => total + Number(count),
    0,
  );
  if (!loan || !["active", "delinquent"].includes(loan.status)) {
    return { valid: false, payment, message: "債務状態が更新されました。" };
  }
  if (Object.entries(payment).some(([resource, count]) => (
    !Number.isInteger(count)
    || count < 1
    || count > Number(available?.[resource] || 0)
  ))) {
    return { valid: false, payment, message: "現在使える手札を超えて指定しています。" };
  }
  if (loan.status === "active") {
    const borrowed = loan.borrowed_resource;
    if (paymentTotal !== 2 || Number(payment[borrowed] || 0) < 1) {
      return {
        valid: false,
        payment,
        message: `期限内は${RESOURCE_LABELS[borrowed] || "借りた資源"}1枚を含む、合計2枚をまとめて返済します。`,
      };
    }
    return {
      valid: true,
      payment,
      message: `${formatMarketBundle(payment)} を返済し、債務を完済します。`,
    };
  }
  const remaining = Math.max(1, Number(loan.remaining_cards) || 1);
  if (paymentTotal < 1 || paymentTotal > remaining) {
    return {
      valid: false,
      payment,
      message: `延滞債務は任意資源を1〜${remaining}枚選んで返済できます。`,
    };
  }
  return {
    valid: true,
    payment,
    message: paymentTotal === remaining
      ? `${formatMarketBundle(payment)} を返済し、延滞債務を完済します。`
      : `${formatMarketBundle(payment)} を返済します。返済後の残債は${remaining - paymentTotal}枚です。`,
  };
}

function openCreditEditor() {
  const presentation = resourceCreditPresentation(
    state.snapshot?.state?.variant_state,
    state.snapshot?.state?.players || [],
    state.snapshot?.command_options || [],
    state.welcome?.seat_index,
  );
  if (presentation.ownLoan) openCreditRepayEditor(presentation.ownLoan);
  else openCreditBorrowEditor();
}

function openCreditBorrowEditor() {
  if (
    state.creditEditorOpen
    || state.marketEditorOpen
    || state.auctionEditorOpen
    || state.replayIndex !== null
  ) return;
  const borrowOptions = (state.snapshot?.command_options || []).filter(
    (option) => option.command === "credit_borrow"
      && TRADE_RESOURCE_KEYS.includes(option.args?.resource),
  );
  if (!borrowOptions.length) {
    showToast("資源の借入は自分の行動手番に行えます。", true);
    return;
  }
  creditEditorReturnFocus = document.activeElement || elements["credit-open-button"];
  hideTradePrompt({ restoreFocus: false });
  state.creditDraft = { mode: "borrow", resource: null };
  state.creditEditorOpen = true;
  elements["credit-editor"].hidden = false;
  renderCreditEditor();
  syncModalBodyState();
  window.requestAnimationFrame(() => {
    elements["credit-editor"]?.querySelector?.(".credit-editor-card")?.focus();
  });
}

function openCreditRepayEditor(loan) {
  if (
    state.creditEditorOpen
    || state.marketEditorOpen
    || state.auctionEditorOpen
    || state.replayIndex !== null
  ) return;
  if (!creditRepayOption(loan)) {
    showToast("返済は自分の行動手番に行えます。", true);
    return;
  }
  creditEditorReturnFocus = document.activeElement || elements["credit-open-button"];
  hideTradePrompt({ restoreFocus: false });
  state.creditDraft = {
    mode: "repay",
    loanId: loan.loan_id,
    revision: loan.revision,
    payment: emptyMarketBundle(),
  };
  state.creditEditorOpen = true;
  elements["credit-editor"].hidden = false;
  renderCreditEditor();
  syncModalBodyState();
  window.requestAnimationFrame(() => {
    elements["credit-editor"]?.querySelector?.(".credit-editor-card")?.focus();
  });
}

function closeCreditEditor({ restoreFocus = true } = {}) {
  const wasOpen = state.creditEditorOpen;
  state.creditEditorOpen = false;
  state.creditDraft = null;
  if (elements["credit-editor"]) elements["credit-editor"].hidden = true;
  if (!wasOpen) return;
  syncModalBodyState();
  if (restoreFocus) creditEditorReturnFocus?.focus?.();
  creditEditorReturnFocus = null;
  if (state.snapshot?.state) {
    renderIncomingTradePrompt(
      state.snapshot.state,
      state.snapshot.command_options || [],
      state.welcome?.seat_index,
    );
  }
}

function selectCreditBorrowResource(resource) {
  if (state.creditDraft?.mode !== "borrow" || !TRADE_RESOURCE_KEYS.includes(resource)) return;
  state.creditDraft.resource = resource;
  renderCreditEditor();
}

function adjustCreditPayment(resource, delta) {
  if (
    state.creditDraft?.mode !== "repay"
    || !TRADE_RESOURCE_KEYS.includes(resource)
    || ![-1, 1].includes(Number(delta))
  ) return;
  const loan = creditLoanFromSnapshot(state.creditDraft.loanId);
  if (!loan) return;
  const available = availableMarketResources();
  const current = Number(state.creditDraft.payment[resource]) || 0;
  const currentTotal = resourceTotal(state.creditDraft.payment);
  const maximumTotal = loan.status === "delinquent"
    ? Math.max(1, Number(loan.remaining_cards) || 1)
    : 2;
  const maximum = Math.min(
    Number(available[resource]) || 0,
    Math.max(0, current + maximumTotal - currentTotal),
  );
  state.creditDraft.payment[resource] = Math.max(
    0,
    Math.min(maximum, current + Number(delta)),
  );
  renderCreditEditor();
}

function renderCreditEditor() {
  if (!state.creditEditorOpen || !state.creditDraft) return;
  const borrow = state.creditDraft.mode === "borrow";
  const loan = borrow ? null : creditLoanFromSnapshot(state.creditDraft.loanId);
  const available = availableMarketResources();
  elements["credit-editor-kicker"].textContent = borrow
    ? "BORROW ONE RESOURCE"
    : loan?.status === "delinquent"
      ? "REPAY DELINQUENT DEBT"
      : "REPAY ACTIVE LOAN";
  elements["credit-editor-title"].textContent = borrow
    ? "銀行から資源を借りる"
    : loan?.status === "delinquent"
      ? "延滞債務を返済する"
      : "期限内に返済する";
  elements["credit-editor-description"].textContent = borrow
    ? "銀行在庫がある資源から1種類を選びます。"
    : loan?.status === "delinquent"
      ? `任意資源で残り${Number(loan?.remaining_cards) || 0}枚を分割返済できます。`
      : `${RESOURCE_LABELS[loan?.borrowed_resource] || "借りた資源"}1枚を含む合計2枚を選びます。`;
  const body = elements["credit-editor-body"];
  body.replaceChildren(
    borrow
      ? createCreditBorrowChoices()
      : createCreditRepaymentEditor(available, loan),
  );
  const validation = creditDraftValidation(state.creditDraft, available, loan);
  elements["credit-editor-summary"].textContent = validation.message;
  elements["credit-editor-submit"].textContent = borrow
    ? "この資源を借りる"
    : "この内容で返済する";
  elements["credit-editor-submit"].disabled = !validation.valid || state.commandPending;
  elements["credit-editor-footnote"].textContent = borrow
    ? "借入中は公開VPが−1されます。期限を過ぎると任意3枚の延滞債務となり、公開VPは−2です。"
    : loan?.status === "delinquent"
      ? "延滞中も対局は進行します。完済すると公開VPの−2が解除されます。"
      : "期限内の返済は一括です。完済すると公開VPの−1が解除されます。";
}

function createCreditBorrowChoices() {
  const choices = document.createElement("div");
  choices.className = "credit-resource-choices";
  const options = (state.snapshot?.command_options || []).filter(
    (option) => option.command === "credit_borrow"
      && TRADE_RESOURCE_KEYS.includes(option.args?.resource),
  );
  for (const resource of TRADE_RESOURCE_KEYS) {
    const available = options.some((option) => option.args.resource === resource);
    const selected = state.creditDraft?.resource === resource;
    const button = document.createElement("button");
    button.type = "button";
    button.className = `credit-resource-choice${selected ? " selected" : ""}`;
    button.textContent = available
      ? `${RESOURCE_LABELS[resource]}を1枚`
      : `${RESOURCE_LABELS[resource]}・銀行在庫なし`;
    button.disabled = !available || state.commandPending;
    button.setAttribute("aria-pressed", selected ? "true" : "false");
    button.addEventListener("click", () => selectCreditBorrowResource(resource));
    choices.append(button);
  }
  return choices;
}

function createCreditRepaymentEditor(available, loan) {
  const section = document.createElement("section");
  section.className = "credit-payment-editor";
  const list = document.createElement("div");
  list.className = "market-resource-list";
  const currentTotal = resourceTotal(state.creditDraft?.payment);
  const maximumTotal = loan?.status === "delinquent"
    ? Math.max(1, Number(loan?.remaining_cards) || 1)
    : 2;
  for (const resource of TRADE_RESOURCE_KEYS) {
    const current = Number(state.creditDraft?.payment?.[resource]) || 0;
    const row = document.createElement("div");
    row.className = "market-resource-row";
    row.append(
      textElement(
        "span",
        `${RESOURCE_LABELS[resource]}（手札${available[resource]}）${loan?.status === "active" && resource === loan.borrowed_resource ? "・必須" : ""}`,
        "market-resource-name",
      ),
      createCreditPaymentButton(resource, -1, current <= 0),
      textElement("span", String(current), "market-resource-count"),
      createCreditPaymentButton(
        resource,
        1,
        current >= Number(available[resource] || 0) || currentTotal >= maximumTotal,
      ),
    );
    list.append(row);
  }
  section.append(list);
  return section;
}

function createCreditPaymentButton(resource, delta, disabled) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "market-adjust-button";
  button.textContent = delta > 0 ? "+" : "−";
  button.disabled = disabled || state.commandPending;
  button.setAttribute(
    "aria-label",
    `返済する${RESOURCE_LABELS[resource]}を${delta > 0 ? "増やす" : "減らす"}`,
  );
  button.addEventListener("click", () => adjustCreditPayment(resource, delta));
  return button;
}

function submitCreditDraft() {
  if (!state.creditEditorOpen || !state.creditDraft) return;
  const borrow = state.creditDraft.mode === "borrow";
  const loan = borrow ? null : creditLoanFromSnapshot(state.creditDraft.loanId);
  const validation = creditDraftValidation(
    state.creditDraft,
    availableMarketResources(),
    loan,
  );
  const option = borrow
    ? (state.snapshot?.command_options || []).find((candidate) => (
      candidate.command === "credit_borrow"
      && candidate.args?.resource === validation.resource
    ))
    : creditRepayOption(loan);
  if (!validation.valid || !option) {
    renderCreditEditor();
    return;
  }
  const command = borrow
    ? { command: "credit_borrow", args: { resource: validation.resource } }
    : {
      command: "credit_repay",
      args: {
        loan_id: option.args.loan_id,
        revision: option.args.revision,
        payment: validation.payment,
      },
    };
  closeCreditEditor();
  sendGameCommand(command);
}

function closeMarketEditor({ restoreFocus = true } = {}) {
  const editor = elements["market-editor"];
  const wasOpen = state.marketEditorOpen;
  state.marketEditorOpen = false;
  state.marketDraft = null;
  if (editor) editor.hidden = true;
  if (!wasOpen) return;
  syncModalBodyState();
  if (restoreFocus) marketEditorReturnFocus?.focus?.();
  marketEditorReturnFocus = null;
  if (state.snapshot?.state) {
    renderIncomingTradePrompt(
      state.snapshot.state,
      state.snapshot.command_options || [],
      state.welcome?.seat_index,
    );
  }
}

function submitMarketDraft() {
  if (!state.marketEditorOpen || !state.marketDraft) return;
  const createOption = (state.snapshot?.command_options || []).find(
    (option) => option.command === "market_create",
  );
  const validation = marketDraftValidation(state.marketDraft);
  if (!createOption || !validation.valid) {
    renderMarketEditor();
    return;
  }
  const command = {
    command: "market_create",
    args: {
      offer: validation.offer,
      wanted: validation.wanted,
    },
  };
  closeMarketEditor();
  sendGameCommand(command);
}

function syncModalBodyState() {
  const open = Boolean(
    (elements["rules-drawer"] && !elements["rules-drawer"].hidden)
      || (elements["trade-prompt"] && !elements["trade-prompt"].hidden)
      || state.marketEditorOpen
      || state.auctionEditorOpen
      || state.creditEditorOpen,
  );
  document.body?.classList?.toggle("modal-open", open);
  for (const selector of ["main", ".topbar"]) {
    const background = document.querySelector?.(selector);
    if (!background) continue;
    if (open) {
      background.setAttribute("inert", "");
      background.setAttribute("aria-hidden", "true");
    } else {
      background.removeAttribute("inert");
      background.removeAttribute("aria-hidden");
    }
  }
}

function openRulesDrawer() {
  const drawer = elements["rules-drawer"];
  if (!drawer || !drawer.hidden) return;
  rulesReturnFocus = document.activeElement || elements["rules-toggle"];
  closeMarketEditor({ restoreFocus: false });
  closeAuctionEditor({ restoreFocus: false });
  closeCreditEditor({ restoreFocus: false });
  hideTradePrompt();
  updateRulesVariantNote();
  highlightRelevantRuleCosts();
  drawer.hidden = false;
  elements["rules-toggle"]?.setAttribute("aria-expanded", "true");
  elements["rules-toggle"]?.setAttribute("aria-label", "ルール・建設コストを閉じる");
  syncModalBodyState();
  window.requestAnimationFrame(() => {
    drawer.querySelector?.(".rules-sheet")?.focus();
  });
}

function closeRulesDrawer({ restoreFocus = true } = {}) {
  const drawer = elements["rules-drawer"];
  if (!drawer || drawer.hidden) return;
  drawer.hidden = true;
  elements["rules-toggle"]?.setAttribute("aria-expanded", "false");
  elements["rules-toggle"]?.setAttribute("aria-label", "ルール・建設コストを表示");
  syncModalBodyState();
  if (restoreFocus) rulesReturnFocus?.focus?.();
  rulesReturnFocus = null;
  if (state.snapshot?.state) {
    renderIncomingTradePrompt(
      state.snapshot.state,
      state.snapshot.command_options || [],
      state.welcome?.seat_index,
    );
  }
}

function updateRulesVariantNote() {
  const note = elements["rules-variant-note"];
  if (!note) return;
  const rulesDocument = state.snapshot?.state?.rules || {};
  const rules = rulesDocument.house_rules
    || state.lobby?.settings?.house_rules
    || {};
  const variantState = state.snapshot?.state?.variant_state || null;
  const variantDocument = variantState
    || rulesDocument.variant
    || state.lobby?.settings?.variant
    || { kind: "standard", options: {} };
  const variant = variantDocument.kind || "standard";
  const compositeCatalog = variantDocument.public?.catalog
    ?? variantDocument.options?.catalog
    ?? null;
  const hasForecast = variantIncludesComponent(variantDocument, "forecast_events");
  const hasFrontier = variantIncludesComponent(variantDocument, "frontier");
  const hasTrade2 = variantIncludesComponent(variantDocument, "trade2");
  const hasCredit = variantIncludesComponent(variantDocument, "credit");
  const exceptions = [];
  if (state.lobby?.settings?.player_count === 2) exceptions.push("2人簡易構成");
  if (rules.bank_trade_3_to_1) exceptions.push("銀行交易 3:1");
  if (rules.skip_discard_on_seven) exceptions.push("7の半分捨て札なし");
  if (Array.isArray(rules.disabled_development_cards)
      && rules.disabled_development_cards.length) {
    exceptions.push("一部の発展カードを除外");
  }
  if (variant === "composite") {
    exceptions.push(
      compositeCatalog === COMPOSITE_GRAND_CAMPAIGN_CATALOG
        ? "グランドキャンペーン（全部入り）"
        : "イベント＆経済（複合）モード",
    );
  } else if (hasForecast) {
    exceptions.push("予告イベントモード");
  }
  if (variant === "frontier") exceptions.push("フロンティア探索モード");
  if (variant !== "composite" && hasCredit) {
    exceptions.push("資源信用・借入と返済モード");
  }
  if (variant !== "composite" && hasTrade2) {
    const catalog = variantComponentPublic(variantState, "trade2")?.catalog
      || rulesDocument.variant?.options?.catalog
      || state.lobby?.settings?.variant?.options?.catalog;
    exceptions.push(
      catalog === "market_auction_v1"
        ? "交易2.0・市場と公開競売モード"
        : "交易2.0・常設市場モード",
    );
  }
  if (elements["rules-forecast-note"]) {
    elements["rules-forecast-note"].hidden = !hasForecast;
  }
  if (elements["rules-frontier-note"]) {
    elements["rules-frontier-note"].hidden = !hasFrontier;
  }
  if (elements["rules-campaign-note"]) {
    elements["rules-campaign-note"].hidden = compositeCatalog !== COMPOSITE_GRAND_CAMPAIGN_CATALOG;
  }
  if (elements["rules-market-note"]) {
    elements["rules-market-note"].hidden = !hasTrade2;
  }
  if (elements["rules-credit-note"]) {
    elements["rules-credit-note"].hidden = !hasCredit;
  }
  note.replaceChildren(
    textElement("strong", exceptions.length ? "適用中の例外" : "標準ルール"),
    textElement(
      "span",
      exceptions.length
        ? exceptions.join(" / ") + "。早見表との差分は権威サーバーの設定を優先します。"
        : "公式の基本ルールに沿った建設・交易条件を適用中です。",
    ),
  );
}

function highlightRelevantRuleCosts() {
  const cards = document.querySelectorAll?.(".cost-card") || [];
  const options = state.snapshot?.command_options || [];
  const affordable = new Set(
    options.flatMap((option) => {
      if (option.command === "build" && option.args?.piece) {
        return [option.args.piece];
      }
      if (option.command === "buy_development") return ["development"];
      return [];
    }),
  );
  for (const card of cards) {
    card.classList.toggle("context-match", affordable.has(card.dataset.piece));
  }
}

function trapModalFocus(event, modal) {
  if (event.key !== "Tab") return;
  const focusable = Array.from(
    modal.querySelectorAll?.(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])',
    ) || [],
  ).filter((element) => !element.hidden);
  if (!focusable.length) {
    event.preventDefault();
    modal.querySelector?.('[tabindex="-1"]')?.focus();
    return;
  }
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function handleModalKeydown(event) {
  const roomAccessOpen = state.roomAccessPromptOpen;
  const tradeOpen = elements["trade-prompt"] && !elements["trade-prompt"].hidden;
  const rulesOpen = elements["rules-drawer"] && !elements["rules-drawer"].hidden;
  const marketOpen = state.marketEditorOpen;
  const auctionOpen = state.auctionEditorOpen;
  const creditOpen = state.creditEditorOpen;
  const modal = roomAccessOpen
    ? elements["room-access-prompt"]
    : creditOpen
    ? elements["credit-editor"]
    : auctionOpen
    ? elements["auction-editor"]
    : marketOpen
    ? elements["market-editor"]
    : tradeOpen
      ? elements["trade-prompt"]
      : rulesOpen
      ? elements["rules-drawer"]
      : null;
  if (!modal) return;
  if (event.key === "Escape") {
    event.preventDefault();
    if (roomAccessOpen) closeRoomAccessPrompt();
    else if (creditOpen) closeCreditEditor();
    else if (auctionOpen) closeAuctionEditor();
    else if (marketOpen) closeMarketEditor();
    else if (tradeOpen) dismissTradePrompt();
    else closeRulesDrawer();
    return;
  }
  trapModalFocus(event, modal);
}

elements["create-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const accessMode = elements["invite-only-room"]?.checked
    ? "invite_only"
    : elements["protect-room"]?.checked
      ? "passphrase"
      : "open";
  if (accessMode !== "open" && !roomAccessTransportAllowed()) {
    showToast("この接続では入室保護を利用できません。HTTPS/WSSで接続してください。", true);
    syncCreateRoomProtection();
    return;
  }
  const message = wireMessage("create_room", {
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
  });
  if (accessMode === "invite_only") message.invite_only = true;
  if (accessMode === "passphrase") {
    let passphrase = String(form.get("room_passphrase") || "");
    const validationError = roomPassphraseClientError(passphrase);
    if (validationError) {
      showToast(validationError, true);
      return;
    }
    message.passphrase = passphrase;
    passphrase = null;
  }
  try {
    const request = sendEphemeralPassphraseMessage(
      message,
      elements["create-room-passphrase"],
      elements["create-passphrase-toggle"],
    );
    form.delete("room_passphrase");
    await request;
  } catch (error) {
    showToast(error.message, true);
  }
});

elements["join-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const attempt = roomAccessAttempt(form, state.claimedInvitation);
  state.pendingRoomAccessAttempt = attempt;
  try {
    await submitRoomAccessAttempt(attempt);
  } catch (error) {
    if (!handleRoomAccessThrownError(error)) showToast(error.message, true);
  }
});
elements["decline-claimed-invitation"].addEventListener("click", () => {
  void declineClaimedInvitation();
});

elements["room-access-form"].addEventListener("submit", async (event) => {
  event.preventDefault();
  const attempt = state.pendingRoomAccessAttempt;
  if (!attempt || elements["room-access-submit"].disabled) return;
  const validationError = roomPassphraseClientError(
    elements["join-room-passphrase"].value,
  );
  if (validationError) {
    setRoomAccessError(validationError);
    return;
  }
  try {
    await submitRoomAccessAttempt(attempt, elements["join-room-passphrase"].value);
  } catch (error) {
    if (!handleRoomAccessThrownError(error)) showToast(error.message, true);
  }
});

elements["invite-only-room"].addEventListener("change", syncCreateRoomProtection);
elements["open-room"].addEventListener("change", syncCreateRoomProtection);
elements["protect-room"].addEventListener("change", syncCreateRoomProtection);
elements["create-passphrase-toggle"].addEventListener("click", () => {
  togglePasswordVisibility(
    elements["create-room-passphrase"],
    elements["create-passphrase-toggle"],
  );
});
elements["join-passphrase-toggle"].addEventListener("click", () => {
  togglePasswordVisibility(
    elements["join-room-passphrase"],
    elements["join-passphrase-toggle"],
  );
});
elements["room-access-close"].addEventListener("click", () => closeRoomAccessPrompt());
elements["room-access-cancel"].addEventListener("click", () => closeRoomAccessPrompt());
elements["room-access-prompt"].addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeRoomAccessPrompt();
});

elements["rules-toggle"].addEventListener("click", () => {
  if (elements["rules-drawer"].hidden) openRulesDrawer();
  else closeRulesDrawer();
});
elements["rules-close"].addEventListener("click", () => closeRulesDrawer());
elements["rules-drawer"].addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeRulesDrawer();
});
elements["trade-prompt-close"].addEventListener("click", dismissTradePrompt);
elements["trade-prompt"].addEventListener("click", (event) => {
  if (event.target === event.currentTarget) dismissTradePrompt();
});
elements["market-create-button"].addEventListener("click", openMarketEditor);
elements["market-editor-close"].addEventListener("click", () => closeMarketEditor());
elements["market-editor-cancel"].addEventListener("click", () => closeMarketEditor());
elements["market-editor-submit"].addEventListener("click", submitMarketDraft);
elements["market-editor"].addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeMarketEditor();
});
elements["auction-create-button"].addEventListener("click", openAuctionCreateEditor);
elements["auction-editor-close"].addEventListener("click", () => closeAuctionEditor());
elements["auction-editor-cancel"].addEventListener("click", () => closeAuctionEditor());
elements["auction-editor-submit"].addEventListener("click", submitAuctionDraft);
elements["auction-editor"].addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeAuctionEditor();
});
elements["credit-open-button"].addEventListener("click", openCreditEditor);
elements["credit-editor-close"].addEventListener("click", () => closeCreditEditor());
elements["credit-editor-cancel"].addEventListener("click", () => closeCreditEditor());
elements["credit-editor-submit"].addEventListener("click", submitCreditDraft);
elements["credit-editor"].addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeCreditEditor();
});
if (typeof document.addEventListener === "function") {
  document.addEventListener("keydown", handleModalKeydown);
}

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
  let document;
  try {
    document = await sendMessage(wireMessage("leave_room"));
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  if ((document.events || []).some((event) => event.type === "request_error")) return;
  try {
    await api("/api/resume", { method: "DELETE" });
  } catch (_error) {
    showToast("退出しました。復帰情報の消去は次回接続時に再確認します。", true);
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
elements["copy-player-invite-link"].addEventListener("click", () => {
  copyRoleInvitationLink("player");
});
elements["copy-spectator-invite-link"].addEventListener("click", () => {
  copyRoleInvitationLink("spectator");
});
elements["refresh-invitations"].addEventListener("click", () => {
  void loadActiveInvitations({ force: true });
});
elements["revoke-all-invitations"].addEventListener("click", () => {
  if (!state.activeInvitations.length) return;
  const confirmed = window.confirm(
    `未使用の招待${state.activeInvitations.length}件をすべて取り消しますか？`,
  );
  if (!confirmed) return;
  void revokeActiveInvitations({ all: true });
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
  const capturedInvitation = captureInvitationTokenFromLocation();
  try {
    sessionStorage.removeItem("catan-reconnect");
  } catch (_error) {
    // Storage can be unavailable in hardened browser profiles. Resume state is
    // now held exclusively by an HttpOnly cookie, so cleanup is best-effort.
  }
  setConnection("connecting", "接続準備中");
  syncAIOptions();
  syncCreateRoomProtection();
  const invitationCode = applyInvitationFromLocation();
  try {
    await startBrowserSession({
      allowInvitationResume: !capturedInvitation.present,
      allowRoomResume: !invitationCode && !capturedInvitation.present,
    });
    if (capturedInvitation.present) {
      if (capturedInvitation.room_code && capturedInvitation.token) {
        try {
          const invitation = await claimCapturedInvitation(capturedInvitation);
          if (!invitation || !applyClaimedInvitation(invitation)) {
            throw new Error("invalid invitation response");
          }
        } catch (_error) {
          capturedInvitation.token = null;
          showToast("招待リンクを確認できませんでした。ホストから新しいリンクを受け取るか、参加コードで入室してください。", true);
        }
      } else {
        capturedInvitation.token = null;
        showToast("招待リンクの形式が正しくありません。ホストから新しいリンクを受け取ってください。", true);
      }
    }
    render();
    if (!state.lobby && invitationCode && !state.claimedInvitation) {
      applyInvitationFromLocation();
    }
  } catch (error) {
    setConnection("error", "サーバーに接続できません");
    showToast(error.message, true);
  }
  window.setInterval(pollEvents, POLL_INTERVAL_MS);
}

initialise();
