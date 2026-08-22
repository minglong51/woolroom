"""
Fallback body-language phrasebook. Used when LLM is unavailable or output is rejected.
Pet utterances are 80% body language; this table is the safety net.

Keys map to mood arousal buckets (low/med/high) and valence buckets (grumpy/neutral/content).

The base tables below ARE the builtin cat's voice: loaf, watch, tail-tip,
ears, the warm patch of floor. Quiet, lowercase, body-first — the room
narrates what the body does, never what it means.
"""

from __future__ import annotations

import re
import unicodedata
from random import randrange


# Rotation state: per-cell index of the last line served, so a multi-line cell
# never serves the same line twice in a row. Process-local by design — a
# restart just reshuffles. Keyed by (table, action/spot, arousal, valence).
_last_served: dict[tuple[str, ...], int] = {}


def _pick(phrases: list[str], cell: tuple[str, ...], sequence: int | None) -> str:
    """Seeded pick with an immediate-repeat guard. `sequence` (the event id)
    makes the choice deterministic per event; the guard advances one slot when
    the seed lands on the previously served line."""
    if len(phrases) == 1:
        return phrases[0]
    index = (sequence if sequence is not None else randrange(len(phrases))) % len(phrases)
    if _last_served.get(cell) == index:
        index = (index + 1) % len(phrases)
    _last_served[cell] = index
    return phrases[index]


BODY_LANGUAGE: dict[tuple[str, str], list[str]] = {
    ("low", "grumpy"): [
        "*tucks its face into its shoulder*",
        "*flattens its ears and keeps them there*",
        "*pulls one paw over its eyes*",
    ],
    ("low", "neutral"): [
        "*stretches a paw out, then reels it back in*",
        "*resettles into a tighter loaf*",
        "*slow-blinks at nothing in particular*",
        "*its tail tip taps once, then thinks better of it*",
    ],
    ("low", "content"): [
        "*tucks its nose under one paw*",
        "*goes loose all over, loaf to liquid*",
        "*a long exhale through the nose*",
        "*kneads the rug twice, asleep by the third*",
    ],
    ("med", "grumpy"): [
        "*sits at a diagonal to the whole room*",
        "*grooms one shoulder with sudden focus*",
        "*ears tip sideways and stay tipped*",
    ],
    ("med", "neutral"): [
        "*lifts its chin, whiskers forward*",
        "*tracks the dust mote through the lamplight*",
        "*holds very still, listening-shaped*",
        "*turns its head a few degrees, considering*",
    ],
    ("med", "content"): [
        "*slow-blinks from across the rug*",
        "*stretches its front paws long, chest low*",
        "*a low rumble starts up somewhere in its chest*",
        "*leans its cheek against the nearest warm thing*",
    ],
    ("high", "grumpy"): [
        "*tail lashes; the rest is statue*",
        "*does one fast lap of the room and stops dead*",
        "*crouches low, haunches tight, pupils wide*",
    ],
    ("high", "neutral"): [
        "*ears up, nose working the air*",
        "*stands tall, tail a question mark*",
        "*snaps its head toward a sound only it heard*",
    ],
    ("high", "content"): [
        "*chirrups at the ceiling, pleased with itself*",
        "*tail high, tip curled, on the move*",
        "*does a small happy stomp in place*",
    ],
}

