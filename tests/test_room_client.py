import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "static" / "index.html"
STYLE_CSS = ROOT / "app" / "static" / "style.css"
UI_JS = ROOT / "app" / "static" / "js" / "ui.js"
PRESENCE_JS = ROOT / "app" / "static" / "js" / "presence.js"
API_JS = ROOT / "app" / "static" / "js" / "api.js"
WOOL_JS = ROOT / "app" / "static" / "js" / "wool.js"


def _run_modules(
    tmp_path: Path,
    modules: dict[str, Path],
    script: str,
) -> dict[str, object]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for the room client contract", pytrace=False)

    imports: dict[str, str] = {}
    for name, source in modules.items():
        target = tmp_path / f"{name}.mjs"
        target.write_text(source.read_text())
        imports[name] = target.as_uri()

    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        env={"PATH": str(Path(node).parent), "ROOM_MODULES": json.dumps(imports)},
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_guest_room_interactions_are_explicitly_local_or_disabled() -> None:
    markup = INDEX_HTML.read_text()
    css = STYLE_CSS.read_text()

    assert '<h1 class="name" x-text="pet?.name ?? \'\'"></h1>' in markup
    assert re.search(
        r'<p class="utter"[^>]*role="status"[^>]*aria-live="polite"'
        r'[^>]*aria-atomic="true"',
        markup,
    )

    for class_name in ("sunbtn", "moonbtn"):
        tag = re.search(
            rf'<g\b(?=[^>]*class="[^"]*\b{class_name}\b)[^>]*>',
            markup,
        )
        assert tag is not None
        assert 'tabindex="0"' in tag.group()
        assert "aria-disabled" not in tag.group()
        assert 'woolSkyTap()' in tag.group()

    for class_name in ("leashg", "bowlg", "ballg"):
        tag = re.search(
            rf'<g\b(?=[^>]*class="[^"]*\b{class_name}\b)[^>]*>',
            markup,
        )
        assert tag is not None
        assert ':tabindex="guest ? -1 : 0"' in tag.group()
        assert ':aria-disabled="guest"' in tag.group()

    pet_zone = re.search(r'<rect\b(?=[^>]*id="dogzone")[^>]*>', markup)
    assert pet_zone is not None
    assert ':tabindex="guest ? -1 : 0"' in pet_zone.group()
    assert ':aria-disabled="guest"' in pet_zone.group()

    disabled_rule = re.search(
        r'#wool-scene \[aria-disabled="true"\]\s*\{([^}]*)\}',
        css,
        re.DOTALL,
    )
    assert disabled_rule is not None
    assert "pointer-events: none" in disabled_rule.group(1)
    night_moon_rule = '#wool-scene[data-time="night"] .moonbtn'
    night_disabled_rule = (
        '#wool-scene[data-time="night"] [aria-disabled="true"] { '
        "pointer-events: none; }"
    )
    assert night_disabled_rule in css
    assert css.index(night_disabled_rule) > css.index(night_moon_rule)

    glass = re.search(
        r'<div class="glass-tap-wrap" x-show="guest".*?</div>',
        markup,
        re.DOTALL,
    )
    assert glass is not None
    assert 'id="glass-tap"' in glass.group()
    assert '@click.prevent.stop="_guestToast()"' in glass.group()
    assert 'aria-describedby="glass-tap-note"' in glass.group()
    assert "nothing is posted or saved" in glass.group()

    journal_trigger = re.search(
        r'<template x-if="!guest">\s*<button[^>]*aria-controls="room-journal"'
        r'[^>]*:aria-expanded="showMemory"',
        markup,
    )
    assert journal_trigger is not None
    assert re.search(
        r'<aside id="room-journal"[^>]*x-show="showMemory"'
        r'[^>]*aria-labelledby="room-journal-title"',
        markup,
    )
    assert 'id="room-journal-title"' in markup

    assert len(re.findall(r'class="guest-lantern\b', markup)) == 2
    assert 'class="guest-postcard"' in markup
    assert 'class="guest-warm-trace"' in markup
    assert 'x-text="guestPublicStory().kicker"' in markup
    assert 'x-text="guestPublicStory().line"' in markup
    assert ".guest-lantern { opacity: .48; }" in css

    reduced_motion_start = css.index(
        "@media (prefers-reduced-motion: reduce)", css.index(".glass-tap")
    )
    reduced_motion_end = css.index("/* ─────────────────────── the playdate guest", reduced_motion_start)
    reduced_motion = css[reduced_motion_start:reduced_motion_end]
    assert ".glass-tap, .room-controls-summary, .room-controls-summary::after," in reduced_motion
    assert ".access-pet { transition: none !important; animation: none !important; }" in reduced_motion
    assert ".glass-tap:active { transform: none; }" in reduced_motion
    assert (
        ".room-controls[open] .room-controls-summary::after { transform: rotate(180deg); }"
        in reduced_motion
    )


