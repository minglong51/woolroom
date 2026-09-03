from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WOOLROOM_RELEASE = REPO_ROOT / ".github" / "workflows" / "release-woolroom.yml"
WOOLPACK_RELEASE = REPO_ROOT / ".github" / "workflows" / "release-woolpack.yml"


def _workflow(path: Path) -> dict[str, object]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def test_woolroom_release_has_a_separate_least_privilege_publish_job() -> None:
    workflow = _workflow(WOOLROOM_RELEASE)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    build = jobs["build"]
    publish = jobs["publish"]

    assert build["if"] == "startsWith(github.event.release.tag_name, 'woolroom-v')"
    assert build["permissions"] == {"contents": "read"}
    assert publish["needs"] == "build"
    assert publish["permissions"] == {"id-token": "write"}
    assert publish["environment"]["name"] == "pypi-woolroom"
    assert _workflow(WOOLPACK_RELEASE)["name"] == "publish-woolpack"
    assert workflow["name"] == "publish-woolroom"

    action_uses = re.findall(
        r"^\s*uses:\s*[^@\s]+@([^\s]+)\s*$",
        WOOLROOM_RELEASE.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert action_uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in action_uses)


def test_woolroom_release_validates_and_smokes_the_exact_artifacts() -> None:
    source = WOOLROOM_RELEASE.read_text(encoding="utf-8")

    required_fragments = (
        'test "$RELEASE_TAG" = "woolroom-v${core_version}"',
        'git merge-base --is-ancestor "$head_sha" refs/remotes/origin/main',
        'core_version != pack_version',
        'dependency not in core["project"]["dependencies"]',
        "--default-index https://pypi.org/simple",
        '"woolpack==${WOOLROOM_VERSION}"',
        'https://pypi.org/pypi/woolroom/{version}/json',
        "refusing to republish an existing Woolroom version",
        "uv build --package woolpack",
        "uv build --package woolroom",
        "twine check --strict dist/*",
        'pack_wheel="pack-dist/woolpack-${WOOLROOM_VERSION}-py3-none-any.whl"',
        'for artifact in "$wheel" "$sdist"',
        'pip install "$pack_wheel" "$artifact"',
        'distribution("woolpack").read_text("direct_url.json")',
        "smoke did not install the exact local Woolpack wheel",
        'bin/woolroom-db" upgrade',
        'client.get("/healthz")',
        'bin/woolroom-db" inspect',
        "pypa/gh-action-pypi-publish@",
    )
    assert all(fragment in source for fragment in required_fragments)

    assert "path: dist/" in source
    assert "path: pack-dist/" not in source
    assert "woolpack-v${core_version}" not in source
