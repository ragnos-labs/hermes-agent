"""Runtime security-floor checks for the Node toolchain copied into the image."""

from __future__ import annotations

import json
import subprocess

from packaging.version import Version


def test_bundled_npm_dependencies_meet_security_floors(built_image: str) -> None:
    script = r"""
const root = "/usr/local/lib/node_modules/npm/node_modules";
const names = ["tar", "brace-expansion", "ip-address", "undici"];
const versions = Object.fromEntries(
  names.map((name) => [name, require(`${root}/${name}/package.json`).version]),
);
console.log(JSON.stringify(versions));
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "node",
            built_image,
            "-e",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    versions = json.loads(result.stdout)
    minimums = {
        "tar": "7.5.21",
        "brace-expansion": "5.0.7",
        "ip-address": "10.3.1",
        "undici": "6.28.0",
    }

    assert versions.keys() == minimums.keys()
    for package, minimum in minimums.items():
        assert Version(versions[package]) >= Version(minimum), (
            f"npm bundles vulnerable {package} {versions[package]}; "
            f"security floor is {minimum}"
        )


def test_copied_node_and_npm_are_executable(built_image: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            built_image,
            "-ec",
            "node --version && npm --version && npx --version",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )

    lines = result.stdout.splitlines()
    assert lines[0].startswith("v26.")
    assert lines[1] == lines[2]
