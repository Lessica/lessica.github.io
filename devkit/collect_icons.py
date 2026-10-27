#!/usr/bin/env python3
"""Refresh local icons for packages that also have a Havoc depiction."""

from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

import requests
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HAVOC_PACKAGES_URL = "https://havoc.app/Packages.gz"
ICON_DIRECTORY = REPOSITORY_ROOT / "icons"
ICON_MAPPING = ICON_DIRECTORY / "index.yaml"
CACHE_FILE = REPOSITORY_ROOT / ".cache" / "havoc.app" / "Packages.gz"


def log(message: str) -> None:
    print(message, file=sys.stderr)


def version_is_greater(first: str, second: str) -> bool:
    try:
        result = subprocess.run(
            ["dpkg", "--compare-versions", first, "gt", second],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return first > second


def load_havoc_packages() -> str:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    log(f"Downloading {HAVOC_PACKAGES_URL}")
    response = requests.get(
        HAVOC_PACKAGES_URL,
        headers={"User-Agent": "82flex-apt-builder"},
        timeout=(15, 120),
    )
    response.raise_for_status()
    CACHE_FILE.write_bytes(response.content)
    return gzip.decompress(response.content).decode("utf-8")


def highest_havoc_controls(packages: str, wanted: set[str]) -> dict[str, str]:
    controls: dict[str, str] = {}
    versions: dict[str, str] = {}
    for block in packages.split("\n\n"):
        package_match = re.search(r"^Package: (.+)$", block, re.MULTILINE)
        version_match = re.search(r"^Version: (.+)$", block, re.MULTILINE)
        if not package_match or not version_match:
            continue
        package = package_match.group(1).strip()
        version = version_match.group(1).strip()
        if package not in wanted:
            continue
        previous = versions.get(package)
        if previous and version_is_greater(previous, version):
            continue
        versions[package] = version
        controls[package] = block
    return controls


def icon_url(block: str) -> str | None:
    match = re.search(r"^Icon: (.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else None


def download_icon(url: str) -> tuple[str, str] | None:
    response = requests.get(
        url,
        headers={"User-Agent": "82flex-apt-builder"},
        timeout=(15, 120),
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    suffix = {"image/png": ".png", "image/jpeg": ".jpg"}.get(content_type)
    if not suffix:
        log(f"Unsupported image format for {url}: {content_type}")
        return None
    name = url.split("/")[-1] + suffix
    (ICON_DIRECTORY / name).write_bytes(response.content)
    return name, url


def main() -> int:
    manifest = json.loads((REPOSITORY_ROOT / "manifest.json").read_text(encoding="utf-8"))
    havoc_ids = manifest.get("havocMappings") or {}

    existing: dict[str, str] = {}
    if ICON_MAPPING.exists():
        existing = yaml.safe_load(ICON_MAPPING.read_text(encoding="utf-8")) or {}

    controls = highest_havoc_controls(load_havoc_packages(), set(havoc_ids))
    icons: dict[str, str] = {}
    for package, block in controls.items():
        url = icon_url(block)
        if not url:
            continue

        current = existing.get(package)
        prefix = url.split("/")[-1] + "."
        if current and current.startswith(prefix) and (ICON_DIRECTORY / current).exists():
            icons[package] = current
            continue

        log(f"Downloading {url}")
        downloaded = download_icon(url)
        if downloaded:
            icons[package] = downloaded[0]

    if icons == existing:
        log("No icon changes.")
        return 0

    ICON_DIRECTORY.mkdir(parents=True, exist_ok=True)
    ICON_MAPPING.write_text(
        yaml.dump(icons, allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )
    log(f"Updated {ICON_MAPPING}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, OSError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
