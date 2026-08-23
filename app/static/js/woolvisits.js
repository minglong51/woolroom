// The door next door + playdate choreography (host room side): the
// sibling-room crossing, the visitor figure's travel, arrivals and
// departures, and the away gate. Split from wool.js along its own
// banner — same component `this`, merged in app.js.
export const woolVisitMethods = {
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
};
