// The wool room — SVG-DOM scene that replaced the canvas renderer.
// Design contract (visual redesign build note):
//   still room, lively dog · light-not-dye clock · andon lamps carry presence ·
//   every gesture has a tap floor · a performance may refuse input but must
//   acknowledge it · at rest only the breath moves.
//
// The figure-injection methods (petFigureSvg/visitorSvg/…) live in
// figures.js as their own method group — see the note there about why
// nothing here imports it (depth-1 module graph, on purpose).
// The former single 1500-line group is now four: this core (boot,
// clock, motion primitives, locomotion, touch, presentation reads),
// woolvisits.js (door + playdates), woolevents.js (scene-event lane),
// and woolfx.js (fx + verb performances). One component `this` still.

export const sceneMethods = {
  _petPronoun() {
    // Pronoun is per-pet identity, served with the payload; default he.
    return this.pet?.pronoun || "he";
  },

  startCanvas() {
    const motionPreference = window.matchMedia("(prefers-reduced-motion: reduce)");
    this._wool = {
      busy: false,
      gait: "waddle",
      px: 0,
      py: 0,
      petStreak: 0,
      lastTapAt: 0,
      nightN: 0,
      fxLog: {},
      flashTimers: {},
      eventQueue: [],
      eventDraining: false,
      pendingStashEventId: null,
      ballResetTimer: null,
      seenEventIds: new Set(),
      seenEventOrder: [],
      reduceMotion: motionPreference.matches,
    };
    motionPreference.addEventListener?.("change", (event) => {
      if (this._wool) this._wool.reduceMotion = event.matches;
    });
    this.$nextTick(() => void this._woolBoot());
  },

  async _woolBoot() {
    const scene = document.getElementById("wool-scene");
    if (!scene) return;
    this._woolApplyHour();
    this._woolHourTimer = setInterval(() => this._woolApplyHour(), 60000);
    this._woolBindDog(scene);
    if (this.pet && !this.guest) {
      await Promise.all([this._woolLoadShelf(), this._woolLoadNotes()]);
    }
    for (const event of this._pendingSceneEvents || []) {
      this._woolSceneEvent(event);
    }
    this._pendingSceneEvents = [];
    // An fx that landed before the rig existed (page load mid-moment) gets
    // its tail end performed once the room is up.
    if (this._pendingFx) {
      const fx = this._pendingFx;
      this._pendingFx = null;
      this._woolRemoteFx(fx);
    }
    this._woolIdleSchedule();
    // Booted mid-playdate: no entrance animation — he's simply already
    // here, settled at the rug's edge. The arrival beat is for live visits.
    if (this.pet?.visit?.role === "host" && this.pet.visit.visitor) {
      this._visitor = { x: -74, y: 8 };
      this.$nextTick(() => this._visitorSetPos(-74, 8));
    }
    this._woolMaybeRunReturn();
    // Booted straight into an empty room: he's next door. Say so once,
    // gently — an unexplained empty rug reads as a dead room.
    if (this.pet?.visit?.role === "away" && this.pet?.name) {
      setTimeout(() => {
        if (this.pet?.visit?.role === "away") {
          this._woolSay(
            `${this.pet.name} slipped through the door — visiting ${this.pet.visit.host_name || "next door"}.`,
            4200,
          );
        }
      }, 1200);
    }
    // desktop gift: eyes track a nearby cursor
    if (window.matchMedia("(hover: hover)").matches) {
      scene.addEventListener("pointermove", (e) => this._woolGaze(e));
      scene.addEventListener("pointerleave", () => this._woolGaze(null));
    }
  },

  // ---- the clock: material constant, light changes ----
  // Light and sleep are decoupled on purpose (2026-07-17): the room
  // goes dark at 21 (aesthetic), but the cat stays rousable until 23 —
  // evening is when couples actually visit.
  _woolApplyHour() {
    const scene = document.getElementById("wool-scene");
    if (!scene) return;
    const h = this.currentHour ?? (new Date().getHours() + new Date().getMinutes() / 60);
    const band = h >= 7 && h < 17 ? "day" : (h >= 17 && h < 21) || (h >= 5 && h < 7) ? "dusk" : "night";
    scene.dataset.time = band;
    scene.classList.toggle("dogsleep", h >= 23 || h < 5);
  },
  woolIsNight() {
    // "Night" for behavior = the cat is down for the night, not room light.
    const scene = document.getElementById("wool-scene");
    return scene ? scene.classList.contains("dogsleep") : false;
  },

  // ---- idle: sparse ambient aliveness, never a metronome ----
  // One self-rescheduling timer, jittered 4–9s. A beat fires only when the
  // room is calm: tab visible, no performance running, dog awake, and no
  // pose_detail posture to fight (a skipped beat is the polite no-op).
  _woolIdleSchedule() {
    clearTimeout(this._woolIdleTimer);
    this._woolIdleTimer = setTimeout(() => this._woolIdleBeat(), 4000 + Math.random() * 5000);
  },
  _woolIdleBeat() {
    const w = this._wool;
    if (!w) return;
    if (document.visibilityState !== "visible" || w.reduceMotion) {
      this._woolIdleSchedule();
      return;
    }
    const asleep = this.animState === "sleeping" || this.woolIsNight();
    const sideEyeing = this.poseDetail?.eye_style === "side_eye";
    const hungry = !!this.pet?.hungry && !this.guest;
    if (!w.busy && !asleep) {
      const roll = Math.random();
      if (hungry && roll < 0.3) {
        // hungry eyes keep finding the bowl
        this._woolFlash("bowlgaze", 1800);
      } else if (roll < 0.62 && !sideEyeing) {
        this._woolFlash("blink", 150);
        // sometimes a double-blink, the way dogs actually do it
        if (Math.random() < 0.22) {
          setTimeout(() => { if (!w.busy) this._woolFlash("blink", 150); }, 420);
        }
      } else if (roll < 0.85) {
        this._woolFlash("earflick", 500);
      } else if (!sideEyeing) {
        // a slow look-away, held for a breath, then back
        this._woolFlash("sideeye", 1700);
      }
    }
    this._woolIdleSchedule();
  },

  // ---- gait: mood picks the gait ----
  _woolGaitForMood() {
    if (this.animState === "sleeping") return "scoot";
    if (this.animState === "playful" || this.animState === "alert") return "hop";
    return "waddle";
  },

  // ---- helpers ----
  _woolSay(line, ms = 5000) {
    this.woolLine = line;
    clearTimeout(this._woolLineTimer);
    this._woolLineTimer = setTimeout(() => (this.woolLine = ""), ms);
  },
  _woolFlash(cls, ms) {
    const scene = document.getElementById("wool-scene");
    if (!scene) return;
    const timers = this._wool?.flashTimers;
    if (timers?.[cls]) clearTimeout(timers[cls]);
    scene.classList.remove(cls);
    void scene.offsetWidth;
    scene.classList.add(cls);
    const timer = setTimeout(() => {
      scene.classList.remove(cls);
      if (timers) delete timers[cls];
    }, this._woolMotionHold(ms));
    if (timers) timers[cls] = timer;
  },
  _woolBusyAck() {
    this._woolFlash("earflick", 500);
    this._woolSay("(mid-something — one moment)", 1600);
  },
  async _woolPerform(fn) {
    const w = this._wool;
    if (w.busy) return false;
    w.busy = true;
    try { await fn(); } finally {
      w.busy = false;
      // rooms fill slowly — but they do fill: refresh the shelf and rug
      if (this.pet && !this.guest) this._woolLoadShelf();
      setTimeout(() => this._woolMaybeRunReturn(), 0);
    }
    return true;
  },
  _woolWait(ms) {
    return new Promise((r) => setTimeout(r, this._woolMotionHold(ms)));
  },
  _woolMotionHold(ms) {
    return this._wool?.reduceMotion ? Math.min(ms, 160) : ms;
  },
  _woolFrame(dur, fn) {
    if (this._wool.reduceMotion) { fn(1); return Promise.resolve(); }
    return new Promise((res) => {
      const t0 = performance.now();
      const step = (now) => {
        if (this._wool.reduceMotion) {
          fn(1);
          res();
          return;
        }
        const t = Math.min(1, (now - t0) / dur);
        fn(t);
        if (t < 1) requestAnimationFrame(step); else res();
      };
      requestAnimationFrame(step);
    });
  },
  _woolSetPos(x, y) {
    this._wool.px = x; this._wool.py = y;
    const g = document.getElementById("dogmove");
    if (g) g.setAttribute("transform", `translate(${x} ${y})`);
  },
  _woolSetGait(sx, sy, rot) {
    const g = document.getElementById("dog-gait");
    if (g) g.setAttribute("transform",
      `translate(200 452) scale(${sx} ${sy}) rotate(${rot}) translate(-200 -452)`);
  },
  _woolEar(deg) {
    const g = document.querySelector("#wool-scene .earg");
    if (g) g.style.transform = `rotate(${deg}deg)`;
  },
  _woolDust(x, y) {
    if (this._wool?.reduceMotion) return;
    const d = document.getElementById(Math.random() < 0.5 ? "dust1" : "dust2");
    if (!d) return;
    d.setAttribute("cx", 200 + x - 20);
    d.setAttribute("cy", 452 + y);
    d.classList.remove("puff");
    void d.getBoundingClientRect();
    d.classList.add("puff");
  },

  // ---- locomotion: anticipation → arc → landing squash, ears lag ----
  async _woolCrouch(ms = 90) {
    await this._woolFrame(ms, (t) => this._woolSetGait(1 + 0.05 * t, 1 - 0.08 * t, 0));
  },
  async _woolHop(x0, y0, x1, y1, dur) {
    const dir = Math.sign(x1 - x0) || 1;
    const lift = this.pet?.stage_proportions?.pawLiftScale || 1;
    await this._woolFrame(dur, (t) => {
      const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const arc = 26 * lift * Math.sin(Math.PI * t);
      this._woolSetPos(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e - arc);
      const s = Math.sin(Math.PI * t);
      this._woolSetGait(1 - 0.09 * s, 1 + 0.14 * s, dir * 4 * s);
      this._woolEar(-dir * 6 * s);
    });
    this._woolSetPos(x1, y1);
    this._woolDust(x1, y1);
    await this._woolFrame(90, (t) => {
      const s = Math.sin(Math.PI * t);
      this._woolSetGait(1 + 0.1 * s, 1 - 0.12 * s, 0);
      this._woolEar(5 * (1 - t));
    });
    this._woolSetGait(1, 1, 0); this._woolEar(0);
  },
  async _woolWaddle(x0, y0, x1, y1, dur, i) {
    const dir = Math.sign(x1 - x0) || 1;
    const rock = i % 2 ? 1 : -1;
    await this._woolFrame(dur, (t) => {
      const e = t * t * (3 - 2 * t);
      this._woolSetPos(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e - 4 * Math.sin(Math.PI * t));
      this._woolSetGait(1, 1 + 0.02 * Math.sin(Math.PI * t), rock * 6 * Math.sin(Math.PI * t) + dir * 1.5);
      this._woolEar(-rock * 4 * Math.sin(Math.PI * t));
    });
    this._woolSetGait(1, 1, 0); this._woolEar(0);
  },
  async _woolScoot(x0, y0, x1, y1, dur) {
    const dir = Math.sign(x1 - x0) || 1;
    await this._woolFrame(dur * 0.55, (t) => {
      const e = t * t;
      this._woolSetPos(x0 + (x1 - x0) * 0.4 * e, y0 + (y1 - y0) * 0.4 * e);
      this._woolSetGait(1 + 0.22 * t, 1 - 0.14 * t, dir * 2 * t);
    });
    await this._woolFrame(dur * 0.45, (t) => {
      const e = 1 - Math.pow(1 - t, 2);
      this._woolSetPos(x0 + (x1 - x0) * (0.4 + 0.6 * e), y0 + (y1 - y0) * (0.4 + 0.6 * e));
      this._woolSetGait(1.22 - 0.28 * t, 0.86 + 0.16 * t, dir * 2 * (1 - t));
    });
    this._woolSetGait(1, 1, 0);
    this._woolDust(x1, y1);
  },
  async _woolTravel(tx, ty, fast) {
    const w = this._wool;
    const gait = this._woolGaitForMood();
    const steps = { hop: 46, waddle: 30, scoot: 60 }[gait];
    const durs = { hop: fast ? 170 : 250, waddle: fast ? 130 : 190, scoot: fast ? 240 : 340 }[gait];
    const n = Math.max(1, Math.round(Math.hypot(tx - w.px, ty - w.py) / steps));
    await this._woolCrouch();
    for (let i = 0; i < n; i++) {
      const x1 = w.px + (tx - w.px) / (n - i);
      const y1 = w.py + (ty - w.py) / (n - i);
      if (gait === "hop") await this._woolHop(w.px, w.py, x1, y1, durs);
      else if (gait === "waddle") await this._woolWaddle(w.px, w.py, x1, y1, durs, i);
      else await this._woolScoot(w.px, w.py, x1, y1, durs);
    }
  },
  async _woolShimmy() {
    for (let i = 0; i < 5; i++) {
      await this._woolFrame(120, (t) => this._woolSetGait(
        1 + 0.04 * Math.sin(Math.PI * t), 1 - 0.05 * Math.sin(Math.PI * t),
        (i % 2 ? 1 : -1) * 9 * Math.sin(Math.PI * t)));
    }
    this._woolSetGait(1, 1, 0);
  },
  async _woolMelt() {
    const scene = document.getElementById("wool-scene");
    scene?.classList.add("melted");
    await this._woolFrame(700, (t) => {
      const e = 1 - Math.pow(1 - t, 3);
      this._woolSetGait(1 + 0.2 * e, 1 - 0.24 * e, 0);
    });
    await this._woolWait(1200);
    await this._woolFrame(500, (t) => this._woolSetGait(1.2 - 0.2 * t, 0.76 + 0.24 * t, 0));
    this._woolSetGait(1, 1, 0);
    scene?.classList.remove("melted");
    this._woolFlash("shakeoff", 700);
  },

  // ---- the figure: stroke vs tap vs double-tap ----
  _woolBindDog(scene) {
    const zone = document.getElementById("dogzone");
    const svg = scene.querySelector("svg");
    if (!zone || !svg) return;
    let down = false, dist = 0, lx = 0, ly = 0, startX = 0, startY = 0;
    const pt = (e) => {
      const r = svg.getBoundingClientRect();
      const cx = e.touches ? e.touches[0].clientX : e.clientX;
      const cy = e.touches ? e.touches[0].clientY : e.clientY;
      return [(cx - r.left) * 400 / r.width, (cy - r.top) * 520 / r.height];
    };
    zone.addEventListener("pointerdown", (e) => {
      if (this.guest) { this._guestToast(); return; }
      down = true; dist = 0;
      [lx, ly] = pt(e); startX = lx; startY = ly;
      try { zone.setPointerCapture(e.pointerId); } catch (_) { /* */ }
      if (!this.woolIsNight() && !this._wool.busy) scene.classList.add("stroking");
    });
    zone.addEventListener("pointermove", (e) => {
      if (!down) return;
      const [x, y] = pt(e);
      dist += Math.hypot(x - lx, y - ly);
      [lx, ly] = [x, y];
    });
    const release = () => {
      if (!down) return;
      down = false;
      scene.classList.remove("stroking");
      this._woolResolveTouch(dist, startY, startX);
    };
    zone.addEventListener("pointerup", release);
    zone.addEventListener("pointercancel", release);
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); this._woolResolveTouch(0, 320); }
    });
  },
  _woolResolveTouch(dist, y, x = 200) {
    if (this.guest) { this._guestToast(); return; }
    const w = this._wool;
    if (w.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolNightRefusal(); return; }
    const now = Date.now();
    const isDouble = now - w.lastTapAt < 450 && dist < 24;
    w.lastTapAt = now;
    if (isDouble) { this.woolZoomies(); return; }
    const spot = this.figureTouchZone(this.pet?.species, x, y);
    if (dist > 24) w.brushedAt = Date.now();
    this._woolFlash("fiber-touch", 420);
    this.petAtSpot(spot);
  },

  // Night refusals rotate — one of them admits morning exists.
  _woolNightRefusal() {
    const w = this._wool;
    const lines = this.voice?.wool?.night_refusals || [];
    if (lines.length) this._woolSay(lines[w.nightN++ % lines.length], 3000);
    this._woolFlash("earflick", 600);
  },

  woolLumpTap() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    this._woolSay(this.voice?.wool?.lump_joke || "", 4500);
    this._woolFlash("sideeye", 1600);
  },
  woolLampTap(who) {
    if (who === "self") {
      if (this.woolHearts.length) {
        const hearts = this.woolHearts;
        const name = this.applyAlias(hearts[0].by_display_name) || "your other human";
        this._woolSay(
          hearts.length > 1
            ? this._voiceFmt(this.voice?.wool?.lamp_hearts_many, { name, count: hearts.length })
            : this._voiceFmt(this.voice?.wool?.lamp_hearts_one, { name }),
          6000,
        );
        this.woolHearts = [];
        hearts.forEach((h) => this._woolMarkSeen(h));
        this._woolFlash("earflick", 500);
        return;
      }
      this._woolSay(this.voice?.wool?.lamp_self_known || "", 3000);
      return;
    }
    const heart = document.getElementById("lamp-heart");
    if (heart) {
      heart.classList.remove("show");
      void heart.getBoundingClientRect();
      heart.classList.add("show");
      setTimeout(() => heart.classList.remove("show"), 3200);
    }
    this._woolFlash("alert", 800);
    this.sendQuickReaction("♥");
  },
  woolPatchTap(fragment, when) {
    this._woolSay(`“${fragment}” · ${when}`, 5000);
    if (!this._wool.busy && !this.woolIsNight()) this._woolFlash("earflick", 500);
  },
  woolSkyTap() {
    const t = document.getElementById("wool-scene")?.dataset.time;
    const line = t === "night" ? "the moon keeps the watch tonight."
      : t === "dusk" ? "the light is packing up for the day."
      : "the sun is doing its one quiet job.";
    this._woolSay(line, 3000);
  },

  // arrival theater — ws.js flips partnerArrivedFlash; ui watches it via this hook
  woolPartnerArrived() {
    if (this._wool?.busy || this.woolIsNight()) return;
    this._woolPerform(async () => {
      this._woolFlash("alert", 900);
      await this._woolWait(700);
      await this._woolTravel(105, 8, true);
      this._woolFlash("happy", 2600);
      await this._woolShimmy();
      await this._woolWait(1400);
      await this._woolTravel(0, 0, false);
    });
  },

  // ---- desktop gaze: pupils drift toward a nearby cursor ----
  _woolGaze(e) {
    const eyes = document.getElementById("dog-eyes");
    if (!eyes) return;
    if (!e) { eyes.setAttribute("transform", ""); return; }
    const svg = document.querySelector("#wool-scene svg");
    const r = svg.getBoundingClientRect();
    const x = (e.clientX - r.left) * 400 / r.width;
    const y = (e.clientY - r.top) * 520 / r.height;
    const dx = Math.max(-3, Math.min(3, (x - 200) / 40));
    const dy = Math.max(-2, Math.min(2, (y - 334) / 60));
    eyes.setAttribute("transform", `translate(${dx} ${dy})`);
  },

  // ---- unheard whispers: settle-notes + waiting hearts ----
  // The cat's "i kept it" is finally true: a whisper the partner never saw
  // waits in the room — ♥ presses onto your lamp, words fold into a paper
  // note on the floor — until it is tapped, then it is heard and released.
  async _woolLoadNotes() {
    try {
      const res = await fetch(`/api/unseen-notes${this._petQs()}`, { credentials: "same-origin" });
      if (!res.ok) return;
      const notes = (await res.json()).notes || [];
      this.woolHearts = notes.filter((n) => n.text.trim() === "♥");
      this.woolNotes = notes.filter((n) => n.text.trim() !== "♥").slice(0, 3);
    } catch (_) { /* the room keeps them for next time */ }
  },
  async _woolMarkSeen(note) {
    try {
      await fetch(`/api/unseen-notes/${note.event_id}/seen`, {
        method: "POST",
        credentials: "same-origin",
      });
    } catch (_) { /* unseen again next load — worst case it waits longer */ }
  },
  woolNoteTap(note) {
    const name = this.applyAlias(note.by_display_name) || "your other human";
    this._woolSay(`“${note.text}” — ${name}, left for you`, 6500);
    this.woolNotes = this.woolNotes.filter((n) => n.event_id !== note.event_id);
    this._woolMarkSeen(note);
    if (!this._wool.busy && !this.woolIsNight()) this._woolFlash("earflick", 500);
  },

  // ---- shelf + rug patches from real kept moments ----
  async _woolLoadShelf() {
    try {
      const res = await fetch(`/api/memory${this._petQs()}`, { credentials: "same-origin" });
      if (!res.ok) return;
      const mem = await res.json();
      this.memory = mem;
      this.woolShelf = (mem.moments || []).slice(0, 3).map((m) => ({
        fragment: m.fragment,
        when: (m.created_at || "").slice(0, 10) || "kept",
      }));
      this.woolPatches = (mem.moments || []).slice(3, 8).map((m) => ({
        fragment: m.fragment,
        when: (m.created_at || "").slice(0, 10) || "kept",
      }));
    } catch (_) { /* shelf stays bare; the room fills slowly */ }
  },

  // trace cue → in-scene evidence classes (tenet 5)
  woolTraceClasses() {
    // Cues arrive as dicts ({mode, created_at, ...}); collect their modes.
    // (The old Set-of-dicts version made partner cues silently never match.)
    const all = [...(this.partnerTraceCues || []), this.sharedTraceCue].filter(Boolean);
    const modes = new Set(all.map((c) => (typeof c === "string" ? c : c.mode)).filter(Boolean));
    const phoneAnchors = new Set(
      all.filter((cue) => cue?.mode === "phone_glow").map((cue) => cue.anchor),
    );
    return {
      "trace-kibble": modes.has("kibble") || modes.has("bowl"),
      "trace-rug": modes.has("warm_spot") || modes.has("rumpled_rug"),
      "trace-leash": modes.has("leash"),
      "trace-phone-shelf": phoneAnchors.has("shelf"),
      "trace-phone-floor": phoneAnchors.has("floor"),
      "trace-brushed": this._woolBrushedNow(all),
      "trace-stash": !!this.pet?.hidden_thing && !this._wool?.pendingStashEventId,
    };
  },
  woolTraceStyle() {
    const all = [...(this.partnerTraceCues || []), this.sharedTraceCue].filter(Boolean);
    const values = { strong: .96, soft: .62, faint: .3 };
    const opacity = (mode, anchor = null) => Math.max(
      0,
      ...all
        .filter((cue) => (
          cue?.mode === mode && (anchor === null || cue?.anchor === anchor)
        ))
        .map((cue) => values[cue.intensity] || .3),
    );
    return [
      `--trace-kibble-opacity: ${Math.max(opacity("kibble"), opacity("bowl"))}`,
      `--trace-rug-opacity: ${Math.max(opacity("warm_spot"), opacity("rumpled_rug"))}`,
      `--trace-leash-opacity: ${opacity("leash")}`,
      `--trace-phone-shelf-opacity: ${opacity("phone_glow", "shelf")}`,
      `--trace-phone-floor-opacity: ${opacity("phone_glow", "floor")}`,
    ].join("; ");
  },
  // The brushed nap holds a stroke for about an hour — yours immediately,
  // your partner's whenever their cue is younger than that.
  _woolBrushedNow(cues) {
    if (this._wool?.brushedAt && Date.now() - this._wool.brushedAt < 3600e3) return true;
    return (cues || []).some((c) => {
      if (!c || c.mode !== "brushed_coat" || !c.created_at) return false;
      const iso = c.created_at.endsWith("Z") ? c.created_at : c.created_at + "Z";
      const age = Date.now() - Date.parse(iso);
      return Number.isFinite(age) && age >= 0 && age < 3600e3;
    });
  },

  // lamp state ladder: ember → afterglow → lit (+ arrival handled as flash)
  woolPartnerLampState() {
    if (this.partnerOnline && this.partnerOnline()) return "lit";
    const m = this.partnerAbsenceMinutes;
    if (typeof m === "number" && m >= 0 && m < 60) return "afterglow";
    return "ember";
  },

  // ---- the rig: server-sent age + pose, applied as quiet transforms ----
  // render_scale wraps the figure inside #dog-gait so it composes with every
  // locomotion transform (hop/waddle/scoot write to the parents).
  dogScaleTransform() {
    const s = this.pet?.render_scale;
    if (!s || s === 1) return "";
    return `translate(200 452) scale(${s}) translate(-200 -452)`;
  },
  // Pose + age read through CSS custom properties (set on #wool-scene, read
  // by the rig in style.css) so JS-written transforms never fight them.
  dogRigStyle() {
    const pose = this.poseDetail || {};
    const props = this.pet?.stage_proportions || {};
    const lean = (pose.body_lean || 0) * 0.8;
    const headY = (pose.head_shift_y || 0) + (props.headOffsetY || 0);
    const energy = Math.max(0, Math.min(1, (this.pet?.mood_arousal ?? 50) / 100));
    return [
      `--dog-lean: ${lean}deg`,
      `--dog-head-y: ${headY}px`,
      `--dog-ear-y: ${props.earOffsetY || 0}px`,
      `--dog-head-scale: ${props.headScale || 1}`,
      `--dog-body-x: ${props.bodyScaleX || 1}`,
      `--dog-body-y: ${props.bodyScaleY || 1}`,
      `--dog-chest-scale: ${props.chestScale || 1}`,
      `--dog-tail-scale: ${props.tailScale || 1}`,
      `--dog-breath-period: ${(4.6 * (props.breathPeriodScale || 1)).toFixed(2)}s`,
      `--dog-tail-period: ${(11 - energy * 4).toFixed(2)}s`,
      `--dog-energy: ${energy.toFixed(2)}`,
    ].join("; ");
  },
  woolPoseClasses() {
    const pose = this.poseDetail || {};
    return {
      "pose-sideeye": pose.eye_style === "side_eye",
      "pose-stilltail": pose.tail_motion === "still",
      "pose-wagfast": pose.tail_motion === "fast",
      "pose-earsback": pose.ear_angle === "back",
      "pose-oneeye": pose.eye_style === "one_eye",
      "pose-focusmote": pose.focus_target === "mote",
    };
  },
};
