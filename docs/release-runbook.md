# Release Runbook — Publishing `traitprint` to PyPI

How to ship a release of the `traitprint` package. The recommended path is
**trusted publishing** via the
[`publish.yml`](../.github/workflows/publish.yml) GitHub Actions workflow —
no long-lived API token anywhere. A manual `twine` fallback is documented at
the end.

## 0. One-time setup (trusted publishing)

The workflow is committed but **inert** until both sides are configured.
Do this once, in the PyPI and GitHub UIs:

### PyPI side (Wesley, in the PyPI UI)

If `traitprint` does **not** exist on PyPI yet (first release):

1. Log in at <https://pypi.org> → account → **Publishing**
   (<https://pypi.org/manage/account/publishing/>).
2. Under **Add a new pending publisher → GitHub**, enter:
   - PyPI project name: `traitprint`
   - Owner: `DataViking-Tech`
   - Repository name: `traitprint`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
3. Submit. The first successful workflow publish claims the project name
   and converts the pending publisher into a real one.

If the project already exists on PyPI:

1. Go to <https://pypi.org/manage/project/traitprint/settings/publishing/>.
2. **Add a new publisher → GitHub** with the same four values as above.

### GitHub side

1. Repo → **Settings → Environments → New environment** → name it `pypi`
   (must match the workflow and the PyPI publisher config exactly).
2. Optional but recommended: add yourself as a **required reviewer** on the
   environment — every publish then needs a one-click manual approval.

## 1. Pre-release checklist (local)

```bash
# 1. On an up-to-date main
git checkout main && git pull

# 2. Version is correct in ALL THREE places
grep '^version' pyproject.toml          # e.g. version = "0.9.0"
head -20 CHANGELOG.md                   # has a dated ## [0.9.0] section
grep '"version"' gemini-extension.json  # Gemini extension manifest tracks pyproject
                                        # (tests/test_distribution.py enforces the match)

# 3. Quality gates
pytest -q                               # all green
ruff check src/ tests/                  # clean
mypy src/                               # clean

# 4. Build and inspect
pip install build twine                 # if missing
rm -rf dist/
python -m build                         # → dist/traitprint-X.Y.Z.tar.gz + .whl
twine check dist/*                      # metadata renders OK

# 5. The wheel must contain the packaged Agent Skills
python3 -c "import zipfile,glob; [w]=glob.glob('dist/*.whl'); \
  s=[n for n in zipfile.ZipFile(w).namelist() if n.startswith('traitprint/data/skills/')]; \
  print(len(s),'skill files'); assert s"

# 6. Smoke-test the wheel in a clean venv
python3 -m venv /tmp/tp-release && /tmp/tp-release/bin/pip install dist/*.whl
/tmp/tp-release/bin/traitprint --version
rm -rf /tmp/tp-release
```

The `build` job in `publish.yml` re-runs steps 4–5 in CI, so a release can
never ship without them — but catching failures locally is cheaper.

## 2. Release (recommended: tag → trusted publishing)

```bash
git checkout main && git pull           # release from main only
git tag v0.9.0                          # tag MUST match pyproject version
git push origin v0.9.0
```

That's it. The tag push triggers `publish.yml`:

1. **test** — ruff + mypy + pytest across Python 3.10–3.13.
2. **build** — verifies the tag matches `pyproject.toml`, builds
   sdist + wheel, asserts the wheel contains `traitprint/data/skills/`,
   runs `twine check`, uploads `dist/` as an artifact.
3. **publish** — waits for the `pypi` environment (manual approval if you
   configured a reviewer), then publishes via OIDC trusted publishing.

Watch it: repo → **Actions → Release to PyPI**, or
`gh run watch --repo DataViking-Tech/traitprint`.

### Verify

```bash
pip index versions traitprint                     # new version listed
python3 -m venv /tmp/tp-verify && /tmp/tp-verify/bin/pip install traitprint==0.9.0
/tmp/tp-verify/bin/traitprint --version
rm -rf /tmp/tp-verify
```

Then create the GitHub release: `gh release create v0.9.0 --notes-file -`
with the CHANGELOG section for the version as the body.

### If the workflow fails

- **test/build failure** — fix on main, delete the tag
  (`git push origin :refs/tags/v0.9.0 && git tag -d v0.9.0`), re-tag the
  fixed commit. Nothing was published.
- **publish failure with `invalid-publisher`** — the PyPI publisher config
  (owner/repo/workflow/environment) doesn't match; recheck §0. PyPI's error
  message lists the OIDC claims it received.
- **Version already exists on PyPI** — PyPI never allows re-upload of a
  version, even after deletion. Bump to a post-release (`0.9.0.post1`) or
  the next patch and start over.

## 3. Fallback: manual `twine` upload

Only if Actions is unavailable. Requires a PyPI API token (account →
**API tokens**, scope it to the `traitprint` project).

```bash
# After the full §1 checklist:
export TWINE_USERNAME=__token__
export TWINE_PASSWORD="$PYPI_API_TOKEN"   # pypi-... token, from a secret store
twine upload dist/*
```

Never commit the token; pass it via the environment for the one command and
unset it after. Prefer getting trusted publishing fixed over making manual
uploads a habit.
