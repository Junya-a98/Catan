import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const audioSource = readFileSync(
  new URL("../web/audio.js", import.meta.url),
  "utf8",
);

const STORAGE_KEYS = Object.freeze({
  sfx: "catan.audio.sfx",
  bgm: "catan.audio.bgm",
  volume: "catan.audio.volume",
});

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(
      type,
      listeners.filter((candidate) => candidate !== listener),
    );
  }

  dispatch(type, event = {}) {
    for (const listener of [...(this.listeners.get(type) || [])]) {
      listener(event);
    }
  }

  dispatchEvent(event) {
    this.dispatch(event.type, event);
    return true;
  }
}

class FakeButton extends FakeEventTarget {
  constructor(id) {
    super();
    this.id = id;
    this.dataset = {};
    this.attributes = new Map();
    this.statusNode = { textContent: "" };
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  querySelector(selector) {
    return selector === "[data-audio-state]" ? this.statusNode : null;
  }
}

class FakeStorage {
  constructor(initialValues = {}, shouldThrow = false) {
    this.values = new Map(
      Object.entries(initialValues).map(([key, value]) => [key, String(value)]),
    );
    this.shouldThrow = shouldThrow;
  }

  getItem(key) {
    if (this.shouldThrow) {
      throw new Error("storage unavailable");
    }
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    if (this.shouldThrow) {
      throw new Error("storage unavailable");
    }
    this.values.set(key, String(value));
  }
}

class FakeAudioParam {
  constructor(value = 0) {
    this.value = value;
  }

  setValueAtTime(value) {
    this.value = value;
  }

  linearRampToValueAtTime(value) {
    this.value = value;
  }

  exponentialRampToValueAtTime(value) {
    this.value = value;
  }

  setTargetAtTime(value) {
    this.value = value;
  }
}

class FakeAudioNode extends FakeEventTarget {
  connect(destination) {
    this.destination = destination;
    return destination;
  }
}

class FakeGainNode extends FakeAudioNode {
  constructor() {
    super();
    this.gain = new FakeAudioParam(1);
  }
}

class FakeOscillatorNode extends FakeAudioNode {
  constructor() {
    super();
    this.frequency = new FakeAudioParam();
    this.type = "sine";
  }

  start(when) {
    this.startedAt = when;
  }

  stop(when) {
    this.stoppedAt = when;
  }
}

class FakeBufferSourceNode extends FakeAudioNode {
  start(when) {
    this.startedAt = when;
  }

  stop(when) {
    this.stoppedAt = when;
  }
}

class FakeBiquadFilterNode extends FakeAudioNode {
  constructor() {
    super();
    this.frequency = new FakeAudioParam();
    this.Q = new FakeAudioParam();
    this.type = "lowpass";
  }
}

function createAudioContextClass(counter) {
  return class FakeAudioContext {
    constructor() {
      counter.instances += 1;
      this.state = "running";
      this.currentTime = 1;
      this.sampleRate = 8000;
      this.destination = new FakeAudioNode();
    }

    createGain() {
      return new FakeGainNode();
    }

    createOscillator() {
      return new FakeOscillatorNode();
    }

    createBuffer(_channels, frameCount) {
      const channel = new Float32Array(frameCount);
      return { getChannelData: () => channel };
    }

    createBufferSource() {
      return new FakeBufferSourceNode();
    }

    createBiquadFilter() {
      return new FakeBiquadFilterNode();
    }

    resume() {
      this.state = "running";
      return Promise.resolve();
    }
  };
}

function createHarness({ initialStorage = {}, storageThrows = false } = {}) {
  const storage = new FakeStorage(initialStorage, storageThrows);
  const counter = { instances: 0, timeouts: 0, clears: 0 };
  const sfxButton = new FakeButton("audio-sfx-toggle");
  const bgmButton = new FakeButton("audio-bgm-toggle");
  const volumeControl = new FakeButton("audio-volume");
  volumeControl.value = "";
  const buttons = new Map([
    [sfxButton.id, sfxButton],
    [bgmButton.id, bgmButton],
    [volumeControl.id, volumeControl],
  ]);
  const document = new FakeEventTarget();
  document.readyState = "complete";
  document.hidden = false;
  document.getElementById = (id) => buttons.get(id) || null;

  const window = new FakeEventTarget();
  window.AudioContext = createAudioContextClass(counter);
  window.localStorage = storage;
  window.setTimeout = () => {
    counter.timeouts += 1;
    return counter.timeouts;
  };
  window.clearTimeout = () => {
    counter.clears += 1;
  };

  class FakeCustomEvent {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  }

  const navigator = {
    userActivation: { isActive: false, hasBeenActive: false },
  };
  const sandbox = {
    console,
    CustomEvent: FakeCustomEvent,
    document,
    navigator,
    window,
  };
  vm.createContext(sandbox);
  vm.runInContext(audioSource, sandbox, { filename: "web/audio.js" });

  return {
    audio: window.CatanAudio,
    buttons: { sfx: sfxButton, bgm: bgmButton, volume: volumeControl },
    contextCounter: counter,
    document,
    navigator,
    storage,
  };
}

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
}

