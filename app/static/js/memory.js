// Memory drawer: fetch, pinning, date grouping.
export const memoryMethods = {
    async openMemory() {
      this.showMemory = true;
      try {
        const r = await fetch(`/api/memory${this._petQs()}`, { credentials: "same-origin" });
        if (r.ok) this.memory = await r.json();
      } catch (_) { /* */ }
    },

    async pinNote(note) {
      if (!note?.event_id) return;
      try {
        const r = await fetch(`/api/memory/pin${this._petQs()}`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ event_id: note.event_id }),
        });
        if (r.ok) {
          this.pinnedEventIds = [...this.pinnedEventIds, note.event_id];
        }
      } catch (_) { /* */ }
    },

    memoryDaysLine() {
      if (!this.memory?.adopted_at) return "a while";
      const days = Math.floor((Date.now() - new Date(this.memory.adopted_at).getTime()) / (24 * 60 * 60 * 1000));
      if (days <= 0) return "today";
      if (days === 1) return "1 day";
      return `${days} days`;
    },

    groupedMemoryMoments() {
      const moments = (this.memory?.moments || []).slice();
      if (!moments.length) return [];
      const now = new Date();
      const groups = { today: [], yesterday: [], earlier_week: [], earlier: [] };
      for (const m of moments) {
        const d = new Date(m.created_at);
        const diffDays = Math.floor((now - d) / (24 * 60 * 60 * 1000));
        if (diffDays === 0) groups.today.push(m);
        else if (diffDays === 1) groups.yesterday.push(m);
        else if (diffDays < 7) groups.earlier_week.push(m);
        else groups.earlier.push(m);
      }
      const labeled = [
        { label: "today", items: groups.today },
        { label: "yesterday", items: groups.yesterday },
        { label: "earlier this week", items: groups.earlier_week },
        { label: "earlier", items: groups.earlier },
      ].filter((g) => g.items.length);
      return labeled;
    },
};
