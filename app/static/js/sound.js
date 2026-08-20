// The room's voice: tiny synthesized sounds for the cat's actions.
// No audio assets — every motif is a few oscillators and noise bursts
// through a quiet master gain, so the sound stays as handmade as the wool.
// Off by default (the room's heritage is silent); the header toggle opts in,
// and that tap doubles as the user gesture that unlocks the AudioContext.
//
// The cat speaks in three primitives: _mew (the vowel one), _mrrp (the
// trill), _purr (the engine). Everything else — kibble ticks, paw steps,
// the message chime — is the room, not the cat.

// fx/scene-event modes → motif keys. Action base steps arrive as "action:x"
// and are stripped in _sndForMode; this table covers the named fx modes
// (mood overrides, quirk reactions, remote scene_fx).
const MODE_SOUNDS = {
  greet: "greet",
  petting: "pet",
  petting_head: "pet",
  petting_ear: "pet",
  petting_tail: "pet",
  petting_belly: "melt",
  petting_melt: "melt",
  flinch_away: "grumble",
  ignored: "grumble",
  threshold_refusal: "grumble",
  side_eye: "grumble",
  kibble: "feed",
  leash_tug: "walk",
  call_ring: "call",
  message_ping: "message",
  zoomie: "zoomie",
  head_tilt: "curious",
  sigh_settle: "sigh",
  lean_in: "happy",
  carry: "carry",
  stash: "stash",
};

// Motifs take the component (s) and schedule their notes relative to now.
// Keep them short and quiet — a felt room, not an arcade.
const MOTIFS = {
  // mew, then a brighter mrrp — the two-syllable hello
  greet(s) { s._mew(0, 600, 0.42, 0.13); s._mrrp(0.2, 700, 0.3); },
  // warm rising trill
  happy(s) {
    s._mrrp(0, 540, 0.3, 0.13);
    s._mrrp(0.15, 760, 0.22, 0.11);
  },
  // soft boop + a pleased little trill
  pet(s) {
    s._tone({ f: 340, f1: 255, dur: 0.16, g: 0.3 });
    s._mrrp(0.12, 840, 0.14, 0.08);
  },
  // the engine switches on: purr under a long contented slide down
  melt(s) {
    s._purr(0, 0.8, 0.2);
    s._tone({ f: 380, f1: 170, dur: 0.7, g: 0.13 });
  },
  // kibble ticks, then nom-nom two-tone chews and a satisfied little trill
  feed(s) {
    [0, 0.14, 0.3].forEach((t) => s._burst({ t, dur: 0.03, f: 3600, g: 0.28 }));
    [0.55, 0.73, 0.91, 1.09].forEach((t, i) => {
      s._burst({ t, dur: 0.07, f: i % 2 ? 560 : 420, q: 0.9, g: 0.36, type: "lowpass" });
      s._burst({ t: t + 0.075, dur: 0.05, f: i % 2 ? 460 : 340, q: 0.9, g: 0.3, type: "lowpass" });
    });
    s._mrrp(1.34, 720, 0.12, 0.09);
    s._tone({ f: 520, f1: 380, dur: 0.22, g: 0.11, t: 1.44 });
  },
  // collar-bell jingle + four soft paw steps
  walk(s) {
    [0, 0.22].forEach((t) => {
      s._tone({ f: 5200, dur: 0.1, g: 0.1, t });
      s._tone({ f: 6600, dur: 0.09, g: 0.08, t: t + 0.03 });
    });
    [0.05, 0.25, 0.45, 0.65].forEach((t) => s._burst({
      t, dur: 0.05, f: 260, q: 0.8, g: 0.22, type: "lowpass",
    }));
  },
  // come-here whistle + one attentive mew
  call(s) {
    s._tone({ f: 880, f1: 1175, dur: 0.12, g: 0.26 });
    s._tone({ f: 1175, f1: 880, dur: 0.14, g: 0.24, t: 0.14 });
    s._mew(0.46, 640, 0.36, 0.15);
  },
  // a note landing: soft chime with a fifth
  message(s) {
    s._tone({ f: 660, dur: 0.5, g: 0.2 });
    s._tone({ f: 990, dur: 0.45, g: 0.1, t: 0.02 });
  },
  // trill + the ball's boing
  play(s) {
    s._mrrp(0, 700, 0.32, 0.12);
    s._tone({ f: 300, f1: 900, dur: 0.18, g: 0.22, t: 0.15 });
    s._tone({ f: 900, f1: 450, dur: 0.16, g: 0.18, t: 0.31 });
  },
  // four trills picking up speed
  zoomie(s) {
    [0, 0.15, 0.3, 0.45].forEach((t, i) => s._mrrp(t, 560 + i * 90, 0.3, 0.09));
  },
  // a low, unimpressed mrrr
  grumble(s) {
    s._mew(0, 300, 0.2, 0.22);
    s._tone({ f: 130, f1: 85, dur: 0.35, type: "sawtooth", g: 0.09 });
  },
  // a long exhale settling into the rug
  sigh(s) { s._tone({ f: 360, f1: 150, dur: 0.8, g: 0.15 }); },
  // hm? (one rising trill, head tilted)
  curious(s) { s._mrrp(0, 480, 0.24, 0.14); },
  // two careful steps with something in mouth
  carry(s) {
    s._burst({ t: 0, dur: 0.05, f: 300, q: 0.8, g: 0.18, type: "lowpass" });
    s._burst({ t: 0.18, dur: 0.05, f: 300, q: 0.8, g: 0.18, type: "lowpass" });
  },
  // something small going under the rug
  stash(s) {
    s._tone({ f: 95, dur: 0.14, g: 0.36 });
    s._burst({ t: 0.1, dur: 0.12, f: 1800, g: 0.09 });
  },
};

