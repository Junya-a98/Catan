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
    this.attributes = {};
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
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus(options) { this.focusCalls.push(options); }
  scrollIntoView(options) { this.scrollCalls.push(options); }
  remove() {}
}

function loadAnimationFunctions() {
  const byId = new Map();
  const audioCalls = [];
  const sessionValues = new Map();
  const clipboard = {
    fail: false,
    writes: [],
    async writeText(value) {
      if (this.fail) throw new Error("clipboard unavailable");
      this.writes.push(value);
    },
  };
  const historyCalls = [];
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
    navigator: { clipboard },
    URLSearchParams,
    sessionStorage: {
      getItem(key) { return sessionValues.get(key) ?? null; },
      setItem(key, value) { sessionValues.set(key, String(value)); },
      removeItem(key) { sessionValues.delete(key); },
    },
    __audioCalls: audioCalls,
    __clipboard: clipboard,
    __historyCalls: historyCalls,
    __sessionValues: sessionValues,
  };
  sandbox.window = {
    WebSocket: undefined,
    location: {
      protocol: "http:",
      host: "localhost:8765",
      origin: "http://localhost:8765",
      pathname: "/",
      search: "",
      hash: "",
    },
    history: {
      replaceState(_state, _title, url) { historyCalls.push(url); },
    },
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
    normalizeInvitationRoomCode,
    parseInvitationRoomQuery,
    parseInvitationTokenFragment,
    currentBrowserOrigin,
    invitationURL,
    invitationGrantURL,
    captureInvitationTokenFromLocation,
    applyInvitationFromLocation,
    showInvitationFallback,
    hideInvitationFallback,
    copyInvitationLink,
    invitationPublicMetadata,
    invitationExpiryLabel,
    normalizeInvitationId,
    activeInvitationMetadata,
    activeInvitationList,
    activeInvitationExpiryLabel,
    currentInvitationListSignature,
    clearActiveInvitationState,
    renderActiveInvitationManager,
    loadActiveInvitations,
    ensureActiveInvitationList,
    revokeActiveInvitations,
    applyClaimedInvitation,
    clearClaimedInvitation,
    claimCapturedInvitation,
    resumeFriendInvitationFromCookie,
    declineClaimedInvitation,
    deliverInvitationURL,
    copyRoleInvitationLink,
    browserHostname,
    isLoopbackBrowserLocation,
    roomAccessTransportAllowed,
    setPasswordVisibility,
    clearPassphraseInput,
    roomAccessAttempt,
    roomAccessAttemptLabel,
    roomPassphraseClientError,
    roomAccessPublicPresentation,
    retryAfterSecondsFromEvent,
    handleRoomAccessRequestError,
    openRoomAccessPrompt,
    closeRoomAccessPrompt,
    messageRequiresHttpTransport,
    hasSessionWelcome,
    confirmRoomResumeAfterWelcome,
    variantIncludesComponent,
    variantComponentPublic,
    isCoreV2ForecastPublicState,
    forecastEventPresentation,
    forecastParameterLabel,
    forecastHarborTargetId,
    forecastAnnouncedHarborId,
    campaignHarborBlockadeLabel,
    campaignHarborBlockadeTargetId,
    forecastActiveTiming,
    renderForecastEvent,
    frontierPresentation,
    renderFrontierStatus,
    currentTurnSeat,
    domesticTradeActorSeat,
    domesticTradePresentation,
    ownResourceSummary,
    tradeOfferSignature,
    formatTradeBundle,
    buildTradeReceiveOperator,
    renderTradePromptActions,
    renderActions,
    tradeMarketPresentation,
    marketCreateUnavailableLabel,
    formatMarketBundle,
    marketDraftValidation,
    renderTradeMarket,
    tradeAuctionPresentation,
    renderTradeAuction,
    auctionDraftValidation,
    resourceCreditPresentation,
    creditDeadlineLabel,
    creditDraftValidation,
    renderResourceCredit,
    updateRulesVariantNote,
    commandLabel,
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
    harborForecastPresentation,
    drawBoardHarbor,
    elements,
    clipboard: globalThis.__clipboard,
    navigatorObject: navigator,
    historyCalls: globalThis.__historyCalls,
    audioCalls: globalThis.__audioCalls,
  };`, sandbox, { filename: "web/app.js" });
  return sandbox.__animationTest;
}

const animation = loadAnimationFunctions();

test("invitation URLs contain only the current origin and normalized room code", () => {
  assert.equal(animation.normalizeInvitationRoomCode("ab12cd"), "AB12CD");
  assert.equal(animation.normalizeInvitationRoomCode("ABC123"), "ABC123");
  assert.equal(animation.normalizeInvitationRoomCode("ABC12-"), null);
  assert.equal(animation.normalizeInvitationRoomCode("ＡBC123"), null);
  assert.equal(animation.normalizeInvitationRoomCode("ab12ß"), null);
  assert.equal(animation.normalizeInvitationRoomCode("abc12ſ"), null);
  assert.equal(animation.normalizeInvitationRoomCode("ıbc123"), null);
  assert.equal(animation.normalizeInvitationRoomCode(" ABC123"), null);
  assert.equal(
    animation.invitationURL("http://192.168.1.20:8765", "ab12cd"),
    "http://192.168.1.20:8765/?room=AB12CD",
  );
  assert.equal(
    animation.invitationURL("https://catan.example.test", "ROOM01"),
    "https://catan.example.test/?room=ROOM01",
  );
  const url = animation.invitationURL("http://localhost:8765", "ABC123");
  assert.doesNotMatch(url, /token|session|role|display/i);
  assert.equal(animation.invitationURL("http://localhost:8765/path", "ABC123"), null);
  const token = "A".repeat(43);
  assert.equal(
    animation.invitationGrantURL("https://catan.example.test", "abc123", token),
    `https://catan.example.test/?room=ABC123#invite=${token}`,
  );
  assert.equal(
    animation.invitationGrantURL("https://catan.example.test", "ABC123", "short"),
    null,
  );
});

test("opaque invitation fragments are strict and scrubbed before network startup", () => {
  const token = "B".repeat(43);
  const parsed = animation.parseInvitationTokenFragment(`#invite=${token}`);
  assert.equal(parsed.present, true);
  assert.equal(parsed.token, token);
  assert.equal(animation.parseInvitationTokenFragment(`#invite=${token}&role=player`).token, null);
  assert.equal(animation.parseInvitationTokenFragment("#invite=short").token, null);
  assert.equal(animation.parseInvitationTokenFragment("").present, false);

  animation.historyCalls.length = 0;
  const captured = animation.captureInvitationTokenFromLocation(
    {
      pathname: "/",
      search: "?room=ab12cd&tracking=remove-me",
      hash: `#invite=${token}`,
    },
    { replaceState(_state, _title, url) { animation.historyCalls.push(url); } },
  );
  assert.equal(captured.present, true);
  assert.equal(captured.room_code, "AB12CD");
  assert.equal(captured.token, token);
  assert.deepEqual([...animation.historyCalls], ["/?room=AB12CD"]);
  assert.doesNotMatch(animation.historyCalls[0], /invite|BBBB/);

  animation.historyCalls.length = 0;
  const malformed = animation.captureInvitationTokenFromLocation(
    { pathname: "/", search: "?room=ABC123", hash: "#invite=bad" },
    { replaceState(_state, _title, url) { animation.historyCalls.push(url); } },
  );
  assert.equal(malformed.present, true);
  assert.equal(malformed.token, null);
  assert.deepEqual([...animation.historyCalls], ["/?room=ABC123"]);
});

test("room query prefills the join form without joining and cleans invalid input", () => {
  const valid = animation.parseInvitationRoomQuery("?room=ab12cd&token=do-not-keep");
  assert.equal(valid.code, "AB12CD");
  assert.equal(valid.canonicalSearch, "?room=AB12CD");
  assert.equal(animation.parseInvitationRoomQuery("?room=ABC123&room=DEF456").code, null);
  assert.equal(animation.parseInvitationRoomQuery("?room=ABC12-").code, null);
  assert.equal(animation.parseInvitationRoomQuery("?other=ABC123").present, false);

  const form = animation.elements["join-form"];
  const note = animation.elements["invite-prefill-note"];
  form.elements.room_code.value = "";
  form.elements.display_name.focusCalls.length = 0;
  form.scrollCalls.length = 0;
  note.hidden = true;
  animation.historyCalls.length = 0;
  const code = animation.applyInvitationFromLocation(
    {
      pathname: "/",
      search: "?room=ab12cd&token=do-not-keep",
      hash: "#secret",
    },
    { replaceState(_state, _title, url) { animation.historyCalls.push(url); } },
  );
  assert.equal(code, "AB12CD");
  assert.equal(form.elements.room_code.value, "AB12CD");
  assert.equal(note.hidden, false);
  assert.equal(form.elements.display_name.focusCalls.length, 1);
  assert.equal(form.scrollCalls.length, 1);
  assert.deepEqual([...animation.historyCalls], ["/?room=AB12CD"]);

  form.elements.room_code.value = "UNCHANGED";
  animation.historyCalls.length = 0;
  assert.equal(
    animation.applyInvitationFromLocation(
      { pathname: "/", search: "?room=ABC12-", hash: "" },
      { replaceState(_state, _title, url) { animation.historyCalls.push(url); } },
    ),
    null,
  );
  assert.equal(form.elements.room_code.value, "UNCHANGED");
  assert.equal(note.hidden, true);
  assert.deepEqual([...animation.historyCalls], ["/"]);
});

test("clipboard failure exposes a safe manual invitation link", async () => {
  animation.state.lobby = { room_code: "ABC123" };
  animation.clipboard.fail = true;
  animation.clipboard.writes.length = 0;
  assert.equal(await animation.copyInvitationLink(), false);
  assert.equal(animation.elements["invite-link-fallback"].hidden, false);
  assert.equal(
    animation.elements["invite-link-value"].value,
    "http://localhost:8765/?room=ABC123",
  );
  assert.doesNotMatch(animation.elements["invite-link-value"].value, /token|session|role/i);

  animation.clipboard.fail = false;
  assert.equal(await animation.copyInvitationLink(), true);
  assert.deepEqual(
    [...animation.clipboard.writes],
    ["http://localhost:8765/?room=ABC123"],
  );
  assert.equal(animation.elements["invite-link-fallback"].hidden, true);
});

