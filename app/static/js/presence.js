const GUEST_PUBLIC_STORIES = [
  { kicker: "someone was here", line: "the rug is still warm near one lamp." },
  { kicker: "a small trace", line: "one lamp has dimmed; the postcard stayed where it was left." },
  { kicker: "the room kept it", line: "a folded paper waits on the wall while the room settles." },
];

// Household/presence copy, partner aliases, realtime labels, room notes.
export const presenceMethods = {
    applyAlias(name) {
      if (!name) return name;
      return this.aliasMap[name] || name;
    },

    async setAlias(targetName, alias) {
      const clean = (alias || "").trim();
      const next = { ...this.aliasMap };
      if (clean) next[targetName] = clean;
      else delete next[targetName];
      this.aliasMap = next;
      try {
        await fetch("/api/aliases", {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ partner_aliases: next }),
        });
      } catch (_) { /* network blip — keep local state, retry on next set */ }
    },

    partnerDisplayNames() {
      const all = Array.isArray(this.pet?.household_names) ? this.pet.household_names : [];
      const self = this.user?.display_name;
      return all.filter((n) => n !== self);
    },

    formatRoomNoteLine(note) {
      const raw = note?.line || "";
      if (!raw) return "";
      if (note.user_id && this.user?.id && note.user_id === this.user.id) return raw;
      const alias = this.applyAlias(note.display_name);
      if (!alias || alias === note.display_name) return raw;
      return raw.split(note.display_name).join(alias);
    },
    householdNames() {
      const raw = Array.isArray(this.pet?.household_names) ? this.pet.household_names : [];
      const self = this.user?.display_name;
      return raw.map((name) => (name === self ? name : this.applyAlias(name)));
    },

    otherHumanName() {
      const names = this.householdNames();
      const selfDisplay = this.user?.display_name;
      // After applyAlias, the partner's name may be transformed but self is not,
      // so finding the not-self entry still works.
      if (!selfDisplay) return names[0] || "your other human";
      return names.find((name) => name !== selfDisplay) || "your other human";
    },

    householdLine() {
      const names = this.householdNames();
      const petName = this.pet?.name || "the pet";
      const online = this.pet?.online_count || 0;
      if (this.guest) return `${petName}'s room.`;
      if (this.pet?.visit?.role === "away") {
        return `${petName}'s room, while ${petName} visits next door.`;
      }
      if (names.length >= 2 && online >= 2) {
        return this._voiceFmt(this.voice?.presence?.pair_here_together, { a: names[0], b: names[1], pet: petName });
      }
      if (names.length >= 2 && online === 1) {
        return `${petName} is keeping the room with you until ${this.otherHumanName()} comes back.`;
      }
      if (names.length >= 2) {
        return this._voiceFmt(this.voice?.presence?.pair_share_room, { a: names[0], b: names[1], pet: petName });
      }
      if (this.user?.display_name && this.pet?.name) {
        return `for now, this room belongs to ${this.user.display_name} and ${this.pet.name}.`;
      }
      return "this room is still becoming shared.";
    },

    guestPublicStory() {
      const utcDay = Math.floor(Date.now() / 86_400_000);
      return GUEST_PUBLIC_STORIES[utcDay % GUEST_PUBLIC_STORIES.length];
    },

    presenceMode() {
      const joined = this.pet?.participant_count || 0;
      const online = this.pet?.online_count || 0;
      if (joined < 2) return "awaiting";
      if (online >= 2) return "together";
      return "apart";
    },

    stagePresenceLine() {
      // Guests get no presence copy — who's home is the humans' business.
      if (this.guest) return "";
      if (this.pet?.visit?.role === "away") {
        return `${this.pet?.name || "the little one"} is through the door, visiting ${this.pet.visit.host_name || "next door"}.`;
      }
      const mode = this.presenceMode();
      if (mode === "awaiting") return "one side of the rug is still waiting.";
      if (mode === "together") return "the room feels fully inhabited.";
      // Positive framing for the apart state — the room holds the absent
      // partner's place instead of announcing the absence.
      return `${this.pet?.name || "the pet"} is keeping ${this.otherHumanName()}'s spot warm.`;
    },

    coupleRhythmLine() {
      const rhythm = this.pet?.couple_rhythm;
      if (!rhythm) return "";
      // Real-time presence already covers the "both connected" feeling;
      // this is the async layer — "you've both touched the room lately".
      if (rhythm.together_recent && this.presenceMode() !== "together") {
        return `${this.pet?.name || "the pet"} has felt both of you in the last hour.`;
      }
      if (rhythm.lopsided_hours && rhythm.lopsided_hours >= 4) {
        const hours = rhythm.lopsided_hours;
        const otherName = this.otherHumanName();
        if (rhythm.partner_minutes !== null && rhythm.partner_minutes > rhythm.viewer_minutes) {
          // Same fact ("partner last touched the room ~N hours ago"), framed
          // as time shared with the cat rather than time the partner owes.
          return `you and ${this.pet?.name || "the pet"} have had the room to yourselves for about ${hours} hour${hours === 1 ? "" : "s"}.`;
        }
        // Never count the viewer's own hours back at them — "you've been
        // away" is the guilt pattern this product exists to refuse. Absence
        // is held, not owed.
        return `${this.pet?.name || "the pet"} kept your spot on the rug warm.`;
      }
      return "";
    },

    partnerOnline() {
      // The viewer is by definition one of the online sockets, so partner is
      // present only when the channel reports >= 2 connected.
      const joined = this.pet?.participant_count || 0;
      const online = this.pet?.online_count || 0;
      return joined >= 2 && online >= 2;
    },

    realtimeLabel() {
      // Only rendered while not live (the pill hides when healthy), so the
      // copy reads as the room settling rather than a connection status.
      const labels = {
        idle: "joining the room...",
        connecting: "joining the room...",
        live: "",
        reconnecting: "catching up...",
      };
      return labels[this.wsState] ?? "joining the room...";
    },

    realtimeToneClass() {
      if (this.wsState === "live") return "realtime-live";
      if (this.wsState === "reconnecting") return "realtime-warn";
      return "";
    },

    roomNotes() {
      const persisted = Array.isArray(this.pet?.room_notes) ? this.pet.room_notes : [];
      return [...this.localRoomNotes, ...persisted]
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
        .slice(0, 4);
    },

    _addLocalRoomNote(line, eventType = "presence") {
      const note = {
        event_type: eventType,
        created_at: new Date().toISOString(),
        line,
      };
      this.localRoomNotes = [note, ...this.localRoomNotes].slice(0, 4);
    },

    inviteCardTitle() {
      if ((this.pet?.participant_count || 0) >= 2) {
        return "the room is shared now.";
      }
      return "one key is still waiting for the other human.";
    },

    inviteCardNote() {
      if ((this.pet?.participant_count || 0) >= 2) {
        return this.voice?.presence?.invite_note_shared || "";
      }
      return "when they arrive, this stops being yours alone.";
    },
};
