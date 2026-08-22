---
name: Pack submission
about: The community index is live — submit your pack as a one-line PR to
  woolroom-packs (this issue form is the fallback if a PR is not your thing)
title: "pack: <name>"
labels: pack
---

> **The normal path is a PR, not this issue:** add one line to the table in
> [woolroom-packs](https://github.com/minglong51/woolroom-packs) — see its
> README for the three-step loop. Use this issue only if you can't open a PR.

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