test("invitation controls are accessible and retain the original room-code copy", () => {
  assert.match(indexSource, /id="copy-room-code"[^>]*>コピー<\/button>/);
  assert.match(indexSource, /id="copy-player-invite-link"[^>]*>プレイヤー招待<\/button>/);
  assert.match(indexSource, /id="copy-spectator-invite-link"[^>]*>観戦招待<\/button>/);
  assert.match(indexSource, /id="invitation-guidance"[^>]*hidden[^>]*>招待リンクは1時間有効・1回限り/);
  assert.match(indexSource, /id="invite-link-fallback"[^>]*role="status"[^>]*hidden/);
  assert.match(indexSource, /id="invite-link-value"[^>]*readonly[^>]*aria-label="招待リンク"/);
  assert.match(indexSource, /id="invite-prefill-note"[^>]*role="status"[^>]*hidden/);
  assert.match(indexSource, /id="invitation-manager"[^>]*aria-labelledby="invitation-manager-title"[^>]*hidden/);
  assert.match(indexSource, /id="invitation-manager-status"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(indexSource, /id="invitation-list"[^>]*aria-label="有効な未使用招待"/);
  assert.match(indexSource, /id="refresh-invitations"[^>]*aria-controls="invitation-list"/);
  assert.match(indexSource, /id="revoke-all-invitations"[^>]*aria-controls="invitation-list"/);
  assert.match(cssSource, /\.room-code-actions\s*\{/);
  assert.match(cssSource, /@media \(max-width: 440px\)[\s\S]*\.room-code-actions/);
  assert.match(cssSource, /@media \(max-width: 440px\)[\s\S]*\.invitation-list-item\s*\{[\s\S]*grid-template-columns: 1fr/);
});

test("raw invitation wins, then claim-cookie resume runs before room resume", () => {
  assert.match(
    appSource,
    /async function initialise\(\) \{\s*const capturedInvitation = captureInvitationTokenFromLocation\(\);/,
  );
  assert.ok(
    appSource.indexOf("const capturedInvitation = captureInvitationTokenFromLocation();")
      < appSource.indexOf('sessionStorage.removeItem("catan-reconnect")'),
  );
  assert.match(appSource, /allowInvitationResume: !capturedInvitation\.present/);
  assert.match(
    appSource,
    /allowRoomResume: !invitationCode && !capturedInvitation\.present/,
  );
  assert.match(appSource, /await claimCapturedInvitation\(capturedInvitation\)/);
  assert.match(
    appSource,
    /if \(\s*!state\.welcome\s*&& allowInvitationResume\s*&& roomAccessTransportAllowed\(\)\s*\)/,
  );
  assert.match(appSource, /if \(!state\.welcome && !restoredInvitation && allowRoomResume\)/);
  assert.ok(
    appSource.indexOf("await resumeFriendInvitationFromCookie()")
      < appSource.indexOf("await resumeRoomFromCookie()"),
  );
  assert.doesNotMatch(
    appSource.slice(
      appSource.indexOf("function applyInvitationFromLocation"),
      appSource.indexOf("function hideInvitationFallback"),
    ),
    /sendMessage|join_room|requestSubmit|\.submit\(/,
  );
});

test("claim-cookie resume and explicit decline are token-free", async () => {
  const calls = [];
  const invitation = await animation.resumeFriendInvitationFromCookie(
    async (path, options) => {
      calls.push([path, options.method, options.body]);
      return {
        api_version: 1,
        invitation: {
          room_code: "ABC123",
          role: "player",
          issued_at_ms: 1_999_996_400_000,
          expires_at_ms: 2_000_000_000_000,
        },
      };
    },
  );
  assert.deepEqual(calls, [["/api/invitations/resume", "POST", "{}"]]);
  assert.equal(invitation.room_code, "ABC123");
  assert.equal(Object.hasOwn(invitation, "token"), false);
  assert.equal(
    await animation.resumeFriendInvitationFromCookie(async () => ({
      api_version: 1,
      invitation: {
        room_code: "ABC123",
        role: "player",
        expires_at_ms: 2_000_000_000_000,
        claim_token: "S".repeat(43),
      },
    })),
    null,
  );

  animation.applyClaimedInvitation(invitation);
  assert.equal(animation.elements["decline-claimed-invitation"].hidden, false);
  const declined = await animation.declineClaimedInvitation(
    async (path, options) => {
      calls.push([path, options.method, options.body]);
      return { api_version: 1, cleared: true };
    },
  );
  assert.equal(declined, true);
  assert.equal(animation.state.claimedInvitation, null);
  assert.equal(animation.elements["decline-claimed-invitation"].hidden, true);
  assert.deepEqual(calls.at(-1), ["/api/invitations/claim", "DELETE", "{}"]);
});

test("claim sends the bearer once, clears the captured reference, and accepts token-free metadata", async () => {
  const token = "C".repeat(43);
  const captured = { present: true, room_code: "ABC123", token };
  const requests = [];
  const invitation = await animation.claimCapturedInvitation(
    captured,
    async (path, options) => {
      requests.push({ path, options: { ...options } });
      assert.deepEqual(JSON.parse(options.body), { room_code: "ABC123", token });
      return {
        api_version: 1,
        invitation: {
          room_code: "ABC123",
          role: "spectator",
          expires_at_ms: 2_000_000_000_000,
        },
      };
    },
  );
  assert.equal(captured.token, null);
  assert.deepEqual([...requests.map((item) => item.path)], ["/api/invitations/claim"]);
  assert.equal(invitation.room_code, "ABC123");
  assert.equal(invitation.role, "spectator");
  assert.equal(invitation.expires_at_ms, 2_000_000_000_000);
  assert.equal(Object.hasOwn(invitation, "token"), false);
});

test("claimed invitations lock role and room and omit a user-controlled wire role", () => {
  const form = animation.elements["join-form"];
  const roomCode = animation.elements["join-room-code"];
  const playerRole = animation.elements["join-player-role"];
  const spectatorRole = animation.elements["join-spectator-role"];
  const note = animation.elements["invite-prefill-note"];
  roomCode.value = "";
  playerRole.checked = true;
  spectatorRole.checked = false;
  note.hidden = true;

  assert.equal(animation.applyClaimedInvitation({
    room_code: "ZZ99YY",
    role: "spectator",
    expires_at_ms: 2_000_000_000_000,
  }), true);
  assert.equal(roomCode.value, "ZZ99YY");
  assert.equal(roomCode.readOnly, true);
  assert.equal(playerRole.disabled, true);
  assert.equal(spectatorRole.disabled, true);
  assert.equal(playerRole.checked, false);
  assert.equal(spectatorRole.checked, true);
  assert.match(note.textContent, /観戦者用の期限付き招待/);

  const attempt = animation.roomAccessAttempt(
    new Map([
      ["room_code", "ATTACK"],
      ["display_name", "Viewer"],
      ["role", "player"],
    ]),
    animation.state.claimedInvitation,
  );
  assert.equal(attempt.room_code, "ZZ99YY");
  assert.equal(attempt.display_name, "Viewer");
  assert.equal(Object.hasOwn(attempt, "role"), false);

  animation.clearClaimedInvitation({ preserveRoomCode: true });
  assert.equal(roomCode.readOnly, false);
  assert.equal(playerRole.disabled, false);
  assert.equal(spectatorRole.disabled, false);
});

test("host copies role-bound invitations without rendering a bearer fallback", async () => {
  const token = "D".repeat(43);
  const issued = {
    invitation_id: "I".repeat(22),
    token,
    room_code: "AB12CD",
    role: "player",
    issued_at_ms: 1_999_996_400_000,
    expires_at_ms: 2_000_000_000_000,
  };
  animation.state.lobby = null;
  animation.state.invitationCopyPending = false;
  animation.clipboard.fail = false;
  animation.clipboard.writes.length = 0;
  assert.equal(
    await animation.copyRoleInvitationLink("player", async (path, options) => {
      assert.equal(path, "/api/invitations");
      assert.deepEqual(JSON.parse(options.body), { role: "player" });
      return { api_version: 1, invitation: issued };
    }),
    true,
  );
  assert.deepEqual(
    [...animation.clipboard.writes],
    [`http://localhost:8765/?room=AB12CD#invite=${token}`],
  );
  assert.equal(issued.token, null);

  const failedToken = "E".repeat(43);
  animation.clipboard.fail = true;
  animation.elements["invite-link-value"].value = "";
  const failedRequests = [];
  assert.equal(
    await animation.copyRoleInvitationLink("spectator", async (path, options) => {
      failedRequests.push([path, options.method, JSON.parse(options.body)]);
      if (options.method === "DELETE") {
        return { api_version: 1, revoked_count: 1, invitations: [] };
      }
      return {
        api_version: 1,
        invitation: {
          invitation_id: "J".repeat(22),
          token: failedToken,
          room_code: "AB12CD",
          role: "spectator",
          issued_at_ms: 1_999_996_400_000,
          expires_at_ms: 2_000_000_000_000,
        },
      };
    }),
    false,
  );
  assert.deepEqual(failedRequests, [
    ["/api/invitations", "POST", { role: "spectator" }],
    ["/api/invitations", "DELETE", { invitation_id: "J".repeat(22) }],
  ]);
  assert.equal(animation.elements["invite-link-value"].value, "");
  assert.doesNotMatch(animation.elements.toast.textContent, new RegExp(failedToken));
  assert.match(animation.elements.toast.textContent, /自動で取り消しました/);
  animation.clipboard.fail = false;
});

test("invitation delivery falls back to Web Share without rendering the bearer", async () => {
  const token = "K".repeat(43);
  const shared = [];
  animation.clipboard.fail = true;
  animation.navigatorObject.share = async (payload) => { shared.push({ ...payload }); };
  animation.state.lobby = null;
  assert.equal(
    await animation.copyRoleInvitationLink("spectator", async () => ({
      api_version: 1,
      invitation: {
        invitation_id: "L".repeat(22),
        token,
        room_code: "AB12CD",
        role: "spectator",
        issued_at_ms: 1_999_996_400_000,
        expires_at_ms: 2_000_000_000_000,
      },
    })),
    true,
  );
  assert.deepEqual(shared, [{
    title: "カタン風ゲームへの招待",
    text: "1回限りの期限付き招待です。",
    url: `http://localhost:8765/?room=AB12CD#invite=${token}`,
  }]);
  assert.equal(animation.elements["invite-link-value"].value, "");
  assert.doesNotMatch(animation.elements.toast.textContent, new RegExp(token));
  delete animation.navigatorObject.share;
  animation.clipboard.fail = false;
});

test("active invitation adapters retain only public management metadata", () => {
  const first = {
    invitation_id: "M".repeat(22),
    room_code: "AB12CD",
    role: "player",
    issued_at_ms: 1_999_996_400_000,
    expires_at_ms: 2_000_000_000_000,
  };
  const second = {
    invitation_id: "N".repeat(22),
    room_code: "AB12CD",
    role: "spectator",
    issued_at_ms: 1_999_996_300_000,
    expires_at_ms: 1_999_999_000_000,
  };
  const invitations = animation.activeInvitationList(
    { api_version: 1, invitations: [first, second] },
    "AB12CD",
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(invitations)),
    [
      { invitation_id: "N".repeat(22), role: "spectator", expires_at_ms: 1_999_999_000_000 },
      { invitation_id: "M".repeat(22), role: "player", expires_at_ms: 2_000_000_000_000 },
    ],
  );
  assert.equal(animation.activeInvitationList({ api_version: 1, invitations: [{ ...first, token: "Q".repeat(43) }] }, "AB12CD"), null);
  assert.equal(animation.activeInvitationList({ api_version: 1, invitations: [{ ...first, token_digest: "a".repeat(64) }] }, "AB12CD"), null);
  assert.equal(animation.activeInvitationList({ api_version: 1, invitations: [{ ...first, room_code: "ZZ99YY" }] }, "AB12CD"), null);
  assert.equal(animation.activeInvitationList({ api_version: 1, invitations: [], token: "Q".repeat(43) }, "AB12CD"), null);
});

test("host invitation manager loads once and applies the authoritative revocation list", async () => {
  const invitation = {
    invitation_id: "P".repeat(22),
    room_code: "AB12CD",
    role: "player",
    issued_at_ms: 1_999_996_400_000,
    expires_at_ms: 2_000_000_000_000,
  };
  animation.clearActiveInvitationState({ roomCode: "AB12CD" });
  animation.state.welcome = { role: "host" };
  animation.state.lobby = {
    room_code: "AB12CD",
    phase: "waiting",
    player_members: 1,
    spectators: 0,
  };
  const requests = [];
  const request = async (path, options) => {
    requests.push([path, options.method, JSON.parse(options.body)]);
    if (options.method === "DELETE") {
      return { api_version: 1, revoked_count: 1, invitations: [] };
    }
    return {
      api_version: 1,
      invitations: requests.some((entry) => entry[1] === "DELETE") ? [] : [invitation],
    };
  };
  const signature = animation.currentInvitationListSignature();
  assert.equal(await animation.loadActiveInvitations({ request, signature }), true);
  assert.equal(await animation.loadActiveInvitations({ request, signature }), false);
  assert.deepEqual(requests, [["/api/invitations/list", "POST", {}]]);
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.state.activeInvitations)),
    [{ invitation_id: "P".repeat(22), role: "player", expires_at_ms: 2_000_000_000_000 }],
  );
  assert.equal(await animation.revokeActiveInvitations({ invitationId: "P".repeat(22), request }), true);
  assert.deepEqual(requests.slice(1), [
    ["/api/invitations", "DELETE", { invitation_id: "P".repeat(22) }],
  ]);
  assert.equal(animation.state.activeInvitations.length, 0);
  animation.state.welcome = null;
  animation.state.lobby = null;
  animation.clearActiveInvitationState();
});

test("room passphrases are available only on loopback HTTP or HTTPS", () => {
  assert.equal(
    animation.roomAccessTransportAllowed({ protocol: "http:", hostname: "127.0.0.1" }),
    true,
  );
  assert.equal(
    animation.roomAccessTransportAllowed({ protocol: "http:", hostname: "::1" }),
    true,
  );
  assert.equal(
    animation.roomAccessTransportAllowed({ protocol: "http:", hostname: "localhost" }),
    true,
  );
  assert.equal(
    animation.roomAccessTransportAllowed({ protocol: "http:", hostname: "192.168.1.20" }),
    false,
  );
  assert.equal(
    animation.roomAccessTransportAllowed({ protocol: "https:", hostname: "catan.example" }),
    true,
  );
});

test("protected-room controls are accessible and keep secrets out of durable client state", () => {
  assert.match(indexSource, /id="invite-only-room"[^>]*type="radio"[^>]*value="invite_only"[^>]*checked/);
  assert.match(indexSource, /id="open-room"[^>]*type="radio"[^>]*value="open"/);
  assert.match(indexSource, /id="protect-room"[^>]*type="radio"[^>]*value="passphrase"[^>]*aria-controls="create-passphrase-fields"/);
  assert.match(indexSource, /id="create-room-passphrase"[^>]*type="password"[^>]*minlength="15"[^>]*data-max-characters="64"[^>]*autocomplete="new-password"/);
  assert.match(indexSource, /id="join-room-passphrase"[^>]*type="password"[^>]*minlength="15"[^>]*data-max-characters="64"[^>]*autocomplete="current-password"/);
  assert.match(indexSource, /id="room-access-prompt"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(indexSource, /id="join-passphrase-toggle"[^>]*aria-pressed="false"[^>]*aria-controls="join-room-passphrase"/);
  assert.doesNotMatch(indexSource, /onpaste\s*=/i);
  assert.match(cssSource, /\.room-access-card\s*\{/);
  assert.match(cssSource, /@media \(max-width: 440px\)[\s\S]*\.room-access-actions/);

  const persistentWrites = [...appSource.matchAll(/sessionStorage\.setItem\([\s\S]{0,260}/g)]
    .map((match) => match[0])
    .join("\n");
  assert.doesNotMatch(persistentWrites, /passphrase|room_passphrase/i);
  assert.match(
    appSource,
    /Object\.prototype\.hasOwnProperty\.call\(message, "passphrase"\)[\s\S]{0,100}delete message\.passphrase/,
  );
  assert.match(appSource, /if \(accessMode === "invite_only"\) message\.invite_only = true/);
  const inviteCopySource = appSource.slice(
    appSource.indexOf("async function copyRoleInvitationLink"),
    appSource.indexOf("function variantConfigDocument"),
  );
  assert.doesNotMatch(inviteCopySource, /showInvitationFallback/);
  assert.match(inviteCopySource, /document\.invitation\.token = null/);
});

test("protected-room public lobby adapter accepts only the exact boolean document", () => {
  assert.equal(
    animation.roomAccessPublicPresentation({ passphrase_required: true }),
    "招待／パスフレーズ",
  );
  assert.equal(
    animation.roomAccessPublicPresentation({ passphrase_required: false }),
    "参加コード",
  );
  assert.equal(
    animation.roomAccessPublicPresentation({
      passphrase_required: false,
      invite_only: true,
    }),
    "期限付き招待のみ",
  );
  assert.equal(
    animation.roomAccessPublicPresentation({
      passphrase_required: true,
      invite_only: true,
    }),
    null,
  );
  assert.equal(animation.roomAccessPublicPresentation({}), null);
  assert.equal(animation.roomAccessPublicPresentation({ passphrase_required: 1 }), null);
  assert.equal(
    animation.roomAccessPublicPresentation({
      passphrase_required: true,
      passphrase: "must-not-be-accepted",
    }),
    null,
  );
  assert.match(appSource, /renderLobbySettings\(lobby\.settings, lobby\.access\)/);
  assert.doesNotMatch(appSource, /settings\.passphrase_required/);
});

test("passphrase client validation counts normalized Unicode characters", () => {
  assert.equal(animation.roomPassphraseClientError("a".repeat(15)), null);
  assert.equal(animation.roomPassphraseClientError("🔐".repeat(64)), null);
  assert.match(animation.roomPassphraseClientError("🔐".repeat(14)), /15〜64文字/);
  assert.match(animation.roomPassphraseClientError("a".repeat(65)), /15〜64文字/);
  assert.match(animation.roomPassphraseClientError(" ".repeat(15)), /空白だけ/);
  assert.match(animation.roomPassphraseClientError(`safe phrase here\u0000`), /制御文字/);
});

test("join and spectate attempts prompt only after authoritative authentication failure", () => {
  animation.state.claimedInvitation = null;
  const playerAttempt = animation.roomAccessAttempt(new Map([
    ["room_code", "ab12cd"],
    ["display_name", "Player"],
    ["role", "player"],
  ]));
  assert.equal(playerAttempt.room_code, "AB12CD");
  assert.equal(animation.roomAccessAttemptLabel(playerAttempt), "部屋 AB12CD に参加");
  assert.equal(Object.hasOwn(playerAttempt, "passphrase"), false);

  animation.state.pendingRoomAccessAttempt = playerAttempt;
  assert.equal(animation.handleRoomAccessRequestError({
    code: "authentication_failed",
    message: "認証情報を確認できませんでした。",
  }), true);
  assert.equal(animation.elements["room-access-prompt"].hidden, false);
  assert.equal(animation.elements["room-access-target"].textContent, "部屋 AB12CD に参加");
  assert.equal(animation.elements["join-room-passphrase"].value, "");

  assert.equal(animation.handleRoomAccessRequestError({
    code: "room_access_rate_limited",
    message: "しばらく待ってください。",
    retry_after_seconds: 3,
  }), true);
  assert.equal(animation.elements["room-access-submit"].disabled, true);
  assert.match(animation.elements["room-access-submit"].textContent, /再試行まで 3秒/);
  animation.closeRoomAccessPrompt({ restoreFocus: false });

  const spectatorAttempt = animation.roomAccessAttempt(new Map([
    ["room_code", "ZZ99YY"],
    ["display_name", "Viewer"],
    ["role", "spectator"],
  ]));
  assert.equal(animation.roomAccessAttemptLabel(spectatorAttempt), "部屋 ZZ99YY に観戦");
});

test("room resume credentials stay server-managed and authority-changing messages use HTTP", () => {
  for (const type of ["create_room", "join_room", "leave_room", "reconnect_room"]) {
    assert.equal(animation.messageRequiresHttpTransport({ type }), true);
  }
  assert.equal(animation.messageRequiresHttpTransport({ type: "set_ready" }), false);
  assert.equal(animation.messageRequiresHttpTransport(null), false);

  assert.doesNotMatch(appSource, /sessionStorage\.(?:getItem|setItem)\(/);
  assert.equal(
    [...appSource.matchAll(/sessionStorage\.removeItem\("catan-reconnect"\)/g)].length,
    1,
  );
  assert.match(
    appSource,
    /api\("\/api\/resume", \{ method: "POST" \}\)/,
  );
  assert.match(
    appSource,
    /request\("\/api\/resume\/confirm", \{ method: "POST" \}\)/,
  );
  assert.equal(
    [...appSource.matchAll(/await confirmRoomResumeAfterWelcome\(document\)/g)].length,
    3,
  );
  assert.doesNotMatch(appSource, /api\/resume\/confirm[^\n]+body\s*:/);
  assert.match(
    appSource,
    /state\.welcome = \{ \.\.\.event \};\s*delete state\.welcome\.reconnect_token/,
  );

  animation.state.welcome = { room_code: "STALE1" };
  animation.state.lobby = { room_code: "STALE1" };
  animation.state.snapshot = { revision: 4 };
  animation.resetRoomState(false);
  assert.equal(animation.state.welcome, null);
  assert.equal(animation.state.lobby, null);
  assert.equal(animation.state.snapshot, null);

  animation.closeRoomAccessPrompt({ restoreFocus: false });
  assert.equal(animation.handleRoomAccessRequestError({
    code: "authentication_failed",
    message: "認証情報を確認できませんでした。",
  }), false);
  assert.equal(animation.elements["room-access-prompt"].hidden, true);
});

test("room resume confirmation is welcome-gated, bodyless, and failure-safe", async () => {
  assert.equal(animation.hasSessionWelcome({ events: [] }), false);
  assert.equal(animation.hasSessionWelcome({
    events: [{ type: "lobby_snapshot" }, { type: "session_welcome" }],
  }), true);

  const calls = [];
  const confirmed = await animation.confirmRoomResumeAfterWelcome({
    events: [{ type: "session_welcome" }],
  }, async (path, options) => {
    calls.push({ path, options });
    return { confirmed: true, events: [] };
  });
  assert.equal(confirmed, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/api/resume/confirm");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(Object.hasOwn(calls[0].options, "body"), false);

  let skippedCalls = 0;
  assert.equal(await animation.confirmRoomResumeAfterWelcome(
    { events: [{ type: "lobby_snapshot" }] },
    async () => { skippedCalls += 1; },
  ), false);
  assert.equal(skippedCalls, 0);

  animation.state.welcome = { room_code: "SAFE01", seat_index: 0 };
  assert.equal(await animation.confirmRoomResumeAfterWelcome(
    { events: [{ type: "session_welcome" }] },
    async () => { throw new Error("temporary network failure"); },
  ), false);
  assert.equal(animation.state.welcome.room_code, "SAFE01");

  let releaseConfirmation;
  let concurrentCalls = 0;
  const request = async () => {
    concurrentCalls += 1;
    return new Promise((resolve) => { releaseConfirmation = resolve; });
  };
  const first = animation.confirmRoomResumeAfterWelcome(
    { events: [{ type: "session_welcome" }] },
    request,
  );
  const duplicate = animation.confirmRoomResumeAfterWelcome(
    { events: [{ type: "session_welcome" }] },
    request,
  );
  assert.equal(concurrentCalls, 1);
  releaseConfirmation({ confirmed: true, events: [] });
  assert.deepEqual(await Promise.all([first, duplicate]), [true, true]);
  assert.equal(concurrentCalls, 1);
});

test("steal and trade actions identify players by name instead of seat number", () => {
  const gameState = {
    players: [
      { name: "Host" },
      { name: "CPU1" },
      { name: "Player3" },
    ],
  };

  assert.equal(
    animation.commandLabel({ command: "steal", args: { seat_index: 1 } }, gameState),
    "CPU1から資源を1枚奪う",
  );
  assert.equal(
    animation.commandLabel({ command: "trade_partner", args: { seat_index: 2 } }, gameState),
    "Player3と交渉する",
  );
});

test("ordinary action buttons do not show the authority implementation hint", () => {
  animation.state.snapshot = {
    state: { phase: { special_phase: null }, players: [{ name: "Host" }] },
  };
  animation.state.targetOptions = new Map();
  animation.state.welcome = { role: "host", seat_index: 0 };

  animation.renderActions([{ command: "roll_dice", args: {} }]);

  assert.equal(animation.elements["action-hint"].textContent, "");
  assert.equal(animation.elements["action-hint"].hidden, true);
  assert.doesNotMatch(appSource, /行動を選ぶと権威サーバーが合法性を再確認/);
});

test("standing-market create button explains why it is unavailable", () => {
  const base = {
    orders: [],
    ownOrderCount: 0,
    ownResourceTotal: 1,
  };

  assert.equal(
    animation.marketCreateUnavailableLabel(base, { replaying: true }),
    "リプレイ中は出品できません",
  );
  assert.equal(
    animation.marketCreateUnavailableLabel(base, { role: "spectator" }),
    "観戦者は出品できません",
  );
  assert.equal(
    animation.marketCreateUnavailableLabel({ ...base, orders: Array(16) }),
    "市場の注文枠が満杯です",
  );
  assert.equal(
    animation.marketCreateUnavailableLabel({ ...base, ownOrderCount: 4 }),
    "自分の注文枠は4件までです",
  );
  assert.equal(
    animation.marketCreateUnavailableLabel({ ...base, ownResourceTotal: 0 }),
    "出品できる資源がありません",
  );
  assert.equal(
    animation.marketCreateUnavailableLabel(base),
    "自分の行動手番に出品できます",
  );
});

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

test("live credit score applies only the public loan penalty", () => {
  animation.state.matchResult = null;
  animation.state.snapshot = {
    board_manifest: {
      nodes: [
        { building: { type: "settlement", owner_player_index: 0 } },
        { building: { type: "settlement", owner_player_index: 0 } },
        { building: { type: "city", owner_player_index: 1 } },
      ],
    },
  };
  const points = animation.calculatePublicPoints({
    players: [{ victory_point_cards: 3 }, { victory_point_cards: null }],
    phase: { name: "main" },
    variant_state: {
      kind: "credit",
      public: {
        loans: [
          { borrower_index: 0, status: "active" },
          { borrower_index: 1, status: "delinquent" },
        ],
      },
    },
  });

  assert.deepEqual([...points], [1, 0]);
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

test("result breakdown separates a debt penalty from secret victory cards", () => {
  const breakdown = animation.createResultVpBreakdown({
    name: "Host",
    victory_points: 3,
    vp_breakdown: {
      settlements: { count: 2, points: 2 },
      cities: { count: 0, points: 0 },
      longest_road: { awarded: false, points: 0 },
      largest_army: { awarded: false, points: 0 },
      debt_penalty: { count: 1, status: "active", points: -1 },
      victory_point_cards: { count: 2, points: 2 },
      total: 3,
    },
  });

  assert.equal(breakdown.children.length, 6);
  assert.equal(breakdown.children[4].textContent, "資源信用（返済中） -1点");
  assert.equal(breakdown.children[5].textContent, "勝利点カード 2点（2枚）");
  assert.match(breakdown.children[4].className, /penalty/);
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

test("trade editor distinguishes spendable resources from standing-market escrow", () => {
  const gameState = {
    players: [{
      resource_total: 5,
      resources: { WOOD: 1, SHEEP: 0, WHEAT: 1, BRICK: 0, ORE: 0 },
    }],
  };

  assert.equal(
    animation.ownResourceSummary(gameState, 0),
    "使用可能 2枚 / 手札5枚（市場・競売で取り置き3枚）",
  );
  assert.equal(
    animation.ownResourceSummary({
      players: [{ resource_total: 2, resources: { WOOD: 2 } }],
    }, 0),
    "あなたの手札 2枚",
  );
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
    "判断中 — CPU1: 建設候補を評価",
  );
  assert.equal(
    animation.aiCommentaryHeading(
      {
        player_name: "CPU1",
        personality: null,
        title: "建設候補を評価",
      },
      "mixed",
      false,
    ),
    "直前のAI判断 — CPU1: 建設候補を評価",
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
    "判断中 — CPU2: 交易候補を評価",
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
        catalog: "core_v2",
        forecast_lead_turns: 2,
        event_interval_turns: 6,
      },
    },
  );
  assert.equal(animation.variantLabel({ kind: "forecast_events" }), "予告イベント");
  assert.equal(animation.variantLabel({ kind: "standard" }), "通常ルール");
  assert.equal(
    animation.variantLabel({
      kind: "frontier",
      options: { catalog: "outer_ring_37_v1" },
    }),
    "フロンティア探索・37タイル",
  );
  assert.equal(
    animation.variantLabel({ kind: "frontier", options: { initial_radius: 1 } }),
    "フロンティア探索・19タイル",
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("frontier"))),
    {
      version: 1,
      kind: "frontier",
      options: {
        catalog: "outer_ring_37_v1",
        initial_radius: 1,
        reveal_rule: "road_adjacent_v1",
      },
    },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("frontier_legacy"))),
    {
      version: 1,
      kind: "frontier",
      options: { initial_radius: 1, reveal_rule: "road_adjacent_v1" },
    },
  );
  assert.equal(animation.variantLabel({ kind: "frontier" }), "フロンティア探索");
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("trade2"))),
    {
      version: 1,
      kind: "trade2",
      options: {
        catalog: "market_auction_v1",
        order_ttl_turns: 4,
        auction_ttl_turns: 4,
      },
    },
  );
  assert.equal(animation.variantLabel({ kind: "trade2" }), "交易2.0・常設市場");
  assert.equal(
    animation.variantLabel({
      kind: "trade2",
      options: { catalog: "market_auction_v1" },
    }),
    "交易2.0・市場と公開競売",
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("credit"))),
    {
      version: 1,
      kind: "credit",
      options: { catalog: "bank_loan_v1" },
    },
  );
  assert.equal(
    animation.variantLabel({ kind: "credit", options: { catalog: "bank_loan_v1" } }),
    "資源信用・借入と返済",
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("composite"))),
    {
      version: 1,
      kind: "composite",
      options: { catalog: "events_economy_v1" },
    },
  );
  assert.equal(
    animation.variantLabel({
      kind: "composite",
      options: { catalog: "events_economy_v1" },
    }),
    "イベント＆経済（複合）",
  );
  assert.equal(
    animation.variantLabel({
      kind: "composite",
      public: { catalog: "events_economy_v1", components: {} },
    }),
    "イベント＆経済（複合）",
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.variantConfigDocument("grand_campaign"))),
    {
      version: 1,
      kind: "composite",
      options: { catalog: "grand_campaign_v1" },
    },
  );
  assert.equal(
    animation.variantLabel({
      kind: "composite",
      options: { catalog: "grand_campaign_v1" },
    }),
    "グランドキャンペーン（全部入り）",
  );
});

