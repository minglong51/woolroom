// Ambient UI: onboarding narration, bookmark ack, hold-to-confirm, clipboard.
export const uiMethods = {
    _guestToast() {
      // The one line a guest ever hears back from the room.
      const name = this.pet?.name || "the pet";
      this._woolSay?.(`${name} noticed you.`, 2200);
      this.guestNoticeFlash = false;
      clearTimeout(this._guestNoticeTimer);
      requestAnimationFrame(() => (this.guestNoticeFlash = true));
      this._guestNoticeTimer = setTimeout(() => (this.guestNoticeFlash = false), 900);
    },

    _maybeStartOnboarding() {
      if (this.guest) return; // the narration is for the humans whose room it is
      let seen = false;
      try { seen = localStorage.getItem("woolroom_onboarded") === "1"; } catch (_) {}
      if (seen) return;
      // Wait a beat so the cat paints first — narration over a black box
      // would feel like a tutorial. Narration over the cat feels like room
      // settling-in.
      setTimeout(() => {
        this.onboardingStep = 1;
        this._advanceOnboarding();
      }, 2000);
    },

    _advanceOnboarding() {
      this._onboardingTimer = setTimeout(() => {
        if (this.onboardingStep >= this.onboardingLines.length) {
          this.onboardingStep = 0;
          try { localStorage.setItem("woolroom_onboarded", "1"); } catch (_) {}
          return;
        }
        this.onboardingStep += 1;
        this._advanceOnboarding();
      }, 4500);
    },

    dismissOnboarding() {
      clearTimeout(this._onboardingTimer);
      this.onboardingStep = 0;
      try { localStorage.setItem("woolroom_onboarded", "1"); } catch (_) {}
    },

    acknowledgeBookmark() {
      this.bookmarkAcknowledged = true;
      try { localStorage.setItem("woolroom_bookmark_ack", "1"); } catch (_) { /* */ }
    },

    async revealRecovery() {
      if (!this.recoveryUrl) await this.loadRecoveryUrl();
      this.showRecovery = true;
    },
    // Hold-to-confirm: ~1.5s press to fire a destructive action.
    // The CSS .danger-hold::before fill-bar provides the visible feedback.
    startHold(action) {
      this.cancelHold();
      this.holdingAction = action;
      this._holdTimer = setTimeout(() => {
        const fired = this.holdingAction;
        this.holdingAction = null;
        if (fired === "logout") this.logout();
        else if (fired === "lock") this.lockSite();
      }, 1500);
    },

    cancelHold() {
      if (this._holdTimer) clearTimeout(this._holdTimer);
      this._holdTimer = null;
      this.holdingAction = null;
    },

    copy(text) {
      if (!text) return;
      navigator.clipboard.writeText(text).catch(() => {});
      this.status = "copied.";
      setTimeout(() => (this.status = ""), 1500);
    },

    copyInvite() {
      this.copy(this.inviteUrl);
      this.inviteCopied = true;
      clearTimeout(this._inviteCopiedTimer);
      this._inviteCopiedTimer = setTimeout(() => (this.inviteCopied = false), 2600);
    },
};
