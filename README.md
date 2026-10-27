# 82Flex APT Repository

Personal APT source for [@Lessica](https://github.com/Lessica), served at
<https://apt.82flex.com>.

```text
deb [trusted=yes] https://apt.82flex.com/ ./
```

## How it works

`manifest.json` lists the GitHub repositories that publish `.deb` assets. The
build resolves each repository's three newest stable releases plus the newest
release of the previous minor version, downloads those assets to `.cache/`,
and writes package entries whose `Filename` is the public GitHub release URL.
No `.deb` is committed, so the repository stays small and clients download
packages straight from their upstream releases.

`Packages`, its compressed variants, `Release`, and `icons/` are committed by
the **Sync with upstream** workflow. GitHub Pages serves the root of `main`.

Packages that upstream deleted from its releases are not indexed. A repository
without downloadable `.deb` assets is skipped with a warning.

## Local build

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python devkit/collect_icons.py
GITHUB_TOKEN="$(gh auth token)" python devkit/build_repository.py
```

`collect_icons.py` refreshes the local icons for packages with a Havoc
depiction. `build_repository.py` downloads the selected release assets and
regenerates every index. Both are also run by the workflow.