test("composite UI reads only fixed public components and presents all three systems", () => {
  const variantState = {
    kind: "composite",
    public: {
      catalog: "events_economy_v1",
      completed_turns: 3,
      components: {
        forecast_events: {
          completed_turns: 3,
          forecast: {
            event_id: "merchant_festival_v1",
            announced_turn: 3,
            resolve_turn: 5,
            parameters: {},
          },
          active_effects: [],
          resolved_count: 0,
        },
        trade2: {
          catalog: "market_auction_v1",
          completed_turns: 3,
          orders: [],
          auctions: [],
        },
        credit: {
          catalog: "bank_loan_v1",
          completed_turns: 3,
          loans: [{
            loan_id: "loan-000000000",
            borrower_index: 0,
            borrowed_resource: "WOOD",
            due_turn: 6,
            status: "active",
            remaining_cards: 2,
            revision: 0,
          }],
        },
      },
    },
  };
  Object.defineProperty(variantState.public.components, "private", {
    get() { throw new Error("private component state must not be read"); },
  });
  Object.defineProperty(variantState.public.components, "future_system", {
    get() { throw new Error("unknown component must not be read"); },
  });
  Object.defineProperty(variantState, "private", {
    get() { throw new Error("outer private state must not be read"); },
  });

  const forecastComponent = variantState.public.components.forecast_events;
  assert.equal(animation.isCoreV2ForecastPublicState(forecastComponent), true);
  assert.equal(
    animation.variantComponentPublic(variantState, "forecast_events"),
    forecastComponent,
  );
  assert.equal(
    animation.variantComponentPublic({
      kind: "composite",
      public: {
        catalog: "events_economy_v1",
        components: {
          ...variantState.public.components,
          forecast_events: { ...forecastComponent, catalog: "core_v2" },
        },
      },
    }, "forecast_events"),
    null,
  );
  assert.equal(animation.variantIncludesComponent(variantState, "forecast_events"), true);
  assert.equal(animation.variantIncludesComponent(variantState, "trade2"), true);
  assert.equal(animation.variantIncludesComponent(variantState, "credit"), true);
  assert.equal(animation.variantIncludesComponent(variantState, "frontier"), false);
  assert.equal(animation.variantComponentPublic(variantState, "future_system"), null);
  assert.equal(
    animation.variantComponentPublic(variantState, "trade2").catalog,
    "market_auction_v1",
  );
  assert.equal(animation.forecastEventPresentation(variantState).visible, true);
  assert.equal(animation.tradeMarketPresentation(variantState).visible, true);
  assert.equal(animation.tradeAuctionPresentation(variantState).visible, true);
  const credit = animation.resourceCreditPresentation(variantState, [{ name: "Host" }]);
  assert.equal(credit.visible, true);
  assert.equal(credit.loans[0].remainingTurns, 3);

  animation.state.matchResult = null;
  animation.state.replayIndex = null;
  animation.state.welcome = { role: "host", seat_index: 0 };
  animation.state.lobby = null;
  animation.state.snapshot = {
    state: {
      players: [{
        name: "Host",
        resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 0 },
      }],
      variant_state: variantState,
      rules: {},
    },
    board_manifest: {
      nodes: [{ building: { type: "settlement", owner_player_index: 0 } }],
    },
  };
  assert.deepEqual(
    [...animation.calculatePublicPoints({
      players: [{ victory_point_cards: 2 }],
      phase: { name: "main" },
      variant_state: variantState,
    })],
    [0],
  );

  const gameState = animation.state.snapshot.state;
  animation.renderForecastEvent(variantState);
  animation.renderTradeMarket(gameState, [], 0);
  animation.renderTradeAuction(gameState, [], 0);
  animation.renderResourceCredit(gameState, [], 0);
  animation.updateRulesVariantNote();
  assert.equal(animation.elements["forecast-event-card"].hidden, false);
  assert.equal(animation.elements["market-panel"].hidden, false);
  assert.equal(animation.elements["auction-panel"].hidden, false);
  assert.equal(animation.elements["credit-panel"].hidden, false);
  assert.equal(animation.elements["rules-forecast-note"].hidden, false);
  assert.equal(animation.elements["rules-market-note"].hidden, false);
  assert.equal(animation.elements["rules-credit-note"].hidden, false);
  assert.equal(
    animation.elements["rules-variant-note"].children[0].textContent,
    "適用中の例外",
  );
});

