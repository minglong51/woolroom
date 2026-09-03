// The pet figures, as SVG fragment strings.
//
// Why strings and not markup in index.html: <template> doesn't parse inside
// <svg>, so Alpine's x-if can't swap species. The figure is static markup
// (every dynamic bit is a CSS class on #wool-scene or a JS-written transform
// on #dogmove/#dog-gait), so x-html injecting a string is the whole trick —
// one figure in the DOM at a time, ids never collide, and the wool rig
// (.breath/.squishg/.earg/.tailg/#dog-eyes) works unchanged for any species
// because the class contract below is identical for all of them.
//
// Visitor variant: a playdate guest renders the same figure without the
// singleton ids (the resident owns #dog-eyes), tinted by its own palette.

// Undyed-wool palettes, mirrored from the data-coat rules in style.css.
// A visitor carries these inline so its coat never fights the room's.
// Pack species bring their own palettes over GET /api/packs (this.packs).
const PALETTES = {
  cat: {
    tuxedo: { body: "#3f3833", belly: "#f4ead6", point: "#2e2824" },
    marmalade: { body: "#d98f52", belly: "#f6ecda", point: "#c07a40" },
    ash: { body: "#8f8a82", belly: "#e6e0d2", point: "#6e675e" },
  },
};

export function paletteFor(species, coat, packs) {
  const table = packs?.[species]?.palettes || PALETTES[species] || PALETTES.cat;
  return table[coat] || Object.values(table)[0];
}

export function packsForPet(card, pet, packs) {
  if (!card || !pet || card.species !== pet.species || card.coat !== pet.coat) return packs;
  return {
    [card.species]: {
      svg: card.svg,
      palettes: { [card.coat]: card.palette },
      geometry: card.geometry,
      pronoun: card.pronoun,
    },
  };
}

