---
name: Pack submission
about: Share a content pack you authored — packs live in your own repo and
  are shared as links; the community index opens at launch
title: "pack: <name>"
labels: pack
---

**Pack name and species id**

**Repository link**
The pack must live in its own public repo — it is installed from a local
directory via `PACK_PATHS`, never uploaded here.

**One line on what it is**

**Lint output**
Run against a current checkout of this repo and paste the full output:

```sh
.venv/bin/python scripts/pack_lint.py <pack-dir> --strict
```

```
(paste here)
```

**Render board eyeballed?**
`scripts/pack_render.py <pack-dir>` — confirm every coat in every pose and
the hitbox overlay against your art.

**Pack license** (the pack's own, e.g. CC0-1.0)

**Anything else**