test("grand campaign reads exactly four nested public components and shows every system", () => {
  const campaignPlan = {
    format: "catan-grand-campaign-plan",
    version: 1,
    catalog: "grand_campaign_v1",
    event_id: "harbor_blockade_v1",
    resolution_number: 2,
    eligible_harbor_ids: ["harbor-2", "harbor-11"],
    outcome: { kind: "target", harbor_id: "harbor-11" },
  };
  const forecastPublic = {
    catalog: "campaign_v1",
    completed_turns: 4,
    forecast: {
      event_id: "harbor_blockade_v1",
      resolve_turn: 6,
      parameters: { campaign_plan: campaignPlan },
    },
    active_effects: [],
    resolved_count: 2,
  };
  const frontierPublic = {
    catalog: "outer_ring_37_v1",
    completed_turns: 4,
    revealed_tiles: ["0,0", "1,0", "0,1", "-1,1", "-1,0", "0,-1", "1,-1"],
    discovery_count: 0,
  };
  const tradePublic = {
    catalog: "market_auction_v1",
    completed_turns: 4,
    orders: [],
    auctions: [],
  };
  const creditPublic = {
    catalog: "bank_loan_v1",
    completed_turns: 4,
    loans: [],
  };
  for (const publicState of [forecastPublic, frontierPublic, tradePublic, creditPublic]) {
    Object.defineProperty(publicState, "private", {
      get() { throw new Error("nested private state must not be read"); },
    });
  }
  Object.defineProperty(campaignPlan, "private", {
    get() { throw new Error("campaign private state must not be read"); },
  });
  const components = {
    forecast_events: forecastPublic,
    frontier: frontierPublic,
    trade2: tradePublic,
    credit: creditPublic,
  };
  Object.defineProperty(components, "private", {
    get() { throw new Error("private component state must not be read"); },
  });
  Object.defineProperty(components, "future_system", {
    get() { throw new Error("unknown component state must not be read"); },
  });
  const variantState = {
    kind: "composite",
    public: {
      catalog: "grand_campaign_v1",
      completed_turns: 4,
      components,
    },
  };
  Object.defineProperty(variantState, "private", {
    get() { throw new Error("outer private state must not be read"); },
  });

  for (const kind of ["forecast_events", "frontier", "trade2", "credit"]) {
    assert.equal(animation.variantIncludesComponent(variantState, kind), true);
    assert.equal(animation.variantComponentPublic(variantState, kind), components[kind]);
  }
  assert.equal(animation.variantIncludesComponent(variantState, "future_system"), false);
  assert.equal(animation.variantComponentPublic(variantState, "future_system"), null);
  assert.equal(
    animation.variantComponentPublic({
      kind: "composite",
      public: {
        catalog: "grand_campaign_v1",
        components: { forecast_events: { ...forecastPublic, catalog: "core_v2" } },
      },
    }, "forecast_events"),
    null,
  );

  const forecast = animation.forecastEventPresentation(variantState);
  assert.equal(forecast.visible, true);
  assert.equal(forecast.countdown, "あと2手番");
  assert.match(forecast.description, /対象: 交換所 #12/);
  assert.deepEqual(
    JSON.parse(JSON.stringify(animation.frontierPresentation(variantState))),
    {
      visible: true,
      count: "7 / 37 公開",
      detail: "外周は未探索です。霧に接する街道を建設すると資源・数字・港が公開されます。",
    },
  );
  assert.equal(animation.tradeMarketPresentation(variantState).visible, true);
  assert.equal(animation.tradeAuctionPresentation(variantState).visible, true);
  assert.equal(animation.resourceCreditPresentation(variantState).visible, true);

  animation.state.matchResult = null;
  animation.state.replayIndex = null;
  animation.state.welcome = { role: "host", seat_index: 0 };
  animation.state.lobby = null;
  animation.state.snapshot = {
    state: {
      players: [{
        name: "Host",
        resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 0 },
      }],
      variant_state: variantState,
      rules: {},
    },
    board_manifest: { tiles: Array(37), nodes: [] },
  };
  const gameState = animation.state.snapshot.state;
  animation.renderForecastEvent(variantState);
  animation.renderFrontierStatus(variantState, 37);
  animation.renderTradeMarket(gameState, [], 0);
  animation.renderTradeAuction(gameState, [], 0);
  animation.renderResourceCredit(gameState, [], 0);
  animation.updateRulesVariantNote();
  assert.equal(animation.elements["forecast-event-card"].hidden, false);
  assert.equal(animation.elements["frontier-status-card"].hidden, false);
  assert.equal(animation.elements["market-panel"].hidden, false);
  assert.equal(animation.elements["auction-panel"].hidden, false);
  assert.equal(animation.elements["credit-panel"].hidden, false);
  assert.equal(animation.elements["rules-campaign-note"].hidden, false);
  assert.equal(animation.elements["rules-forecast-note"].hidden, false);
  assert.equal(animation.elements["rules-frontier-note"].hidden, false);
  assert.equal(animation.elements["rules-market-note"].hidden, false);
  assert.equal(animation.elements["rules-credit-note"].hidden, false);
  assert.match(
    animation.elements["rules-variant-note"].children[1].textContent,
    /グランドキャンペーン（全部入り）/,
  );
});