ACTION_LANGUAGE: dict[str, dict[tuple[str, str], list[str]]] = {
    "greet": {
        ("low", "grumpy"): [
            "*stays in loaf form; one ear rotates your way*",
            "*cracks one eye, finds you, closes it again*",
            "*a single whisker twitch of acknowledgment*",
        ],
        ("low", "neutral"): [
            "*raises its head, lowers it again — hello noted*",
            "*yawns in your direction, which counts*",
            "*watches you arrive from the warm patch*",
            "*flicks one ear at the sound of the door*",
        ],
        ("low", "content"): [
            "*uncoils halfway, then decides the rug wins*",
            "*its tail tip curls at you; no eyes were opened*",
            "*a soft chirrup from the floor, eyes still shut*",
            "*rolls its chin up an inch: you may approach*",
        ],
        ("med", "grumpy"): [
            "*stands, turns one slow circle, sits back down facing you*",
            "*starts toward you, then detours past the bowl*",
            "*holds your gaze a beat too long, then grooms*",
        ],
        ("med", "neutral"): [
            "*pads two steps toward you and parks there*",
            "*sits up tall and lets you finish coming in*",
            "*touches its nose to the air near your knee*",
            "*rises, stretches, arrives at half speed*",
        ],
        ("med", "content"): [
            "*weaves one figure-eight around your ankles*",
            "*presses its cheek to your shin and lingers*",
            "*tail goes straight up at the sight of you*",
            "*meets you halfway and leans its whole side in*",
        ],
        ("high", "grumpy"): [
            "*arrives fast, then audits your day*",
            "*does a full circuit of you before committing to hello*",
            "*stops short, tail low, judging the hour you keep*",
        ],
        ("high", "neutral"): [
            "*materializes at your feet without a sound*",
            "*beats you to the door and looks back*",
            "*crosses the room in three soft bounds*",
        ],
        ("high", "content"): [
            "*bunts your knee hard enough to rock you*",
            "*runs up and stands tall against your leg*",
            "*chirps twice before the door is fully open*",
            "*headbutts your hand before you can offer it*",
        ],
    },
    "pet": {
        ("low", "grumpy"): [
            "*one ear goes down; the hand is on probation*",
            "*endures it, gazing at the far wall*",
            "*tucks tighter — a loaf under siege*",
        ],
        ("low", "neutral"): [
            "*accepts the hand like weather*",
            "*holds still, eyes half-open, neither here nor there*",
            "*breathing slows a notch under your palm*",
            "*stays exactly as it was; the hand changes nothing, officially*",
        ],
        ("low", "content"): [
            "*goes heavier under your hand by degrees*",
            "*a rumble starts low and stays there*",
            "*chin tips up without being asked*",
            "*eyes slide shut on the first pass*",
        ],
        ("med", "grumpy"): [
            "*sits through it with a long-suffering profile*",
            "*its skin ripples once under your hand; it refrains*",
            "*permits a fixed number of strokes, then drifts off*",
        ],
        ("med", "neutral"): [
            "*keeps its spine level under the stroke*",
            "*watches the wall while you do that*",
            "*the tail tip keeps a small, separate opinion*",
            "*holds the pose, whiskers at rest*",
        ],
        ("med", "content"): [
            "*tips into the stroke, then catches itself leaning*",
            "*the purr catches you both by surprise*",
            "*shoulder slides into your palm on the second pass*",
            "*closes its eyes and tilts the good side up*",
        ],
        ("high", "grumpy"): [
            "*melts away from the hand, reconsiders, returns*",
            "*ducks once, comes back with conditions*",
            "*sidesteps, thinks it over, offers the other side*",
        ],
        ("high", "neutral"): [
            "*circles once under your hand like a small planet*",
            "*backs into your palm and parks there*",
            "*keeps moving so the petting has to follow*",
        ],
        ("high", "content"): [
            "*arches hard into the stroke, tail high*",
            "*climbs into your hand chest-first*",
            "*kneads the rug the entire time*",
            "*presses its skull into your knuckles*",
        ],
    },
    "feed": {
        ("low", "grumpy"): [
            "*sniffs the bowl, sighs through its nose, eats*",
            "*eats slowly, making a point no one asked for*",
            "*carries three bites to the far corner, one at a time*",
        ],
        ("low", "neutral"): [
            "*eats in silence, in no hurry*",
            "*works the bowl from edge to middle*",
            "*pauses once to listen, then resumes*",
            "*finishes half before looking up*",
        ],
        ("low", "content"): [
            "*eats, then washes one paw with great care*",
            "*finishes and sits by the bowl a while, full*",
            "*chews with its eyes closed, tail wrapped*",
            "*cleans one whisker at a time afterward*",
        ],
        ("med", "grumpy"): [
            "*audits the bowl, audits you, then eats*",
            "*gives the bowl a long inspection before starting*",
            "*eats with its back half-turned to you*",
        ],
        ("med", "neutral"): [
            "*goes to the bowl without comment*",
            "*eats at an even clip, checking the room once*",
            "*starts at the rim and works inward*",
            "*one ear on the room, the rest on the bowl*",
        ],
        ("med", "content"): [
            "*purrs once mid-meal, then returns to business*",
            "*tail tip curls higher with each bite*",
            "*finishes, licks the rim, looks up mildly*",
            "*eats like the meal was its idea all along*",
        ],
        ("high", "grumpy"): [
            "*hits the bowl at speed, offended it was late*",
            "*shoves the bowl an inch with its nose first*",
            "*gulps, coughs once, glares at no one, continues*",
        ],
        ("high", "neutral"): [
            "*is at the bowl before the kibble settles*",
            "*empties it, then searches the floor for witnesses*",
            "*clears the bowl, then checks it again, in case*",
        ],
        ("high", "content"): [
            "*chirps once with a full mouth, unbothered*",
            "*finishes fast and sits up, bright-eyed*",
            "*licks the bowl with theatrical thoroughness*",
            "*eats like this is the best idea anyone has had*",
        ],
    },
    "walk": {
        ("low", "grumpy"): [
            "*watches you hold the door and takes its time anyway*",
            "*lies down flat the moment you reach for it*",
            "*sniffs the threshold and declines to hurry*",
        ],
        ("low", "neutral"): [
            "*gets up in stages, but gets up*",
            "*stretches fully first; the door can wait*",
            "*pads out behind you at its own pace*",
            "*inspects the doorframe on the way through*",
        ],
        ("low", "content"): [
            "*unfolds from the warm patch and follows, easy*",
            "*stops to smell the door's bottom edge, twice*",
            "*takes the stairs one careful step at a time*",
            "*ambles out like it knows the route by heart*",
        ],
        ("med", "grumpy"): [
            "*plants itself at the threshold for one long moment*",
            "*comes along, but a half-step behind, on record*",
            "*stares back at the warm patch before leaving*",
        ],
        ("med", "neutral"): [
            "*steps out with its tail at half-mast*",
            "*walks the route like a patrol it invented*",
            "*checks back at you once per flight of stairs*",
            "*pauses at the landing to read the air*",
        ],
        ("med", "content"): [
            "*trots ahead a few steps, tail up, then waits*",
            "*stops for one good smell, catches up at a trot*",
            "*moves out like the day finally started*",
            "*shoulders the door open like it pays rent*",
        ],
        ("high", "grumpy"): [
            "*darts out, stops cold, stares at a leaf for a while*",
            "*bolts three steps, then refuses the fourth*",
            "*huffs at the open door, then goes through it anyway*",
        ],
        ("high", "neutral"): [
            "*is through the door before it finishes opening*",
            "*scans the whole landing from the threshold*",
            "*practically vibrates at the door seam*",
        ],
        ("high", "content"): [
            "*pops straight up off all fours at the door*",
            "*does a small sprint, returns, sprints again*",
            "*takes the first flight of stairs at a gallop*",
            "*tail high, trotting, extremely ready*",
        ],
    },
    "call": {
        ("low", "grumpy"): [
            "*one ear swivels. the rest is a closed meeting*",
            "*its tail tip taps once — that is the reply*",
            "*the name lands; the cat files it away*",
        ],
        ("low", "neutral"): [
            "*one eye opens, finds you, considers closing*",
            "*answers with a slow blink from across the room*",
            "*gets up eventually, still half in the nap*",
            "*lifts its head, does the math, rises*",
        ],
        ("low", "content"): [
            "*a small trill answers from the warm patch*",
            "*stretches long, then drifts over at half speed*",
            "*arrives, then parks itself on your feet*",
            "*comes softly, rumble already going*",
        ],
        ("med", "grumpy"): [
            "*lets the second call happen, then comes*",
            "*stops at the midpoint and waits to be negotiated with*",
            "*arrives and sits at a slight angle to you*",
        ],
        ("med", "neutral"): [
            "*pads in from wherever it was, ears up*",
            "*comes halfway, then expects you to close the gap*",
            "*arrives, sits, waits for the reason*",
            "*rounds the corner at a measured trot*",
        ],
        ("med", "content"): [
            "*appears beside you before the second syllable*",
            "*trots in, tail a question mark*",
            "*comes all the way over and leans into your leg*",
            "*arrives purring, like it was already on its way*",
        ],
        ("high", "grumpy"): [
            "*whips around, catches itself, saunters over*",
            "*covers most of the distance, then stops one step too far away*",
            "*comes too quickly to pretend it wasn't listening*",
        ],
        ("high", "neutral"): [
            "*snaps around at its name, ears tall*",
            "*skids in, all ears and readiness*",
            "*crosses the room in a few soft thuds*",
        ],
        ("high", "content"): [
            "*launches off the rug and lands at your feet*",
            "*comes at a gallop, chirping the whole way*",
            "*arrives at speed and bumps your shin hard*",
            "*runs over like this was the plan all day*",
        ],
    },
    "message": {
        ("low", "grumpy"): [
            "*hears the ping and does not lift its head*",
            "*one ear registers the phone; nothing else votes*",
            "*turns its back to the screen, deliberately*",
        ],
        ("low", "neutral"): [
            "*watches the screen light up from inside the loaf*",
            "*regards the glow briefly, then dims again*",
            "*lowers its chin again while you read*",
            "*its tail tip follows your typing, barely*",
        ],
        ("low", "content"): [
            "*scoots an inch toward the glow and stays soft*",
            "*pillows its cheek beside your typing hand*",
            "*rumbles along, reading none of it, keeping you company*",
            "*dozes to the sound of your thumbs*",
        ],
        ("med", "grumpy"): [
            "*regards the phone as a personal rival*",
            "*sits just outside the screen's light, watching it*",
            "*looks from the phone to you, unimpressed by both*",
        ],
        ("med", "neutral"): [
            "*watches the letters happen, head tipped*",
            "*lifts its head for the ping, lowers it after*",
            "*observes the glow from its end of the rug*",
            "*keeps one ear on the phone, one on the room*",
        ],
        ("med", "content"): [
            "*tucks itself along your side as the message lands*",
            "*parks its chin on the table edge near the phone*",
            "*stays awake for the entire exchange*",
            "*curls in close, tail over your wrist*",
        ],
        ("high", "grumpy"): [
            "*orbits the phone once, radiating opinion*",
            "*waves a paw at the screen light, misses on purpose*",
            "*stares at the screen like it made a sound first*",
        ],
        ("high", "neutral"): [
            "*snaps to the ping before you do*",
            "*stands over the phone, tail mid-sway*",
            "*arrives at the glow like it was summoned*",
        ],
        ("high", "content"): [
            "*installs itself in the middle of the conversation*",
            "*inserts its head between you and the screen*",
            "*sits on the warm spot where the phone was*",
            "*supervises the reply, tail tip going*",
        ],
    },
    "play": {
        ("low", "grumpy"): [
            "*watches the toy go by and stays a loaf*",
            "*only the pupils chase it*",
            "*declines, from a lying-down position*",
        ],
        ("low", "neutral"): [
            "*pats it once, gently, and considers the game done*",
            "*chases at a walking pace, once*",
            "*bats it under the rug and calls it finished*",
            "*plays a single sedate rally, then stops*",
        ],
        ("low", "content"): [
            "*holds the toy still with one paw, content*",
            "*nudges it an inch, tail tip going*",
            "*lies down with the toy between its paws*",
            "*carries it two steps and sets it down softly*",
        ],
        ("med", "grumpy"): [
            "*participates once, then parks on the toy*",
            "*wins, then loses interest, in that order*",
            "*chases twice, then remembers its dignity*",
        ],
        ("med", "neutral"): [
            "*chases, pauses, chases again, measured*",
            "*returns it as far as the rug's edge*",
            "*waits for the next throw like a scheduled service*",
            "*stalks it properly, wiggle and all*",
        ],
        ("med", "content"): [
            "*pounces, then looks up, pleased*",
            "*leaves it at your feet, pointedly available*",
            "*does the butt-wiggle before every pounce, on time*",
            "*catches it and parades one lap of the rug*",
        ],
        ("high", "grumpy"): [
            "*steals the toy and moves the game elsewhere*",
            "*grabs the toy and kicks it with both back feet*",
            "*wins the game and immediately changes the rules*",
        ],
        ("high", "neutral"): [
            "*rips after it, slides past, inspects a paw*",
            "*launches, misses, plays it off as grooming*",
            "*zooms past it once, corrects with style*",
        ],
        ("high", "content"): [
            "*erupts into zoomies; the toy was the excuse*",
            "*spins so fast the toy briefly escapes*",
            "*catches it, drops it, asks for more with its whole body*",
            "*backflips off the rug, sticks the landing*",
        ],
    },
}