function catArt() {
  // The builtin figure: a loafing window cat. Flat undyed-wool shapes, one
  // soft contact shadow, minimal dot features. The rig class contract
  // (.tailg/.headg/.earg/#dog-eyes/.brushstreak/.touchfibers/.paw-dream/
  // .coat/.cream/.point/.dog-contact) is what the wool rig animates — the
  // class names are historical, the contract is not. The face is a flatter,
  // wider mask than a muzzle — no brow dots, no snout — and the features
  // stay on the cream face ground so a dark coat keeps its expression.
  return `
    <ellipse class="dog-contact" cx="200" cy="453" rx="48" ry="7.5" fill="rgba(90,72,54,.28)"/>
    <!-- the wrap-around tail: rooted behind the loaf at the tailg pivot
         (252,406), curling down the right side, tip resting in front -->
    <g class="tailg">
      <path d="M244 398 C262 402 272 416 269 434 C266 449 254 455 240 454 L232 453 C226 452 226 445 233 444 L244 443 C253 442 258 438 257 431 C256 420 248 412 240 410 Z" fill="#c07a40" class="point"/>
    </g>
    <!-- the loaf: paws tucked, two cream tips peeking at the base -->
    <path d="M200 318 c34 0 53 26 57 62 c3 30 -8 66 -57 66 c-49 0 -60 -36 -57 -66 c4 -36 23 -62 57 -62 Z" fill="#d98f52" class="coat dog-body"/>
    <g class="brushstreak" aria-hidden="true">
      <path d="M158 390 q-10 9 -12 22" />
      <path d="M169 396 q-11 10 -13 24" />
      <path d="M180 402 q-12 11 -14 25" />
    </g>
    <ellipse cx="200" cy="395" rx="24" ry="33" fill="#f6ecda" class="cream dog-chest"/>
    <ellipse cx="186" cy="440" rx="10" ry="6" fill="#f6ecda" class="cream"/>
    <ellipse cx="214" cy="440" rx="10" ry="6" fill="#f6ecda" class="cream"/>
    <g class="paw-dream">
      <ellipse cx="172" cy="446" rx="9" ry="4.5" fill="#f6ecda" class="cream"/>
    </g>
    <g class="headg">
      <!-- a wide flat skull merged into the loaf top; the cream face mask is
           higher and wider than a muzzle ellipse -->
      <ellipse cx="200" cy="330" rx="46" ry="30" fill="#d98f52" class="coat"/>
      <g class="earg">
        <!-- tall triangles, tips leaning outward — the first thing that reads
             "cat" at 60px -->
        <path d="M160 322 L151 268 L192 306 Z" fill="#c07a40" class="point"/>
        <path d="M164 310 L157 276 L185 300 Z" fill="#f6ecda" class="cream"/>
        <path d="M240 322 L249 268 L208 306 Z" fill="#c07a40" class="point"/>
        <path d="M236 310 L243 276 L215 300 Z" fill="#f6ecda" class="cream"/>
      </g>
      <ellipse cx="200" cy="340" rx="35" ry="22" fill="#f6ecda" class="cream"/>
      <g id="dog-eyes">
        <g class="eyes-open">
          <!-- vertical ovals, wider-set than round dots -->
          <ellipse cx="182" cy="336" rx="4.4" ry="6" fill="#3a2c1e"/>
          <ellipse cx="218" cy="336" rx="4.4" ry="6" fill="#3a2c1e"/>
        </g>
        <g class="eyes-happy">
          <path d="M175 336 q7 4.5 14 0 M211 336 q7 4.5 14 0" stroke="#3a2c1e" stroke-width="2.6" fill="none" stroke-linecap="round"/>
        </g>
        <g class="eyes-side">
          <ellipse cx="176" cy="337" rx="4" ry="5.4" fill="#3a2c1e"/>
          <ellipse cx="212" cy="337" rx="4" ry="5.4" fill="#3a2c1e"/>
          <path d="M170 330.5 q6 -1.5 12 -.5 M206 330 q6 -.5 12 1.5" stroke="#3a2c1e" stroke-width="1.8" fill="none" stroke-linecap="round"/>
        </g>
        <g class="nap-eyes">
          <path d="M175 337 q7 3 14 0" stroke="#3a2c1e" stroke-width="2.4" fill="none" stroke-linecap="round"/>
          <path d="M211 337 q7 3 14 0" stroke="#3a2c1e" stroke-width="2.4" fill="none" stroke-linecap="round"/>
        </g>
        <g class="one-eye-eyes">
          <path d="M175 337 q7 3 14 0" stroke="#3a2c1e" stroke-width="2.4" fill="none" stroke-linecap="round"/>
          <circle cx="218" cy="337" r="2.6" fill="#3a2c1e"/>
        </g>
      </g>
      <!-- tiny inverted-triangle nose + the two-lobe cat mouth; no blush, no
           whiskers — the ears and the wrap tail carry the silhouette -->
      <path d="M195.8 347.2 h8.4 l-4.2 4.6 Z" fill="#3a2c1e"/>
      <path d="M200 351.8 v2.4 M193 355.6 q3.5 3.4 7 0.4 M200 356 q3.5 3.4 7 0" stroke="#3a2c1e" stroke-width="1.6" fill="none" stroke-linecap="round"/>
    </g>
    <g class="touchfibers" aria-hidden="true">
      <path d="M150 332 l-10 -4 M150 346 l-12 1 M250 332 l10 -4 M250 346 l12 1"/>
      <path d="M146 402 l-10 7 M254 402 l10 7"/>
    </g>
    <title>a loafing cat, watching</title>`;
}

// The visitor is drawn from the same art minus the singleton ids — the
// resident pet owns #dog-eyes and the gaze binding.
function deidentify(svg) {
  return svg.replace('id="dog-eyes"', 'class="pet-eyes-visitor"');
}

// Touch hitboxes, in the 400×520 scene coordinates wool.js's pointer math
// produces. The figure defines its own zones — it is the art that knows
// where its ears are. Pack species bring their geometry over GET /api/packs;
// a species in neither table falls back to the cat's frame.
export const SPECIES_GEOMETRY = {
  cat: {
    earBelow: 313,
    headBelow: 368,
    tail: { yAbove: 398, xAbove: 238 },
    belly: { yAbove: 398, xAbove: 180, xBelow: 220 },
  },
};

export function touchZoneFor(species, x, y, packs) {
  const g = packs?.[species]?.geometry || SPECIES_GEOMETRY[species] || SPECIES_GEOMETRY.cat;
  if (y < g.earBelow) return "ear";
  if (y < g.headBelow) return "head";
  if (y > g.tail.yAbove && x > g.tail.xAbove) return "tail";
  if (y > g.belly.yAbove && x > g.belly.xAbove && x < g.belly.xBelow) return "belly";
  return "body";
}

export function figureSvg(species, { visitor = false, packs = null } = {}) {
  // Builtin art for cat; a pack species renders its sanitized pack figure;
  // anything else keeps the cat fallback. Pack art carries the same class
  // contract (.tailg/.headg/.earg/#dog-eyes/…), so the wool rig animates it
  // unchanged — and the same singleton-id rule, so the visitor deidentify
  // works on it verbatim.
  const art = packs?.[species]?.svg || catArt();
  return visitor ? deidentify(art) : art;
}

