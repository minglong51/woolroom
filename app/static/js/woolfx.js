// Fx primitives and the verb performances: what each mode looks like
// in the room, and the local half of every human verb. Motion
// primitives live in wool.js; the event lane calls in from
// woolevents.js.
export const woolFxMethods = {
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
      case "greet": this._woolPerfGreet(); break;
      case "kibble": this._woolPerfFeed(); break;
      case "leash_tug": this._woolPerfWalk(); break;
      case "call_ring": this._woolPerfCall(); break;
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
  async _woolPerfGreet() {
    return this._woolPerform(async () => {
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
  async _woolPerfFeed() {
    return this._woolPerform(async () => {
      const ks = ["k1", "k2", "k3"].map((id) => document.getElementById(id));
      ks.forEach((k) => k && (k.style.opacity = 1));
      this._woolFlash("alert", 800);
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
  async _woolPerfPlay() {
    return this._woolPerform(async () => {
      clearTimeout(this._wool.ballResetTimer);
      this._wool.ballResetTimer = null;
      const ball = document.getElementById("roomball");
      if (ball) ball.style.transform = "translate(-160px, 10px) rotate(-200deg)";
      this._woolFlash("alert", 700);
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
  async _woolPerfWalk() {
    return this._woolPerform(async () => {
      const scene = document.getElementById("wool-scene");
      this._woolFlash("alert", 900);
      this._woolFlash("leashswing", 1200);
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
  async _woolPerfCall() {
    return this._woolPerform(async () => {
      this._woolFlash("alert", 900);
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
};
