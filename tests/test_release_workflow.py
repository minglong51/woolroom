from __future__ import annotations

import io
import re
import tarfile
import tomllib
from pathlib import Path

import pytest
import yaml

from scripts.normalize_sdist import normalize_sdist
from scripts.verify_pypi_release import (
    ReleaseIncompleteError,
    ReleaseVerificationError,
    artifact_digests,
    pypi_release_digests,
    verify_release_state,
)

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
    assert publish["permissions"] == {"contents": "read", "id-token": "write"}
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
        "scripts/verify_pypi_release.py",
        "--require-complete --wait-seconds 60",
        "skip-existing: true",
        "pypa/gh-action-pypi-publish@",
    )
    assert all(fragment in source for fragment in required_fragments)

    assert "path: dist/" in source
    assert "path: pack-dist/" not in source
    assert "woolpack-v${core_version}" not in source


def test_both_publish_workflows_hash_verify_before_and_after_resumable_upload() -> None:
    for path, package_env in (
        (WOOLROOM_RELEASE, "WOOLROOM_VERSION"),
        (WOOLPACK_RELEASE, "WOOLPACK_VERSION"),
    ):
        source = path.read_text(encoding="utf-8")
        assert source.count("scripts/verify_pypi_release.py") == 2
        assert f'--version "${package_env}" --dist dist' in source
        assert source.count("skip-existing: true") == 1
        assert source.index("scripts/verify_pypi_release.py") < source.index(
            "pypa/gh-action-pypi-publish@"
        )
        assert source.rindex("scripts/verify_pypi_release.py") > source.index(
            "pypa/gh-action-pypi-publish@"
        )


def test_both_release_workflows_build_reproducible_retry_artifacts() -> None:
    root_metadata = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    pack_metadata = tomllib.loads(
        (REPO_ROOT / "packages" / "woolpack" / "pyproject.toml").read_text()
    )
    assert root_metadata["build-system"]["requires"] == ["setuptools==84.0.0"]
    assert pack_metadata["build-system"]["requires"] == ["setuptools==84.0.0"]

    for path in (WOOLROOM_RELEASE, WOOLPACK_RELEASE):
        source = path.read_text(encoding="utf-8")
        assert 'release_epoch="$(git show -s --format=%ct HEAD)"' in source
        assert 'export SOURCE_DATE_EPOCH="$release_epoch"' in source
        assert "scripts/normalize_sdist.py" in source
        assert '--source-date-epoch "$release_epoch"' in source


def test_release_state_allows_only_matching_partial_uploads() -> None:
    local = {"project.whl": "a" * 64, "project.tar.gz": "b" * 64}

    assert verify_release_state(
        local,
        {"project.whl": "a" * 64},
        require_complete=False,
    ) == ("project.tar.gz",)
    with pytest.raises(ReleaseIncompleteError, match="still missing"):
        verify_release_state(
            local,
            {"project.whl": "a" * 64},
            require_complete=True,
        )
    for filename in local:
        remote = dict(local)
        remote[filename] = "c" * 64
        with pytest.raises(
            ReleaseVerificationError,
            match=f"hashes conflict.*{re.escape(filename)}",
        ):
            verify_release_state(local, remote, require_complete=False)
    with pytest.raises(ReleaseVerificationError, match="unexpected artifacts"):
        verify_release_state(
            local,
            {"other.whl": "d" * 64},
            require_complete=False,
        )


def test_release_verifier_hashes_local_files_and_parses_pypi_payload(tmp_path: Path) -> None:
    artifact = tmp_path / "project.whl"
    sdist = tmp_path / "project.tar.gz"
    artifact.write_bytes(b"wheel")
    sdist.write_bytes(b"source")
    for path in (artifact, sdist):
        (tmp_path / f"{path.name}.publish.attestation").write_text(
            "signed",
            encoding="utf-8",
        )
    local = artifact_digests(tmp_path)
    assert set(local) == {artifact.name, sdist.name}
    payload = {
        "urls": [
            {
                "filename": filename,
                "digests": {"sha256": digest.upper()},
            }
            for filename, digest in local.items()
        ]
    }

    remote = pypi_release_digests(payload)
    assert remote == local
    assert verify_release_state(local, remote, require_complete=True) == ()


def test_sdist_normalization_is_reproducible(tmp_path: Path) -> None:
    outputs = []
    for index, timestamp in enumerate((1_700_000_001, 1_800_000_002)):
        path = tmp_path / f"build-{index}.tar.gz"
        payload = b"same package source"
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo("project-1.0/source.py")
            member.size = len(payload)
            member.mtime = timestamp
            member.uid = index + 1
            member.gid = index + 2
            archive.addfile(member, io.BytesIO(payload))
        normalize_sdist(path, 1_600_000_000)
        outputs.append(path.read_bytes())

    assert outputs[0] == outputs[1]
