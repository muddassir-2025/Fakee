#!/usr/bin/env python3
"""Package the Fakee Chrome extension for the Chrome Web Store.

Builds a store-ready zip containing only the runtime files (no README, no
`tools/`, no dotfiles), and optionally bakes your deployed backend URL in as
the default so a fresh install does not point at ``localhost``.

The zip has ``manifest.json`` at its root, which is what the Web Store expects.

Usage
-----
    python scripts/package_extension.py --api-base https://api.example.com/api
    python scripts/package_extension.py            # keeps the built-in default

Security note: only the API *base* is baked in. No keys are embedded — the
extension never ships a Groq/search key.
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

# The default backend base lives in these files; a store build should point at
# the deployed backend instead of localhost.
API_BASE_FILES = ["offscreen.js", "sidepanel.js"]
API_BASE_PATTERN = re.compile(r'(apiBase:\s*)"http://localhost:8000/api"')
LOCALHOST_API_BASE = "http://localhost:8000/api"


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


def rewrite_api_base(text: str, api_base: str) -> tuple[str, int]:
    patched, count = API_BASE_PATTERN.subn(rf'\g<1>"{api_base}"', text)
    return patched, count


def build_zip(out_path: Path, api_base: str | None) -> None:
    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    check_manifest_references(manifest)

    version = manifest.get("version", "0.0.0")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    patched_anywhere = False
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in collect_files():
            rel = path.relative_to(EXT_DIR).as_posix()
            data = path.read_bytes()

            if api_base and rel in API_BASE_FILES:
                text = data.decode("utf-8")
                text, count = rewrite_api_base(text, api_base)
                patched_anywhere = patched_anywhere or count > 0
                data = text.encode("utf-8")

            # Arcname is the path relative to the extension root, so
            # manifest.json sits at the zip root as the store requires.
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

    if api_base and not patched_anywhere:
        fail(
            "could not find the default API base to rewrite — the pattern in "
            "extension/offscreen.js may have changed"
        )

    print(f"wrote {out_path.relative_to(REPO_ROOT).as_posix()} (v{version})")


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

        # Confirm the API base actually made it in.
        if "offscreen.js" in names:
            body = archive.read("offscreen.js").decode("utf-8")
            match = re.search(r'apiBase:\s*"([^"]+)"', body)
            if not match:
                fail("could not read apiBase back out of the packaged offscreen.js")
            print(f"packaged default API base: {match.group(1)}")

        print(f"verified {len(names)} files in archive")


def main() -> int:
    parser = argparse.ArgumentParser(description="Package the Fakee extension for the Web Store.")
    parser.add_argument(
        "--api-base",
        help="Bake this backend base into the default settings (e.g. "
        "https://api.example.com/api). Omit to keep the built-in default.",
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
    if api_base and not re.match(r"^https?://", api_base):
        fail("--api-base must start with http:// or https://")

    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("version", "0.0.0")
    out_path = Path(args.out_dir).resolve() / f"fakee-extension-{version}.zip"

    build_zip(out_path, api_base)
    verify_zip(out_path)

    with zipfile.ZipFile(out_path) as archive:
        body = archive.read("offscreen.js").decode("utf-8")
        packaged_base = re.search(r'apiBase:\s*"([^"]+)"', body).group(1)

    if packaged_base == LOCALHOST_API_BASE and not args.allow_localhost:
        print(
            "\nwarning: the packaged build still points at localhost.\n"
            "         Real users cannot reach it. Re-run with "
            "--api-base https://<your-backend>/api before uploading.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