// Species voice dispatch: species id → its motif set, cat as the default.
// Anything not in a species' table falls through to the base MOTIFS — those
// are the ROOM's sounds, not any species' voice. The closures stay
// hand-authored per species for now; a parameterized synth DSL is Phase 1.
const SPECIES_VOICES = {
  cat: MOTIFS,
};

export const soundMethods = {
  initSound() {
    try { this.soundOn = localStorage.getItem("woolroom_sound") === "1"; } catch (_) { /* */ }
    this._sndCtx = null;
    this._sndMaster = null;
    this._sndNoise = null;
  },

  toggleSound() {
    this.soundOn = !this.soundOn;
    try { localStorage.setItem("woolroom_sound", this.soundOn ? "1" : "0"); } catch (_) { /* */ }
    if (this.soundOn) {
      // A small hello, so the tap that turns the voice on also hears it.
      this._snd("happy");
    }
  },

  _sndEnsure() {
    if (!this._sndCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      this._sndCtx = new AC();
      this._sndMaster = this._sndCtx.createGain();
      this._sndMaster.gain.value = 0.16;
      this._sndMaster.connect(this._sndCtx.destination);
    }
    if (this._sndCtx.state === "suspended") {
      this._sndCtx.resume().catch(() => {});
    }
    return this._sndCtx;
  },

  // Play a motif by key. No-ops when the room is muted or the browser still
  // has audio locked (first gesture hasn't happened yet this session).
  _snd(key) {
    if (!this.soundOn) return;
    const ctx = this._sndEnsure();
    if (!ctx || ctx.state !== "running") return;
    const voice = SPECIES_VOICES[this.pet?.species] || SPECIES_VOICES.cat;
    const motif = voice[key] || MOTIFS[key];
    if (motif) motif(this);
  },

  _sndForMode(mode) {
    if (!mode) return;
    const key = MODE_SOUNDS[mode]
      || (mode.startsWith("action:") ? mode.slice(7) : null);
    if (key) this._snd(key);
  },

  // ---- synth primitives ----

  _tone({ f, f1 = f, dur = 0.15, type = "sine", g = 0.4, t = 0 }) {
    const ctx = this._sndCtx;
    const t0 = ctx.currentTime + t;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(f, t0);
    if (f1 !== f) {
      osc.frequency.exponentialRampToValueAtTime(Math.max(1, f1), t0 + dur);
    }
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(g, t0 + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(gain).connect(this._sndMaster);
    osc.start(t0);
    osc.stop(t0 + dur + 0.05);
  },

  // Short filtered noise: kibble ticks, munching, footsteps, rustles.
  _burst({ dur = 0.08, f = 2500, q = 1.2, g = 0.25, t = 0, type = "bandpass" }) {
    const ctx = this._sndCtx;
    if (!this._sndNoise) {
      const len = Math.floor(ctx.sampleRate * 0.5);
      const buf = ctx.createBuffer(1, len, ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
      this._sndNoise = buf;
    }
    const t0 = ctx.currentTime + t;
    const src = ctx.createBufferSource();
    src.buffer = this._sndNoise;
    const filt = ctx.createBiquadFilter();
    filt.type = type;
    filt.frequency.value = f;
    filt.Q.value = q;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(g, t0 + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(filt).connect(gain).connect(this._sndMaster);
    src.start(t0, Math.random() * 0.3);
    src.stop(t0 + dur + 0.05);
  },

  // A small-cat "mew": a mid-high saw chirp through a swept bandpass. The
  // pitch does the "ee-oo" — a quick rise, then the fall that carries the
  // vowel — while the bandpass sweeps down with it. A triangle an octave
  // down gives it chest, and a breath of noise at the onset keeps it soft.
  _mew(t, f = 660, g = 0.5, dur = 0.12) {
    const ctx = this._sndCtx;
    const t0 = ctx.currentTime + t;
    const osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(f, t0);
    osc.frequency.exponentialRampToValueAtTime(f * 1.22, t0 + dur * 0.3);
    osc.frequency.exponentialRampToValueAtTime(f * 0.68, t0 + dur);
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.setValueAtTime(1500, t0);
    bp.frequency.exponentialRampToValueAtTime(850, t0 + dur);
    bp.Q.value = 1.2;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(g, t0 + 0.014);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(bp).connect(gain).connect(this._sndMaster);
    osc.start(t0);
    osc.stop(t0 + dur + 0.05);
    // low body so it reads as chest, not squeak
    this._tone({ f: f * 0.5, f1: f * 0.34, dur, type: "triangle", g: g * 0.4, t });
    // breath at the onset
    this._burst({ t, dur: 0.03, f: 1500, q: 0.7, g: g * 0.2 });
  },

  // A "mrrp" — the closed-mouth trill cats greet with. Shorter and brighter
  // than a mew: triangle through a bandpass, pitch hopping up then settling,
  // with a fast vibrato so it warbles instead of beeping.
  _mrrp(t, f = 520, g = 0.35, dur = 0.1) {
    const ctx = this._sndCtx;
    const t0 = ctx.currentTime + t;
    const osc = ctx.createOscillator();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(f, t0);
    osc.frequency.exponentialRampToValueAtTime(f * 1.4, t0 + dur * 0.45);
    osc.frequency.exponentialRampToValueAtTime(f * 1.1, t0 + dur);
    // the trill: a fast little LFO on the pitch
    const vib = ctx.createOscillator();
    vib.frequency.value = 38;
    const vibGain = ctx.createGain();
    vibGain.gain.value = f * 0.09;
    vib.connect(vibGain).connect(osc.frequency);
    const bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = f * 2.2;
    bp.Q.value = 1.4;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(g, t0 + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(bp).connect(gain).connect(this._sndMaster);
    osc.start(t0);
    vib.start(t0);
    osc.stop(t0 + dur + 0.05);
    vib.stop(t0 + dur + 0.05);
  },

  // The purr: a low rumble gated by a ~23 Hz LFO, so it chugs instead of
  // humming. Filtered noise underneath is the breath in it.
  _purr(t, dur = 0.6, g = 0.16) {
    const ctx = this._sndCtx;
    const t0 = ctx.currentTime + t;
    const osc = ctx.createOscillator();
    osc.type = "triangle";
    osc.frequency.value = 56;
    const lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 320;
    const trem = ctx.createGain();
    trem.gain.value = 0.5;
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 23;
    const lfoGain = ctx.createGain();
    lfoGain.gain.value = 0.5;
    lfo.connect(lfoGain).connect(trem.gain);
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(g, t0 + 0.06);
    gain.gain.setValueAtTime(g, t0 + dur - 0.12);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(lp).connect(trem).connect(gain).connect(this._sndMaster);
    osc.start(t0);
    lfo.start(t0);
    osc.stop(t0 + dur + 0.05);
    lfo.stop(t0 + dur + 0.05);
    this._burst({ t, dur, f: 420, q: 0.6, g: g * 0.35, type: "lowpass" });
  },
};
