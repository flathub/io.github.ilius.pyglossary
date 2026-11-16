#!/usr/bin/env python3
"""Update the Flatpak manifest and metainfo to the latest PyGlossary release."""

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = "ilius/pyglossary"
API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "io.github.ilius.pyglossary.yml"
DEFAULT_METAINFO = ROOT / "io.github.ilius.pyglossary.metainfo.xml"


def fetch_json(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pyglossary-flathub-release-updater",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        message = f"GitHub API request failed: {error.code} {error.reason}"
        if error.code == 403:
            message += " (rate limit; set GITHUB_TOKEN to authenticate)"
        raise SystemExit(message) from error


def release_info(tag=None):
    if tag:
        url = f"{API}/repos/{REPO}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    else:
        url = f"{API}/repos/{REPO}/releases/latest"
    release = fetch_json(url)
    tag = release["tag_name"]
    date = (release.get("published_at") or release.get("created_at") or "")[:10]
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return tag, date


def resolve_commit(tag):
    ref = fetch_json(
        f"{API}/repos/{REPO}/git/ref/tags/{urllib.parse.quote(tag, safe='')}"
    )
    obj = ref["object"]
    if obj["type"] == "tag":
        tag_obj = fetch_json(obj["url"])
        return tag_obj["object"]["sha"]
    return obj["sha"]


def update_manifest_text(text, tag, commit):
    lines = text.splitlines(keepends=True)
    url_re = re.compile(
        r"^\s*url:\s*https://github\.com/ilius/pyglossary\.git\s*$"
    )
    start = next((i for i, line in enumerate(lines) if url_re.match(line)), None)
    if start is None:
        raise SystemExit("pyglossary source not found in manifest")

    changed = False
    for i in range(start + 1, min(start + 8, len(lines))):
        match = re.match(r"^(\s*)tag:\s*(\S+)\s*$", lines[i])
        if match and match.group(2) != tag:
            lines[i] = f"{match.group(1)}tag: {tag}\n"
            changed = True
        match = re.match(r"^(\s*)commit:\s*(\S+)\s*$", lines[i])
        if match and match.group(2) != commit:
            lines[i] = f"{match.group(1)}commit: {commit}\n"
            changed = True
    return "".join(lines), changed


def update_metainfo_text(text, version, date):
    if re.search(rf'<release\s+version="{re.escape(version)}"', text):
        return text, False
    entry = (
        f'    <release version="{version}" date="{date}">\n'
        f"      <description></description>\n"
        f"    </release>\n"
    )
    new_text, count = re.subn(
        r"(<releases>\s*\n)", lambda match: match.group(1) + entry, text, count=1
    )
    if count == 0:
        raise SystemExit("no <releases> element found in metainfo")
    return new_text, True


def update_file(path, transform):
    old_text = path.read_text()
    new_text, changed = transform(old_text)
    return new_text, changed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Update the PyGlossary Flatpak manifest and metainfo "
        "to the latest upstream release."
    )
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MANIFEST, help="manifest path"
    )
    parser.add_argument(
        "--metainfo", type=Path, default=DEFAULT_METAINFO, help="metainfo path"
    )
    parser.add_argument(
        "--tag", help="release tag to use instead of the latest release"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show changes without writing"
    )
    args = parser.parse_args(argv)

    tag, date = release_info(args.tag)
    commit = resolve_commit(tag)
    print(f"latest release: {tag} ({date}) commit {commit}")

    manifest_text, manifest_changed = update_file(
        args.manifest, lambda text: update_manifest_text(text, tag, commit)
    )
    metainfo_text, metainfo_changed = update_file(
        args.metainfo, lambda text: update_metainfo_text(text, tag, date)
    )

    if not manifest_changed:
        print(f"{args.manifest.name}: already up to date")
    if not metainfo_changed:
        print(f"{args.metainfo.name}: already lists {tag}")

    if args.dry_run:
        if manifest_changed:
            print(f"would update {args.manifest.name} to tag {tag} ({commit})")
        if metainfo_changed:
            print(f"would add release {tag} ({date}) to {args.metainfo.name}")
        return 0

    if manifest_changed:
        args.manifest.write_text(manifest_text)
        print(f"updated {args.manifest.name}")
    if metainfo_changed:
        args.metainfo.write_text(metainfo_text)
        print(f"updated {args.metainfo.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
