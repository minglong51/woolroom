#!/usr/bin/env bash
# Headless visual-layer smoke: boots a scratch server, adopts a cat, and
# asserts the invariants the backend test suite cannot see (scene classes,
# busy-window resets, settle-note flow). Catches the bug class of 2026-07-17
# (".happy pinned to playful mood") that 64 green API tests sailed past.
#
# Requires the gstack browse daemon (~/.claude/skills/gstack/browse).
# Usage: scripts/smoke-browse.sh   (exit 0 = pass)
set -u
cd "$(dirname "$0")/.."

B="${BROWSE_BIN:-$HOME/.claude/skills/gstack/browse/dist/browse}"
PORT="${SMOKE_PORT:-8002}"
WORK=$(mktemp -d)
FAILS=0

say()  { printf '%s\n' "$*"; }
pass() { say "  ok: $*"; }
fail() { say "  FAIL: $*"; FAILS=$((FAILS + 1)); }

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
  rm -rf "$WORK"
  # Cookies ignore ports, so this run clobbered any localhost dogfood
  # session — put the browser back the way we found it.
  "$B" state load smoke-prev >/dev/null 2>&1 || true
}
trap cleanup EXIT
"$B" state save smoke-prev >/dev/null 2>&1 || true

say "== boot scratch server on :$PORT =="
DATABASE_URL="sqlite+aiosqlite:///$WORK/smoke.db" OPEN_SIGNUP=1 SITE_PASSWORD= \
  uv run uvicorn app.main:app --port "$PORT" >"$WORK/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 30); do
  curl -s "http://localhost:$PORT/healthz" | grep -q '"ok":true' && break
  sleep 0.5
done
curl -s "http://localhost:$PORT/healthz" | grep -q '"ok":true' || { fail "server never became healthy"; exit 1; }

J() { "$B" js "$1"; }
ALP='Alpine.$data(document.querySelector("main"))'

"$B" viewport 390x844 >/dev/null
"$B" goto "http://localhost:$PORT/" >/dev/null
sleep 1.5
"$B" console --clear >/dev/null

say "== adopt flow =="
J "fetch('/api/start',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({display_name:'smoke'}),credentials:'same-origin'}).then(r=>r.status)" >/dev/null
sleep 1
"$B" goto "http://localhost:$PORT/" >/dev/null; sleep 1.5
"$B" fill "#pet-name" "Smokey" >/dev/null
J "const c=[...document.querySelectorAll('.quirk-card')]; c[0].click(); c[1].click(); 'picked'" >/dev/null
sleep 0.5
J "document.querySelector('.adopt-submit').click(); 'go'" >/dev/null
sleep 2.5
[ "$(J "$ALP.view")" = "scene" ] && pass "adopt reaches scene" || fail "adopt did not reach scene"

# Deterministic scene: force day, cat awake, no onboarding overlay.
# animState must be forced too: .sleeping binds from the server mood, and a
# nighttime run adopts a cat that is already down for the night (the 2026-07-21
# 1am flake — eyes-open read opacity 0 "at rest" with every visual correct).
J "clearInterval($ALP._woolHourTimer); $ALP._woolApplyHour=()=>{}; const s=document.getElementById('wool-scene'); s.dataset.time='day'; s.classList.remove('dogsleep'); $ALP.animState='sitting'; $ALP.onboardingStep=99; 'day'" >/dev/null
sleep 1 # Alpine class binding + 0.3s eye transition must land before asserts read opacity

say "== at-rest invariants =="
[ "$(J "getComputedStyle(document.querySelector('.eyes-open')).opacity")" = "1" ] \
  && pass "eyes open at rest" || fail "eyes not open at rest (day, awake)"
J "$ALP.animState='playful'; 'p'" >/dev/null; sleep 0.6
[ "$(J "getComputedStyle(document.querySelector('.eyes-open')).opacity")" = "1" ] \
  && pass "eyes stay open in playful mood (the .happy regression)" \
  || fail "playful mood closed the eyes — .happy is pinned to mood again"

say "== five verbs: each completes and releases busy =="
for verb in "_woolResolveTouch(30, 400)" "woolFeed()" "woolWalk()" "woolPlay()" "woolCall()"; do
  J "$ALP.$verb; 'started'" >/dev/null
  ok=""
  for _ in $(seq 1 40); do
    sleep 0.5
    [ "$(J "$ALP._wool.busy")" = "false" ] && { ok=1; break; }
  done
  [ -n "$ok" ] && pass "$verb" || fail "$verb never released busy"
done

say "== zoomies leaves the stash lump =="
J "$ALP.woolZoomies(); 'z'" >/dev/null
for _ in $(seq 1 40); do sleep 0.5; [ "$(J "$ALP._wool.busy")" = "false" ] && break; done
[ "$(J "document.getElementById('ruglump').classList.contains('show')")" = "true" ] \
  && pass "lump shown after zoomies" || fail "no lump after zoomies"
J "$ALP.woolLumpTap(); $ALP.woolLine" | grep -q "nothing lives under this rug" \
  && pass "lump speaks" || fail "lump tap said nothing"

say "== dogsleep refuses with a line (23:00 behavior) =="
J "document.getElementById('wool-scene').classList.add('dogsleep'); $ALP.woolFeed(); $ALP.woolLine" \
  | grep -q "bowl can wait" && pass "asleep cat refuses feed with a line" || fail "no refusal line while asleep"
J "document.getElementById('wool-scene').classList.remove('dogsleep'); 'awake'" >/dev/null

say "== settle-note: partner whisper waits, tap reads it =="
TOKEN=$(J "$ALP.inviteUrl" | sed 's|.*/join/||')
JAR="$WORK/partner.jar"
curl -s -L -c "$JAR" -o /dev/null "http://localhost:$PORT/join/$TOKEN"
curl -s -b "$JAR" -c "$JAR" -X POST "http://localhost:$PORT/api/start" \
  -H 'content-type: application/json' -d '{"display_name":"smokepartner"}' >/dev/null
curl -s -b "$JAR" -X POST "http://localhost:$PORT/api/action" \
  -H 'content-type: application/json' -d '{"type":"message","text":"smoke test whisper"}' >/dev/null
"$B" reload >/dev/null; sleep 2
J "clearInterval($ALP._woolHourTimer); $ALP.onboardingStep=99; 'r'" >/dev/null
[ "$(J "$ALP.woolNotes.length")" = "1" ] && pass "whisper waits as a note" || fail "no settle-note after partner whisper"
J "$ALP.woolNoteTap($ALP.woolNotes[0]); $ALP.woolLine" | grep -q "smoke test whisper" \
  && pass "note reads its text" || fail "note tap did not read the whisper"
sleep 0.5
"$B" reload >/dev/null; sleep 2
[ "$(J "$ALP.woolNotes.length")" = "0" ] && pass "seen persists across reload" || fail "note came back after being read"

say "== console noise check =="
ERRS=$("$B" console --errors 2>/dev/null \
  | grep -v "WebSocket\|join-pending\|UNTRUSTED\|^---\|Failed to load resource\|(no console errors)" | grep -c "error")
[ "$ERRS" = "0" ] && pass "no unexpected console errors" || fail "$ERRS unexpected console errors"

say ""
if [ "$FAILS" = "0" ]; then say "SMOKE PASS"; exit 0; else say "SMOKE FAIL ($FAILS)"; exit 1; fi
