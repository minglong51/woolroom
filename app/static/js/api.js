// HTTP API calls: session bootstrap, adopt/join flows, actions, messages,
// logout/lock.
export const apiMethods = {
    async loadVoice() {
      // The client copy pack — fetched once at boot, stored on the root as
      // this.voice. Static per deploy; a failed fetch leaves voice null and
      // the copy readers below degrade to silence (the server that can't
      // serve /api/voice can't serve /api/me either).
      try {
        const r = await fetch("/api/voice", { credentials: "same-origin" });
        if (r.ok) this.voice = await r.json();
      } catch (_) { /* voice stays null */ }
      this.onboardingLines = this.voice?.onboarding || [];
    },

    async loadPacks() {
      // Pack-species figure assets — fetched once at boot, stored on the
      // root as this.packs. Static per deploy; a failed fetch leaves packs
      // null and figures.js falls back to the cat frame for any species it
      // doesn't know (the no-packs behavior, unchanged).
      try {
        const r = await fetch("/api/packs", { credentials: "same-origin" });
        if (r.ok) this.packs = await r.json();
      } catch (_) { /* packs stays null */ }
    },

    async loadAdoptionDefaults() {
      try {
        const r = await fetch("/api/adoption-defaults", { credentials: "same-origin" });
        if (r.ok) this.adoptionDefaults = await r.json();
      } catch (_) {}
      this.pickedCoat = this.adoptionDefaults.primary.coat;
      this.secondCoat = this.adoptionDefaults.secondary.coat;
    },

    _voiceFmt(template, vars) {
      // Fill a {slot}-style voice template with live data. Split/join, not
      // regex: values may contain anything (names, punctuation).
      let out = template || "";
      for (const [k, v] of Object.entries(vars || {})) {
        out = out.split(`{${k}}`).join(String(v));
      }
      return out;
    },

    _petCardEntry(pet) {
      if (!pet?.id) return null;
      const entry = this.petCardCache?.[pet.id];
      if (!entry || entry.species !== pet.species || entry.coat !== pet.coat) return null;
      return entry;
    },

    petCardFor(pet) {
      return this._petCardEntry(pet)?.card || null;
    },

    _cachePetCard(pet, card) {
      if (!pet?.id) return null;
      const value = (
        card
        && card.species === pet.species
        && card.coat === pet.coat
      ) ? card : null;
      this.petCardCache = {
        ...(this.petCardCache || {}),
        [pet.id]: { species: pet.species, coat: pet.coat, card: value },
      };
      if (this.pet?.id === pet.id) this.card = value;
      if (this.pet?.visit?.visitor?.id === pet.id) this._visitorArt = null;
      return value;
    },

    _invalidatePetCard(petId) {
      if (!petId || !this.petCardCache?.[petId]) return;
      const next = { ...this.petCardCache };
      delete next[petId];
      this.petCardCache = next;
      if (this.pet?.id === petId) this.card = null;
      if (this.pet?.visit?.visitor?.id === petId) this._visitorArt = null;
    },

    _resetPetCards() {
      this.petCardCache = {};
      this.card = null;
      this._cardLoads?.clear?.();
      this._cardCacheGeneration = (this._cardCacheGeneration || 0) + 1;
      this._visitorArt = null;
    },

    _cardSubjectForId(petId) {
      if (this.pet?.id === petId) return this.pet;
      const visitor = this.pet?.visit?.visitor;
      if (visitor?.id === petId) return visitor;
      const ceremony = this.ceremonyPet?.();
      return ceremony?.id === petId ? ceremony : null;
    },

    async _refreshPetCard(pet, { force = false } = {}) {
      if (!pet?.id || !pet.species || !pet.coat) return null;
      const subject = { id: pet.id, species: pet.species, coat: pet.coat };
      const generation = this._cardCacheGeneration || 0;
      if (!force) {
        const cached = this._petCardEntry(subject);
        if (cached) return cached.card;
      } else {
        this._invalidatePetCard(subject.id);
      }
      const loadKey = `${generation}\u0000${subject.id}\u0000${subject.species}\u0000${subject.coat}`;
      const loads = this._cardLoads || (this._cardLoads = new Set());
      if (loads.has(loadKey)) return null;
      loads.add(loadKey);
      try {
        const r = await fetch(`/api/card?pet=${encodeURIComponent(subject.id)}`, {
          credentials: "same-origin",
        });
        if (generation !== (this._cardCacheGeneration || 0)) return null;
        if (!r.ok) {
          const current = this._cardSubjectForId(subject.id);
          if (
            !current
            || current.species !== subject.species
            || current.coat !== subject.coat
          ) return null;
          this._cachePetCard(current, null);
          return null;
        }
        const data = await r.json();
        if (generation !== (this._cardCacheGeneration || 0)) return null;
        const current = this._cardSubjectForId(subject.id);
        if (
          !current
          || current.species !== subject.species
          || current.coat !== subject.coat
        ) return null;
        return this._cachePetCard(current, data.card || null);
      } catch (_) {
        return null;
      } finally {
        loads.delete(loadKey);
      }
    },

    _syncVisiblePetCards() {
      const ceremony = this.ceremonyPet?.() || null;
      const visitor = this.pet?.visit?.role === "host" ? this.pet.visit.visitor : null;
      const seen = new Set();
      const subjects = [this.pet, ceremony, visitor];
      for (const subject of subjects) {
        if (subject?.id) seen.add(subject.id);
      }
      for (const petId of Object.keys(this.petCardCache || {})) {
        if (!seen.has(petId)) this._invalidatePetCard(petId);
      }
      seen.clear();
      for (const subject of subjects) {
        if (!subject?.id || seen.has(subject.id)) continue;
        seen.add(subject.id);
        this._refreshPetCard(subject);
      }
    },

    _settleOwnerTransition() {
      requestAnimationFrame(() => window.scrollTo({ top: 0, behavior: "auto" }));
      if (!this.pet || this.guest) return;
      void Promise.all([this._woolLoadShelf(), this._woolLoadNotes()]);
      this._maybeStartOnboarding();
    },

    async loadMe(allowPendingJoin = true) {
      const r = await fetch("/api/me", { credentials: "same-origin" });
      const data = await r.json();
      this._resetPetCards();
      this.user = data.user;
      this.guest = !!data.guest;
      this.aliasMap = (data.user && data.user.partner_aliases) || {};
      this.pet = data.pet;
      this.pets = data.pets || [];
      this.activePetId = data.active_pet_id || data.pet?.id || null;
      this.pendingInvite = data.pending_invite || null;
      this.openSignup = !!data.open_signup;
      // The recovery link is a credential: /api/me no longer carries it.
      // Fetch it only while the bookmark card still needs it; the settings
      // reveal fetches on demand.
      if (data.user && !this.bookmarkAcknowledged && !this.recoveryUrl) {
        await this.loadRecoveryUrl();
      }
      if (!this.user) {
        // No session — a guest-cookie visitor still gets the room, read-only.
        if (this.guest) { await this.loadGuestScene(); return; }
        this.view = "landing";
        return;
      }
      if (!this.pet) {
        if (allowPendingJoin && this.pendingInvite && await this.tryJoinPendingInvite()) {
          await this.loadMe(false);
          return;
        }
        await this.loadQuirks();
        this.view = "adopt";
        return;
      }
      this._cachePetCard(this.pet, data.card || null);
      this._applyPetState(this.pet);
      this.view = "scene";
      // Any path that lands on a pet (init, adopt, invite join) needs the
      // socket — without this the second human's scene never goes live.
      this.connectWs();
      if (this.pet.participant_count < 2) await this.ensureInvite();
      else this.inviteUrl = null;
    },

    async loadQuirks() {
      const r = await fetch("/api/quirks");
      const data = await r.json();
      this.quirks = data.quirks;
    },

    async loadRecoveryUrl() {
      try {
        const r = await fetch("/api/recovery-url", { credentials: "same-origin" });
        if (r.ok) this.recoveryUrl = (await r.json()).recovery_url;
      } catch (_) { /* the reveal button retries */ }
    },

    async loadGuestScene() {
      // Read-only boot: sanitized scene only — no user, no invite, no memory.
      try {
        const r = await fetch("/api/guest/scene", { credentials: "same-origin" });
        if (!r.ok) {
          this.guest = false;
          this.view = "landing";
          return;
        }
        const data = await r.json();
        this.pet = data.pet;
        this._cachePetCard(this.pet, data.card || null);
        this._applyPetState(this.pet);
        this.view = "scene";
      } catch (_) {
        this.view = "landing";
      }
    },

    async start() {
      if (this.busy) return;
      this.busy = true;
      try {
        const r = await fetch("/api/start", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ display_name: this.displayName || "friend" }),
          credentials: "same-origin",
        });
        if (!r.ok) {
          const detail = await this._extractErrorDetail(r, "could not start");
          throw new Error(detail);
        }
        const data = await r.json();
        if (data.pending_invite_error) {
          this.status = data.pending_invite_error;
        }
        await this.loadMe();
        this._settleOwnerTransition();
      } catch (e) {
        this.status = String(e.message || e || "could not start. try again?");
      } finally {
        this.busy = false;
      }
    },
    async adopt() {
      if (this.busy) return;
      this.busy = true;
      try {
        const r = await fetch("/api/adopt", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name: this.petName, quirks: this.pickedQuirks, coat: this.pickedCoat }),
          credentials: "same-origin",
        });
        if (!r.ok) {
          const detail = await this._extractErrorDetail(r, "adopt failed");
          throw new Error(detail);
        }
        await this.loadMe();
        this.connectWs();
        this._settleOwnerTransition();
      } catch (e) {
        this.status = String(e.message || e || "adopt failed");
      } finally {
        this.busy = false;
      }
    },

    async tryJoinPendingInvite() {
      try {
        const r = await fetch("/api/join-pending", {
          method: "POST",
          credentials: "same-origin",
        });
        if (r.ok) return true;
        if (r.status === 404) return false;
        const data = await r.json().catch(() => ({}));
        this.status = data.detail || "could not join invite.";
      } catch {}
      return false;
    },

    async ensureInvite() {
      // In-flight guard: loadMe awaits this and the WS pet_state handler fires
      // it again on connect — without the guard a fresh boot POSTed /api/invite
      // two or three times (observed 2026-07-28 resource timing).
      if (this.inviteUrl || this._inviteInflight) return;
      this._inviteInflight = true;
      try {
        const r = await fetch("/api/invite", { method: "POST", credentials: "same-origin" });
        if (r.ok) {
          const url = (await r.json()).url;
          if ((this.pet?.participant_count || 0) < 2) this.inviteUrl = url;
        }
      } catch {}
      finally { this._inviteInflight = false; }
    },

    // The active room's query param — every room-scoped call carries it so
    // the server never has to guess which pet an action is for.
    _petQs() {
      return this.pet?.id ? `?pet=${encodeURIComponent(this.pet.id)}` : "";
    },

    async act(type) {
      await this._postAction({ type });
    },

    async petAtSpot(spot) {
      // Canvas-driven pet: skip the global actingAction state so the dock
      // doesn't flash-disable. The visual feedback should be on the figure itself.
      await this._postAction({ type: "pet", spot }, { skipDockLock: true });
    },
    async setCoat(coat) {
      // Settings-drawer coat change. The WS broadcast recolors the partner's
      // room live; we set ours optimistically in case the socket is down.
      if (this.guest) { this._guestToast(); return; }
      if (!this.pet || this.pet.coat === coat) return;
      try {
        const r = await fetch(`/api/coat${this._petQs()}`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ coat }),
        });
        if (r.ok) {
          const data = await r.json();
          this.pet = { ...this.pet, coat: data.coat };
          this._invalidatePetCard(this.pet.id);
          this._cachePetCard(this.pet, data.card || null);
        }
      } catch (_) { /* keeps the old wool; try again from settings */ }
    },
    async sendMessage() {
      const text = this.messageText.trim();
      if (!text) return;
      this.messageText = "";
      this.showComposer = false;
      // The cat carries the whisper toward their lamp, then it lands.
      if (this._woolSendMessageFx) this._woolSendMessageFx();
      await this._postAction({ type: "message", text });
    },

    async sendQuickReaction(text) {
      // Preset one-tap message — same backend path as "say something",
      // but no composer / no typing. Lightweight emotional unit.
      if (this.actingAction) return;
      if (this._woolSendMessageFx) this._woolSendMessageFx();
      await this._postAction({ type: "message", text });
    },

    async _postAction(body, opts = {}) {
      // Guests watch quietly — every action path (verbs, canvas hotspots,
      // whispers, lamp hearts) funnels through here, so one guard covers all.
      if (this.guest) { this._guestToast(); return; }
      const skipDockLock = !!opts.skipDockLock;
      if (!skipDockLock && this.actingAction) return;
      if (!skipDockLock) this.actingAction = body.type;
      this.status = "";
      const randomId = (
        window.crypto
        && typeof window.crypto.randomUUID === "function"
      )
        ? window.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
      const requestBody = {
        ...body,
        origin_id: `client:${randomId}`,
      };
      this._pendingActionOrigins.add(requestBody.origin_id);
      if (!skipDockLock) this._actingOriginId = requestBody.origin_id;
      try {
        let r = null;
        let transportError = null;
        for (let attempt = 0; attempt < 2; attempt += 1) {
          try {
            r = await fetch(`/api/action${this._petQs()}`, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify(requestBody),
              credentials: "same-origin",
            });
            transportError = null;
          } catch (e) {
            transportError = e;
          }
          if (r && (r.status < 500 || attempt === 1)) break;
        }
        if (!r) throw transportError || new Error("action failed");
        if (!r.ok) {
          const detail = await this._extractErrorDetail(r, "action failed");
          throw new Error(detail);
        }
        const data = await r.json();
        if (data.pet && this.wsState !== "live") this._applyPetState(data.pet);
        if (data.scene_event) this._setSceneEvent(data.scene_event);
        if (data.response) this._showResponse(data.response.text, data.response.is_utterance, 8000, data.response);
        this.status = "";
      } catch (e) {
        this.status = String(e.message || e || "action failed");
      } finally {
        this._pendingActionOrigins.delete(requestBody.origin_id);
        if (!skipDockLock && this._actingOriginId === requestBody.origin_id) {
          this._actingOriginId = null;
          this.actingAction = null;
        }
      }
    },

    async _extractErrorDetail(r, fallback) {
      try {
        const data = await r.clone().json();
        return data.detail || fallback;
      } catch (_) {
        return fallback;
      }
    },

    // ───── household rooms: crossing, playdates, the second pet ─────

    async switchRoom(petId) {
      // Cross to the sibling room: server remembers where you left, the
      // payload repaints, the socket re-aims at the new room.
      if (this.guest || !petId || this.pet?.id === petId) return;
      try {
        const r = await fetch("/api/room", {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ pet_id: petId }),
        });
        if (!r.ok) return;
        const data = await r.json();
        this._applyRoomSwitch(data.pet, data.card || null);
      } catch (_) { /* the door stays shut on a bad connection */ }
    },

    _applyRoomSwitch(newPet, newCard) {
      if (!newPet?.id) return;
      try { localStorage.setItem("woolroom_door_known", "1"); } catch (_) {}
      const prevId = this.pet?.id;
      // Snapshot the visit diff-base BEFORE this.pet moves to the new room —
      // _applyPetState otherwise diffs the new visit against itself and the
      // playdate beats never fire for the one who followed through the door.
      const prevVisit = this.pet?.visit || null;
      this.pet = newPet;
      this._cachePetCard(newPet, newCard || null);
      this.activePetId = newPet.id;
      this.woolLine = "";
      this.lastResponse = null;
      this.woolNotes = [];
      this.woolHearts = [];
      this.woolShelf = [];
      this.woolPatches = [];
      this.localRoomNotes = [];
      // Reset first: the old room's guest art is cleared before the new
      // payload's beats fire, so a stale _visitorArt can't trigger them.
      if (this._woolRoomSwitched) this._woolRoomSwitched(prevId);
      this._applyPetState(newPet, prevVisit);
      // Re-aim the realtime lane at the new room.
      if (this.ws) {
        try {
          this.ws.onmessage = null;
          this.ws.onclose = null;
          this.ws.close();
        } catch (_) {}
        this.ws = null;
      }
      this.connectWs();
      if (this._woolLoadShelf && this._woolLoadNotes) {
        this._woolLoadShelf();
        this._woolLoadNotes();
      }
    },

    async startVisitAndFollow() {
      // Double-tap on the door: this room's little one comes along next door.
      if (this.guest || !this.pet?.id || !this.pet?.sibling) return;
      const fromId = this.pet.id;
      const toId = this.pet.sibling.id;
      try {
        const r = await fetch("/api/visit", {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ pet_id: fromId }),
        });
        if (!r.ok) return;
        await this.switchRoom(toId);
      } catch (_) { /* no playdate on a bad connection */ }
    },

    async endVisit() {
      if (this.guest || !this.pet?.id) return;
      try {
        await fetch("/api/visit/end", {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ pet_id: this.pet.id }),
        });
      } catch (_) { /* the visit times out on its own */ }
    },

    // ───── the second pet: adopter's form, partner's ceremony ─────

    canAdoptSecond() {
      if (this.guest || !this.user) return false;
      if (!Array.isArray(this.pets) || this.pets.length !== 1) return false;
      return (this.pets[0].participant_count || 0) >= 2;
    },

    openSecondAdopt() {
      this.showSecondAdopt = true;
      if (!this.quirks.length) this.loadQuirks();
    },

    async adoptSecond() {
      if (this.busy || !this.secondName.trim() || !this.secondQuirk) return;
      this.busy = true;
      try {
        const r = await fetch("/api/adopt-second", {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            name: this.secondName.trim(),
            quirk: this.secondQuirk,
            coat: this.secondCoat,
          }),
        });
        if (!r.ok) {
          const detail = await this._extractErrorDetail(r, "could not bring it home");
          throw new Error(detail);
        }
        const data = await r.json();
        this.showSecondAdopt = false;
        this.showSettings = false;
        this.status = "";
        await this.loadMe();
        // Straight into its room — the first meeting is the adopter's.
        // switchRoom (not a bare apply) so last-room-left persists: a
        // reload tomorrow lands wherever this moment left it.
        if (data.pet?.id) await this.switchRoom(data.pet.id);
      } catch (e) {
        this.status = String(e.message || e || "could not bring it home");
      } finally {
        this.busy = false;
      }
    },

    ceremonyPet() {
      if (this.guest || !Array.isArray(this.pets)) return null;
      return this.pets.find((p) => p.pending) || null;
    },

    ceremonyFirstQuirk() {
      // Her first habit is sewn in by the adopter — shown, not pickable.
      return this.ceremonyPet()?.quirks?.[0] || null;
    },

    openCeremony() {
      this.ceremonyPick = null;
      this.showCeremony = true;
      if (!this.quirks.length) this.loadQuirks();
    },

    async confirmSecondQuirk() {
      const pending = this.ceremonyPet();
      if (!pending || !this.ceremonyPick || this.ceremonyBusy) return;
      this.ceremonyBusy = true;
      try {
        const r = await fetch("/api/second-quirk", {
          method: "POST",
          credentials: "same-origin",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ pet_id: pending.id, quirk: this.ceremonyPick }),
        });
        if (!r.ok) {
          const detail = await this._extractErrorDetail(r, "not yet");
          throw new Error(detail);
        }
        const data = await r.json();
        this.showCeremony = false;
        this.status = "";
        await this.loadMe();
        // His room opens for the first time — go meet him, and let the
        // last-room pointer remember the meeting place.
        if (data.pet?.id) await this.switchRoom(data.pet.id);
      } catch (e) {
        this.status = String(e.message || e || "not yet");
      } finally {
        this.ceremonyBusy = false;
      }
    },

    async logout() {
      await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
      location.reload();
    },

    async lockSite() {
      await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
      await fetch("/api/site-access/logout", { method: "POST", credentials: "same-origin" });
      location.assign("/access");
    },
};
