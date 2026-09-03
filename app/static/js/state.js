// Reactive data props for the woolroom Alpine component. Factory (not a
// shared const) so nested object/array literals are fresh per instance.
export function createState() {
  return {
    // UI state
    view: "landing",        // landing | adopt | scene
    busy: false,
    status: "",
    showSettings: false,
    showComposer: false,
    showRecovery: false,
    showMemory: false,
    recoveryCardExpanded: false,
    memory: null,
    pinnedEventIds: [],
    bookmarkAcknowledged: false,
    aliasMap: {},
    aliasDraft: {},
    actingAction: null,
    _actingOriginId: null,
    _pendingActionOrigins: new Set(),
    holdingAction: null,    // "logout" | "lock" — which destructive action is being held
    _holdTimer: null,

    // Data
    user: null,
    pet: null,
    pets: [],               // every room of the household, founding first
    activePetId: null,
    guest: false,           // read-only visitor — no session, watch only
    card: null,
    petCardCache: {},
    _cardLoads: new Set(),
    _cardCacheGeneration: 0,
    voice: null,            // client copy pack (GET /api/voice), set at boot
    packs: null,            // pack-species figure assets (GET /api/packs), set at boot
    adoptionDefaults: {
      primary: { species: "cat", coat: "marmalade" },
      secondary: { species: "cat", coat: "marmalade" },
    },
    quirks: [],
    pickedQuirks: [],
    quirkAudition: null,
    quirkAuditionStatus: "",
    _quirkAuditionTimer: null,
    pickedCoat: "marmalade",
    displayName: "",
    petName: "",
    // the second room: adopt form (adopter) + ceremony (partner)
    showSecondAdopt: false,
    secondName: "",
    secondQuirk: null,
    secondCoat: "marmalade",
    showCeremony: false,
    ceremonyPick: null,
    ceremonyBusy: false,
    _doorTapTimer: null,
    messageText: "",
    inviteUrl: null,
    pendingInvite: null,
    openSignup: false,
    recoveryUrl: null,
    lastResponse: null,
    _lastResponseTimer: null,
    inviteCopied: false,
    _inviteCopiedTimer: null,
    lastMilestone: null,
    _lastMilestoneTimer: null,
    sceneFx: null,
    sceneEvent: null,
    _sceneFxTimer: null,
    wsState: "idle",
    lastRealtimeAt: null,
    localRoomNotes: [],

    // WS
    ws: null,
    wsRetry: 0,

    // Wool room
    animState: "sleeping",
    poseDetail: {},
    sharedTrace: null,
    sharedTraceCue: null,
    partnerTraceCues: [],
    returnCue: null,
    partnerAbsenceMinutes: null,
    partnerArrivedFlash: false,
    _partnerArrivedTimer: null,
    guestNoticeFlash: false,
    _guestNoticeTimer: null,
    currentHour: new Date().getHours() + new Date().getMinutes() / 60,
    _hourTimer: null,
    bootVersion: null,
    freshVersionAvailable: false,
    onboardingStep: 0,
    soundOn: false,
    woolLine: "",
    _woolLineTimer: null,
    woolShelf: [],
    woolPatches: [],
    woolNotes: [],
    woolHearts: [],
    visitorLinger: false,   // keep the guest rendered through the going-home beat
    _visitorArt: null,
    _visitor: null,
    _wool: null,
    _woolHourTimer: null,
    _woolIdleTimer: null,

    // First-session narration, filled from the voice pack at boot
    // (loadVoice). Empty until then — the overlay only shows after the
    // scene paints, by which time the boot fetch has landed.
    onboardingLines: [],
    _onboardingTimer: null,
  };
}
