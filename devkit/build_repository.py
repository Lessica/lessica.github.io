#!/usr/bin/env python3
"""Build the APT repository indexes from GitHub release assets.

The repository stores only indexes and static files. Every package entry
points at the public GitHub release asset it came from, so no .deb is
committed and clones stay small.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import json
import lzma
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
import zstandard as zstd

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPOSITORY_ROOT / "manifest.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT
DEFAULT_CACHE = REPOSITORY_ROOT / ".cache" / "downloads"

PREVIEW_TAG = re.compile(
    r"(^|[^A-Za-z0-9])(alpha|beta|rc|pre|preview|dev|nightly|snapshot)([^A-Za-z0-9]|[0-9]|$)",
    re.IGNORECASE,
)
REPOSITORY_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
ARCHITECTURE = re.compile(r"^[A-Za-z0-9._-]+$")

PACKAGE_INDEX_FILES = ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz", "Packages.zst")


class BuildError(RuntimeError):
    """The repository could not be built from the manifest."""


@dataclass(frozen=True)
class Asset:
    repository: str
    slug: str
    tag: str
    name: str
    url: str
    size: int
    digest: str | None = None


def log(message: str) -> None:
    print(message, file=sys.stderr)


def load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BuildError(f"Missing package manifest: {path}") from error
    except json.JSONDecodeError as error:
        raise BuildError(f"Invalid JSON in {path}: {error}") from error

    if not isinstance(manifest, dict):
        raise BuildError(f"{path} must contain a JSON object")

    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise BuildError(f"{path} must define a non-empty packages array")

    repositories = set()
    for entry in packages:
        if not isinstance(entry, dict):
            raise BuildError(f"{path} has a package entry that is not an object")
        repository = entry.get("repository")
        if not isinstance(repository, str) or not repository.strip():
            raise BuildError(f"{path} has a package entry without a repository")
        slug = repository_slug(repository)
        if slug in repositories:
            raise BuildError(f"{path} lists {slug} more than once")
        repositories.add(slug)

        architectures = entry.get("architectures")
        if (
            not isinstance(architectures, list)
            or not architectures
            or not all(
                isinstance(architecture, str) and ARCHITECTURE.fullmatch(architecture)
                for architecture in architectures
            )
        ):
            raise BuildError(f"{slug} must define a non-empty architectures array")
        if len(set(architectures)) != len(architectures):
            raise BuildError(f"{slug} lists an architecture more than once")

        all_packages = entry.get("allPackages", False)
        if not isinstance(all_packages, bool):
            raise BuildError(f"{slug} must use a boolean for allPackages")

    keep_versions = manifest.get("keepVersions", 3)
    if not isinstance(keep_versions, int) or isinstance(keep_versions, bool) or keep_versions < 1:
        raise BuildError("keepVersions must be a positive integer")

    for field in ("havocMappings", "packageTitleMappings"):
        value = manifest.get(field, {})
        if not isinstance(value, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in value.items()
        ):
            raise BuildError(f"{field} must map strings to strings")

    hidden = manifest.get("hiddenPackages", [])
    if not isinstance(hidden, list) or not all(isinstance(item, str) for item in hidden):
        raise BuildError("hiddenPackages must be an array of package names")

    base_url = manifest.get("baseUrl", "")
    if not isinstance(base_url, str):
        raise BuildError("baseUrl must be a string")

    return manifest


def repository_slug(repository: str) -> str:
    slug = repository.strip()
    for prefix in ("https://github.com/", "http://github.com/", "git@github.com:"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    slug = slug.removesuffix("/").removesuffix(".git")
    if not REPOSITORY_SLUG.fullmatch(slug):
        raise BuildError(f"Cannot derive an owner/name slug from repository: {repository}")
    return slug


def minor_key(tag: str | None) -> tuple[int, int] | None:
    match = re.match(r"^[vV]?([0-9]+)\.([0-9]+)", tag or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


def stable_releases(releases: list[dict]) -> list[dict]:
    return [
        release
        for release in releases
        if isinstance(release.get("tag_name"), str)
        and release["tag_name"]
        and not release.get("draft")
        and not release.get("prerelease")
        and not PREVIEW_TAG.search(release["tag_name"])
    ]


def select_releases(releases: list[dict], keep_versions: int) -> list[dict]:
    """Newest stable releases, plus the newest release of the previous minor."""
    stable = stable_releases(releases)
    if not stable:
        return []

    selected = stable[:keep_versions]
    current = minor_key(stable[0]["tag_name"])
    if current is None:
        return selected

    older = [
        release
        for release in stable
        if (key := minor_key(release["tag_name"])) is not None and key < current
    ]
    if not older:
        return selected

    previous = max(minor_key(release["tag_name"]) for release in older)
    candidate = next(
        release for release in older if minor_key(release["tag_name"]) == previous
    )
    if candidate["tag_name"] not in {release["tag_name"] for release in selected}:
        selected.append(candidate)
    return selected


def select_assets(
    release: dict, architectures: list[str], all_packages: bool
) -> list[tuple[str, dict]]:
    assets = release.get("assets") or []
    selected: list[tuple[str, dict]] = []
    for architecture in architectures:
        suffix = f"_{architecture.lower()}.deb"
        candidates = [
            asset
            for asset in assets
            if isinstance(asset.get("name"), str)
            and asset["name"].lower().endswith(suffix)
            and asset.get("state", "uploaded") == "uploaded"
            and str(asset.get("browser_download_url", "")).startswith("https://")
        ]
        if not candidates:
            continue
        for asset in candidates if all_packages else candidates[:1]:
            selected.append((architecture, asset))
    return selected


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "82flex-apt-builder",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, url: str, params: dict | None = None, attempts: int = 5) -> requests.Response:
        delay = 2
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.get(url, params=params, timeout=(15, 120))
                if response.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(
                        f"GitHub returned {response.status_code} for {url}", response=response
                    )
                if (
                    response.status_code == 403
                    and response.headers.get("X-RateLimit-Remaining") == "0"
                ):
                    raise requests.HTTPError(
                        f"GitHub API rate limit reached for {url}", response=response
                    )
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                if attempt == attempts:
                    raise BuildError(f"Request failed after {attempts} attempts: {url}") from error
                log(f"Request failed (attempt {attempt}/{attempts}): {error}")
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise AssertionError("unreachable")


def list_releases(client: GitHubClient, slug: str) -> list[dict]:
    url: str | None = f"https://api.github.com/repos/{slug}/releases"
    params: dict | None = {"per_page": 100}
    releases: list[dict] = []
    while url:
        response = client.get(url, params=params)
        page = response.json()
        if not isinstance(page, list):
            raise BuildError(f"GitHub did not return a release list for {slug}")
        releases.extend(page)
        url = response.links.get("next", {}).get("url")
        params = None
    return releases


def collect_assets(client: GitHubClient, manifest: dict) -> tuple[list[Asset], list[str]]:
    assets: list[Asset] = []
    skipped: list[str] = []
    seen: set[str] = set()

    for entry in manifest["packages"]:
        repository = entry["repository"]
        slug = repository_slug(repository)
        releases = list_releases(client, slug)
        selected = select_releases(releases, manifest.get("keepVersions", 3))
        if not selected:
            log(f"{slug}: no stable release; skipping")
            skipped.append(slug)
            continue

        repository_assets = 0
        for release in selected:
            tag = release.get("tag_name", "?")
            chosen = select_assets(release, entry["architectures"], entry.get("allPackages", False))
            if not chosen:
                log(f"{slug}@{tag}: no matching .deb asset; skipping release")
                continue
            for architecture, asset in chosen:
                name = asset["name"]
                if name in seen:
                    raise BuildError(f"Duplicate package file name across manifest entries: {name}")
                seen.add(name)
                assets.append(
                    Asset(
                        repository=repository,
                        slug=slug,
                        tag=tag,
                        name=name,
                        url=asset["browser_download_url"],
                        size=int(asset.get("size") or 0),
                        digest=asset.get("digest"),
                    )
                )
                repository_assets += 1
                log(f"{slug}@{tag}: {name} ({architecture})")

        if repository_assets == 0:
            log(f"{slug}: no downloadable .deb assets; skipping")
            skipped.append(slug)

    return assets, skipped


def is_debian_archive(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(8) == b"!<arch>\n"
    except OSError:
        return False


def download_asset(client: GitHubClient, asset: Asset, cache_dir: Path) -> Path:
    path = cache_dir / asset.name
    if path.exists() and path.stat().st_size == asset.size and is_debian_archive(path):
        return path

    cache_dir.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    log(f"Downloading {asset.name} from {asset.slug}@{asset.tag}")
    try:
        with client.session.get(asset.url, stream=True, timeout=(15, 300)) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        actual_size = partial.stat().st_size
        if asset.size and actual_size != asset.size:
            raise BuildError(
                f"Size mismatch for {asset.name}: expected {asset.size}, got {actual_size}"
            )
        if not is_debian_archive(partial):
            raise BuildError(f"Downloaded file is not a Debian archive: {asset.name}")
        if asset.digest:
            algorithm, _, expected = asset.digest.partition(":")
            if algorithm == "sha256":
                actual = hashlib.sha256(partial.read_bytes()).hexdigest()
                if actual != expected:
                    raise BuildError(f"Checksum mismatch for {asset.name}")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    return path


def read_control(path: Path) -> str:
    try:
        result = subprocess.run(
            ["dpkg-deb", "-f", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as error:
        raise BuildError("dpkg-deb is required to read package metadata") from error
    except subprocess.CalledProcessError as error:
        raise BuildError(f"Cannot read control data from {path}: {error.stderr.strip()}") from error
    control = result.stdout.replace("\r\n", "\n").strip()
    if not control:
        raise BuildError(f"Empty control data in {path}")
    return control


def control_field(control: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.*)$", control, re.MULTILINE)
    return match.group(1).strip() if match else ""


def strip_fields(control: str, names: set[str]) -> str:
    lowered = {name.lower() for name in names}
    kept: list[str] = []
    dropping = False
    for line in control.splitlines():
        if not line.strip():
            dropping = False
            kept.append(line)
            continue
        if not line[0].isspace():
            key, separator, _ = line.partition(":")
            dropping = bool(separator) and key.lower() in lowered
        if not dropping:
            kept.append(line)
    return "\n".join(kept).strip()


def file_hashes(path: Path) -> dict[str, str]:
    hashes = {
        "MD5sum": hashlib.md5(),
        "SHA1": hashlib.sha1(),
        "SHA256": hashlib.sha256(),
        "SHA512": hashlib.sha512(),
    }
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            for digest in hashes.values():
                digest.update(chunk)
    return {name: digest.hexdigest() for name, digest in hashes.items()}


def render_package(
    control: str,
    asset: Asset,
    path: Path,
    manifest: dict,
    icon_mappings: dict[str, str],
) -> tuple[str, str, str, str] | None:
    package = control_field(control, "Package")
    version = control_field(control, "Version")
    architecture = control_field(control, "Architecture")
    if not package or not version or not architecture:
        raise BuildError(f"{asset.name} has no Package, Version, or Architecture field")

    if package in set(manifest.get("hiddenPackages") or []):
        log(f"{asset.name}: hiding package {package}")
        return None

    control = strip_fields(control, {"Filename", "Size", "MD5sum", "SHA1", "SHA256", "SHA512"})

    title = (manifest.get("packageTitleMappings") or {}).get(package)
    if title:
        control = re.sub(
            r"^Name: .*$", f"Name: {title}", control, count=1, flags=re.MULTILINE
        )

    havoc_id = (manifest.get("havocMappings") or {}).get(package)
    if havoc_id:
        control = strip_fields(control, {"Depiction", "SileoDepiction", "Icon"})
        control += f"\nDepiction: https://havoc.app/depiction/{havoc_id}"
        control += f"\nSileoDepiction: https://havoc.app/package/{havoc_id}/depiction.json"
        icon = icon_mappings.get(package)
        base_url = (manifest.get("baseUrl") or "").rstrip("/")
        if base_url and icon:
            control += f"\nIcon: {base_url}/icons/{icon}"

    size = path.stat().st_size
    control += f"\nFilename: {asset.url}"
    control += f"\nSize: {size}"
    for name, digest in file_hashes(path).items():
        control += f"\n{name}: {digest}"
    return package, version, architecture, control.strip()


def write_packages(output_dir: Path, blocks: list[tuple[str, str, str, str]]) -> None:
    if not blocks:
        raise BuildError("No packages were produced from the manifest")

    blocks = sorted(blocks, key=lambda block: (block[0].lower(), block[1], block[2]))
    content = "\n\n".join(block[3] for block in blocks) + "\n"
    data = content.encode("utf-8")

    (output_dir / "Packages").write_bytes(data)
    (output_dir / "Packages.gz").write_bytes(gzip.compress(data, compresslevel=9, mtime=0))
    (output_dir / "Packages.bz2").write_bytes(bz2.compress(data, compresslevel=9))
    (output_dir / "Packages.xz").write_bytes(
        lzma.compress(data, format=lzma.FORMAT_XZ, preset=9 | lzma.PRESET_EXTREME)
    )
    (output_dir / "Packages.zst").write_bytes(zstd.ZstdCompressor(level=19).compress(data))


def write_release(output_dir: Path) -> None:
    contents = {
        name: (output_dir / name).read_bytes() for name in PACKAGE_INDEX_FILES
    }
    lines = [
        "Origin: 82Flex",
        "Label: 82Flex",
        "Suite: stable",
        "Version: 1.0",
        "Codename: 82Flex_Repo",
        "Architectures: iphoneos-arm iphoneos-arm64 iphoneos-arm64e",
        "Components: main",
        "Description: Personal repository of @Lessica",
        f"Date: {datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}",
    ]
    for label, algorithm in (
        ("MD5Sum", "md5"),
        ("SHA1", "sha1"),
        ("SHA256", "sha256"),
        ("SHA512", "sha512"),
    ):
        lines.append(f"{label}:")
        for name in PACKAGE_INDEX_FILES:
            digest = hashlib.new(algorithm, contents[name]).hexdigest()
            lines.append(f" {digest} {len(contents[name])} {name}")
    (output_dir / "Release").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_icons(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise BuildError(f"{path} must contain a package-to-icon mapping")
    return {str(key): str(value) for key, value in data.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    icons = load_icons(args.output_dir / "icons" / "index.yaml")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        log("GITHUB_TOKEN is not set; using anonymous API requests")

    client = GitHubClient(token)
    assets, skipped = collect_assets(client, manifest)
    log(f"Selected {len(assets)} package files from {len(manifest['packages'])} repositories")

    blocks: list[tuple[str, str, str, str]] = []
    for asset in assets:
        path = download_asset(client, asset, args.cache_dir)
        control = read_control(path)
        rendered = render_package(control, asset, path, manifest, icons)
        if rendered:
            blocks.append(rendered)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_packages(args.output_dir, blocks)
    write_release(args.output_dir)

    packages = {block[0] for block in blocks}
    log(
        f"Wrote {len(blocks)} entries for {len(packages)} packages; "
        f"{len(assets)} release assets, {len(skipped)} skipped repositories"
    )
    for slug in skipped:
        log(f"  skipped: {slug}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