test("campaign harbor forecasts distinguish a public target from a safe skip", () => {
  const basePlan = {
    format: "catan-grand-campaign-plan",
    version: 1,
    catalog: "grand_campaign_v1",
    event_id: "harbor_blockade_v1",
    resolution_number: 0,
  };
  const target = {
    ...basePlan,
    eligible_harbor_ids: ["harbor-3"],
    outcome: { kind: "target", harbor_id: "harbor-3" },
  };
  const skip = {
    ...basePlan,
    eligible_harbor_ids: [],
    outcome: { kind: "skip", reason: "no_revealed_harbors" },
  };
  assert.equal(
    animation.campaignHarborBlockadeLabel({ campaign_plan: target }),
    "対象: 交換所 #4",
  );
  assert.equal(
    animation.campaignHarborBlockadeTargetId({ campaign_plan: target }),
    "harbor-3",
  );
  assert.equal(
    animation.campaignHarborBlockadeLabel({ campaign_plan: skip }),
    "公開済み交換所なし・今回は発動なし",
  );
  assert.equal(
    animation.campaignHarborBlockadeLabel({
      campaign_plan: {
        ...target,
        eligible_harbor_ids: ["harbor-2"],
      },
    }),
    "",
  );
  assert.equal(
    animation.campaignHarborBlockadeTargetId({
      campaign_plan: {
        ...target,
        eligible_harbor_ids: ["harbor-2"],
      },
    }),
    null,
  );

  const campaignVariantState = {
    kind: "composite",
    public: {
      catalog: "grand_campaign_v1",
      components: {
        forecast_events: {
          catalog: "campaign_v1",
          completed_turns: 2,
          forecast: {
            event_id: "harbor_blockade_v1",
            announced_turn: 2,
            resolve_turn: 4,
            parameters: { campaign_plan: target },
          },
          active_effects: [{
            event_id: "harbor_blockade_v1",
            started_turn: 1,
            expires_turn: 3,
            parameters: {
              campaign_plan: {
                ...basePlan,
                eligible_harbor_ids: ["harbor-1"],
                outcome: { kind: "target", harbor_id: "harbor-1" },
              },
            },
          }],
          resolved_count: 1,
        },
      },
    },
  };
  Object.defineProperty(campaignVariantState, "private", {
    get() { throw new Error("private forecast state must not be read"); },
  });
  assert.equal(animation.forecastAnnouncedHarborId(campaignVariantState), "harbor-3");
  assert.equal(
    animation.forecastHarborTargetId(
      "harbor_blockade_v1",
      { harbor_id: "harbor-4" },
      null,
    ),
    "harbor-4",
  );
  assert.equal(
    animation.forecastHarborTargetId(
      "harbor_blockade_v1",
      { harbor_id: "harbor-9" },
      null,
    ),
    null,
  );
  assert.equal(
    animation.forecastHarborTargetId(
      "earthquake_v1",
      { harbor_id: "harbor-4" },
      null,
    ),
    null,
  );

  const skipPresentation = animation.forecastEventPresentation({
    kind: "composite",
    public: {
      catalog: "grand_campaign_v1",
      components: {
        forecast_events: {
          catalog: "campaign_v1",
          completed_turns: 2,
          forecast: {
            event_id: "harbor_blockade_v1",
            resolve_turn: 4,
            parameters: { campaign_plan: skip },
          },
          active_effects: [],
          resolved_count: 0,
        },
      },
    },
  });
  assert.equal(skipPresentation.description, "公開済み交換所なし・今回は発動なし。");
  assert.equal(
    skipPresentation.compact,
    "港湾封鎖・あと2手番・公開済み交換所なし・今回は発動なし",
  );
});