def bucket_arousal(value: int) -> str:
    if value < 33:
        return "low"
    if value < 67:
        return "med"
    return "high"


def bucket_valence(value: int) -> str:
    if value < 33:
        return "grumpy"
    if value < 67:
        return "neutral"
    return "content"


# Tiny pet-noise utterances. Used only for `message` action, ~50% of the time,
# so the cat actually answers the message instead of always narrating itself
# looking at a phone. Kept very short — the product principle is "quiet but
# present", not chatty. Every valence bucket must keep at least one speakable
# (non-asterisk) line: the utterance path filters body lines out and indexes
# what remains.
MESSAGE_TINY_UTTERANCES: dict[str, list[str]] = {
    "grumpy": [
        "mrrt.",
        "hff.",
        "mrrr.",
        "...mh.",
        "*one slow, unimpressed blink*",
    ],
    "neutral": [
        "mrrp?",
        "mew?",
        "prrp?",
        "*soft trill, questioning*",
        "*nose lifts a fraction*",
    ],
    "content": [
        "mew.",
        "prrp.",
        "mrrp mrrp.",
        "*a warm little chirp*",
        "*happy trill, tail tip up*",
    ],
}

MESSAGE_CONTEXT_LANGUAGE: dict[str, dict[str, list[str]]] = {
    "greeting": {
        "grumpy": [
            "*one ear turns your way; the rest declines*",
            "*acknowledges the hello with half a blink*",
        ],
        "neutral": [
            "*looks up and shifts over, making room*",
            "*closes the distance to exactly hello range*",
        ],
        "content": [
            "*the tail gets there first*",
            "*crosses straight to you and leans in*",
        ],
    },
    "affection": {
        "grumpy": [
            "*holds very still, which is its version of melting*",
            "*closes half the distance, denying everything*",
        ],
        "neutral": [
            "*meets your eyes, then bumps your hand once*",
            "*lays its chin across your fingers and leaves it there*",
        ],
        "content": [
            "*melts into you with its eyes shut*",
            "*slow-blinks at you, twice, on purpose*",
        ],
    },
    "rest": {
        "grumpy": [
            "*tucks in with one last look at the room*",
            "*lies down as though the suggestion was its own*",
        ],
        "neutral": [
            "*loafs up within arm's reach, eyes going*",
            "*tucks its paws in and dims, slowly*",
        ],
        "content": [
            "*molds itself to your side until the room goes soft*",
            "*sighs once, warm, and settles for good*",
        ],
    },
    "concern": {
        "grumpy": [
            "*stations itself nearby and keeps quiet watch*",
            "*sits closer than usual, saying nothing*",
        ],
        "neutral": [
            "*comes over and studies your face a long moment*",
            "*rests its chin by your hand and keeps watch*",
        ],
        "content": [
            "*presses close and stays pressed*",
            "*parks itself on your feet and refuses to budge*",
        ],
    },
}

