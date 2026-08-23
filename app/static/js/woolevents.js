// The scene-event lane: dedupe, return cues, the drain queue, and the
// hydrated plan runner that turns a broadcast into a performance. The
// primitives it drives live in wool.js (motion) and woolfx.js (fx).
export const woolEventMethods = {
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
        case "greet": await this._woolPerfGreet(); break;
        case "feed": await this._woolPerfFeed(); break;
        case "walk":
          if (modes.has("threshold_refusal")) {
            await this._woolPerfThresholdWalk();
            consumedModes.add("threshold_refusal");
          } else {
            await this._woolPerfWalk();
          }
          break;
        case "call": await this._woolPerfCall(); break;
        case "play": await this._woolPerfPlay(); break;
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
        case "greet": await this._woolPerfGreet(); break;
        case "feed": await this._woolPerfFeed(); break;
        case "walk": await this._woolPerfWalk(); break;
        case "call": await this._woolPerfCall(); break;
        case "play": await this._woolPerfPlay(); break;
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


  // A scene_fx arriving over WS is the partner's action asking to be SEEN
  // here. The fxLog window (the fx's own duration) swallows two kinds of
  // repeats: the actor's own echo, and the same fx piggybacking on later
  // pet_state pushes until it expires server-side.
};
