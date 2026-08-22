# Security

## Reporting

Report vulnerabilities through **GitHub Security Advisories** on this
repository (Security → Advisories → "Report a vulnerability"). Please do not
open public issues for security reports. This project is maintained-lite:
expect an acknowledgement in days, not hours.

## Threat model, in five lines

1. **Packs are data, never code.** A content pack is YAML plus one SVG — no
   scripting, no CSS, no remote fetch — so installing a stranger's pack can
   never run a stranger's code.
2. **The loader sanitizes fail-closed.** Every pack is validated at boot
   behind an allowlist-only SVG sanitizer, byte/complexity caps, directory
   confinement with symlinks refused, and `yaml.safe_load` only; any
   violation refuses the boot, and a refused pack registers nothing.
3. **There is no remote install path.** Packs load from local directories
   named by `PACK_PATHS`; every byte served is a byte the host chose.
4. **A known-bad-pack kill-list is a designed loader gate** that lands
   before any future remote-install path exists; it is not yet needed while
   every pack is host-installed by hand.
5. **The web surface is small:** signed-cookie sessions (no passwords),
   invite-only pairing, an optional outer site password, a read-only guest
   mode with private fields stripped server-side, and an LLM lane that is
   opt-in and budget-capped per pet per day.

woolroom is self-hosted software: the host is the trust boundary, and the
deployment (TLS, backups, the optional site password) is the host's
responsibility. See `.env.example` for the knobs.