// A playdate guest, wrapped in its own coat palette so an ash cat can visit
// a tuxedo cat without either wool bleeding into the other.
export function visitorMarkup(species, coat, packs, card = null, pet = null) {
  const resolvedPacks = packsForPet(card, pet, packs);
  const p = paletteFor(species, coat, resolvedPacks);
  return (
    `<g style="--dog-body:${p.body}; --dog-belly:${p.belly}; --dog-point:${p.point}">`
    + figureSvg(species, { visitor: true, packs: resolvedPacks })
    + "</g>"
  );
}

// Alpine method group (merged in app.js like every other js/ module).
// Keeping the figure methods here — not imported INTO wool.js — is what
// lets every module stay a depth-1 import of app.js: one import-map rule
// each, all versioned and immutable, no early-resolution warning.
// Pack-species resolution happens here too: the pure helpers above take the
// packs map as an argument; these methods hand them this.packs (the boot
// /api/packs fetch, null when the deploy carries no packs).
export const figureMethods = {
  petFigureSvg() {
    const packs = packsForPet(this.petCardFor?.(this.pet), this.pet, this.packs);
    return figureSvg(this.pet?.species || "cat", { packs });
  },
  figureCoatStyle() {
    // Pack species carry no data-coat rules in style.css (those are written
    // per builtin coat), so their scene palette vars arrive inline from the
    // pack's own palettes — same mechanism visitorMarkup wraps a guest in.
    // Builtin cat (and the unknown-species cat fallback) return "": the
    // data-coat rules keep owning their recolor, unchanged.
    const species = this.pet?.species;
    const card = this.petCardFor?.(this.pet) || null;
    const packs = packsForPet(card, this.pet, this.packs);
    if (!species || (!card && PALETTES[species]) || !packs?.[species]) return "";
    const p = paletteFor(species, this.pet?.coat, packs);
    return `--dog-body:${p.body}; --dog-belly:${p.belly}; --dog-point:${p.point}`;
  },
  petPreviewSvg(species) {
    // Landing + initial-adoption previews stay generic: there is no persisted
    // pet subject whose private card could be requested. The singleton ids
    // are removed because the hidden live room still owns them.
    return figureSvg(species || "cat", { visitor: true, packs: this.packs });
  },
  previewCoatStyle(species, coat) {
    if (!species || PALETTES[species] || !this.packs?.[species]) return "";
    const p = paletteFor(species, coat, this.packs);
    return `--dog-body:${p.body}; --dog-belly:${p.belly}; --dog-point:${p.point}`;
  },
  ceremonyPreviewSvg() {
    const pet = this.ceremonyPet?.();
    const packs = packsForPet(this.petCardFor?.(pet), pet, this.packs);
    return figureSvg(pet?.species || "cat", { visitor: true, packs });
  },
  ceremonyPreviewCoatStyle() {
    const pet = this.ceremonyPet?.();
    if (!pet) return "";
    const card = this.petCardFor?.(pet) || null;
    const packs = packsForPet(card, pet, this.packs);
    if (!card && (PALETTES[pet.species] || !packs?.[pet.species])) return "";
    const p = paletteFor(pet.species, pet.coat, packs);
    return `--dog-body:${p.body}; --dog-belly:${p.belly}; --dog-point:${p.point}`;
  },
  visitorSvg() {
    // Compute-on-read, not just on transition: a boot mid-visit (payload
    // applied before the rig exists) must still paint the guest.
    const v = this.pet?.visit?.visitor;
    if (v && !this._visitorArt) this._visitorArt = this.visitorArtFor(v);
    return this._visitorArt || "";
  },
  visitorScaleTransform() {
    const s = this.pet?.visit?.visitor?.render_scale;
    if (!s || s === 1) return "";
    return `translate(200 452) scale(${s}) translate(-200 -452)`;
  },
  visitorArtFor(visitor) {
    if (!visitor) return "";
    return visitorMarkup(
      visitor.species,
      visitor.coat,
      this.packs,
      this.petCardFor?.(visitor),
      visitor,
    );
  },
  figureTouchZone(species, x, y) {
    return touchZoneFor(
      species,
      x,
      y,
      packsForPet(this.petCardFor?.(this.pet), this.pet, this.packs),
    );
  },
};
