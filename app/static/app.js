// Alpine root for woolroom. No build step - plain ES modules served by FastAPI.
// This entry merges the method groups under /static/js/ into the single
// component object Alpine instantiates via x-data="woolroom()".
import { createState } from "./js/state.js";
import { uiMethods } from "./js/ui.js";
import { memoryMethods } from "./js/memory.js";
import { presenceMethods } from "./js/presence.js";
import { apiMethods } from "./js/api.js";
import { quirkMethods } from "./js/quirks.js";
import { wsMethods } from "./js/ws.js";
import { sceneMethods } from "./js/wool.js";
import { woolVisitMethods } from "./js/woolvisits.js";
import { woolEventMethods } from "./js/woolevents.js";
import { woolFxMethods } from "./js/woolfx.js";
import { figureMethods } from "./js/figures.js";
import { soundMethods } from "./js/sound.js";

function woolroom() {
  return {
    ...createState(),

    async init() {
      this.initSound();
      try {
        this.bookmarkAcknowledged = localStorage.getItem("woolroom_bookmark_ack") === "1";
      } catch (_) { /* private mode etc. */ }
      await Promise.all([this.loadMe(), this.loadVoice(), this.loadPacks()]);
      this._dismissBootSplash();
      this.startCanvas();
      if (this.pet) {
        this.connectWs();
        this._maybeStartOnboarding();
      }
      // Window-on-the-wall sky gradient tracks local time of day. One minute
      // resolution is fine for a slow ambient effect.
      this._hourTimer = setInterval(() => {
        this.currentHour = new Date().getHours() + new Date().getMinutes() / 60;
      }, 60000);
    },

    _dismissBootSplash() {
      const s = document.getElementById("boot-splash");
      if (!s) return;
      s.classList.add("leaving");
      setTimeout(() => s.remove(), 340);
    },
    ...uiMethods,
    ...memoryMethods,
    ...presenceMethods,
    ...apiMethods,
    ...quirkMethods,
    ...wsMethods,
    ...sceneMethods,
    ...woolVisitMethods,
    ...woolEventMethods,
    ...woolFxMethods,
    ...figureMethods,
    ...soundMethods,
  };
}

// x-data="woolroom()" resolves against window - as a module, the function is
// no longer an implicit global, so export it explicitly before Alpine
// starts walking the DOM.
window.woolroom = woolroom;
