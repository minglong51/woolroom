// WebSocket connection, server-event handling, pet-state application,
// responses/milestones, scene-fx timers.
export const wsMethods = {
    // ───── WebSocket ─────

    connectWs() {
      if (!this.pet) return;
      // Idempotent — init, adopt and loadMe all call this; never double-open.
      if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      // The socket is room-scoped: switching rooms re-aims it.
      const room = this.pet?.id ? `?pet=${encodeURIComponent(this.pet.id)}` : "";
      const url = `${proto}://${location.host}/ws${room}`;
      this.wsState = this.wsRetry > 0 ? "reconnecting" : "connecting";
      try {
        this.ws = new WebSocket(url);
      } catch {
        this._scheduleReconnect();
        return;
      }
      this.ws.onopen = () => {
        this.wsRetry = 0;
        this.wsState = "live";
        this.lastRealtimeAt = Date.now();
      };
      this.ws.onmessage = (ev) => this._onWs(JSON.parse(ev.data));
      this.ws.onclose = () => {
        clearInterval(this._pingTimer);
        this._scheduleReconnect();
      };
      this.ws.onerror = () => {
        this.wsState = "reconnecting";
      };
      // Keep-alive so intermediaries don't drop the socket.
      clearInterval(this._pingTimer);
      this._pingTimer = setInterval(() => {
        if (this.ws && this.ws.readyState === 1) this.ws.send("ping");
      }, 20000);
    },

    _scheduleReconnect() {
      this.wsState = this.pet ? "reconnecting" : "idle";
      this.wsRetry = Math.min(this.wsRetry + 1, 6);
      setTimeout(() => this.pet && this.connectWs(), this.wsRetry * 1000);
    },

    _onWs(ev) {
      this.lastRealtimeAt = Date.now();
      this.wsState = "live";
      if (ev.type === "pet_state" && ev.pet) {
        if (this.pet?.id && ev.pet.id !== this.pet.id) return;
        const cardSubjectChanged = (
          this.pet?.id === ev.pet.id
          && (
            this.pet?.species !== ev.pet.species
            || this.pet?.coat !== ev.pet.coat
          )
        );
        if (cardSubjectChanged) this._invalidatePetCard?.(ev.pet.id);
        this._applyPetState(ev.pet);
        if (this.guest) return; // no invites to mint, no presence to track
        if ((this.pet?.participant_count || 0) >= 2) {
          this.inviteUrl = null;
        } else if (!this.inviteUrl) {
          this.ensureInvite();
        }
      } else if (ev.type === "scene_event" && ev.event) {
        this._setSceneEvent(ev.event);
      } else if (ev.type === "response") {
        this._showResponse(ev.text, ev.is_utterance, 8000, ev);
        if (ev.action === "message" && ev.by_user_id && ev.by_user_id !== this.user?.id
            && this._woolMessageArrived) {
          this._woolMessageArrived();
        }
      } else if (ev.type === "outing") {
        this._showResponse(`*later: ${ev.story}*`, false, 12000, { action: "outing" });
      } else if (ev.type === "milestone") {
        this._showMilestone(ev);
      } else if (ev.type === "household") {
        // The second pet arrived while we were home: refresh the room list
        // so the door + ceremony card appear without a reload.
        this._refreshPets();
        if (ev.event === "second_arrived" && ev.by_user_id && ev.by_user_id !== this.user?.id) {
          const name = this.applyAlias(ev.by_display_name) || "your other human";
          this._addLocalRoomNote(`${name} brought someone small home just now.`);
          if (this._woolSay) this._woolSay("(a small snuffling, somewhere behind the wall)", 6000);
        }
      } else if (ev.type === "presence") {
        if (this.pet) {
          this.pet = { ...this.pet, online_count: ev.online_count };
        }
        if (this.user && ev.user_id && ev.user_id !== this.user.id) {
          const name = this.applyAlias(ev.display_name) || "your other human";
          this._addLocalRoomNote(
            ev.joined ? `${name} stepped into the room just now.` : `${name} slipped out for now.`,
          );
          this.status = ev.joined ? `${name} just arrived.` : `${name} left for now.`;
          clearTimeout(this._presenceStatusTimer);
          this._presenceStatusTimer = setTimeout(() => (this.status = ""), 3000);
          if (ev.joined) {
            // Arrival theater: the lamp flares and the cat runs to it.
            this.partnerArrivedFlash = true;
            clearTimeout(this._partnerArrivedTimer);
            this._partnerArrivedTimer = setTimeout(() => (this.partnerArrivedFlash = false), 2500);
            if (this.woolPartnerArrived) this.woolPartnerArrived();
          }
        }
      }
    },

    _applyPetState(pet, prevVisitOverride) {
      // Callers that swap this.pet before applying (the room switch) pass the
      // visit diff-base explicitly; everyone else diffs against current state.
      const prevVisit = prevVisitOverride !== undefined
        ? prevVisitOverride
        : this.pet?.visit || null;
      this.pet = { ...this.pet, ...pet };
      if (!this.guest && (this.pet?.participant_count || 0) >= 2) {
        this.inviteUrl = null;
      }
      this.animState = pet.animation_state;
      this.poseDetail = pet.pose_detail || {};
      this.sharedTrace = pet.shared_trace || null;
      this.sharedTraceCue = pet.shared_trace_cue || this._deriveSharedTraceCue(this.sharedTrace);
      this.partnerTraceCues = Array.isArray(pet.partner_trace_cues) ? pet.partner_trace_cues : [];
      this.returnCue = pet.return_cue || null;
      this.partnerAbsenceMinutes = typeof pet.partner_absence_minutes === "number"
        ? pet.partner_absence_minutes
        : null;
      for (const event of pet.scene_events || []) {
        this._setSceneEvent(event);
      }
      this._setSceneFx(pet.scene_fx || null);
      // Long-lived open tabs catch fresh deploys via this version pin —
      // first scene payload sets bootVersion, every subsequent one
      // compares. Mismatch flips a soft pill the user can dismiss or tap
      // to reload. No auto-reload — don't yank typing/composing state.
      if (pet.app_version) {
        if (!this.bootVersion) {
          this.bootVersion = pet.app_version;
        } else if (pet.app_version !== this.bootVersion) {
          this.freshVersionAvailable = true;
        }
      }
      if (this._woolMaybeRunReturn) {
        setTimeout(() => this._woolMaybeRunReturn(), 0);
      }
      // Playdate transitions ride the payload diff: a visit appearing starts
      // the arrival beat in the host room (or the away hush in the empty one);
      // its disappearance is the going-home beat.
      const nextVisit = this.pet?.visit || null;
      if (this._woolVisitTransition) {
        this._woolVisitTransition(prevVisit, nextVisit);
      }
      this._syncVisiblePetCards?.();
    },

    async _refreshPets() {
      try {
        const r = await fetch("/api/me", { credentials: "same-origin" });
        if (!r.ok) return;
        const data = await r.json();
        this.pets = data.pets || [];
        if (data.pet?.id === this.pet?.id) {
          this._cachePetCard?.(this.pet, data.card || null);
        }
        this._syncVisiblePetCards?.();
      } catch (_) { /* the door repaints on the next full load */ }
    },

    async _refreshActiveCard(petId, species, coat) {
      return this._refreshPetCard?.({ id: petId, species, coat }, { force: true });
    },

    reloadForFreshVersion() {
      try { location.reload(); } catch (_) { /* */ }
    },

    _showResponse(text, isUtterance, durationMs = 8000, meta = {}) {
      const byUserId = meta.by_user_id || null;
      const byDisplayName = byUserId && this.sharedTrace?.user_id === byUserId
        ? this.sharedTrace.display_name
        : null;
      this.lastResponse = {
        text,
        is_utterance: isUtterance,
        by_user_id: byUserId,
        by_display_name: byDisplayName,
        action: meta.action || null,
      };
      clearTimeout(this._lastResponseTimer);
      // Linger scales with reading length — a fixed fade cut longer lines
      // off mid-read. ~55ms/char is a comfortable pace; floor at the
      // caller's duration, ceiling 20s so a panel never squats.
      const linger = Math.min(20000, Math.max(durationMs, 2500 + (text || "").length * 55));
      this._lastResponseTimer = setTimeout(() => {
        this.lastResponse = null;
      }, linger);
    },

    _showMilestone(ev) {
      // Milestones surface as a longer, gilded speech panel above the
      // regular response panel. Sits for 20s so neither human misses it.
      this.lastMilestone = {
        kind: ev.kind,             // "first_seen" | "count"
        event_type: ev.event_type,
        count: ev.count,
        fragment: ev.fragment,
        by_user_id: ev.by_user_id,
        by_display_name: ev.by_display_name,
      };
      clearTimeout(this._lastMilestoneTimer);
      this._lastMilestoneTimer = setTimeout(() => (this.lastMilestone = null), 20000);
    },

    milestoneKicker() {
      const m = this.lastMilestone;
      if (!m) return "";
      if (m.kind === "first_seen") {
        return `first ${m.event_type === "message" ? "thing said" : m.event_type}`;
      }
      if (m.kind === "ceremony") {
        return "all the way home";
      }
      if (m.kind === "anniversary") {
        return `${m.count} days together`;
      }
      // count
      const label = {
        pet: "pets", feed: "meals", walk: "walks",
        greet: "greetings", call: "calls", message: "things said", play: "plays",
      }[m.event_type] || m.event_type;
      return `${m.count} ${label}`;
    },

    dismissMilestone() {
      clearTimeout(this._lastMilestoneTimer);
      this.lastMilestone = null;
    },

    _deriveSharedTraceCue(trace) {
      if (!trace || !this.user || !trace.user_id || trace.user_id === this.user.id) return null;
      // Canonical source: app/room_contract.py TRACE_CUE_MAP — keep in sync
      // (tests/test_room_contract.py enforces).
      const mapping = {
        greet: { mode: "warm_spot", anchor: "rug" },
        pet: { mode: "brushed_coat", anchor: "dog" },
        feed: { mode: "bowl", anchor: "floor" },
        walk: { mode: "leash", anchor: "door" },
        call: { mode: "phone_glow", anchor: "shelf" },
        message: { mode: "phone_glow", anchor: "floor" },
        play: { mode: "rumpled_rug", anchor: "rug" },
      };
      const base = mapping[trace.event_type];
      if (!base) return null;
      const intensity = trace.freshness === "fresh" ? "strong"
        : trace.freshness === "recent" ? "soft"
        : "faint";
      return {
        ...base,
        intensity,
        event_type: trace.event_type,
        display_name: trace.display_name || "your other human",
      };
    },

    _setSceneFx(fx) {
      clearTimeout(this._sceneFxTimer);
      this.sceneFx = fx;
      if (!fx?.remaining_ms) return;
      this._sceneFxTimer = setTimeout(() => { this.sceneFx = null; }, fx.remaining_ms);
      if (
        fx.event_id
        && (
          this._woolHasSeenSceneEvent?.(fx.event_id)
          || (this._pendingSceneEvents || []).some((event) => event.id === fx.event_id)
        )
      ) return;
      if (this._woolRemoteFx) this._woolRemoteFx(fx);
    },

    _setSceneEvent(event) {
      const localOrigin = (
        !!event.origin_id
        && this._pendingActionOrigins.has(event.origin_id)
      );
      const sceneEvent = localOrigin
        ? { ...event, _woolLocalOrigin: true }
        : event;
      this.sceneEvent = sceneEvent;
      if (localOrigin) {
        this._pendingActionOrigins.delete(event.origin_id);
      }
      if (
        localOrigin
        && this._actingOriginId === event.origin_id
      ) {
        this._actingOriginId = null;
        this.actingAction = null;
      }
      if (this._woolSceneEvent) this._woolSceneEvent(sceneEvent);
    },
};
