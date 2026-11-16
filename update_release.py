#!/usr/bin/env python3
"""Create a pull request that updates PyGlossary to the latest release."""

import argparse
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = "ilius/pyglossary"
ATOM_URL = f"https://github.com/{REPO}/releases.atom"
ATOM = "{http://www.w3.org/2005/Atom}"
ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "io.github.ilius.pyglossary.yml"
DEFAULT_METAINFO = ROOT / "io.github.ilius.pyglossary.metainfo.xml"


def fetch_text(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "pyglossary-flathub-release-updater"}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"request to {url} failed: {error.code} {error.reason}"
        ) from error


def release_date(page_url):
    text = fetch_text(page_url)
    match = re.search(r'datetime="(\d{4}-\d{2}-\d{2})T', text)
    return match.group(1) if match else None


def release_info(tag=None):
    if tag:
        page_url = (
            f"https://github.com/{REPO}/releases/tag/"
            f"{urllib.parse.quote(tag, safe='')}"
        )
        updated = ""
    else:
        root = ET.fromstring(fetch_text(ATOM_URL))
        entry = root.find(f"{ATOM}entry")
        if entry is None:
            raise SystemExit("no releases found in atom feed")
        tag = (entry.findtext(f"{ATOM}title") or "").strip()
        updated = entry.findtext(f"{ATOM}updated") or ""
        link = entry.find(f"{ATOM}link")
        page_url = link.get("href") if link is not None else ""
        if not tag or not page_url:
            raise SystemExit("could not parse the latest release from atom feed")

    date = release_date(page_url)
    if not date and updated:
        date = updated[:10]
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return tag, date


def resolve_commit(tag):
    result = subprocess.run(
        [
            "git",
            "ls-remote",
            f"https://github.com/{REPO}.git",
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"git ls-remote failed:\n{detail}")

    refs = {}
    for line in result.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        refs[ref] = sha
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not commit:
        raise SystemExit(f"tag {tag!r} not found in {REPO}")
    return commit


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


def run_git(*args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(f"git {' '.join(args)} failed:\n{detail}")
    return result


def current_branch():
    return run_git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def branch_exists(branch):
    result = run_git(
        "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
    )
    return result.returncode == 0


def ensure_clean():
    status = run_git("status", "--porcelain").stdout.strip()
    if status:
        raise SystemExit(f"working tree is not clean:\n{status}")


def git_path(path):
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def remote_base_url(remote):
    url = run_git("remote", "get-url", remote).stdout.strip()
    match = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", url)
    if match:
        host, path = match.groups()
        return f"https://{host}/{path}"
    match = re.match(r"^ssh://git@([^/]+)/(.+?)(?:\.git)?$", url)
    if match:
        host, path = match.groups()
        return f"https://{host}/{path}"
    match = re.match(r"^https?://([^/]+)/(.+?)(?:\.git)?$", url)
    if match:
        host, path = match.groups()
        return f"https://{host}/{path}"
    raise SystemExit(f"could not derive a web URL from remote {remote!r}: {url}")


def pull_request_url(remote, branch):
    base = remote_base_url(remote)
    return f"{base}/pull/new/{urllib.parse.quote(branch, safe='')}"


def open_in_browser(url):
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        print(f"open this URL to create the pull request: {url}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create a branch that updates the PyGlossary Flatpak "
        "manifest and metainfo to the latest upstream release, commit it, "
        "push it, and open the pull request page."
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
    parser.add_argument("--branch", help="branch name (default: release-<tag>)")
    parser.add_argument("--base", help="base branch or commit to branch from")
    parser.add_argument("--remote", default="origin", help="git remote to push to")
    parser.add_argument(
        "--no-push", action="store_true", help="commit locally without pushing"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the pull request URL instead of opening it",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show changes without writing"
    )
    args = parser.parse_args(argv)

    tag, date = release_info(args.tag)
    commit = resolve_commit(tag)
    print(f"latest release: {tag} ({date}) commit {commit}")

    manifest_text, manifest_changed = update_manifest_text(
        args.manifest.read_text(), tag, commit
    )
    metainfo_text, metainfo_changed = update_metainfo_text(
        args.metainfo.read_text(), tag, date
    )

    if not manifest_changed and not metainfo_changed:
        print(f"already up to date at {tag}")
        return 0

    if args.dry_run:
        if manifest_changed:
            print(f"would update {args.manifest.name} to tag {tag} ({commit})")
        if metainfo_changed:
            print(f"would add release {tag} ({date}) to {args.metainfo.name}")
        return 0

    ensure_clean()
    branch = args.branch or f"release-{tag}"
    if branch_exists(branch):
        raise SystemExit(f"branch {branch!r} already exists")

    if args.base:
        run_git("checkout", "-b", branch, args.base)
    else:
        run_git("checkout", "-b", branch)

    if manifest_changed:
        args.manifest.write_text(manifest_text)
    if metainfo_changed:
        args.metainfo.write_text(metainfo_text)

    paths = [
        git_path(path)
        for path, changed in (
            (args.manifest, manifest_changed),
            (args.metainfo, metainfo_changed),
        )
        if changed
    ]
    run_git("add", *paths)
    run_git("commit", "-m", f"Release {tag}")
    print(f"committed release {tag} on branch {branch}")

    pr_url = None
    if not args.no_push:
        run_git("push", "-u", args.remote, branch)
        print(f"pushed {branch} to {args.remote}")

    if not args.no_push or not args.no_browser:
        pr_url = pull_request_url(args.remote, branch)

    if args.no_browser:
        if pr_url:
            print(pr_url)
    else:
        open_in_browser(pr_url)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
