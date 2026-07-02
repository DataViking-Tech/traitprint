# Vendored career-ops interop fixtures

Upstream: [santifer/career-ops](https://github.com/santifer/career-ops)
(MIT — the upstream `LICENSE` file is preserved verbatim next to the
vendored files). "career-ops" appears here as a *nominative* reference to
the external project these fixtures pin; it is not a Traitprint product
identifier.

Pinned tag: **career-ops-v1.16.0** (one directory per pinned tag).

The three vendored files are the interop *contract surface* consumed by
Traitprint's bundle exporter (`vault export -f career-bundle`) and
working-dir importer (`vault import-story-bank`):

| File | Contract it pins |
|---|---|
| `match-star.mjs` | the story-bank block/field regex family our importer's tolerant parser must keep accepting, and whose parse our exported `interview-prep/story-bank.md` must keep satisfying |
| `config/profile.example.yml` | the profile.yml key structure (`candidate` / `narrative` / `target_roles`) our exporter emits and our importer maps to `update_profile` basics |
| `templates/states.yml` | upstream's application-state vocabulary (context for the tracker-import work; not currently consumed by code) |

A weekly GitHub Actions workflow
(`.github/workflows/interop-drift.yml`) diffs these three files against
the upstream **latest release** and opens an issue when they drift, so a
new pin (and any parser/exporter adjustments) happens deliberately
instead of silently rotting.

Do not edit the vendored files — re-vendor at a new tag in a new
`<tag>/` directory instead, and update the pin here and in the workflow.
