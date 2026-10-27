import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "build_repository", Path(__file__).resolve().parents[1] / "devkit/build_repository.py"
)
build_repository = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_repository)


def release(tag, draft=False, prerelease=False, assets=None):
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": assets or [],
    }


def asset(name, architecture="iphoneos-arm64e", size=10):
    return {
        "name": name,
        "size": size,
        "state": "uploaded",
        "browser_download_url": f"https://github.com/example/repo/releases/download/v1.0/{name}",
        "digest": None,
        "_architecture": architecture,
    }


class ReleaseSelectionTests(unittest.TestCase):
    def test_minor_key(self):
        self.assertEqual(build_repository.minor_key("v4.7.5-3677"), (4, 7))
        self.assertEqual(build_repository.minor_key("1.29.0"), (1, 29))
        self.assertIsNone(build_repository.minor_key("nightly"))

    def test_stable_releases_skip_drafts_prereleases_and_preview_tags(self):
        releases = [
            release("v2.0"),
            release("v2.0-beta1"),
            release("v1.9", draft=True),
            release("v1.8", prerelease=True),
            release("v1.7"),
        ]
        self.assertEqual(
            [item["tag_name"] for item in build_repository.stable_releases(releases)],
            ["v2.0", "v1.7"],
        )

    def test_selection_keeps_newest_and_previous_minor(self):
        releases = [
            release("v1.3.3"),
            release("v1.3.2"),
            release("v1.3.1"),
            release("v1.2.9"),
            release("v1.2.8"),
            release("v1.1.0"),
        ]
        selected = build_repository.select_releases(releases, 3)
        self.assertEqual(
            [item["tag_name"] for item in selected],
            ["v1.3.3", "v1.3.2", "v1.3.1", "v1.2.9"],
        )

    def test_selection_does_not_repeat_previous_minor(self):
        releases = [release("v1.0.4"), release("v1.0.3"), release("v1.0.2"), release("v1.0.1")]
        selected = build_repository.select_releases(releases, 3)
        self.assertEqual(
            [item["tag_name"] for item in selected], ["v1.0.4", "v1.0.3", "v1.0.2"]
        )

    def test_selection_without_stable_releases(self):
        releases = [release("v1.0-beta1"), release("v2.0", draft=True)]
        self.assertEqual(build_repository.select_releases(releases, 3), [])

    def test_assets_one_per_architecture(self):
        release_item = release(
            "v1.0",
            assets=[
                asset("com.example.one_1.0_iphoneos-arm.deb", "iphoneos-arm"),
                asset("com.example.one_1.0_iphoneos-arm64.deb", "iphoneos-arm64"),
                asset("com.example.one_1.0_iphoneos-arm64e.deb", "iphoneos-arm64e"),
            ],
        )
        selected = build_repository.select_assets(
            release_item, ["iphoneos-arm", "iphoneos-arm64", "iphoneos-arm64e"], False
        )
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0][0], "iphoneos-arm")

    def test_assets_missing_architecture_is_skipped(self):
        release_item = release(
            "v1.0",
            assets=[asset("com.example.one_1.0_iphoneos-arm.deb", "iphoneos-arm")],
        )
        selected = build_repository.select_assets(
            release_item, ["iphoneos-arm", "iphoneos-arm64e"], False
        )
        self.assertEqual([item[0] for item in selected], ["iphoneos-arm"])

    def test_all_packages_returns_every_asset_for_an_architecture(self):
        release_item = release(
            "v1.0",
            assets=[
                asset("com.example.one_1.0_iphoneos-arm64e.deb"),
                asset("com.example.two_1.0_iphoneos-arm64e.deb"),
            ],
        )
        selected = build_repository.select_assets(
            release_item, ["iphoneos-arm64e"], True
        )
        self.assertEqual(len(selected), 2)


class PackageRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.deb = Path(self.temp.name) / "com.example.one_1.0_iphoneos-arm64e.deb"
        self.deb.write_bytes(b"!<arch>\n" + b"payload")

    def test_renders_release_url_and_havoc_metadata(self):
        control = """Package: com.example.one
Name: Example
Version: 1.0
Architecture: iphoneos-arm64e
Description: Example package.
"""
        item = build_repository.Asset(
            repository="https://github.com/example/repo",
            slug="example/repo",
            tag="v1.0",
            name=self.deb.name,
            url="https://github.com/example/repo/releases/download/v1.0/com.example.one_1.0_iphoneos-arm64e.deb",
            size=self.deb.stat().st_size,
        )
        manifest = {
            "baseUrl": "https://apt.example.com",
            "havocMappings": {"com.example.one": "example"},
            "packageTitleMappings": {"com.example.one": "Example Renamed"},
            "hiddenPackages": [],
        }
        rendered = build_repository.render_package(
            control, item, self.deb, manifest, {"com.example.one": "icon.png"}
        )
        self.assertIsNotNone(rendered)
        block = rendered[3]
        self.assertIn(f"Filename: {item.url}", block)
        self.assertIn("Name: Example Renamed", block)
        self.assertIn("Depiction: https://havoc.app/depiction/example", block)
        self.assertIn("Icon: https://apt.example.com/icons/icon.png", block)
        self.assertNotIn("downloads/", block)
        self.assertIn(f"Size: {self.deb.stat().st_size}", block)
        self.assertIn("SHA256:", block)

    def test_hidden_package_is_dropped(self):
        control = """Package: com.example.hidden
Version: 1.0
Architecture: iphoneos-arm64e
"""
        item = build_repository.Asset(
            repository="https://github.com/example/repo",
            slug="example/repo",
            tag="v1.0",
            name=self.deb.name,
            url="https://github.com/example/repo/releases/download/v1.0/pkg.deb",
            size=self.deb.stat().st_size,
        )
        rendered = build_repository.render_package(
            control,
            item,
            self.deb,
            {"hiddenPackages": ["com.example.hidden"]},
            {},
        )
        self.assertIsNone(rendered)

    def test_manifest_rejects_duplicate_repositories(self):
        manifest = {
            "packages": [
                {
                    "repository": "https://github.com/example/repo",
                    "architectures": ["iphoneos-arm64e"],
                },
                {
                    "repository": "https://github.com/example/repo",
                    "architectures": ["iphoneos-arm64e"],
                },
            ]
        }
        path = Path(self.temp.name) / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(build_repository.BuildError):
            build_repository.load_manifest(path)


if __name__ == "__main__":
    unittest.main()