_CONCERN_MESSAGES = {
    "i'm sad",
    "i am sad",
    "i'm upset",
    "i am upset",
    "i'm not okay",
    "i'm not ok",
    "i feel awful",
    "i feel terrible",
    "i had a bad day",
    "bad day",
    "i'm stressed",
    "i am stressed",
    "i'm anxious",
    "i'm lonely",
    "i feel lonely",
    "i want to cry",
    "i don't feel well",
    "i feel sick",
    "i'm exhausted",
    "i'm overwhelmed",
    "i'm worried",
    "i'm scared",
    "我难过",
    "我很难过",
    "我不开心",
    "我心情不好",
    "我难受",
    "我想哭",
    "我好累",
    "我压力好大",
    "我很孤单",
    "我難過",
    "我很難過",
    "我不開心",
    "我難受",
    "我壓力好大",
    "我很孤單",
}
_REST_MESSAGES = {
    "go to sleep",
    "go to bed",
    "time for bed",
    "bedtime",
    "get some rest",
    "take a nap",
    "nap time",
    "rest now",
    "have a rest",
    "lie down",
    "sleep well",
    "good night",
    "睡觉",
    "去睡觉",
    "睡吧",
    "休息",
    "休息一下",
    "晚安",
    "睡覺",
    "去睡覺",
}
_AFFECTION_MESSAGES = {
    "i love you",
    "love you",
    "good dog",
    "good boy",
    "good girl",
    "you're the best",
    "you are the best",
    "i missed you",
    "you're so cute",
    "you are so cute",
    "good job",
    "well done",
    "i like you",
    "you're a good dog",
    "thank you",
    "thanks",
    "我爱你",
    "爱你",
    "好可爱",
    "真可爱",
    "乖",
    "真乖",
    "好棒",
    "谢谢",
    "我愛你",
    "愛你",
    "好可愛",
    "真可愛",
    "謝謝",
}
_GREETING_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "hiya",
    "good morning",
    "morning",
    "good afternoon",
    "good evening",
    "how are you",
    "how are you doing",
    "what's up",
    "你好",
    "早",
    "早上好",
    "早安",
    "下午好",
    "晚上好",
    "在吗",
    "哈喽",
    "在嗎",
    "哈囉",
}