test("announced public harbor is marked on the visible board badge", () => {
  const presentation = animation.harborForecastPresentation({
    id: "harbor-3",
    label: "木 2:1",
    forecast_blocked: false,
  }, true);
  assert.equal(presentation.announced, true);
  assert.match(presentation.className, /forecast-harbor-announced/);
  assert.match(presentation.title, /港湾封鎖を予告中/);
  assert.equal(presentation.statusLabel, "⚠ #4 予告");

  const layer = new FakeElement("harbor-layer");
  animation.drawBoardHarbor(layer, {
    harbor: {
      id: "harbor-3",
      label: "木 2:1",
      resource: "WOOD",
      forecast_blocked: false,
    },
    geometry: {
      axis: { x: 1, y: 0 },
      outward: { x: 0, y: -1 },
    },
    dock: {
      innerLeft: { x: 0, y: 0 },
      outerLeft: { x: 0, y: -10 },
      innerRight: { x: 10, y: 0 },
      outerRight: { x: 10, y: -10 },
      connectorStart: { x: 5, y: -10 },
    },
    rect: { x: 20, y: -20, width: 70, height: 30 },
    connectorLead: { x: 13, y: -10 },
    connectorEnd: { x: 20, y: -5 },
  }, true);
  assert.equal(layer.children.length, 1);
  const badge = layer.children[0];
  assert.match(badge.getAttribute("class"), /forecast-harbor-announced/);
  assert.equal(badge.getAttribute("data-harbor-id"), "harbor-3");
  assert.equal(badge.getAttribute("data-forecast-announced"), "true");
  assert.match(badge.children[0].textContent, /港湾封鎖を予告中/);
  assert.ok(badge.children.some((child) => child.textContent === "⚠ #4 予告"));
  assert.match(cssSource, /\.forecast-harbor-announced/);
  assert.match(cssSource, /\.forecast-harbor-notice-announced/);
});

test("composite and grand campaign remain distinct lobby choices with responsive order", () => {
  assert.match(indexSource, /value="composite">イベント＆経済（複合）/);
  assert.match(indexSource, /value="grand_campaign">グランドキャンペーン（全部入り）/);
  assert.match(indexSource, /グランドキャンペーン（全部入り）[^<]*は37タイル探索/);
  assert.match(indexSource, /name="variant_kind"[^>]*aria-describedby="variant-kind-note"/);
  assert.match(indexSource, /id="frontier-status-card"[^>]*aria-labelledby="frontier-status-title"/);
  assert.match(indexSource, /id="rules-forecast-note"/);
  assert.match(indexSource, /id="rules-frontier-note"[^>]*hidden/);
  assert.match(indexSource, /id="rules-campaign-note"[^>]*hidden/);
  assert.match(cssSource, /events_economy_v1[^}]+\.market-panel\s*\{\s*order:\s*2;/s);
  assert.match(cssSource, /events_economy_v1[^}]+\.event-panel\s*\{\s*order:\s*6;/s);
  assert.match(cssSource, /grand_campaign_v1[^}]+\.market-panel\s*\{\s*order:\s*2;/s);
  assert.match(cssSource, /grand_campaign_v1[^}]+\.event-panel\s*\{\s*order:\s*6;/s);
  assert.match(
    cssSource,
    /@media \(max-width: 760px\)[\s\S]*\.market-order-list,[\s\S]*\.credit-loan-list\s*\{[^}]*max-height:\s*none;[^}]*overflow-y:\s*visible;/,
  );
});

test("resource credit presents public deadlines, debt, and exact own commands", () => {
  const presentation = animation.resourceCreditPresentation(
    {
      kind: "credit",
      public: {
        catalog: "bank_loan_v1",
        completed_turns: 7,
        loans: [
          {
            loan_id: "loan-000000000",
            borrower_index: 0,
            borrowed_resource: "WOOD",
            opened_turn: 5,
            due_turn: 9,
            status: "active",
            remaining_cards: 2,
            revision: 1,
          },
          {
            loan_id: "loan-000000001",
            borrower_index: 1,
            borrowed_resource: "ORE",
            opened_turn: 2,
            due_turn: 5,
            status: "delinquent",
            remaining_cards: 2,
            revision: 3,
          },
        ],
      },
    },
    [{ name: "Host", resources: { WOOD: 1 } }, { name: "Guest" }],
    [{
      command: "credit_repay",
      args: { loan_id: "loan-000000000", revision: 1 },
    }],
    0,
  );

  assert.equal(presentation.visible, true);
  assert.equal(presentation.countLabel, "債務 2件");
  assert.equal(presentation.loans[0].borrowerName, "Host");
  assert.equal(presentation.loans[0].remainingTurns, 2);
  assert.equal(presentation.loans[0].publicVpPenalty, 1);
  assert.equal(presentation.loans[0].repayOption.args.revision, 1);
  assert.equal(presentation.loans[1].delinquent, true);
  assert.equal(presentation.loans[1].remaining_cards, 2);
  assert.equal(presentation.loans[1].publicVpPenalty, 2);
  assert.equal(
    animation.resourceCreditPresentation({ kind: "standard", public: {} }).visible,
    false,
  );
  assert.equal(
    animation.creditDeadlineLabel({ delinquent: false, remainingTurns: 0 }),
    "この手番終了まで",
  );
  assert.equal(
    animation.creditDeadlineLabel({ delinquent: false, remainingTurns: 2 }),
    "返済まで2手番",
  );
  assert.equal(
    animation.creditDeadlineLabel({ delinquent: true, remainingTurns: 0 }),
    "延滞中",
  );
});