test("audio defaults to SFX on and BGM off without creating a context", () => {
  const harness = createHarness();
  const state = harness.audio.getState();

  assert.equal(state.sfxEnabled, true);
  assert.equal(state.bgmEnabled, false);
  assert.equal(state.volume, 0.65);
  assert.equal(state.scene, "home");
  assert.equal(state.unlocked, false);
  assert.equal(harness.contextCounter.instances, 0);
  assert.equal(harness.buttons.sfx.getAttribute("aria-pressed"), "true");
  assert.equal(harness.buttons.bgm.getAttribute("aria-pressed"), "false");
});

test("untrusted or absent activation cannot create audio or play effects", async () => {
  const harness = createHarness();

  assert.equal(harness.audio.playDice(), false);
  assert.equal(harness.audio.playBuild("road"), false);
  assert.equal(harness.audio.playTrade(), false);
  assert.equal(harness.audio.playTradeInvite(), false);
  assert.equal(harness.audio.playVictory(), false);
  assert.equal(await harness.audio.unlock(), false);

  harness.document.dispatch("pointerdown", { isTrusted: false });
  await flushMicrotasks();
  assert.equal(harness.contextCounter.instances, 0);
  assert.equal(harness.audio.playDice(), false);
});

test("a trusted interaction unlocks the context and all effects can play", async () => {
  const harness = createHarness();

  harness.document.dispatch("pointerdown", { isTrusted: true });
  await flushMicrotasks();

  assert.equal(harness.contextCounter.instances, 1);
  assert.equal(harness.audio.getState().unlocked, true);
  assert.equal(harness.audio.playDice(), true);
  assert.equal(harness.audio.playBuild("city"), true);
  assert.equal(harness.audio.playTrade(), true);
  assert.equal(harness.audio.playTradeInvite(), true);
  assert.equal(harness.audio.playVictory(), true);
});

test("toggles and valid volume persist only validated primitive settings", () => {
  const harness = createHarness();

  assert.equal(harness.audio.toggleSfx(), false);
  assert.equal(harness.audio.toggleBgm(), true);
  assert.equal(harness.audio.setVolume(0.4), 0.4);
  assert.equal(harness.storage.getItem(STORAGE_KEYS.sfx), "false");
  assert.equal(harness.storage.getItem(STORAGE_KEYS.bgm), "true");
  assert.equal(harness.storage.getItem(STORAGE_KEYS.volume), "0.40");

  assert.equal(harness.audio.setSfxEnabled("true"), false);
  assert.equal(harness.audio.setBgmEnabled(0), true);
  assert.equal(harness.audio.setVolume(Number.NaN), 0.4);
  assert.equal(harness.audio.setVolume(2), 0.4);
  assert.equal(harness.storage.getItem(STORAGE_KEYS.volume), "0.40");
});

test("invalid or unavailable localStorage falls back without throwing", () => {
  const invalid = createHarness({
    initialStorage: {
      [STORAGE_KEYS.sfx]: "yes",
      [STORAGE_KEYS.bgm]: "1",
      [STORAGE_KEYS.volume]: "1.5",
    },
  });
  assert.equal(invalid.audio.isSfxEnabled(), true);
  assert.equal(invalid.audio.isBgmEnabled(), false);
  assert.equal(invalid.audio.getVolume(), 0.65);

  const unavailable = createHarness({ storageThrows: true });
  assert.equal(unavailable.audio.isSfxEnabled(), true);
  assert.equal(unavailable.audio.isBgmEnabled(), false);
  assert.equal(unavailable.audio.getVolume(), 0.65);
  assert.doesNotThrow(() => unavailable.audio.toggleSfx());
  assert.doesNotThrow(() => unavailable.audio.toggleBgm());
  assert.doesNotThrow(() => unavailable.audio.setVolume(0.25));
});

test("scene changes are validated, idempotent, and never unlock audio", async () => {
  const harness = createHarness({
    initialStorage: { [STORAGE_KEYS.bgm]: "true" },
  });

  assert.equal(harness.audio.setScene("lobby"), "lobby");
  assert.equal(harness.audio.setScene("unknown"), "lobby");
  assert.equal(harness.contextCounter.instances, 0);

  harness.document.dispatch("pointerdown", { isTrusted: true });
  await flushMicrotasks();
  assert.equal(harness.contextCounter.instances, 1);
  const beforeChange = harness.contextCounter.timeouts;
  assert.equal(harness.audio.setScene("game"), "game");
  assert.ok(harness.contextCounter.timeouts > beforeChange);
  const afterChange = harness.contextCounter.timeouts;
  assert.equal(harness.audio.setScene("game"), "game");
  assert.equal(harness.contextCounter.timeouts, afterChange);
  assert.equal(harness.audio.getState().scene, "game");
});

test("volume slider restores and persists the validated master volume", () => {
  const harness = createHarness({
    initialStorage: { [STORAGE_KEYS.volume]: "0.42" },
  });
  assert.equal(harness.buttons.volume.value, "42");
  assert.equal(
    harness.buttons.volume.getAttribute("aria-label"),
    "全体音量 42%",
  );

  harness.buttons.volume.value = "30";
  harness.buttons.volume.dispatch("input", {
    currentTarget: harness.buttons.volume,
  });
  assert.equal(harness.audio.getVolume(), 0.3);
  assert.equal(harness.storage.getItem(STORAGE_KEYS.volume), "0.30");
  assert.equal(
    harness.buttons.volume.getAttribute("aria-label"),
    "全体音量 30%",
  );
});
