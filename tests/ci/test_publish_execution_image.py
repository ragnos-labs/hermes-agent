"""Exercise OCI validation and recoverable publication without registry effects."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest


@pytest.fixture
def publisher(monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "scripts/ci"
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "publisher", directory / "publish_execution_image.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def image(publisher, tmp_path):
    expected = {
        "source_sha": "a" * 40,
        "release_tag": "v2026.10.6-ragnos.1",
        "platform": "linux/amd64",
        "schema_sha256": "b" * 64,
        "action_schema_sha256": "c" * 64,
    }
    files = {}

    def blob(value):
        body = json.dumps(value).encode()
        identity = publisher.digest(body)
        files["blobs/sha256/" + identity[7:]] = body
        return {"digest": identity, "size": len(body)}

    config = blob({
        "os": "linux",
        "architecture": "amd64",
        "config": {
            "Labels": {
                "org.opencontainers.image.source": "https://github.com/ragnos-labs/hermes-agent",
                "org.opencontainers.image.revision": expected["source_sha"],
                "io.ragnos.hermes.execution.contract": "hermes.execution.read.v1",
                "io.ragnos.hermes.execution.schema-sha256": expected["schema_sha256"],
                "io.ragnos.hermes.execution.action-contract": "hermes.execution.action.v1",
                "io.ragnos.hermes.execution.action-schema-sha256": expected[
                    "action_schema_sha256"
                ],
                "io.ragnos.hermes.execution.action-source-sha": expected["source_sha"],
            }
        },
    })
    root = blob({"schemaVersion": 2, "config": config, "layers": []})
    files["index.json"] = json.dumps({"manifests": [root]}).encode()
    files["oci-layout"] = b'{"imageLayoutVersion":"1.0.0"}'
    archive = tmp_path / "image.tar"
    with tarfile.open(archive, "w") as stream:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            stream.addfile(member, io.BytesIO(body))
    return expected, archive, files, root


def test_archive_binds_source_and_platform(publisher, image):
    expected, path, files, root = image
    image_digest, body = publisher.archive(path, expected)
    assert image_digest == root["digest"]
    assert body == files["blobs/sha256/" + image_digest[7:]]
    for changed, reason in (
        ({"source_sha": "d" * 40}, "source_mismatch"),
        ({"platform": "linux/arm64"}, "platform_mismatch"),
    ):
        with pytest.raises(ValueError, match=reason):
            publisher.archive(path, {**expected, **changed})


@pytest.mark.parametrize("delivered", [True, False])
def test_publication_reconciles_push_failure_without_rebuilding(
    publisher, image, monkeypatch, tmp_path, delivered
):
    expected, archive, files, root = image
    registry = {}
    effects = []
    monkeypatch.setattr(publisher, "manifest", lambda target: registry.get(target))

    def run(*argv):
        if argv[1:3] == ("blob", "fetch"):
            return files["blobs/sha256/" + argv[-1].rsplit("@sha256:", 1)[1]]
        effects.append(argv)
        if argv[1] == "cp":
            if delivered:
                registry[argv[-1]] = files["blobs/sha256/" + root["digest"][7:]]
            raise ValueError("lost response")
        if argv[1:3] == ("manifest", "push"):
            registry[argv[3]] = Path(argv[4]).read_bytes()
            raise ValueError("lost tag response")

    monkeypatch.setattr(publisher, "run", run)
    record = tmp_path / "publication.json"
    if not delivered:
        with pytest.raises(ValueError, match="delivery_uncertain"):
            publisher.publish(expected, archive, record)
        assert json.loads(record.read_bytes())["state"] == "publication_pending"
        assert json.loads(record.read_bytes())["image_digest"] == root["digest"]
        return
    result = publisher.publish(expected, archive, record)
    assert result["state"] == "artifact_published"
    assert len(effects) == 3
    archive.unlink()
    assert publisher.publish(expected, archive, record) == result
    assert len(effects) == 3
    registry[publisher.IMAGE + ":" + expected["release_tag"]] = b"{}"
    with pytest.raises(ValueError, match="tag_conflict"):
        publisher.publish(expected, archive, record)
    assert len(effects) == 3


@pytest.mark.parametrize(
    "error", ["unauthorized", "TLS handshake timeout", "unexpected EOF"]
)
def test_registry_uncertainty_never_becomes_absence(publisher, monkeypatch, error):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, 1, b"", error.encode()),
    )
    with pytest.raises(ValueError, match="state_unknown"):
        publisher.manifest(publisher.IMAGE + ":v2026.10.6-ragnos.1")