test("resource credit validates borrowing, active full repayment, and delinquent partial repayment", () => {
  const available = { WOOD: 2, SHEEP: 1, WHEAT: 0, BRICK: 0, ORE: 0 };
  assert.equal(
    animation.creditDraftValidation({ mode: "borrow", resource: "WOOD" }).valid,
    true,
  );
  assert.equal(
    animation.creditDraftValidation({ mode: "borrow", resource: null }).valid,
    false,
  );
  const active = {
    borrowed_resource: "WOOD",
    status: "active",
    remaining_cards: 2,
  };
  assert.equal(
    animation.creditDraftValidation(
      { mode: "repay", payment: { WOOD: 1, SHEEP: 1 } },
      available,
      active,
    ).valid,
    true,
  );
  assert.equal(
    animation.creditDraftValidation(
      { mode: "repay", payment: { SHEEP: 1 } },
      available,
      active,
    ).valid,
    false,
  );
  const partial = animation.creditDraftValidation(
    { mode: "repay", payment: { SHEEP: 1 } },
    available,
    { borrowed_resource: "ORE", status: "delinquent", remaining_cards: 3 },
  );
  assert.equal(partial.valid, true);
  assert.match(partial.message, /残債は2枚/);
});

test("resource credit DOM keeps public debt visible while only the borrower gets repayment controls", () => {
  animation.state.welcome = { role: "player", seat_index: 0 };
  animation.state.replayIndex = null;
  animation.state.commandPending = false;
  const gameState = {
    players: [
      { name: "Host", resources: { WOOD: 1, SHEEP: 1 } },
      { name: "Guest" },
    ],
    variant_state: {
      kind: "credit",
      public: {
        catalog: "bank_loan_v1",
        completed_turns: 4,
        loans: [{
          loan_id: "loan-000000000",
          borrower_index: 1,
          borrowed_resource: "ORE",
          opened_turn: 2,
          due_turn: 4,
          status: "delinquent",
          remaining_cards: 2,
          revision: 2,
        }],
      },
    },
  };
  animation.renderResourceCredit(gameState, [], 0);
  assert.equal(animation.elements["credit-panel"].hidden, false);
  assert.equal(animation.elements["credit-loan-list"].children.length, 1);
  assert.equal(animation.elements["credit-open-button"].disabled, true);
  assert.match(animation.elements["credit-hint"].textContent, /通常債務/);

  gameState.variant_state.public.loans[0].borrower_index = 0;
  animation.renderResourceCredit(gameState, [{
    command: "credit_repay",
    args: { loan_id: "loan-000000000", revision: 2 },
  }], 0);
  assert.equal(animation.elements["credit-open-button"].disabled, false);
  assert.match(animation.elements["credit-open-button"].textContent, /残り2枚/);
});

test("public auction presentation binds bids and seller awards to exact revisions", () => {
  const presentation = animation.tradeAuctionPresentation(
    {
      kind: "trade2",
      public: {
        catalog: "market_auction_v1",
        completed_turns: 8,
        orders: [],
        auctions: [{
          auction_id: "auction-000000002",
          seller_index: 0,
          offer: { WOOD: 1 },
          minimum_bid_cards: 1,
          created_turn: 7,
          expires_turn: 11,
          revision: 3,
          bids: [{ bidder_index: 1, offer: { ORE: 1 }, revision: 1 }],
        }],
      },
    },
    [
      { name: "Host", resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 0 } },
      { name: "Guest", resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 0 } },
    ],
    [{
      command: "auction_accept",
      args: {
        auction_id: "auction-000000002",
        revision: 3,
        bidder_index: 1,
      },
    }],
    0,
  );
  assert.equal(presentation.visible, true);
  assert.equal(presentation.countLabel, "1 / 8");
  assert.equal(presentation.auctions[0].remainingTurns, 3);
  assert.equal(presentation.auctions[0].bids[0].bidderName, "Guest");
  assert.equal(
    presentation.auctions[0].bids[0].acceptOption.args.revision,
    3,
  );
});

test("auction editor validates minimum totals and forbids bidding the lot resource", () => {
  const available = { WOOD: 2, SHEEP: 2, WHEAT: 0, BRICK: 0, ORE: 1 };
  assert.equal(
    animation.auctionDraftValidation(
      { mode: "create", offer: { WOOD: 1 }, minimumBidCards: 2 },
      available,
    ).valid,
    true,
  );
  const auction = {
    offer: { WOOD: 1 },
    minimum_bid_cards: 2,
    bids: [],
  };
  assert.equal(
    animation.auctionDraftValidation(
      { mode: "bid", offer: { WOOD: 2 } },
      available,
      auction,
    ).valid,
    false,
  );
  assert.equal(
    animation.auctionDraftValidation(
      { mode: "bid", offer: { SHEEP: 2 } },
      available,
      auction,
    ).valid,
    true,
  );
});

test("standing market presentation uses public orders, named sellers, and exact authority options", () => {
  const presentation = animation.tradeMarketPresentation(
    {
      kind: "trade2",
      public: {
        catalog: "standing_market_v1",
        completed_turns: 7,
        orders: [{
          order_id: "market-000000004",
          seller_index: 1,
          offer: { WOOD: 2 },
          wanted: { ORE: 1 },
          created_turn: 6,
          expires_turn: 10,
          revision: 1,
        }],
      },
    },
    [
      { name: "Host", resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 1 } },
      { name: "CPU1", resources: null },
    ],
    [
      { command: "market_create", args: {} },
      { command: "market_fill", args: { order_id: "market-000000004", revision: 1 } },
      { command: "market_fill", args: { order_id: "market-000000099", revision: 1 } },
    ],
    0,
  );

  assert.equal(presentation.visible, true);
  assert.equal(presentation.countLabel, "1 / 16");
  assert.equal(presentation.orders[0].sellerName, "CPU1");
  assert.equal(presentation.orders[0].remainingTurns, 3);
  assert.equal(presentation.orders[0].canAfford, true);
  assert.equal(presentation.orders[0].fillOption.args.order_id, "market-000000004");
  assert.equal(presentation.orders[0].cancelOption, null);
  assert.equal(animation.formatMarketBundle({ WOOD: 2, ORE: 1 }), "木2 + 鉄1");
  assert.equal(
    animation.tradeMarketPresentation({ kind: "standard" }).visible,
    false,
  );
});

test("standing market accepts only the current exact authority option and exposes own cancellation", () => {
  const variantState = {
    kind: "trade2",
    public: {
      catalog: "standing_market_v1",
      completed_turns: 4,
      orders: [
        {
          order_id: "market-000000001",
          seller_index: 0,
          offer: { WOOD: 1 },
          wanted: { ORE: 1 },
          created_turn: 3,
          expires_turn: 7,
          revision: 2,
        },
        {
          order_id: "market-000000002",
          seller_index: 1,
          offer: { SHEEP: 1 },
          wanted: { WHEAT: 1 },
          created_turn: 4,
          expires_turn: 8,
          revision: 3,
        },
      ],
    },
  };
  const presentation = animation.tradeMarketPresentation(
    variantState,
    [
      { name: "Host", resources: { WOOD: 0, SHEEP: 0, WHEAT: 1, BRICK: 0, ORE: 0 } },
      { name: "CPU1", resources: null },
    ],
    [
      { command: "market_cancel", args: { order_id: "market-000000001", revision: 2 } },
      { command: "market_fill", args: { order_id: "market-000000002", revision: 2 } },
    ],
    0,
  );

  assert.equal(presentation.orders[0].isOwn, true);
  assert.equal(presentation.orders[0].cancelOption.args.revision, 2);
  assert.equal(presentation.orders[0].fillOption, null);
  assert.equal(presentation.orders[1].canAfford, true);
  assert.equal(presentation.orders[1].fillOption, null, "stale order revisions must not become clickable");
  assert.equal(presentation.orders[1].cancelOption, null);
});

test("standing market remains public but read-only for replay and spectators", () => {
  resetAnimationState();
  const marketSnapshot = snapshot(91);
  marketSnapshot.state.players = [
    { name: "Host", resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 1 } },
    { name: "CPU1", resources: null },
  ];
  marketSnapshot.state.variant_state = {
    kind: "trade2",
    public: {
      completed_turns: 1,
      orders: [{
        order_id: "market-000000003",
        seller_index: 1,
        offer: { WOOD: 1 },
        wanted: { ORE: 1 },
        created_turn: 0,
        expires_turn: 4,
        revision: 1,
      }],
    },
  };
  marketSnapshot.command_options = [
    { command: "market_create", args: {} },
    { command: "market_fill", args: { order_id: "market-000000003", revision: 1 } },
  ];

  animation.state.replayIndex = 0;
  const replayOptions = animation.commandOptionsForView(marketSnapshot);
  const replayPresentation = animation.tradeMarketPresentation(
    marketSnapshot.state.variant_state,
    marketSnapshot.state.players,
    replayOptions,
    0,
  );
  assert.equal(replayPresentation.orders.length, 1);
  assert.equal(replayPresentation.createOption, null);
  assert.equal(replayPresentation.orders[0].fillOption, null);

  const spectatorPresentation = animation.tradeMarketPresentation(
    marketSnapshot.state.variant_state,
    marketSnapshot.state.players,
    [],
    null,
  );
  assert.equal(spectatorPresentation.orders.length, 1);
  assert.equal(spectatorPresentation.orders[0].isOwn, false);
  assert.equal(spectatorPresentation.orders[0].fillOption, null);
  assert.equal(spectatorPresentation.orders[0].cancelOption, null);
});

