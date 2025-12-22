import json
import re
import winreg
from pathlib import Path
from typing import Dict, Optional, List, Any


# ----------------------------- Apollo Apps JSON ----------------------------- #
class ApolloAppsJSON:
    def __init__(self, apps_json_path: str, root: Optional[str] = None):
        """
        :param apps_json_path: Path to apps.json
        :param root: Optional root path of Apollo installation for resolving relative assets
        """
        self.apps_json_path = Path(apps_json_path)
        self.root = Path(root) if root else None
        self.data = self._load_apps_json()
        self.apps: List[dict] = self.data.get("apps", [])
        # Build a lookup by UUID for easy access
        self.by_uuid: Dict[str, dict] = {app["uuid"]: app for app in self.apps if "uuid" in app}

    # --------------------- Load / Save --------------------- #
    def _load_apps_json(self) -> dict:
        if not self.apps_json_path.exists():
            raise FileNotFoundError(f"apps.json not found: {self.apps_json_path}")
        with open(self.apps_json_path, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON in {self.apps_json_path}: {e}") from e
        if not isinstance(data, dict) or "apps" not in data:
            raise RuntimeError(f"Invalid apps.json structure in {self.apps_json_path}")
        return data

    def save(self) -> None:
        """Write current in-memory data back to apps.json"""
        with open(self.apps_json_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    # --------------------- Querying --------------------- #
    def get_app_by_uuid(self, uuid: str) -> Optional[dict]:
        """Return app dictionary for a given UUID, or None if not found."""
        return self.by_uuid.get(uuid)

    def list_uuids(self) -> List[str]:
        """Return a list of all UUIDs in the apps.json"""
        return list(self.by_uuid.keys())

    def list_apps(self) -> List[dict]:
        """Return the full list of apps"""
        return self.apps

    # --------------------- Filtering --------------------- #
    def filter_apps(self, **criteria) -> List[dict]:
        """
        Filter apps by key-value pairs.
        Example: filter_apps(name="Steam Big Picture")
        """
        results = []
        for app in self.apps:
            match = True
            for key, val in criteria.items():
                if app.get(key) != val:
                    match = False
                    break
            if match:
                results.append(app)
        return results

    def remove_app_by_uuid(self, uuid: str) -> bool:
        """
        Remove an app by UUID from the in-memory structure and update lookup.
        Returns True if removed, False if UUID not found.
        """
        app = self.by_uuid.pop(uuid, None)
        if not app:
            return False
        self.apps = [a for a in self.apps if a.get("uuid") != uuid]
        self.data["apps"] = self.apps
        return True

    # --------------------- Image Path Resolution --------------------- #
    def get_image_path(self, uuid: str) -> Optional[Path]:
        """
        Return the full path to the app's image.
        - Absolute path is returned as-is
        - Relative filename resolves to <root>/assets/<filename>
        """
        app = self.get_app_by_uuid(uuid)
        if not app:
            return None

        img_path = app.get("image-path")
        if not img_path:
            return None

        img_path = Path(img_path)
        if img_path.is_absolute():
            return img_path
        elif self.root:
            # Resolve relative to root/assets
            return self.root / "assets" / img_path
        else:
            # Cannot resolve relative path without root
            return img_path


# ----------------------------- Apollo Install Detection ----------------------------- #

KNOWN_APOLLO_FORKS: Dict[str, Dict[str, List[str]]] = {
    "Apollo": {"aliases": ["apollo", "vibepollo"]},
    "Sunshine": {"aliases": ["sunshine"]},
}


def displayname_matches_apollo(display_name: str) -> Optional[str]:
    """
    Returns the canonical fork key if matched, else None.
    """
    if not display_name:
        return None

    name = display_name.lower()
    for fork, meta in KNOWN_APOLLO_FORKS.items():
        for alias in meta["aliases"]:
            if alias in name:
                return fork
    return None


def is_valid_apollo_install(path: Path) -> bool:
    """
    Valid Apollo install must contain Config/apps.json
    """
    return (path / "Config" / "apps.json").is_file()


def find_apollo_install() -> List[Dict[str, Any]]:
    """
    Returns a list of dictionaries describing Apollo installs.

    Each dict contains:
        - fork
        - display_name
        - root
        - apps_json
    """
    installs: List[Dict[str, Any]] = []

    registry_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]

    for base in registry_paths:
        try:
            root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
        except OSError:
            continue

        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
                display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                install_loc, _ = winreg.QueryValueEx(sub, "InstallLocation")
            except OSError:
                continue

            name = displayname_matches_apollo(display_name)
            if not name or not install_loc:
                continue

            path = Path(install_loc)
            if not is_valid_apollo_install(path):
                continue

            installs.append({
                "name": name,
                "display_name": display_name.strip(),
                "root": path,
                "apps_json": path / "Config" / "apps.json",
            })

    return installs