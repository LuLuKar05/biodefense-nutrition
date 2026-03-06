"""
profile_manager.py — Local profile persistence & cross-channel linking
======================================================================
Handles:
  - Full profiles:    data/profiles/<user_id>.json
  - Partial profiles: data/profiles/<user_id>.partial.json
  - Link map:         data/profiles/links.json

All health data stays on the user's machine. Zero-knowledge.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT: Path = Path(__file__).resolve().parent.parent.parent
PROFILES_DIR: Path = ROOT / "data" / "profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)
LINKS_FILE: Path = PROFILES_DIR / "links.json"


# ── Link map helpers ────────────────────────────────────────

def _load_links() -> dict[str, str]:
    """Load channel-link map: {secondary_channel_id: primary_user_id}."""
    if LINKS_FILE.exists():
        try:
            return json.loads(LINKS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_links(links: dict[str, str]) -> None:
    LINKS_FILE.write_text(json.dumps(links, indent=2), encoding="utf-8")


def resolve_user_id(channel_id: str) -> str:
    """Resolve a channel-specific ID to the primary user ID (or itself)."""
    links = _load_links()
    return links.get(str(channel_id), str(channel_id))


def link_channel(primary_id: str, secondary_id: str) -> None:
    """Link a secondary channel ID to a primary profile."""
    links = _load_links()
    links[str(secondary_id)] = str(primary_id)
    _save_links(links)


def get_link_code(user_id: str) -> str:
    """Generate a deterministic but short link code from user_id."""
    import hashlib
    h = hashlib.sha256(str(user_id).encode()).hexdigest()[:8].upper()
    return f"BDN-{h[:4]}-{h[4:]}"


def find_by_link_code(code: str) -> str | None:
    """Find user_id that matches a link code."""
    for profile_path in PROFILES_DIR.glob("*.json"):
        if profile_path.name in ("links.json",):
            continue
        uid = profile_path.stem.replace(".partial", "")
        if get_link_code(uid) == code.upper().strip():
            return uid
    return None


# ── Partial profile (onboarding in progress) ───────────────

def _partial_path(user_id: str) -> Path:
    return PROFILES_DIR / f"{user_id}.partial.json"


def _full_path(user_id: str) -> Path:
    return PROFILES_DIR / f"{user_id}.json"


def load_partial(user_id: str) -> dict[str, str]:
    """Load partial profile or return empty dict."""
    uid = resolve_user_id(user_id)
    path = _partial_path(uid)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("profile", {})
        except json.JSONDecodeError:
            return {}
    return {}


def save_partial(user_id: str, profile: dict[str, str]) -> Path:
    """Save partial profile to disk. Overwrites existing partial."""
    uid = resolve_user_id(user_id)
    path = _partial_path(uid)
    data = {
        "user_id": uid,
        "profile": profile,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial",
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def delete_partial(user_id: str) -> None:
    """Remove partial profile if it exists."""
    uid = resolve_user_id(user_id)
    path = _partial_path(uid)
    if path.exists():
        path.unlink()


# ── Full profile (onboarding complete) ─────────────────────

def load_profile(user_id: str) -> dict[str, str] | None:
    """Load completed profile or None if not found."""
    uid = resolve_user_id(user_id)
    path = _full_path(uid)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("profile", {})
        except json.JSONDecodeError:
            return None
    return None


def save_profile(user_id: str, profile: dict[str, str]) -> Path:
    """Save completed profile and remove partial."""
    uid = resolve_user_id(user_id)
    data: dict[str, Any] = {
        "user_id": uid,
        "profile": profile,
        "link_code": get_link_code(uid),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "version": "2.0",
    }
    path = _full_path(uid)

    # Preserve created_at if updating existing
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            data["created_at"] = old.get("created_at", data["created_at"])
        except json.JSONDecodeError:
            pass

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # Clean up partial
    delete_partial(uid)
    return path


def update_field(user_id: str, field: str, value: str) -> dict[str, str] | None:
    """Update a single field in an existing profile. Returns updated profile or None."""
    profile = load_profile(user_id)
    if profile is None:
        return None
    profile[field] = value
    save_profile(user_id, profile)
    return profile


def delete_profile(user_id: str) -> None:
    """Delete both full and partial profile."""
    uid = resolve_user_id(user_id)
    for path in (_full_path(uid), _partial_path(uid)):
        if path.exists():
            path.unlink()


def profile_exists(user_id: str) -> bool:
    """Check if a completed profile exists."""
    uid = resolve_user_id(user_id)
    return _full_path(uid).exists()


def has_partial(user_id: str) -> bool:
    """Check if a partial (in-progress) profile exists."""
    uid = resolve_user_id(user_id)
    return _partial_path(uid).exists()


def get_profile_or_partial(user_id: str) -> tuple[dict[str, str], bool]:
    """
    Load the best available profile data.
    Returns (profile_dict, is_complete).
    """
    uid = resolve_user_id(user_id)
    full = load_profile(uid)
    if full is not None:
        return full, True
    partial = load_partial(uid)
    return partial, False