test("standing market buttons are enabled only by matching authority options", () => {
  resetAnimationState();
  animation.state.welcome = { role: "host", seat_index: 0 };
  const gameState = {
    players: [
      { name: "Host", resources: { WOOD: 0, SHEEP: 0, WHEAT: 0, BRICK: 0, ORE: 3 } },
      { name: "CPU1", resources: null },
    ],
    variant_state: {
      kind: "trade2",
      public: {
        completed_turns: 2,
        orders: [
          {
            order_id: "market-000000010",
            seller_index: 0,
            offer: { WOOD: 1 },
            wanted: { ORE: 1 },
            created_turn: 1,
            expires_turn: 5,
            revision: 1,
          },
          {
            order_id: "market-000000011",
            seller_index: 1,
            offer: { SHEEP: 1 },
            wanted: { ORE: 1 },
            created_turn: 1,
            expires_turn: 5,
            revision: 2,
          },
          {
            order_id: "market-000000012",
            seller_index: 1,
            offer: { WHEAT: 1 },
            wanted: { ORE: 1 },
            created_turn: 1,
            expires_turn: 5,
            revision: 3,
          },
        ],
      },
    },
  };
  const options = [
    { command: "market_cancel", args: { order_id: "market-000000010", revision: 1 } },
    { command: "market_fill", args: { order_id: "market-000000011", revision: 2 } },
    { command: "market_fill", args: { order_id: "market-000000012", revision: 2 } },
  ];

  animation.renderTradeMarket(gameState, options, 0);
  const cards = animation.elements["market-order-list"].children;
  assert.equal(cards.length, 3);
  assert.equal(cards[0].children.at(-1).textContent, "この注文を取り消す");
  assert.equal(cards[0].children.at(-1).disabled, false);
  assert.equal(cards[1].children.at(-1).textContent, "この条件で購入");
  assert.equal(cards[1].children.at(-1).disabled, false);
  assert.equal(cards[2].children.at(-1).textContent, "自分の行動手番に購入");
  assert.equal(cards[2].children.at(-1).disabled, true);

  animation.state.commandPending = true;
  animation.renderTradeMarket(gameState, options, 0);
  const pendingCards = animation.elements["market-order-list"].children;
  assert.equal(pendingCards[0].children.at(-1).disabled, true);
  assert.equal(pendingCards[1].children.at(-1).disabled, true);
});

test("market order draft enforces available escrow, nonempty sides, overlap, and 19-card cap", () => {
  const available = { WOOD: 2, SHEEP: 1, WHEAT: 0, BRICK: 0, ORE: 0 };
  const valid = animation.marketDraftValidation({
    offer: { WOOD: 2 },
    wanted: { ORE: 1 },
  }, available);
  assert.equal(valid.valid, true);
  assert.deepEqual(JSON.parse(JSON.stringify(valid.offer)), { WOOD: 2 });
  assert.deepEqual(JSON.parse(JSON.stringify(valid.wanted)), { ORE: 1 });

  assert.equal(animation.marketDraftValidation({
    offer: { WOOD: 3 },
    wanted: { ORE: 1 },
  }, available).valid, false);
  assert.equal(animation.marketDraftValidation({
    offer: { WOOD: 1 },
    wanted: { WOOD: 1 },
  }, available).valid, false);
  assert.equal(animation.marketDraftValidation({
    offer: { WOOD: 1 },
    wanted: { ORE: 20 },
  }, available).valid, false);
  assert.equal(animation.marketDraftValidation({
    offer: {},
    wanted: { ORE: 1 },
  }, available).valid, false);
  assert.equal(animation.marketDraftValidation({
    offer: { WOOD: 1 },
    wanted: {},
  }, available).valid, false);
  assert.equal(animation.marketDraftValidation({
    offer: { WOOD: 1.5 },
    wanted: { ORE: 1 },
  }, available).valid, false);
});

test("market authority commands stay out of the generic action button grid", () => {
  animation.state.snapshot = {
    state: { phase: { special_phase: null }, players: [{ name: "Host" }] },
  };
  animation.state.targetOptions = new Map();
  animation.state.welcome = { role: "host", seat_index: 0 };

  animation.renderActions([
    { command: "market_create", args: {} },
    { command: "market_fill", args: { order_id: "market-000000004", revision: 1 } },
    { command: "auction_create", args: {} },
    { command: "auction_bid", args: { auction_id: "auction-000000001", revision: 1 } },
    { command: "credit_borrow", args: { resource: "WOOD" } },
    { command: "credit_repay", args: { loan_id: "loan-000000000", revision: 1 } },
    { command: "roll_dice", args: {} },
  ]);

  assert.equal(animation.elements["action-list"].children.length, 1);
  assert.equal(animation.elements["action-list"].children[0].textContent, "ダイスを振る");
  assert.equal(animation.elements["action-hint"].hidden, true);
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
  assert.equal(
    animation.frontierPresentation({
      kind: "frontier",
      public: {
        catalog: "outer_ring_37_v1",
        revealed_tiles: ["-1,0", "0,0", "1,0"],
        discovery_count: 0,
      },
    }, 37).count,
    "3 / 37 公開",
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
  assert.deepEqual(Array.from(presentation.active), ["豊作: 次の麦生産に+1・次の麦生産まで"]);
  assert.equal(
    animation.forecastEventPresentation({ kind: "standard", public: {} }).visible,
    false,
  );
});

test("forecast mode controls and persistent event card are present and styled", () => {
  assert.match(indexSource, /<select name="variant_kind"[^>]*>[\s\S]*value="forecast_events"/);
  assert.match(indexSource, /id="forecast-event-card"[^>]*hidden/);
  assert.match(indexSource, /id="forecast-active-list"/);
  assert.ok(indexSource.indexOf('id="forecast-compact-strip"') < indexSource.indexOf('id="board-shell"'));
  assert.match(indexSource, /id="forecast-live-status"[^>]*role="status"/);
  assert.match(cssSource, /\.forecast-event-card\s*\{/);
  assert.match(cssSource, /\.forecast-event-card\[hidden\]\s*\{[\s\S]*display:\s*none/);
  assert.match(cssSource, /@media \(max-width:\s*760px\)[\s\S]*\.forecast-compact-strip/);
});

test("all core_v2 forecast targets and active durations have readable labels", () => {
  assert.equal(animation.forecastParameterLabel("harbor_blockade_v1", { harbor_id: "harbor-4" }), "対象: 交換所 #5");
  assert.equal(animation.forecastParameterLabel("bandit_raid_v1", { target_number: 8 }), "対象数字: 8");
  assert.equal(animation.forecastParameterLabel("earthquake_v1", { sector: 2 }), "対象: 南西側");
  assert.equal(animation.forecastActiveTiming({ event_id: "harbor_blockade_v1", expires_turn: 9 }, 7), "残り2手番");
  assert.equal(animation.forecastActiveTiming({ event_id: "construction_boom_v1", expires_turn: null }, 7), "次の有料街道まで");

  const harborForecast = animation.forecastEventPresentation({
    kind: "forecast_events",
    public: {
      completed_turns: 1,
      forecast: {
        event_id: "harbor_blockade_v1",
        resolve_turn: 3,
        parameters: { harbor_id: "harbor-4" },
      },
      active_effects: [],
    },
  });
  assert.equal(
    harborForecast.compact,
    "港湾封鎖・あと2手番・対象: 交換所 #5",
  );

  for (const eventId of [
    "wheat_harvest_v1",
    "sheep_drought_v1",
    "harbor_blockade_v1",
    "construction_boom_v1",
    "merchant_festival_v1",
    "bandit_raid_v1",
    "earthquake_v1",
  ]) {
    const presentation = animation.forecastEventPresentation({
      kind: "forecast_events",
      public: {
        completed_turns: 1,
        forecast: { event_id: eventId, resolve_turn: 3, parameters: {} },
        active_effects: [],
      },
    });
    assert.equal(presentation.visible, true);
    assert.notEqual(presentation.title, "未知のイベント");
  }
});

test("frontier mode includes fog status and generated terrain asset", () => {
  assert.match(indexSource, /<select name="variant_kind"[^>]*>[\s\S]*value="frontier"/);
  assert.match(indexSource, /id="frontier-status-card"[^>]*hidden/);
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

test("standing market has a dedicated responsive panel, accessible editor, and escrow disclosure", () => {
  assert.match(indexSource, /<select name="variant_kind"[^>]*>[\s\S]*value="trade2"/);
  assert.match(indexSource, /id="market-panel"[^>]*aria-labelledby="market-panel-title"[^>]*hidden/);
  assert.match(indexSource, /id="market-editor"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(indexSource, /id="rules-market-note"[^>]*hidden/);
  assert.match(cssSource, /\.market-order-terms\s*\{[\s\S]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(cssSource, /@media \(max-width: 440px\)[\s\S]*\.market-editor-grid[\s\S]*grid-template-columns:\s*1fr/);
  assert.match(appSource, /市場・競売に\$\{reservedCount\}枚取り置き中/);
  assert.match(appSource, /state\.marketEditorOpen[\s\S]*syncModalBodyState/);
  assert.match(indexSource, /id="auction-panel"[^>]*aria-labelledby="auction-panel-title"/);
  assert.match(indexSource, /id="auction-editor"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(cssSource, /\.auction-list\s*\{[\s\S]*overflow-y:\s*auto/);
  assert.match(appSource, /state\.auctionEditorOpen[\s\S]*syncModalBodyState/);
});

test("resource credit has a public responsive panel and an accessible private editor", () => {
  assert.match(indexSource, /<select name="variant_kind"[^>]*>[\s\S]*value="credit"/);
  assert.match(indexSource, /id="credit-panel"[^>]*aria-labelledby="credit-panel-title"[^>]*hidden/);
  assert.match(indexSource, /id="credit-loan-list"[^>]*aria-live="polite"/);
  assert.match(indexSource, /id="credit-availability"/);
  assert.match(indexSource, /id="credit-editor"[^>]*role="dialog"[^>]*aria-modal="true"/);
  assert.match(indexSource, /id="credit-editor-summary"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(indexSource, /id="rules-credit-note"[^>]*hidden/);
  assert.match(cssSource, /\.credit-loan-list\s*\{[\s\S]*overflow-y:\s*auto/);
  assert.match(cssSource, /@media \(max-width: 440px\)[\s\S]*\.credit-editor-actions[\s\S]*grid-template-columns:\s*1fr/);
  assert.match(appSource, /state\.creditEditorOpen[\s\S]*syncModalBodyState/);
  assert.match(appSource, /loan\.status === "delinquent"[\s\S]*remaining_cards/);
});

test("unsolicited websocket pushes do not consume a pending command response", () => {
  assert.match(
    appSource,
    /document\.kind === "response"\s*\? state\.socketRequests\.shift\(\)\s*:\s*null/,
  );
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
