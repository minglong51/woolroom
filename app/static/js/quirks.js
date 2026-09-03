// Adoption quirk picker + landing-page copy.
export const quirkMethods = {
    coatOptions() {
      // Coat ids+order come from the voice payload, which mirrors the
      // server-side species registry (coats_for in app/data/species.py) — no
      // more client-side duplicate. Labels are copy, served alongside;
      // colors live in style.css (data-coat selectors), so swatch and pet
      // can never drift apart.
      return this._coatOptionsFor(this.pet?.species || this.primaryAdoptionSpecies());
    },

    secondCoatOptions() {
      return this._coatOptionsFor(this.secondaryAdoptionSpecies());
    },

    primaryAdoptionSpecies() {
      return this.adoptionDefaults?.primary?.species || "cat";
    },

    secondaryAdoptionSpecies() {
      return this.adoptionDefaults?.secondary?.species || "cat";
    },

    speciesLabel(species) {
      return (species || "pet").replaceAll("_", " ");
    },

    primaryPetLabel() {
      return `your ${this.speciesLabel(this.primaryAdoptionSpecies())}`;
    },

    _coatOptionsFor(species) {
      const ids = this.voice?.coats?.[species] || [];
      const labels = this.voice?.coat_labels || {};
      return ids.map((id) => ({ id, label: labels[id] || id }));
    },

    toggleQuirk(id) {
      const i = this.pickedQuirks.indexOf(id);
      if (i >= 0) {
        this.pickedQuirks.splice(i, 1);
        clearTimeout(this._quirkAuditionTimer);
        this.quirkAudition = null;
        this.quirkAuditionStatus = "";
        return;
      }
      if (this.pickedQuirks.length < 2) this.pickedQuirks.push(id);
      this.auditionQuirk(
        id,
        this.pickedQuirks.length >= 2 && !this.pickedQuirks.includes(id),
      );
    },

    auditionQuirk(id, selectionBlocked = false) {
      clearTimeout(this._quirkAuditionTimer);
      this.quirkAudition = null;
      const quirk = this.quirks.find((candidate) => candidate.id === id);
      const prefix = quirk ? `previewing ${quirk.label.toLowerCase()}` : "previewing habit";
      this.quirkAuditionStatus = selectionBlocked
        ? `${prefix}. remove one chosen habit to swap.`
        : `${prefix}. ${this.quirkPreview(id)}.`;
      this.$nextTick(() => {
        this.quirkAudition = id;
        this._quirkAuditionTimer = setTimeout(() => {
          if (this.quirkAudition === id) {
            this.quirkAudition = null;
            this.quirkAuditionStatus = "";
          }
        }, 2200);
      });
    },

    quirkAuditionClasses() {
      if (!this.quirkAudition) return {};
      return {
        [`audition-${this.quirkAudition.replaceAll("_", "-")}`]: true,
      };
    },

    quirkPreview(id, species) {
      const pack = this.voice?.quirks || {};
      const line = (pack.previews || {})[id];
      if (line == null) return pack.preview_fallback || "";
      if (typeof line === "string") return line;
      // Per-species preview dict: resolve by the asked-for (or current)
      // species, falling back to the configured first pet, then the builtin
      // cat, then any value present.
      const which = species || this.pet?.species || this.primaryAdoptionSpecies();
      return line[which] ?? line.cat ?? Object.values(line)[0];
    },

    quirkMood(id) {
      const pack = this.voice?.quirks || {};
      return (pack.moods || {})[id] || pack.mood_fallback || "";
    },

    adoptPreviewLine() {
      const name = (this.petName || "").trim() || this.primaryPetLabel();
      const species = this.speciesLabel(this.primaryAdoptionSpecies());
      const picks = this.pickedQuirks.map((id) => this.quirkPreview(id));
      if (picks.length === 0) {
        return `${name} is still just a shape in the room. choose the habits that will follow them home.`;
      }
      if (picks.length === 1) {
        return `${name} already feels like the kind of ${species} who ${picks[0]}.`;
      }
      return `${name} already feels like the kind of ${species} who ${picks[0]}, and ${picks[1]}.`;
    },

    selectedQuirkPreviewLines() {
      if (!this.pickedQuirks.length) {
        return [
          "pick two permanent habits",
          "they should feel small, specific, and lived-in",
        ];
      }
      return this.pickedQuirks
        .map((id) => this.quirks.find((q) => q.id === id))
        .filter(Boolean)
        .map((q) => `${q.label}: ${this.quirkPreview(q.id)}`);
    },

    landingPreviewName() {
      // "someone", not "you" — the line reads `${who} steps in quietly`,
      // and "you steps" is a grammar bug.
      return (this.displayName || "").trim() || "someone";
    },

    hasPendingInvite() {
      return !!this.pendingInvite?.pet_id;
    },

    canSignup() {
      return this.hasPendingInvite() || this.openSignup;
    },

    landingPreviewLine() {
      const who = this.landingPreviewName();
      if (this.hasPendingInvite()) {
        const petName = this.pendingInvite.pet_name || "the cat";
        return `${who} is about to step into ${petName}'s room, where the quiet has already been shared.`;
      }
      return `${who} steps in quietly. the room notices, but it does not make a scene.`;
    },

    landingKicker() {
      if (this.hasPendingInvite()) return "join the room";
      if (this.openSignup) return "new room";
      return "no entry yet";
    },

    landingTitle() {
      if (this.hasPendingInvite()) {
        const petName = this.pendingInvite.pet_name || "the cat";
        return `you've been invited to ${petName}.`;
      }
      if (this.openSignup) return "start your room.";
      return "this room is small, and someone you trust holds the key.";
    },

    landingCopy() {
      if (this.hasPendingInvite()) {
        const petName = this.pendingInvite.pet_name || "the cat";
        const adoptedBy = this.pendingInvite.adopted_by;
        if (adoptedBy) {
          return `enter your name and you'll join ${petName}, already being cared for by ${adoptedBy}.`;
        }
        return `enter your name and this browser will join ${petName}'s room right away.`;
      }
      if (this.openSignup) {
        return `enter your name to begin. you'll name ${this.primaryPetLabel()} next.`;
      }
      return "tap your saved link to come back in. if this is your first time here, ask the person who invited you for a fresh one.";
    },

    landingButtonLabel() {
      if (this.hasPendingInvite()) return "join room";
      if (this.openSignup) return "begin";
      return "enter";
    },

    landingNotePrimary() {
      if (this.hasPendingInvite()) return "this should take you straight into the shared room, not into adoption.";
      if (this.openSignup) return "the second human joins later with an invite link.";
      return "no name field by design — typing a new name would just create a duplicate account.";
    },

    landingNoteSecondary() {
      if (this.hasPendingInvite()) return "returning later: your recovery link will still rebind this device.";
      if (this.openSignup) {
        return `your recovery link will be generated once you name ${this.primaryPetLabel()}.`;
      }
      return "lost your link? ask your partner to send a fresh invite from their settings drawer.";
    },
};
