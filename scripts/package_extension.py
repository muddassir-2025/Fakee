#!/usr/bin/env python3
"""Package the Fakee Chrome extension for the Chrome Web Store.

Builds a store-ready zip containing only the runtime files (no README, no
`tools/`, no dotfiles), and optionally bakes your deployed backend and website
URLs in as the defaults so a fresh install does not point at ``localhost``.

The zip has ``manifest.json`` at its root, which is what the Web Store expects.

Usage
-----
    python scripts/package_extension.py \\
        --api-base https://api.example.com/api \\
        --site-url https://example.com
    python scripts/package_extension.py --allow-localhost   # dev/test zip

Security note: only URLs are baked in. No keys are embedded — the extension
never ships a Groq/search key.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = REPO_ROOT / "extension"
IGNORED_NAMES = {"__MACOSX", ".DS_Store"}

# Everything the extension needs at runtime. Dev-only files (README.md,
# tools/make-icons.mjs, this script's own outputs) are deliberately excluded.
RUNTIME_FILES = [
    "manifest.json",
    "background.js",
    "offscreen.html",
    "offscreen.js",
    "sidepanel.html",
    "sidepanel.css",
    "sidepanel.js",
]

# Which files carry which localhost default, and how to spot it. A store build
# rewrites these; if a default is ever reworded the build fails loudly rather
# than shipping a zip that still points at localhost.
LOCALHOST_API_BASE = "http://localhost:8000/api"
LOCALHOST_SITE_URL = "http://localhost:5173"
PATCH_PATTERNS: dict[str, dict[str, re.Pattern[str]]] = {
    "offscreen.js": {
        "api base": re.compile(r'(apiBase:\s*)"http://localhost:8000/api"'),
    },
    "sidepanel.js": {
        "api base": re.compile(r'(apiBase:\s*)"http://localhost:8000/api"'),
        "site URL": re.compile(r'(siteUrl:\s*)"http://localhost:5173"'),
    },
}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def collect_files() -> list[Path]:
    """Return the runtime files, verifying each exists."""
    files: list[Path] = []
    for name in RUNTIME_FILES:
        path = EXT_DIR / name
        if not path.is_file():
            fail(f"missing required extension file: extension/{name}")
        files.append(path)

    icons = sorted((EXT_DIR / "icons").glob("*.png"))
    if not icons:
        fail("no icons found in extension/icons (run `node tools/make-icons.mjs` first)")
    files.extend(icons)
    return files


def check_manifest_references(manifest: dict) -> None:
    """Every file the manifest points at must exist (catches broken builds)."""
    referenced: list[str] = []
    for key in ("icons",):
        referenced.extend((manifest.get(key) or {}).values())
    action = manifest.get("action") or {}
    referenced.extend((action.get("default_icon") or {}).values())
    if action.get("default_popup"):
        referenced.append(action["default_popup"])
    if manifest.get("side_panel", {}).get("default_path"):
        referenced.append(manifest["side_panel"]["default_path"])
    if manifest.get("background", {}).get("service_worker"):
        referenced.append(manifest["background"]["service_worker"])
    if manifest.get("options_page"):
        referenced.append(manifest["options_page"])

    missing = [ref for ref in referenced if not (EXT_DIR / ref).is_file()]
    if missing:
        fail("manifest references missing files: " + ", ".join(missing))


def patch_defaults(
    rel: str,
    text: str,
    *,
    api_base: str | None,
    site_url: str | None,
) -> str:
    """Rewrite the localhost defaults this file carries.

    Only what was asked for is touched, so a dev test zip keeps localhost. A
    requested replacement whose pattern is missing is an error, not silence.
    """
    values = {"api base": api_base, "site URL": site_url}
    for label, pattern in PATCH_PATTERNS.get(rel, {}).items():
        value = values[label]
        if not value:
            continue
        text, count = pattern.subn(rf'\g<1>"{value}"', text)
        if not count:
            fail(f"could not find the default {label} in extension/{rel} — the pattern changed")
    return text


def build_zip(out_path: Path, api_base: str | None, site_url: str | None) -> None:
    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    check_manifest_references(manifest)

    version = manifest.get("version", "0.0.0")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in collect_files():
            rel = path.relative_to(EXT_DIR).as_posix()
            data = path.read_bytes()

            if rel in PATCH_PATTERNS:
                data = patch_defaults(
                    rel,
                    data.decode("utf-8"),
                    api_base=api_base,
                    site_url=site_url,
                ).encode("utf-8")

            # Arcname is the path relative to the extension root, so
            # manifest.json sits at the zip root as the store requires.
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

    print(f"wrote {out_path.relative_to(REPO_ROOT).as_posix()} (v{version})")


def read_defaults(archive: zipfile.ZipFile, rel: str, *, require: tuple[str, ...] = ("apiBase",)) -> dict[str, str]:
    """Read the baked-in default URLs back out of a packaged script."""
    body = archive.read(rel).decode("utf-8")
    found: dict[str, str] = {}
    for key in ("apiBase", "siteUrl"):
        match = re.search(rf'{key}:\s*"([^"]+)"', body)
        if match:
            found[key] = match.group(1)
    missing = [key for key in require if key not in found]
    if missing:
        fail(f"could not read {', '.join(missing)} back out of the packaged extension/{rel}")
    return found


def verify_zip(out_path: Path) -> None:
    """Read the zip back and confirm the store-critical invariants."""
    with zipfile.ZipFile(out_path) as archive:
        names = archive.namelist()

        if "manifest.json" not in names:
            fail("packaged zip has no manifest.json at its root")

        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))

        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            fail("packaged zip contains unsafe paths")

        leaked = [n for n in names if Path(n).name in IGNORED_NAMES]
        if leaked:
            fail("packaged zip contains ignored files: " + ", ".join(leaked))

        if any(n.startswith("tools/") or n.endswith("README.md") for n in names):
            fail("packaged zip leaked dev-only files")

        # Every icon the manifest advertises must be inside the archive.
        for icon in (manifest.get("icons") or {}).values():
            if icon not in names:
                fail(f"manifest advertises {icon} but it is not in the zip")

        # Confirm the baked-in defaults actually made it in: the zip is the only
        # artifact the store sees, so it has to carry the deployment asked for.
        if "offscreen.js" not in names:
            fail("packaged zip has no offscreen.js")
        print(f"packaged default API base: {read_defaults(archive, 'offscreen.js')['apiBase']}")
        if "sidepanel.js" in names:
            site = read_defaults(archive, "sidepanel.js").get("siteUrl")
            if site:
                print(f"packaged default website:  {site}")

        print(f"verified {len(names)} files in archive")


def main() -> int:
    parser = argparse.ArgumentParser(description="Package the Fakee extension for the Web Store.")
    parser.add_argument(
        "--api-base",
        help="Bake this backend base into the default settings (e.g. "
        "https://api.example.com/api). Omit to keep the built-in default.",
    )
    parser.add_argument(
        "--site-url",
        help="Bake this website URL into the default settings (e.g. "
        "https://fakee-five.vercel.app). It is where the panel signs in and gets its "
        "report session. Omit to keep the built-in default.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(REPO_ROOT / "dist"),
        help="Directory for the zip (default: ./dist).",
    )
    parser.add_argument(
        "--allow-localhost",
        action="store_true",
        help="Do not warn when the packaged build still points at localhost.",
    )
    args = parser.parse_args()

    api_base = args.api_base.rstrip("/") if args.api_base else None
    site_url = args.site_url.rstrip("/") if args.site_url else None
    for flag, value in (("--api-base", api_base), ("--site-url", site_url)):
        if value and not re.match(r"^https?://", value):
            fail(f"{flag} must start with http:// or https://")

    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("version", "0.0.0")
    out_path = Path(args.out_dir).resolve() / f"fakee-extension-{version}.zip"

    build_zip(out_path, api_base, site_url)
    verify_zip(out_path)

    with zipfile.ZipFile(out_path) as archive:
        packaged_api = read_defaults(archive, "offscreen.js")["apiBase"]
        packaged_site = read_defaults(archive, "sidepanel.js").get("siteUrl")

    if not args.allow_localhost:
        stale = []
        if packaged_api == LOCALHOST_API_BASE:
            stale.append("--api-base https://<your-backend>/api")
        if packaged_site == LOCALHOST_SITE_URL:
            stale.append("--site-url https://<your-site>")
        if stale:
            print(
                "\nwarning: the packaged build still points at localhost, so real users\n"
                "         cannot reach it. Re-run with " + " ".join(stale) + " before uploading.",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