def test_guest_story_and_glass_gesture_are_local_and_synthetic(
    tmp_path: Path,
) -> None:
    result = _run_modules(
        tmp_path,
        {"ui": UI_JS, "presence": PRESENCE_JS, "wool": WOOL_JS},
        r"""
const modules = JSON.parse(process.env.ROOM_MODULES);
const {uiMethods} = await import(modules.ui);
const {presenceMethods} = await import(modules.presence);
const {sceneMethods} = await import(modules.wool);

const sideEffects = [];
const timers = [];
globalThis.fetch = (...args) => { sideEffects.push(["fetch", ...args]); };
globalThis.localStorage = {
  getItem(key) { sideEffects.push(["get", key]); },
  setItem(key, value) { sideEffects.push(["set", key, value]); },
};
globalThis.requestAnimationFrame = (callback) => callback();
globalThis.clearTimeout = () => {};
globalThis.setTimeout = (callback, delay) => {
  timers.push({callback, delay});
  return timers.length;
};

const said = [];
const guest = {
  pet: {name: "Pebble"},
  guestNoticeFlash: false,
  _guestNoticeTimer: null,
  _woolSay(line, duration) { said.push([line, duration]); },
};
uiMethods._guestToast.call(guest);

const skySaid = [];
globalThis.document = {
  getElementById() { return {dataset: {time: "day"}}; },
};
sceneMethods.woolSkyTap.call({
  _woolSay(line, duration) { skySaid.push([line, duration]); },
});

const originalNow = Date.now;
const day = 86_400_000;
const base = Date.UTC(2026, 8, 1);
const context = new Proxy({}, {
  get() { throw new Error("guestPublicStory read request context"); },
});
const stories = [];
for (let offset = 0; offset < 4; offset += 1) {
  Date.now = () => base + offset * day;
  stories.push(presenceMethods.guestPublicStory.call(context));
}
Date.now = originalNow;

process.stdout.write(JSON.stringify({
  sideEffects,
  timers: timers.map(({delay}) => delay),
  said,
  skySaid,
  notice: guest.guestNoticeFlash,
  stories,
}));
""",
    )

    assert result["sideEffects"] == []
    assert result["timers"] == [900]
    assert result["said"] == [["Pebble noticed you.", 2200]]
    assert result["skySaid"] == [["the sun is doing its one quiet job.", 3000]]
    assert result["notice"] is True
    stories = result["stories"]
    assert isinstance(stories, list)
    assert all(set(story) == {"kicker", "line"} for story in stories)
    assert len({json.dumps(story, sort_keys=True) for story in stories[:3]}) == 3
    assert stories[3] == stories[0]


def test_owner_transition_hydrates_after_identity_change(tmp_path: Path) -> None:
    result = _run_modules(
        tmp_path,
        {"api": API_JS},
        r"""
const modules = JSON.parse(process.env.ROOM_MODULES);
const {apiMethods} = await import(modules.api);

globalThis.requestAnimationFrame = (callback) => callback();
const directCalls = [];
globalThis.window = {
  scrollTo(options) { directCalls.push(["scroll", options]); },
};
apiMethods._settleOwnerTransition.call({
  pet: {id: "pet"},
  guest: false,
  _woolLoadShelf() { directCalls.push(["shelf"]); },
  _woolLoadNotes() { directCalls.push(["notes"]); },
  _maybeStartOnboarding() { directCalls.push(["onboarding"]); },
});

const guestCalls = [];
globalThis.window = {
  scrollTo(options) { guestCalls.push(["scroll", options]); },
};
apiMethods._settleOwnerTransition.call({
  pet: {id: "demo"},
  guest: true,
  _woolLoadShelf() { guestCalls.push(["shelf"]); },
  _woolLoadNotes() { guestCalls.push(["notes"]); },
  _maybeStartOnboarding() { guestCalls.push(["onboarding"]); },
});

globalThis.fetch = async () => ({ok: true, json: async () => ({})});
const startCalls = [];
const startContext = {
  busy: false,
  displayName: "Reader",
  status: "",
  async loadMe() { startCalls.push("loadMe"); },
  _settleOwnerTransition() { startCalls.push("settle"); },
};
await apiMethods.start.call(startContext);

const adoptCalls = [];
const adoptContext = {
  busy: false,
  petName: "Pebble",
  pickedQuirks: ["one", "two"],
  pickedCoat: "ash",
  status: "",
  async loadMe() { adoptCalls.push("loadMe"); },
  connectWs() { adoptCalls.push("connectWs"); },
  _settleOwnerTransition() { adoptCalls.push("settle"); },
};
await apiMethods.adopt.call(adoptContext);

process.stdout.write(JSON.stringify({
  directCalls,
  guestCalls,
  startCalls,
  startBusy: startContext.busy,
  adoptCalls,
  adoptBusy: adoptContext.busy,
}));
""",
    )

    assert result["directCalls"] == [
        ["scroll", {"top": 0, "behavior": "auto"}],
        ["shelf"],
        ["notes"],
        ["onboarding"],
    ]
    assert result["guestCalls"] == [["scroll", {"top": 0, "behavior": "auto"}]]
    assert result["startCalls"] == ["loadMe", "settle"]
    assert result["startBusy"] is False
    assert result["adoptCalls"] == ["loadMe", "connectWs", "settle"]
    assert result["adoptBusy"] is False
