#!/usr/bin/env python3
"""Publish one source-bound OCI image, retaining exact bytes before effects."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from classify_ghcr_release_absence import FORK_RELEASE_TAG_PATTERN, is_explicit_absence

IMAGE = "ghcr.io/ragnos-labs/hermes-agent"


def digest(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def run(*argv: str) -> bytes:
    result = subprocess.run(argv, capture_output=True, check=False)
    if result.returncode:
        raise ValueError("registry_operation_failed")
    return result.stdout


def manifest(reference: str) -> bytes | None:
    result = subprocess.run(
        ["oras", "manifest", "fetch", reference], capture_output=True, check=False
    )
    if result.returncode == 0:
        body = result.stdout
        json.loads(body)
        if "@sha256:" in reference and digest(body) != reference.rsplit("@", 1)[1]:
            raise ValueError("registry_digest_mismatch")
        return body
    if is_explicit_absence(result.stderr.decode(errors="replace")):
        return None
    raise ValueError("registry_state_unknown")


def verify_image(body: bytes, read_blob, expected: dict[str, str]) -> None:
    root = json.loads(body)
    if "manifests" in root:
        matches = [
            item
            for item in root["manifests"]
            if item.get("platform", {}).get("os") == "linux"
            and item.get("platform", {}).get("architecture")
            == expected["platform"].split("/")[1]
        ]
        if len(matches) != 1:
            raise ValueError("image_platform_mismatch")
        root = json.loads(read_blob(matches[0]))
    config = json.loads(read_blob(root["config"]))
    if (config.get("os"), config.get("architecture")) != tuple(
        expected["platform"].split("/")
    ):
        raise ValueError("image_platform_mismatch")
    labels = config.get("config", {}).get("Labels", {})
    required = {
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
    if any(labels.get(key) != value for key, value in required.items()):
        raise ValueError("image_source_mismatch")


def checked_blob(body: bytes, descriptor: dict) -> bytes:
    if digest(body) != descriptor["digest"] or len(body) != descriptor["size"]:
        raise ValueError("image_blob_mismatch")
    return body


def registry_blob(descriptor: dict) -> bytes:
    return checked_blob(
        run("oras", "blob", "fetch", IMAGE + "@" + descriptor["digest"]), descriptor
    )


def archive(path: Path, expected: dict[str, str]) -> tuple[str, bytes]:
    with tarfile.open(path) as stream:
        members = {
            item.name.removeprefix("./"): item for item in stream if item.isfile()
        }
        if len(members) != sum(item.isfile() for item in stream.getmembers()):
            raise ValueError("image_archive_duplicate")
        if any(not item.isfile() and not item.isdir() for item in stream.getmembers()):
            raise ValueError("image_archive_member_invalid")
        if any(
            name not in {"index.json", "oci-layout"}
            and not re.fullmatch(r"blobs/sha256/[0-9a-f]{64}", name)
            for name in members
        ):
            raise ValueError("image_archive_member_invalid")

        def read(name: str) -> bytes:
            source = stream.extractfile(members[name])
            if source is None:
                raise ValueError("image_archive_member_invalid")
            return source.read()

        if json.loads(read("oci-layout")) != {"imageLayoutVersion": "1.0.0"}:
            raise ValueError("image_layout_invalid")
        descriptors = json.loads(read("index.json"))["manifests"]
        if len(descriptors) != 1:
            raise ValueError("image_inventory_invalid")

        def blob(descriptor: dict) -> bytes:
            return checked_blob(
                read("blobs/sha256/" + descriptor["digest"][7:]), descriptor
            )

        for name in members:
            if (
                name.startswith("blobs/")
                and digest(read(name))[7:] != name.rsplit("/", 1)[1]
            ):
                raise ValueError("image_blob_mismatch")
        body = blob(descriptors[0])
        verify_image(body, blob, expected)
        return digest(body), body


def reconcile(expected: dict[str, str]) -> tuple[str | None, bytes | None]:
    targets = ("sha-" + expected["source_sha"], expected["release_tag"])
    bodies = [manifest(IMAGE + ":" + target) for target in targets]
    present = [body for body in bodies if body is not None]
    if not present:
        return None, None
    if any(body != present[0] for body in present):
        raise ValueError("registry_tag_conflict")
    verify_image(present[0], registry_blob, expected)
    return digest(present[0]), present[0]


def publish(expected: dict[str, str], path: Path, record: Path) -> dict:
    image_digest, body = reconcile(expected)
    if image_digest is None:
        image_digest, body = archive(path, expected)
    result = {
        **expected,
        "image_repository": IMAGE,
        "image_digest": image_digest,
        "state": "publication_pending",
    }
    record.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    # The same OCI bytes and digest survive uncertain delivery. Never rebuild
    # or overwrite a conflicting source tag during reconciliation.
    observed = manifest(IMAGE + "@" + image_digest)
    if observed is None:
        try:
            run(
                "oras",
                "cp",
                "--from-oci-layout",
                str(path) + "@" + image_digest,
                IMAGE + "@" + image_digest,
            )
        except ValueError:
            if manifest(IMAGE + "@" + image_digest) != body:
                raise ValueError("registry_delivery_uncertain") from None
    if manifest(IMAGE + "@" + image_digest) != body:
        raise ValueError("registry_digest_mismatch")
    verify_image(body, registry_blob, expected)
    root_file = record.with_suffix(".manifest.json")
    root_file.write_bytes(body)
    for tag in ("sha-" + expected["source_sha"], expected["release_tag"]):
        target = IMAGE + ":" + tag
        observed = manifest(target)
        if observed is not None and observed != body:
            raise ValueError("registry_tag_conflict")
        if observed is None:
            try:
                run("oras", "manifest", "push", target, str(root_file))
            except ValueError:
                if manifest(target) != body:
                    raise ValueError("registry_delivery_uncertain") from None
        if manifest(target) != body:
            raise ValueError("registry_tag_readback_mismatch")
    result["state"] = "artifact_published"
    record.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    expected = {
        key: os.environ[key.upper()]
        for key in (
            "source_sha",
            "release_tag",
            "platform",
            "schema_sha256",
            "action_schema_sha256",
        )
    }
    if not re.fullmatch(r"[0-9a-f]{40}", expected["source_sha"]) or not re.fullmatch(
        FORK_RELEASE_TAG_PATTERN, expected["release_tag"]
    ):
        raise ValueError("publication_identity_invalid")
    if expected["platform"] not in {"linux/amd64", "linux/arm64"}:
        raise ValueError("image_platform_invalid")
    if sys.argv[1] == "preflight":
        image_digest, _ = reconcile(expected)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"build={'true' if image_digest is None else 'false'}\n")
            output.write(f"digest={image_digest or ''}\n")
    else:
        result = publish(expected, Path(sys.argv[2]), Path(sys.argv[3]))
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
