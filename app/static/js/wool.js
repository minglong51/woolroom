// The wool room — SVG-DOM scene that replaced the canvas renderer.
// Design contract (visual redesign build note):
//   still room, lively dog · light-not-dye clock · andon lamps carry presence ·
//   every gesture has a tap floor · a performance may refuse input but must
//   acknowledge it · at rest only the breath moves.
//
// The figure-injection methods (petFigureSvg/visitorSvg/…) live in
// figures.js as their own method group — see the note there about why
// nothing here imports it (depth-1 module graph, on purpose).

// Fx modes each local action can make the server broadcast (default fx +
// mood overrides + quirk fx). Used to swallow the actor's own echo without
// blocking the partner's mirror of the same moment.
const ACTION_FX_MODES = {
  greet: ["greet", "sigh_settle"],
  pet: ["petting", "petting_melt", "flinch_away", "carry",
    "petting_head", "petting_ear", "petting_tail", "petting_belly"],
  feed: ["kibble"],
  walk: ["leash_tug", "stash"],
  call: ["call_ring", "carry"],
  message: ["message_ping", "head_tilt", "carry"],
  play: ["zoomie"],
};

export const sceneMethods = {
  // ── the door next door ──
  doorState() {
    return this.pet?.sibling?.animation_state === "sleeping" ? "asleep" : "awake";
  },
  doorUndiscovered() {
    if (this.guest || !this.pet?.sibling) return false;
    try { return localStorage.getItem("woolroom_door_known") !== "1"; } catch (_) { return false; }
  },
  woolDoorTap() {
    // Tap = you cross. Double-tap = this room's little one comes along.
    // The single crossing waits out the double-tap window — a deliberate
    // 280ms so the two intentions never fire as each other.
    if (this.guest || !this.pet?.sibling) return;
    if (this._doorTapTimer) {
      clearTimeout(this._doorTapTimer);
      this._doorTapTimer = null;
      this._woolFlash("lamp-touch", 300);
      this.startVisitAndFollow();
      return;
    }
    this._doorTapTimer = setTimeout(() => {
      this._doorTapTimer = null;
      this._woolCrossTheDoor();
    }, 280);
  },
  _woolCrossTheDoor() {
    const sibling = this.pet?.sibling;
    if (!sibling) return;
    this._woolFlash("return-wake", 700);
    this.switchRoom(sibling.id);
  },
  _woolRoomSwitched() {
    // The figure art reacts to pet.species on its own; what needs resetting
    // here is the motion rig + any guest still mid-visit in the old room.
    if (this._wool) {
      this._wool.px = 0;
      this._wool.py = 0;
      this._wool.petStreak = 0;
      this._woolSetPos(0, 0);
      this._woolSetGait(1, 1, 0);
      this._woolEar(0);
    }
    this.visitorLinger = false;
    this._visitorArt = null;
    this._visitor = null;
    if (this.pet?.name) this._woolSay(`${this.pet.name}'s room.`, 2400);
  },

  // ── playdate choreography (host room side) ──
  _woolVisitTransition(prev, next) {
    if (!this._wool) return;
    if (prev?.id === next?.id) return;
    if (next?.role === "host" && next.visitor) {
      this._visitorArt = this.visitorArtFor(next.visitor.species, next.visitor.coat);
      this._woolVisitArrived(next);
    } else if (prev?.role === "host" && !next) {
      this._woolVisitLeft(prev);
    } else if (prev?.role === "away" && !next) {
      // He's home again — the figure fades back in on its own (the away
      // class lifts with the payload), a soft line marks the return.
      this._woolSay(`${this.pet?.name || "the little one"} trotted back home.`, 3200);
    } else if (!prev && next?.role === "away") {
      this._woolSay(
        `${this.pet?.name || "he"} slipped through the door — visiting ${next.host_name || "next door"}.`,
        4200,
      );
    }
  },
  _visitorSetPos(x, y) {
    const g = document.getElementById("visitormove");
    if (g) g.setAttribute("transform", `translate(${x} ${y})`);
  },
  async _visitorTravel(tx, ty, dur = 800) {
    const from = this._visitor || { x: -137, y: 0 };
    await this._woolFrame(dur, (t) => {
      const e = t * t * (3 - 2 * t);
      const bob = Math.abs(Math.sin(t * Math.PI * 3)) * -5;
      this._visitorSetPos(from.x + (tx - from.x) * e, from.y + (ty - from.y) * e + bob);
    });
    this._visitor = { x: tx, y: ty };
    this._visitorSetPos(tx, ty);
  },
  async _woolVisitArrived(visit) {
    this._visitor = { x: -137, y: 0 };
    this.$nextTick(() => this._visitorSetPos(-137, 0));
    this._woolFlash("return-wake", 1100);
    await this._woolWait(350);
    // A resident mid-performance doesn't get interrupted; the guest just
    // settles in and the room notices on the next beat.
    if (this._wool?.busy) return;
    await this._woolPerform(async () => {
      await this._visitorTravel(-88, 2, 700);
      this._woolFlash("alert", 700);
      await this._woolWait(450);
      await this._visitorTravel(-74, 8, 480);
      this._woolFlash("happy", 1600);
      await this._woolWait(900);
    });
    if (visit.visitor?.name) this._woolSay(`${visit.visitor.name} is visiting.`, 3200);
  },
  async _woolVisitLeft(prev) {
    if (!this._visitorArt) return;
    this.visitorLinger = true;
    const name = prev?.visitor?.name || "the visitor";
    let guard = 0;
    while (this._wool?.busy && guard++ < 40) {
      await new Promise((r) => setTimeout(r, 150));
    }
    if (this._wool && !this._wool.reduceMotion) {
      await this._woolPerform(async () => {
        await this._visitorTravel(-137, 0, 780);
      });
    }
    this.visitorLinger = false;
    this._visitorArt = null;
    this._visitor = null;
    this._woolSay(`${name} trotted back home.`, 3000);
  },
  _woolAwayGate() {
    // This room's pet is next door — actions wait for his return.
    if (this.pet?.visit?.role !== "away") return false;
    this._woolSay(
      `${this.pet?.name || "he"} slipped through the door — visiting ${this.pet.visit.host_name || "next door"}.`,
      3200,
    );
    this._woolFlash("earflick", 500);
    return true;
  },
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

  _woolHasSeenSceneEvent(eventId) {
    return !!eventId && !!this._wool?.seenEventIds.has(eventId);
  },
  _woolSceneEvent(event) {
    if (!event?.id) return;
    const queuedEvent = Number.isFinite(event._woolQueuedAt)
      ? event
      : {
          ...event,
          _woolQueuedAt: performance.now(),
        };
    const w = this._wool;
    if (!w) {
      this._pendingSceneEvents = this._pendingSceneEvents || [];
      if (!this._pendingSceneEvents.some((pending) => pending.id === event.id)) {
        this._pendingSceneEvents.push(queuedEvent);
      }
      return;
    }
    if (w.seenEventIds.has(event.id)) return;
    w.seenEventIds.add(event.id);
    w.seenEventOrder.push(event.id);
    while (w.seenEventOrder.length > 64) {
      w.seenEventIds.delete(w.seenEventOrder.shift());
    }
    w.eventQueue.push(queuedEvent);
    if ((event.plan || []).some((step) => (
      step?.mode === "stash" && step?.relation === "after"
    ))) {
      w.pendingStashEventId = event.id;
    }
    this._woolDrainSceneEvents();
  },
  _woolReturnStorageKey() {
    if (!this.pet?.id || !this.user?.id) return null;
    return `woolroom_return_seen:${this.pet.id}:${this.user.id}`;
  },
  _woolSeenReturnCues() {
    const key = this._woolReturnStorageKey();
    if (!key) return [];
    try {
      const value = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(value) ? value.filter((id) => typeof id === "string") : [];
    } catch (_) {
      return [];
    }
  },
  _woolRememberReturnCue(id) {
    const key = this._woolReturnStorageKey();
    if (!key || !id) return;
    const seen = this._woolSeenReturnCues();
    if (!seen.includes(id)) seen.push(id);
    try {
      localStorage.setItem(key, JSON.stringify(seen.slice(-32)));
    } catch (_) {}
  },
  _woolMaybeRunReturn(attempt = 0) {
    if (!this._wool || !this.pet || !this.user || this.guest) return;
    const seen = new Set(this._woolSeenReturnCues());
    const note = [...(this.woolNotes || []), ...(this.woolHearts || [])]
      .find((candidate) => (
        candidate?.event_id
        && !seen.has(`trace:${candidate.event_id}`)
      ));
    const messageCue = note
      ? (this.partnerTraceCues || []).find((cue) => (
          cue?.event_type === "message" && String(cue.id) === `trace:${note.event_id}`
        )) || {
          id: `trace:${note.event_id}`,
          event_type: "message",
          mode: "phone_glow",
          anchor: "floor",
          intensity: "soft",
        }
      : null;
    const cue = [messageCue, this.returnCue]
      .find((candidate) => candidate?.id && !seen.has(candidate.id));
    if (!cue?.id || cue.intensity === "faint") return;
    if (this._wool.busy || this._wool.eventDraining || this._wool.eventQueue.length) {
      if (attempt < 20) {
        setTimeout(() => this._woolMaybeRunReturn(attempt + 1), 250);
      }
      return;
    }
    this._woolRememberReturnCue(cue.id);
    void this._woolRunReturnCue(cue);
  },
  async _woolRunReturnCue(cue) {
    this._woolFlash("return-wake", 1800);
    if (this.woolIsNight()) {
      this._woolFlash("earflick", 700);
      return;
    }
    this._snd(cue.event_type);
    if (cue.event_type === "message") {
      await this._woolPerfMessage("self");
      return;
    }
    await this._woolPerform(async () => {
      switch (cue.event_type) {
        case "feed":
          await this._woolTravel(-74, 8, false);
          this._woolFlash("eating", 1400);
          await this._woolWait(700);
          await this._woolTravel(0, 0, false);
          break;
        case "pet":
          this._woolFlash("squished", 1000);
          this._woolFlash("happy", 1500);
          await this._woolWait(1100);
          break;
        case "walk":
          this._woolFlash("leashswing", 1200);
          await this._woolTravel(76, -2, false);
          this._woolFlash("headtilt", 1400);
          await this._woolWait(600);
          await this._woolTravel(0, 0, false);
          break;
        case "greet":
          this._woolFlash("happy", 1500);
          await this._woolFrame(380, (t) => this._woolSetGait(1, 1, 5 * t));
          await this._woolWait(700);
          await this._woolFrame(320, (t) => this._woolSetGait(1, 1, 5 * (1 - t)));
          this._woolSetGait(1, 1, 0);
          break;
        case "call":
          this._woolFlash("alert", 1000);
          this._woolFlash("headtilt", 1600);
          await this._woolWait(1200);
          break;
        case "play":
          this._woolFlash("alert", 700);
          this._woolFlash("happy", 1400);
          await this._woolWait(1000);
          break;
      }
    });
  },
  async _woolDrainSceneEvents() {
    const w = this._wool;
    if (!w || w.eventDraining) return;
    if (w.busy) {
      setTimeout(() => this._woolDrainSceneEvents(), 80);
      return;
    }
    w.eventDraining = true;
    try {
      while (w.eventQueue.length) {
        while (w.busy) {
          await new Promise((resolve) => setTimeout(resolve, 50));
        }
        const event = w.eventQueue.shift();
        const initialRemaining = Number.isFinite(event.remaining_ms)
          ? event.remaining_ms
          : Number(event.duration_ms) || 0;
        const startedAt = Date.parse(event.started_at || "");
        const duration = Number(event.duration_ms);
        const wallAge = Date.now() - startedAt;
        const canonicalRemaining = (
          Number.isFinite(startedAt)
          && Number.isFinite(duration)
          && wallAge >= 0
          && wallAge <= duration + 30000
        )
          ? Math.max(0, duration - wallAge)
          : initialRemaining;
        const queueAge = Math.max(0, performance.now() - event._woolQueuedAt);
        const runnableEvent = {
          ...event,
          remaining_ms: Math.max(
            0,
            Math.min(initialRemaining, canonicalRemaining) - queueAge,
          ),
        };
        await this._woolRunSceneEvent(runnableEvent);
        if (event?.id?.startsWith("action:")) {
          this._woolRememberReturnCue(`trace:${event.id.slice(7)}`);
        }
      }
    } finally {
      w.eventDraining = false;
      if (w.eventQueue.length) this._woolDrainSceneEvents();
    }
  },
  async _woolRunSceneEvent(event) {
    if (Array.isArray(event.plan) && event.plan.length) {
      await this._woolRunScenePlan(event);
      return;
    }
    const modifiers = Array.isArray(event.modifiers) ? event.modifiers : [];
    const modes = new Set(modifiers.map((modifier) => modifier?.mode).filter(Boolean));
    const ignored = modes.has("ignored");
    const consumedModes = new Set();
    const previousState = this.animState;
    if (event.animation_state) this.animState = event.animation_state;
    try {
      if (!this.woolIsNight()) {
        this._sndForMode(ignored ? "ignored" : event.action ? `action:${event.action}` : null);
      }
      if (ignored) {
        await this._woolRunModifier(
          modifiers.find((modifier) => modifier?.mode === "ignored"),
          event,
        );
        return;
      }
      switch (event.action) {
        case "greet": await this._woolPerfGreet(false); break;
        case "feed": await this._woolPerfFeed(false); break;
        case "walk":
          if (modes.has("threshold_refusal")) {
            await this._woolPerfThresholdWalk();
            consumedModes.add("threshold_refusal");
          } else {
            await this._woolPerfWalk(false);
          }
          break;
        case "call": await this._woolPerfCall(false); break;
        case "play": await this._woolPerfPlay(false); break;
        case "message": {
          const lamp = event.actor_user_id && event.actor_user_id === this.user?.id
            ? "partner"
            : "self";
          await this._woolRunMessageStep(lamp);
          break;
        }
        case "pet": {
          const hasPetReaction = [...modes].some((mode) => (
            mode === "flinch_away"
            || mode === "petting"
            || mode.startsWith("petting_")
          ));
          if (!hasPetReaction) await this._woolFxPetting();
          break;
        }
      }
      for (const modifier of modifiers) {
        if (consumedModes.has(modifier?.mode)) continue;
        await this._woolRunModifier(modifier, event);
      }
      if (
        event.action === "pet"
        && event._woolLocalOrigin
        && event.actor_user_id
        && event.actor_user_id === this.user?.id
        && !modes.has("flinch_away")
      ) {
        this._wool.petStreak++;
        if (this._wool.petStreak >= 3) {
          this._wool.petStreak = 0;
          this._postAction({ type: "play", variant: "zoomie" });
        }
      }
    } finally {
      this.animState = this.pet?.animation_state || previousState;
    }
  },
  async _woolRunScenePlan(event) {
    if (
      Number.isFinite(event.remaining_ms)
      && Number.isFinite(event.duration_ms)
      && event.duration_ms - event.remaining_ms > 250
    ) {
      await this._woolRunHydratedScenePlan(event);
      return;
    }
    const plan = event.plan || [];
    const primary = plan.find((step) => (
      step?.relation === "base" || step?.relation === "replace"
    ));
    const overlays = plan.filter((step) => step?.relation === "overlay");
    const after = plan.filter((step) => step?.relation === "after");
    const modes = new Set(plan.map((step) => step?.mode).filter(Boolean));
    if (!this.woolIsNight()) this._sndForMode(primary?.mode);
    const previousState = this.animState;
    if (event.animation_state) this.animState = event.animation_state;
    try {
      const concurrent = overlays.map((step) => (
        this._woolRunPlanOverlay(step, event)
      ));
      if (primary) concurrent.unshift(this._woolRunPlanStep(primary, event));
      await Promise.all(concurrent);
      for (const step of after) {
        await this._woolRunPlanStep(step, event);
      }
      if (
        event.action === "pet"
        && event._woolLocalOrigin
        && event.actor_user_id
        && event.actor_user_id === this.user?.id
        && !modes.has("ignored")
        && !modes.has("flinch_away")
      ) {
        this._wool.petStreak++;
        if (this._wool.petStreak >= 3) {
          this._wool.petStreak = 0;
          this._postAction({ type: "play", variant: "zoomie" });
        }
      }
    } finally {
      if (this._wool?.pendingStashEventId === event.id) {
        this._wool.pendingStashEventId = null;
      }
      this.animState = this.pet?.animation_state || previousState;
    }
  },
  async _woolRunHydratedScenePlan(event) {
    const plan = event.plan || [];
    const primary = plan.find((step) => (
      step?.relation === "base" || step?.relation === "replace"
    ));
    const overlays = plan.filter((step) => step?.relation === "overlay");
    const after = plan.filter((step) => step?.relation === "after");
    const primarySpan = Math.max(
      Number(primary?.duration_ms) || 0,
      ...overlays.map((step) => Number(step?.duration_ms) || 0),
    );
    let elapsed = Math.max(
      0,
      (Number(event.duration_ms) || 0) - (Number(event.remaining_ms) || 0),
    );
    if (!this.woolIsNight()) this._sndForMode(primary?.mode);
    const previousState = this.animState;
    if (event.animation_state) this.animState = event.animation_state;
    try {
      if (elapsed < primarySpan) {
        const concurrent = overlays
          .filter((step) => elapsed < (Number(step.duration_ms) || 0))
          .map((step) => this._woolRunPlanOverlay(
            {
              ...step,
              duration_ms: (Number(step.duration_ms) || 0) - elapsed,
            },
            event,
          ));
        if (primary) {
          concurrent.unshift(
            this._woolRunHydratedPrimary(primary, event, primarySpan - elapsed),
          );
        }
        await Promise.all(concurrent);
        elapsed = primarySpan;
      }

      let afterElapsed = Math.max(0, elapsed - primarySpan);
      for (const step of after) {
        const duration = Number(step.duration_ms) || 0;
        if (afterElapsed >= duration && duration > 0) {
          if (step.mode === "stash") this._woolFxStash(step);
          afterElapsed -= duration;
          continue;
        }
        if (afterElapsed > 0) {
          await this._woolRunHydratedPrimary(
            step,
            event,
            Math.max(0, duration - afterElapsed),
          );
          afterElapsed = 0;
          continue;
        }
        await this._woolRunPlanStep(step, event);
      }
    } finally {
      if (this._wool?.pendingStashEventId === event.id) {
        this._wool.pendingStashEventId = null;
      }
      this.animState = this.pet?.animation_state || previousState;
    }
  },
  async _woolRunHydratedPrimary(step, event, remainingMs) {
    if (step?.mode === "stash") {
      this._woolFxStash(step);
      return;
    }
    const hold = Math.max(0, remainingMs);
    const mode = step?.mode || "";
    const action = mode.startsWith("action:") ? mode.slice(7) : null;
    const lamp = event.actor_user_id && event.actor_user_id === this.user?.id
      ? "partner"
      : "self";
    await this._woolPerform(async () => {
      const kibble = ["k1", "k2", "k3"]
        .map((id) => document.getElementById(id))
        .filter(Boolean);
      if (action === "feed") {
        kibble.forEach((piece) => { piece.style.opacity = 1; });
        this._woolFlash("eating", Math.min(hold, 1400));
      } else if (action === "walk" || mode === "threshold_refusal") {
        this._woolFlash("leashswing", Math.min(hold, 1200));
        if (mode === "threshold_refusal") {
          this._woolFlash("sideeye", Math.min(hold, 1400));
        }
      } else if (action === "message") {
        this._woolFlash("return-wake", Math.min(hold, 1200));
        this._woolFlash("earflick", Math.min(hold, 700));
      } else if (
        action === "greet"
        || action === "play"
        || mode === "zoomie"
        || mode === "lean_in"
      ) {
        this._woolFlash("happy", Math.min(hold, 1400));
      } else if (action === "call") {
        this._woolFlash("alert", Math.min(hold, 1000));
        this._woolFlash("headtilt", Math.min(hold, 1400));
      } else if (
        action === "pet"
        || mode === "petting"
        || mode.startsWith("petting_")
      ) {
        this._woolFlash("squished", Math.min(hold, 1000));
        this._woolFlash("happy", Math.min(hold, 1200));
      } else if (
        mode === "ignored"
        || mode === "flinch_away"
        || mode === "side_eye"
      ) {
        this._woolFlash("sideeye", Math.min(hold, 1400));
        this._woolFlash("earflick", Math.min(hold, 700));
      } else if (mode === "sigh_settle") {
        this._woolFlash("melted", Math.min(hold, 1400));
      } else if (mode === "carry") {
        this._woolFlash("sideeye", Math.min(hold, 1000));
      }
      await this._woolWait(hold);
      kibble.forEach((piece) => piece.style.removeProperty("opacity"));
      if (action === "message" && lamp === "self" && !this.guest) {
        await this._woolLoadNotes();
      }
    });
  },
  async _woolRunPlanStep(step, event) {
    if (!step?.mode) return;
    if (step.mode.startsWith("action:")) {
      const action = step.mode.slice(7);
      switch (action) {
        case "greet": await this._woolPerfGreet(false); break;
        case "feed": await this._woolPerfFeed(false); break;
        case "walk": await this._woolPerfWalk(false); break;
        case "call": await this._woolPerfCall(false); break;
        case "play": await this._woolPerfPlay(false); break;
        case "message": {
          const lamp = event.actor_user_id && event.actor_user_id === this.user?.id
            ? "partner"
            : "self";
          await this._woolRunMessageStep(lamp);
          break;
        }
        case "pet": await this._woolFxPetting(); break;
      }
      return;
    }
    if (step.mode === "threshold_refusal") {
      await this._woolPerfThresholdWalk();
      return;
    }
    await this._woolRunModifier(step, event);
  },
  async _woolRunPlanOverlay(step, event) {
    if (step?.mode === "head_tilt") {
      this._woolFlash("headtilt", step.duration_ms || 2200);
      await this._woolWait(step.duration_ms || 2200);
      return;
    }
    if (step?.mode === "side_eye") {
      this._woolFlash("sideeye", step.duration_ms || 2000);
      this._woolFlash("earflick", 500);
      await this._woolWait(step.duration_ms || 2000);
      return;
    }
    await this._woolRunModifier(step, event);
  },
  async _woolRunModifier(modifier, event) {
    if (!modifier?.mode) return;
    switch (modifier.mode) {
      case "petting": await this._woolFxPetting(); break;
      case "petting_melt":
      case "petting_belly": await this._woolFxMelt(); break;
      case "petting_head": await this._woolFxPetting("head"); break;
      case "petting_ear": await this._woolFxPetting("ear"); break;
      case "petting_tail": await this._woolFxPetting("tail"); break;
      case "flinch_away": await this._woolFxFlinch(); break;
      case "ignored": await this._woolFxIgnored(); break;
      case "head_tilt":
        this._woolFlash("headtilt", modifier.duration_ms || 2200);
        await this._woolWait(Math.min(modifier.duration_ms || 700, 700));
        break;
      case "sigh_settle": await this._woolFxSigh(); break;
      case "stash": this._woolFxStash(modifier); break;
      case "carry": await this._woolFxCarry(); break;
      case "threshold_refusal": await this._woolFxThresholdRefusal(); break;
      case "lean_in": await this._woolFxLeanIn(); break;
      case "side_eye": await this._woolFxSideEye(); break;
      case "zoomie": await this._woolPerfZoomies(event.remaining_ms || 0); break;
    }
  },

  // ── scene_fx: local actions log the modes they will echo back as ──
  _woolLogActionFx(action) {
    const now = Date.now();
    for (const mode of ACTION_FX_MODES[action] || []) this._wool.fxLog[mode] = now;
  },

  // A scene_fx arriving over WS is the partner's action asking to be SEEN
  // here. The fxLog window (the fx's own duration) swallows two kinds of
  // repeats: the actor's own echo, and the same fx piggybacking on later
  // pet_state pushes until it expires server-side.
  _woolRemoteFx(fx) {
    const w = this._wool;
    if (!fx?.mode) return;
    if (!w) { this._pendingFx = fx; return; }
    if ((fx.remaining_ms || 0) < 900) return; // arrived too late to read
    if (fx.mode === "message_ping") { this._woolCarryMessage("self"); return; }
    const last = w.fxLog[fx.mode] || 0;
    if (Date.now() - last < (fx.duration_ms || 0) + 600) return;
    w.fxLog[fx.mode] = Date.now();
    if (!this.woolIsNight()) this._sndForMode(fx.mode);
    switch (fx.mode) {
      case "greet": this._woolPerfGreet(false); break;
      case "kibble": this._woolPerfFeed(false); break;
      case "leash_tug": this._woolPerfWalk(false); break;
      case "call_ring": this._woolPerfCall(false); break;
      case "zoomie": this._woolPerfZoomies(fx.remaining_ms); break;
      case "petting": this._woolFxPetting(); break;
      case "petting_melt": case "petting_belly": this._woolFxMelt(); break;
      case "petting_head": this._woolFxPetting("head"); break;
      case "petting_ear": this._woolFxPetting("ear"); break;
      case "petting_tail": this._woolFxPetting("tail"); break;
      case "flinch_away": this._woolFxFlinch(); break;
      case "ignored": this._woolFxIgnored(); break;
      case "head_tilt": this._woolFlash("headtilt", 2200); break;
      case "sigh_settle": this._woolFxSigh(); break;
      case "stash": this._woolFxStash(); break;
      case "carry": this._woolFxCarry(); break;
      case "threshold_refusal": this._woolFxThresholdRefusal(); break;
      case "lean_in": this._woolFxLeanIn(); break;
      case "side_eye": this._woolFxSideEye(); break;
    }
  },

  // Small in-place reactions — flash-only, so they yield to any running
  // performance instead of queueing behind it.
  async _woolFxPetting(spot) {
    if (this._wool.busy) return false;
    if (spot === "ear") this._woolFlash("earflick", 700);
    else this._woolFlash("squished", 900);
    if (spot === "tail") this._woolFlash("shakeoff", 700);
    this._woolFlash("happy", 1600);
    await this._woolWait(900);
    return true;
  },
  _woolFxMelt() {
    return this._woolPerform(async () => this._woolMelt());
  },
  async _woolFxFlinch() {
    return this._woolPerform(async () => {
      const w = this._wool;
      this._woolEar(-8);
      await this._woolScoot(w.px, w.py, w.px, w.py - 16, 320);
      await this._woolWait(800);
      this._woolEar(0);
      await this._woolScoot(w.px, w.py, w.px, w.py + 16, 420);
    });
  },
  async _woolFxSigh() {
    return this._woolPerform(async () => {
      const scene = document.getElementById("wool-scene");
      await this._woolFrame(650, (t) => this._woolSetGait(1 + 0.06 * t, 1 - 0.08 * t, 0));
      scene?.classList.add("melted");
      await this._woolWait(1100);
      scene?.classList.remove("melted");
      await this._woolFrame(450, (t) => this._woolSetGait(1.06 - 0.06 * t, 0.92 + 0.08 * t, 0));
      this._woolSetGait(1, 1, 0);
    });
  },
  // the stash: something came home from the walk and went straight under
  // the rug. the lump shows even mid-performance; the side-eye waits.
  _woolFxStash() {
    document.getElementById("ruglump")?.classList.add("show");
    if (this._wool.busy) return;
    this._woolFlash("sideeye", 1600);
  },
  async _woolFxCarry() {
    return this._woolPerform(async () => {
      await this._woolTravel(-52, 12, true);
      this._woolFlash("sideeye", 1400);
      await this._woolWait(500);
      await this._woolTravel(0, 0, false);
    });
  },
  _woolFxIgnored() {
    return this._woolPerform(async () => {
      this._woolFlash("earflick", 600);
      this._woolFlash("sideeye", 1400);
      await this._woolWait(1400);
    });
  },
  _woolFxThresholdRefusal() {
    return this._woolPerform(async () => {
      this._woolFlash("sideeye", 1600);
      await this._woolFrame(260, (t) => this._woolSetGait(
        1 + 0.12 * t,
        1 - 0.1 * t,
        -3 * t,
      ));
      await this._woolWait(900);
      this._woolSetGait(1, 1, 0);
    });
  },
  _woolFxLeanIn() {
    return this._woolPerform(async () => {
      this._woolFlash("happy", 1800);
      await this._woolFrame(360, (t) => this._woolSetGait(1, 1, 6 * t));
      await this._woolWait(900);
      await this._woolFrame(320, (t) => this._woolSetGait(1, 1, 6 * (1 - t)));
      this._woolSetGait(1, 1, 0);
    });
  },
  _woolFxSideEye() {
    return this._woolPerform(async () => {
      this._woolFlash("sideeye", 1800);
      this._woolFlash("earflick", 500);
      await this._woolWait(1800);
    });
  },

  // pet-as-messenger: the cat carries a whisper to a lamp before it lands.
  // "partner" = this human just sent it (toward their lamp); "self" = it
  // arrived for this reader (toward our lamp, then the note/heart appears).
  _woolCarryMessage(lamp) {
    const w = this._wool;
    if (!w) return false;
    const now = Date.now();
    if (now - (w.fxLog.message_ping || 0) < 3200) return false;
    if (w.busy || this.woolIsNight()) return false;
    this._woolPerfMessage(lamp);
    return true;
  },
  _woolPerfMessage(lamp) {
    const w = this._wool;
    if (!w || w.busy || this.woolIsNight()) return false;
    w.fxLog.message_ping = Date.now();
    return this._woolPerform(async () => {
      await this._woolTravel(lamp === "partner" ? 128 : -128, -30, true);
      this._woolFlash("earflick", 600);
      await this._woolWait(500);
      await this._woolTravel(0, 0, false);
      if (lamp === "self" && !this.guest) await this._woolLoadNotes();
    });
  },
  async _woolRunMessageStep(lamp) {
    if (!this.woolIsNight()) return this._woolPerfMessage(lamp);
    this._woolFlash("return-wake", 900);
    this._woolFlash("earflick", 700);
    await this._woolWait(700);
    if (lamp === "self" && !this.guest) await this._woolLoadNotes();
    return true;
  },
  _woolSendMessageFx() {
    if (!this._wool) return;
    this._woolFlash("lamp-touch", 520);
  },
  _woolMessageArrived() {
    if (!this._wool) { this._woolLoadNotes(); return; }
    if (this._wool.busy) { setTimeout(() => this._woolLoadNotes(), 2400); return; }
    this._woolLoadNotes();
  },

  // ---- performances (local ones also fire the real action) ----
  woolGreet() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolNightRefusal(); return; }
    this.act("greet");
  },
  async _woolPerfGreet(fire) {
    return this._woolPerform(async () => {
      if (fire) { this._woolLogActionFx("greet"); this.act("greet"); }
      this._woolFlash("alert", 800);
      await this._woolWait(400);
      await this._woolTravel(0, 30, true);
      this._woolFlash("happy", 1600);
      await this._woolWait(1200);
      await this._woolTravel(0, 0, false);
    });
  },
  woolFeed() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolSay("the bowl can wait for morning.", 3000); return; }
    this.act("feed");
  },
  async _woolPerfFeed(fire) {
    return this._woolPerform(async () => {
      const ks = ["k1", "k2", "k3"].map((id) => document.getElementById(id));
      ks.forEach((k) => k && (k.style.opacity = 1));
      this._woolFlash("alert", 800);
      if (fire) { this._woolLogActionFx("feed"); this.act("feed"); }
      await this._woolWait(600);
      await this._woolTravel(-96, 14, false);
      this._woolFlash("eating", 2300);
      for (const k of ks) { await this._woolWait(650); if (k) k.style.opacity = 0; }
      await this._woolWait(400);
      this._woolFlash("shakeoff", 700);
      await this._woolWait(650);
      // a full belly earns a visibly happy dog, not just a return to idle
      this._woolFlash("happy", 1600);
      await this._woolShimmy();
      await this._woolTravel(0, 0, false);
      ks.forEach((k) => k?.style.removeProperty("opacity"));
    });
  },
  woolPlay() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolSay(`${this._petPronoun()} sleeps. the ball keeps.`, 3000); return; }
    this.act("play");
  },
  async _woolPerfPlay(fire) {
    return this._woolPerform(async () => {
      clearTimeout(this._wool.ballResetTimer);
      this._wool.ballResetTimer = null;
      const ball = document.getElementById("roomball");
      if (ball) ball.style.transform = "translate(-160px, 10px) rotate(-200deg)";
      this._woolFlash("alert", 700);
      if (fire) { this._woolLogActionFx("play"); this.act("play"); }
      await this._woolWait(400);
      await this._woolTravel(-30, 14, true);
      await this._woolFrame(420, (t) => this._woolSetGait(
        1 + 0.06 * Math.sin(Math.PI * t * 3), 1 - 0.07 * Math.sin(Math.PI * t * 3),
        3 * Math.sin(Math.PI * t * 3)));
      await this._woolCrouch(140);
      await this._woolHop(this._wool.px, this._wool.py, -56, 16, 340);
      this._woolFlash("happy", 1800);
      await this._woolWait(1500);
      await this._woolTravel(0, 0, false);
      this._wool.ballResetTimer = setTimeout(
        () => {
          if (ball) ball.style.transform = "";
          this._wool.ballResetTimer = null;
        },
        this._woolMotionHold(2000),
      );
    });
  },
  woolWalk() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolSay("walks are a daylight religion.", 3000); return; }
    this.act("walk");
  },
  async _woolPerfWalk(fire) {
    return this._woolPerform(async () => {
      const scene = document.getElementById("wool-scene");
      this._woolFlash("alert", 900);
      this._woolFlash("leashswing", 1200);
      if (fire) { this._woolLogActionFx("walk"); this.act("walk"); }
      await this._woolWait(500);
      await this._woolTravel(150, -6, true);
      await this._woolWait(300);
      // the walk itself happens offstage: the room holds its breath
      scene?.classList.add("out-walking");
      await this._woolWait(1700);
      scene?.classList.remove("out-walking");
      this._woolDust(150, -6);
      this._woolFlash("shakeoff", 700);
      await this._woolWait(550);
      await this._woolTravel(0, 0, false);
    });
  },
  async _woolPerfThresholdWalk() {
    return this._woolPerform(async () => {
      this._woolFlash("alert", 900);
      this._woolFlash("leashswing", 1200);
      await this._woolTravel(112, -3, true);
      this._woolFlash("sideeye", 1800);
      await this._woolFrame(260, (t) => this._woolSetGait(
        1 + 0.12 * t,
        1 - 0.1 * t,
        -3 * t,
      ));
      await this._woolWait(900);
      this._woolSetGait(1, 1, 0);
      await this._woolTravel(0, 0, false);
    });
  },
  woolCall() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolSay("mm. (one ear twitches toward you)", 3000); return; }
    this.act("call");
  },
  async _woolPerfCall(fire) {
    return this._woolPerform(async () => {
      this._woolFlash("alert", 900);
      if (fire) { this._woolLogActionFx("call"); this.act("call"); }
      await this._woolWait(400);
      await this._woolTravel(0, 34, true);
      this._woolEar(-5);
      await this._woolWait(1400);
      this._woolEar(0);
      await this._woolTravel(0, 0, false);
    });
  },
  woolZoomies() {
    if (this._wool.busy) { this._woolBusyAck(); return; }
    if (this._woolAwayGate()) return;
    if (this.woolIsNight()) { this._woolSay(`${this._petPronoun()} sleeps. the zoomies keep till morning.`, 3000); return; }
    this._postAction({ type: "play", variant: "zoomie" });
  },
  // budgetMs caps a REMOTE zoomie to the fx's remaining life; 0 = local,
  // run the full eight laps regardless of the clock.
  async _woolPerfZoomies(budgetMs) {
    return this._woolPerform(async () => {
      this._wool.petStreak = 0;
      const laps = [[-96, 26], [2, 52], [98, 24], [0, 0], [-96, 26], [2, 52], [98, 24], [0, 0]];
      const prevState = this.animState;
      this.animState = "playful"; // zoomies hop regardless of mood
      const t0 = performance.now();
      try {
        for (const [x, y] of laps) {
          if (budgetMs && performance.now() - t0 > budgetMs) break;
          await this._woolTravel(x, y, true);
        }
      } finally { this.animState = prevState; }
      if (!budgetMs || performance.now() - t0 < budgetMs) await this._woolMelt();
      // M5, the stash: zoomies leave a lump in the rug. he knows what's
      // under it. he will not discuss it.
      document.getElementById("ruglump")?.classList.add("show");
    });
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
