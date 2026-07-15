(() => {
  "use strict";

  const STORAGE_KEYS = Object.freeze({
    sfx: "catan.audio.sfx",
    bgm: "catan.audio.bgm",
    volume: "catan.audio.volume",
  });
  const DEFAULT_VOLUME = 0.65;
  const MIN_GAIN = 0.0001;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;

  let sfxEnabled = readStoredBoolean(STORAGE_KEYS.sfx, true);
  let bgmEnabled = readStoredBoolean(STORAGE_KEYS.bgm, false);
  let volume = readStoredVolume(STORAGE_KEYS.volume, DEFAULT_VOLUME);
  let interactionObserved = false;
  let audioContext = null;
  let masterGain = null;
  let sfxGain = null;
  let bgmGain = null;
  let bgmActive = false;
  let bgmTimer = null;
  let bgmMeasure = 0;
  const bgmNodes = new Set();

  // This quiet four-measure theme is synthesized locally and was composed
  // specifically for this project. It does not load or reproduce media files.
  const BGM_MEASURES = Object.freeze([
    Object.freeze({
      chord: Object.freeze([146.83, 220.0, 277.18]),
      melody: Object.freeze([440.0, 493.88, 554.37, 493.88]),
    }),
    Object.freeze({
      chord: Object.freeze([164.81, 246.94, 329.63]),
      melody: Object.freeze([415.3, 493.88, 659.25, 554.37]),
    }),
    Object.freeze({
      chord: Object.freeze([130.81, 196.0, 246.94]),
      melody: Object.freeze([392.0, 440.0, 493.88, 440.0]),
    }),
    Object.freeze({
      chord: Object.freeze([146.83, 220.0, 293.66]),
      melody: Object.freeze([369.99, 440.0, 554.37, 493.88]),
    }),
  ]);

  function readStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {
      // Private browsing and hardened browsers may reject localStorage.
    }
  }

  function readStoredBoolean(key, fallback) {
    const stored = readStorage(key);
    if (stored === "true") {
      return true;
    }
    if (stored === "false") {
      return false;
    }
    return fallback;
  }

  function readStoredVolume(key, fallback) {
    const stored = readStorage(key);
    if (stored === null || stored.trim() === "") {
      return fallback;
    }
    const candidate = Number(stored);
    if (!Number.isFinite(candidate) || candidate < 0 || candidate > 1) {
      return fallback;
    }
    return candidate;
  }

  function hasBrowserActivation() {
    const activation = navigator.userActivation;
    return Boolean(
      activation && (activation.isActive || activation.hasBeenActive),
    );
  }

  function createAudioGraph() {
    if (audioContext || !AudioContextClass || !interactionObserved) {
      return audioContext;
    }
    try {
      audioContext = new AudioContextClass();
      masterGain = audioContext.createGain();
      sfxGain = audioContext.createGain();
      bgmGain = audioContext.createGain();
      masterGain.gain.value = volume;
      sfxGain.gain.value = 0.7;
      bgmGain.gain.value = 0.14;
      sfxGain.connect(masterGain);
      bgmGain.connect(masterGain);
      masterGain.connect(audioContext.destination);
      return audioContext;
    } catch (_error) {
      audioContext = null;
      masterGain = null;
      sfxGain = null;
      bgmGain = null;
      return null;
    }
  }

  function unlock() {
    if (!interactionObserved) {
      if (!hasBrowserActivation()) {
        return Promise.resolve(false);
      }
      interactionObserved = true;
      removeInteractionListeners();
    }
    const context = createAudioGraph();
    if (!context) {
      return Promise.resolve(false);
    }
    const pending =
      context.state === "suspended" ? context.resume() : Promise.resolve();
    return Promise.resolve(pending)
      .then(() => {
        const running = context.state === "running";
        if (running && bgmEnabled) {
          startBgm();
        }
        return running;
      })
      .catch(() => false);
  }

  function canPlaySfx() {
    return Boolean(
      sfxEnabled &&
        interactionObserved &&
        audioContext &&
        audioContext.state === "running" &&
        sfxGain,
    );
  }

  function scheduleTone(
    frequency,
    start,
    duration,
    level,
    type,
    destination,
    { attack = 0.012, release = 0.08, trackBgm = false } = {},
  ) {
    if (!audioContext || !destination) {
      return null;
    }
    const oscillator = audioContext.createOscillator();
    const envelope = audioContext.createGain();
    const end = start + duration;
    const attackEnd = start + Math.min(attack, duration / 3);
    const releaseStart = Math.max(attackEnd, end - Math.min(release, duration / 2));
    oscillator.type = type;
    oscillator.frequency.setValueAtTime(frequency, start);
    envelope.gain.setValueAtTime(MIN_GAIN, start);
    envelope.gain.linearRampToValueAtTime(Math.max(level, MIN_GAIN), attackEnd);
    envelope.gain.setValueAtTime(Math.max(level, MIN_GAIN), releaseStart);
    envelope.gain.exponentialRampToValueAtTime(MIN_GAIN, end);
    oscillator.connect(envelope);
    envelope.connect(destination);
    if (trackBgm) {
      bgmNodes.add(oscillator);
      oscillator.addEventListener("ended", () => bgmNodes.delete(oscillator), {
        once: true,
      });
    }
    oscillator.start(start);
    oscillator.stop(end + 0.02);
    return oscillator;
  }

  function scheduleNoise(start, duration, level, centerFrequency) {
    if (!audioContext || !sfxGain) {
      return;
    }
    const frameCount = Math.max(
      1,
      Math.floor(audioContext.sampleRate * duration),
    );
    const buffer = audioContext.createBuffer(
      1,
      frameCount,
      audioContext.sampleRate,
    );
    const samples = buffer.getChannelData(0);
    for (let index = 0; index < samples.length; index += 1) {
      const fade = 1 - index / samples.length;
      samples[index] = (Math.random() * 2 - 1) * fade;
    }
    const source = audioContext.createBufferSource();
    const filter = audioContext.createBiquadFilter();
    const envelope = audioContext.createGain();
    source.buffer = buffer;
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(centerFrequency, start);
    filter.Q.setValueAtTime(0.8, start);
    envelope.gain.setValueAtTime(Math.max(level, MIN_GAIN), start);
    envelope.gain.exponentialRampToValueAtTime(MIN_GAIN, start + duration);
    source.connect(filter);
    filter.connect(envelope);
    envelope.connect(sfxGain);
    source.start(start);
    source.stop(start + duration + 0.01);
  }

  function playDice() {
    if (!canPlaySfx()) {
      return false;
    }
    const start = audioContext.currentTime + 0.01;
    [0, 0.075, 0.15].forEach((offset, index) => {
      scheduleNoise(start + offset, 0.09, 0.22, 620 + index * 170);
      scheduleTone(
        150 + index * 28,
        start + offset,
        0.08,
        0.16,
        "triangle",
        sfxGain,
      );
    });
    return true;
  }

  function playBuild(type = "settlement") {
    if (!canPlaySfx()) {
      return false;
    }
    const buildType = String(type).toLowerCase();
    const profiles = {
      road: [146.83, 196.0],
      settlement: [196.0, 246.94],
      city: [130.81, 196.0, 261.63],
      development: [220.0, 329.63],
    };
    const notes = profiles[buildType] || profiles.settlement;
    const start = audioContext.currentTime + 0.01;
    notes.forEach((frequency, index) => {
      scheduleTone(
        frequency,
        start + index * 0.055,
        0.17,
        0.2,
        index === 0 ? "triangle" : "sine",
        sfxGain,
        { release: 0.12 },
      );
    });
    return true;
  }

  function playTrade() {
    if (!canPlaySfx()) {
      return false;
    }
    const start = audioContext.currentTime + 0.01;
    [392.0, 493.88, 659.25].forEach((frequency, index) => {
      scheduleTone(
        frequency,
        start + index * 0.09,
        0.22,
        0.15,
        "sine",
        sfxGain,
        { release: 0.14 },
      );
    });
    return true;
  }

  function playVictory() {
    if (!canPlaySfx()) {
      return false;
    }
    const start = audioContext.currentTime + 0.02;
    [261.63, 329.63, 392.0, 523.25].forEach((frequency, index) => {
      scheduleTone(
        frequency,
        start + index * 0.16,
        index === 3 ? 0.85 : 0.34,
        0.2,
        "triangle",
        sfxGain,
        { release: index === 3 ? 0.55 : 0.18 },
      );
    });
    [261.63, 329.63, 392.0].forEach((frequency) => {
      scheduleTone(
        frequency,
        start + 0.5,
        0.9,
        0.1,
        "sine",
        sfxGain,
        { attack: 0.04, release: 0.6 },
      );
    });
    return true;
  }

  function scheduleBgmMeasure() {
    if (
      !bgmActive ||
      !bgmEnabled ||
      !audioContext ||
      audioContext.state !== "running" ||
      !bgmGain
    ) {
      bgmActive = false;
      bgmTimer = null;
      return;
    }
    const measure = BGM_MEASURES[bgmMeasure % BGM_MEASURES.length];
    const start = audioContext.currentTime + 0.05;
    measure.chord.forEach((frequency) => {
      scheduleTone(frequency, start, 4.35, 0.1, "sine", bgmGain, {
        attack: 0.65,
        release: 1.15,
        trackBgm: true,
      });
    });
    measure.melody.forEach((frequency, index) => {
      scheduleTone(
        frequency,
        start + 0.42 + index * 0.88,
        0.72,
        0.065,
        "triangle",
        bgmGain,
        { attack: 0.12, release: 0.3, trackBgm: true },
      );
    });
    bgmMeasure = (bgmMeasure + 1) % BGM_MEASURES.length;
    bgmTimer = window.setTimeout(scheduleBgmMeasure, 4300);
  }

  function startBgm() {
    if (
      bgmActive ||
      !bgmEnabled ||
      !interactionObserved ||
      !audioContext ||
      audioContext.state !== "running"
    ) {
      return bgmActive;
    }
    bgmActive = true;
    scheduleBgmMeasure();
    return true;
  }

  function stopBgm() {
    bgmActive = false;
    if (bgmTimer !== null) {
      window.clearTimeout(bgmTimer);
      bgmTimer = null;
    }
    bgmNodes.forEach((node) => {
      try {
        node.stop();
      } catch (_error) {
        // Nodes that already ended cannot be stopped twice.
      }
    });
    bgmNodes.clear();
    bgmMeasure = 0;
  }

  function setSfxEnabled(enabled) {
    if (typeof enabled !== "boolean") {
      return sfxEnabled;
    }
    sfxEnabled = enabled;
    writeStorage(STORAGE_KEYS.sfx, String(enabled));
    notifySettingsChanged();
    return sfxEnabled;
  }

  function toggleSfx() {
    return setSfxEnabled(!sfxEnabled);
  }

  function setBgmEnabled(enabled) {
    if (typeof enabled !== "boolean") {
      return bgmEnabled;
    }
    bgmEnabled = enabled;
    writeStorage(STORAGE_KEYS.bgm, String(enabled));
    if (enabled) {
      void unlock().then((running) => {
        if (running && bgmEnabled) {
          startBgm();
        }
      });
    } else {
      stopBgm();
    }
    notifySettingsChanged();
    return bgmEnabled;
  }

  function toggleBgm() {
    return setBgmEnabled(!bgmEnabled);
  }

  function setVolume(nextVolume) {
    if (
      typeof nextVolume !== "number" ||
      !Number.isFinite(nextVolume) ||
      nextVolume < 0 ||
      nextVolume > 1
    ) {
      return volume;
    }
    volume = nextVolume;
    writeStorage(STORAGE_KEYS.volume, nextVolume.toFixed(2));
    if (masterGain && audioContext) {
      masterGain.gain.setTargetAtTime(volume, audioContext.currentTime, 0.02);
    }
    notifySettingsChanged();
    return volume;
  }

  function getState() {
    return Object.freeze({
      available: Boolean(AudioContextClass),
      unlocked: Boolean(
        interactionObserved &&
          audioContext &&
          audioContext.state === "running",
      ),
      sfxEnabled,
      bgmEnabled,
      volume,
    });
  }

  function renderToggle(button, label, enabled) {
    if (!button) {
      return;
    }
    const status = enabled ? "オン" : "オフ";
    button.setAttribute("aria-pressed", String(enabled));
    button.setAttribute("aria-label", `${label}: ${status}`);
    button.dataset.state = enabled ? "on" : "off";
    const statusNode = button.querySelector("[data-audio-state]");
    if (statusNode) {
      statusNode.textContent = enabled ? "ON" : "OFF";
    }
  }

  function updateControls() {
    renderToggle(
      document.getElementById("audio-sfx-toggle"),
      "効果音",
      sfxEnabled,
    );
    renderToggle(
      document.getElementById("audio-bgm-toggle"),
      "BGM",
      bgmEnabled,
    );
  }

  function notifySettingsChanged() {
    updateControls();
    try {
      window.dispatchEvent(
        new CustomEvent("catan-audio-change", { detail: getState() }),
      );
    } catch (_error) {
      // Settings remain usable if CustomEvent is restricted by the host.
    }
  }

  function observeInteraction(event) {
    if (event && !event.isTrusted) {
      return;
    }
    interactionObserved = true;
    removeInteractionListeners();
    void unlock();
  }

  function removeInteractionListeners() {
    document.removeEventListener("pointerdown", observeInteraction, true);
    document.removeEventListener("keydown", observeInteraction, true);
  }

  function bindControls() {
    updateControls();
    const sfxButton = document.getElementById("audio-sfx-toggle");
    const bgmButton = document.getElementById("audio-bgm-toggle");
    if (sfxButton) {
      sfxButton.addEventListener("click", (event) => {
        observeInteraction(event);
        toggleSfx();
      });
    }
    if (bgmButton) {
      bgmButton.addEventListener("click", (event) => {
        observeInteraction(event);
        toggleBgm();
      });
    }
  }

  document.addEventListener("pointerdown", observeInteraction, true);
  document.addEventListener("keydown", observeInteraction, true);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopBgm();
    } else if (bgmEnabled && interactionObserved) {
      void unlock();
    }
  });
  window.addEventListener("pagehide", stopBgm);

  window.CatanAudio = Object.freeze({
    unlock,
    resume: unlock,
    playDice,
    playBuild,
    playTrade,
    playVictory,
    setSfxEnabled,
    toggleSfx,
    isSfxEnabled: () => sfxEnabled,
    setBgmEnabled,
    toggleBgm,
    isBgmEnabled: () => bgmEnabled,
    setVolume,
    getVolume: () => volume,
    getState,
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindControls, { once: true });
  } else {
    bindControls();
  }
})();