def classify_message(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    normalized = normalized.replace("’", "'")[:200]
    normalized = re.sub(r"[^\w']+", " ", normalized).strip()
    if normalized in _CONCERN_MESSAGES:
        return "concern"
    if normalized in _REST_MESSAGES:
        return "rest"
    if normalized in _AFFECTION_MESSAGES:
        return "affection"
    if normalized in _GREETING_MESSAGES:
        return "greeting"
    return "neutral"


def contextual_message_phrase(
    arousal: int,
    valence: int,
    user_text: str | None,
    event_id: int | None,
    *,
    allow_utterance: bool,
    species: str = "cat",
) -> str:
    intent = classify_message(user_text)
    mood = bucket_valence(valence)
    sequence = event_id if event_id is not None else 0
    if allow_utterance and event_id is not None and event_id > 0 and event_id % 5 == 0:
        overlay = SPECIES_PHRASE_OVERLAYS.get(species)
        tiny = overlay["tiny"] if overlay else MESSAGE_TINY_UTTERANCES
        utterances = [
            phrase
            for phrase in tiny[mood]
            if not (phrase.startswith("*") and phrase.endswith("*"))
        ]
        return utterances[(event_id // 5) % len(utterances)]
    if intent == "neutral":
        key = (bucket_arousal(arousal), mood)
        phrases = ACTION_LANGUAGE["message"][key]
    else:
        phrases = MESSAGE_CONTEXT_LANGUAGE[intent][mood]
    return phrases[sequence % len(phrases)]


PET_SPOT_LANGUAGE: dict[str, dict[tuple[str, str], list[str]]] = {
    "head": {
        ("low", "grumpy"): [
            "*both ears fold; it stays anyway*",
            "*holds its head very still, enduring the affection*",
            "*breathes out hard through its nose, enduring*",
        ],
        ("low", "neutral"): [
            "*head grows heavier in your palm by degrees*",
            "*eyes close under the hand, briefly*",
            "*chin lifts a fraction, then stops*",
        ],
        ("low", "content"): [
            "*head sinks lower the longer you scratch*",
            "*leans its whole skull into your fingers*",
            "*breathes out slow, forehead to palm*",
        ],
        ("med", "grumpy"): [
            "*suffers the scratch with its eyes open*",
            "*keeps one ear folded in protest*",
            "*tracks your other hand the whole time*",
        ],
        ("med", "neutral"): [
            "*angles its head into your palm*",
            "*steers your fingers to the place behind the ear*",
            "*head follows your fingers, hopeful*",
        ],
        ("med", "content"): [
            "*works its forehead into the scratch with intent*",
            "*eyes go soft; the purr switches on*",
            "*leans its cheek across your knuckles*",
        ],
        ("high", "grumpy"): [
            "*ducks, returns, allows it after all*",
            "*shakes its head once, then comes back for more*",
            "*ears flat, head forward — a treaty*",
        ],
        ("high", "neutral"): [
            "*meets your hand halfway, ears tall*",
            "*surfaces under your palm from below*",
            "*bumps your wrist with its brow*",
        ],
        ("high", "content"): [
            "*bunts your palm hard enough to count as a hug*",
            "*plants its entire head in the scratch and stays*",
            "*headbutts twice, rumbling the whole time*",
        ],
    },
    "tail": {
        ("low", "grumpy"): [
            "*the tail goes still, deliberately*",
            "*the tail draws itself in, away from the hand*",
            "*one slow, offended curl*",
        ],
        ("low", "neutral"): [
            "*the tail lifts a fraction, resettles*",
            "*a single lazy curl, then stillness*",
            "*the tail tip stirs, uncommitted*",
        ],
        ("low", "content"): [
            "*the tail sways like slow grass*",
            "*tail tip curls, uncurls, curls again*",
            "*a drowsy question mark behind it*",
        ],
        ("med", "grumpy"): [
            "*the tail whips once: not the tail*",
            "*the tail lashes low against the floor*",
            "*it moves its tail out of reach and holds your gaze*",
        ],
        ("med", "neutral"): [
            "*the tail writes one slow loop in the air*",
            "*it turns to check what the tail is doing*",
            "*one thoughtful curl, then stillness*",
        ],
        ("med", "content"): [
            "*the tail starts moving before the head turns*",
            "*tail tip dances while the face stays calm*",
            "*the tail gives the game away entirely*",
        ],
        ("high", "grumpy"): [
            "*the tail puffs to twice its width*",
            "*one hard lash, then it relocates the tail*",
            "*spins to see what touched it, offended*",
        ],
        ("high", "neutral"): [
            "*the tail bolts upright before the rest reacts*",
            "*a fast thrash, then it pretends composure*",
            "*the tail flags high and stays there*",
        ],
        ("high", "content"): [
            "*the tail waves like a flag in a parade*",
            "*tail straight up, tip hooked, delighted*",
            "*the whole back end sways with it*",
        ],
    },
    "ear": {
        ("low", "grumpy"): [
            "*the ear folds flat and stays folded*",
            "*one ear pins back; the other can't be bothered*",
            "*the ear slides out from under your thumb*",
        ],
        ("low", "neutral"): [
            "*the ear flicks once; the rest of it sleeps*",
            "*the ear softens under your thumb, slowly*",
            "*one ear tips toward your fingers*",
        ],
        ("low", "content"): [
            "*leans the whole ear into the rub*",
            "*the ear goes soft, then the eyelids follow*",
            "*offers the ear like a door handle*",
        ],
        ("med", "grumpy"): [
            "*shakes its head free, then reconsiders*",
            "*flicks the ear loose, twice*",
            "*ears sideways, enduring*",
        ],
        ("med", "neutral"): [
            "*the ear tracks your finger, curious*",
            "*tips the ear sideways, adjusting your angle*",
            "*presents the ear like a document*",
        ],
        ("med", "content"): [
            "*swaps in the other ear, unprompted*",
            "*pays for the ear rub with a slow blink*",
            "*the ear melts; the head follows it down*",
        ],
        ("high", "grumpy"): [
            "*shakes off the hand, then puts its head back in it*",
            "*ears pin sharp, then loosen by half*",
            "*ducks out, returns a second later*",
        ],
        ("high", "neutral"): [
            "*the ears keep scanning the room mid-rub*",
            "*one ear leaves to check a sound, then comes back*",
            "*ears up and busy while you rub*",
        ],
        ("high", "content"): [
            "*a tiny shiver runs down to the shoulders*",
            "*leans so far into the ear rub it tips over*",
            "*the ear folds soft; the purr doubles*",
        ],
    },
    "belly": {
        ("low", "grumpy"): [
            "*tucks a paw over the belly, closing the matter*",
            "*curls tighter; the belly is not on offer*",
            "*rolls half an inch away from the suggestion*",
        ],
        ("low", "neutral"): [
            "*lengthens until the ribs show, briefly*",
            "*the belly shows; one eye stays on duty*",
            "*offers a narrow crescent of belly*",
        ],
        ("low", "content"): [
            "*goes down sideways, paws adrift*",
            "*belly up, asleep before you finish the thought*",
            "*goes liquid, all four paws loose*",
        ],
        ("med", "grumpy"): [
            "*permits exactly one pass over the belly*",
            "*grabs your hand with all four paws — gently, mostly*",
            "*rolls away mid-rub, decision reversed*",
        ],
        ("med", "neutral"): [
            "*shows half the belly, withholding comment*",
            "*flips, then studies the ceiling like it planned this*",
            "*the belly is out; the escape plan is loaded*",
        ],
        ("med", "content"): [
            "*rolls over with hope in its eyes*",
            "*paws fold up; the belly is fully on the table*",
            "*wriggles onto its back, legs everywhere*",
        ],
        ("high", "grumpy"): [
            "*is upright before your hand arrives*",
            "*twists away — too much, not now*",
            "*hands you a paw to hold instead*",
        ],
        ("high", "neutral"): [
            "*drops sideways with a soft thump*",
            "*flops down, paws at four different angles*",
            "*drops, rolls, belly up for exactly four seconds*",
        ],
        ("high", "content"): [
            "*is on its back before the hand is halfway there*",
            "*four paws skyward, not a care in the room*",
            "*rolls over like the rug pulled it down*",
        ],
    },
    "body": {
        ("low", "grumpy"): [
            "*bears the long pass, eyes fixed on the wall*",
            "*its skin shivers once under the pass*",
            "*moves its ribs one inch to the left of the hand*",
        ],
        ("low", "neutral"): [
            "*stays loose-limbed while the hand travels*",
            "*breathing slows along the length of the stroke*",
            "*the flank rises and falls under your palm*",
        ],
        ("low", "content"): [
            "*lists sideways into the stroking hand*",
            "*grows an inch longer with every pass*",
            "*is asleep somewhere around the third pass*",
        ],
        ("med", "grumpy"): [
            "*suffers each pass with its eyes open*",
            "*steps once, repositions, allows it again*",
            "*only the tail refuses to participate*",
        ],
        ("med", "neutral"): [
            "*holds its ground for the whole length of the back*",
            "*plants all four feet while the hand travels*",
            "*moves with the strokes, an even keel*",
        ],
        ("med", "content"): [
            "*leans its ribs into the stroke's path*",
            "*curves its spine up to meet your hand*",
            "*leans a little heavier with each pass*",
        ],
        ("high", "grumpy"): [
            "*flows out from under the hand, then back again*",
            "*steps out, steps back in, offers the other side*",
            "*ripples the skin along the pass, unimpressed*",
        ],
        ("high", "neutral"): [
            "*shifts foot to foot while the hand keeps up*",
            "*turns to keep the good side toward you*",
            "*orbits once beneath the stroking hand*",
        ],
        ("high", "content"): [
            "*spine rises to meet every pass*",
            "*presses its whole side into the long passes*",
            "*leans so hard you become furniture*",
        ],
    },
}


# Species phrase overlays, keyed by species id (the registry's
# `phrase_overlay` key in app/data/species.py points here). Empty builtin:
# the shared base tables ARE the builtin cat's voice. Content packs register
# their overlays here at boot (app/packs/loader.py); each overlay's tables
# are consulted cell-by-cell BEFORE the shared ones, with fall-through.
SPECIES_PHRASE_OVERLAYS: dict[str, dict[str, dict]] = {}


def fallback_phrase(
    arousal: int,
    valence: int,
    action: str | None = None,
    *,
    spot: str | None = None,
    event_id: int | None = None,
    species: str = "cat",
) -> str:
    key = (bucket_arousal(arousal), bucket_valence(valence))
    overlay = SPECIES_PHRASE_OVERLAYS.get(species)
    # Spot-specific pet variants when caller indicated where the touch landed.
    if action == "pet" and spot and spot in PET_SPOT_LANGUAGE:
        if overlay:
            phrases = overlay["spot"].get(spot, {}).get(key)
            if phrases:
                return _pick(phrases, (f"{species}spot", spot, *key), event_id)
        phrases = PET_SPOT_LANGUAGE[spot].get(key)
        if phrases:
            return _pick(phrases, ("spot", spot, *key), event_id)
    # Messages get a small chance of a tiny noise reply instead of pure body
    # language — so typing "hi" can earn a "mrrp?" once in a while.
    if action == "message":
        from random import random

        if random() < 0.5:
            tiny = overlay["tiny"] if overlay else MESSAGE_TINY_UTTERANCES
            return _pick(
                tiny[bucket_valence(valence)],
                ("tiny", bucket_valence(valence), species if overlay else "cat"),
                event_id,
            )
    if action and action in ACTION_LANGUAGE:
        if overlay:
            phrases = overlay["action"].get(action, {}).get(key)
            if phrases:
                return _pick(phrases, (f"{species}action", action, *key), event_id)
        phrases = ACTION_LANGUAGE[action].get(key)
        if phrases:
            return _pick(phrases, ("action", action, *key), event_id)
    if overlay:
        phrases = overlay["body"].get(key)
        if phrases:
            return _pick(phrases, (f"{species}body", *key), event_id)
    return _pick(BODY_LANGUAGE[key], ("body", *key), event_id)
