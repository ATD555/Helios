import json
import winreg
from pathlib import Path
from typing import Dict, Optional, List, Any


# =====================================================================
#                           Environment Apps JSON
# =====================================================================

class EnvironmentAppsJSON:
    """
    Generic handler for any environment's apps.json file.
    Apollo, Sunshine, or future environments all use this.
    """

    def __init__(self, apps_json_path: str, root: Optional[str] = None):
        self.apps_json_path = Path(apps_json_path)
        self.root = Path(root) if root else None

        # Create empty apps.json if missing
        if not self.apps_json_path.exists():
            self.apps_json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.apps_json_path, "w", encoding="utf-8") as f:
                json.dump({"apps": []}, f, indent=4)

        self.data = self._load_apps_json()
        self.apps: List[dict] = self.data.get("apps", [])
        self.by_uuid: Dict[str, dict] = {
            app["uuid"]: app for app in self.apps if "uuid" in app
        }

    def _load_apps_json(self) -> dict:
        if not self.apps_json_path.exists():
            raise FileNotFoundError(f"apps.json not found: {self.apps_json_path}")

        with open(self.apps_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or "apps" not in data:
            raise RuntimeError(f"Invalid apps.json structure in {self.apps_json_path}")

        return data

    def save(self) -> None:
        with open(self.apps_json_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def get_app_by_uuid(self, uuid: str) -> Optional[dict]:
        return self.by_uuid.get(uuid)

    def list_uuids(self) -> List[str]:
        return list(self.by_uuid.keys())

    def list_apps(self) -> List[dict]:
        return self.apps

    def filter_apps(self, **criteria) -> List[dict]:
        return [
            app for app in self.apps
            if all(app.get(k) == v for k, v in criteria.items())
        ]

    def remove_app_by_uuid(self, uuid: str) -> bool:
        if uuid not in self.by_uuid:
            return False

        self.apps = [a for a in self.apps if a.get("uuid") != uuid]
        self.data["apps"] = self.apps
        self.by_uuid.pop(uuid, None)
        return True

    def get_image_path(self, uuid: str) -> Optional[Path]:
        app = self.get_app_by_uuid(uuid)
        if not app:
            return None

        img_path = app.get("image-path")
        if not img_path:
            return None

        img_path = Path(img_path)
        if img_path.is_absolute():
            return img_path

        if self.root:
            return self.root / "assets" / img_path

        return img_path


# =====================================================================
#                           Environment Registry (Dynamic)
# =====================================================================

KNOWN_ENVIRONMENTS: Dict[str, Dict[str, Any]] = {
    "Apollo": {
        "aliases": ["apollo", "vibepollo"],
        "registry_uninstall": [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ],
        "registry_direct": None,
        "config_files": ["apollo.conf", "sunshine.conf"],
        "apps_json": lambda root: root / "Config" / "apps.json",
    },

    "Sunshine": {
        "aliases": ["sunshine"],
        "registry_uninstall": [],  # Sunshine does NOT register here
        "registry_direct": r"SOFTWARE\LizardByte\Sunshine",
        "config_files": ["sunshine.conf", "apollo.conf"],
        "apps_json": lambda root: root / "Config" / "apps.json",
    },
}


# =====================================================================
#                           Environment Matching
# =====================================================================

def match_environment_from_displayname(display_name: str) -> Optional[str]:
    if not display_name:
        return None

    name = display_name.lower()

    for env_name, meta in KNOWN_ENVIRONMENTS.items():
        for alias in meta["aliases"]:
            if alias in name:
                return env_name

    return None

def find_matching_config_file(path: Path, config_files: List[str]) -> Optional[Path]:
    """
    Returns the full path to the first matching config file, or None.
    """
    for filename in config_files:
        lower = path / "config" / filename
        upper = path / "Config" / filename

        if lower.is_file():
            return lower
        if upper.is_file():
            return upper

    return None

# =====================================================================
#                           Dynamic Environment Detection
# =====================================================================

def find_environment_installs(environment: str = "all") -> List[Dict[str, Any]]:
    """
    Detect installations of any known environment (Apollo, Sunshine, etc.)

    Args:
        environment: "Apollo", "Sunshine", or "all"

    Returns:
        A list of dicts with:
            - name
            - display_name
            - root
            - apps_json
    """
    installs: List[Dict[str, Any]] = []
    environment = environment.lower()

    # ------------------------ Loop Through Environments Dynamically ------------------------
    for env_name, meta in KNOWN_ENVIRONMENTS.items():
        if environment not in ("all", env_name.lower()):
            continue

        config_files = meta["config_files"]

        # ------------------------ Uninstall Registry Detection ------------------------
        for reg_path in meta["registry_uninstall"]:
            try:
                root_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
            except OSError:
                continue

            for i in range(winreg.QueryInfoKey(root_key)[0]):
                try:
                    sub = winreg.OpenKey(root_key, winreg.EnumKey(root_key, i))
                    display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                    install_loc, _ = winreg.QueryValueEx(sub, "InstallLocation")
                except OSError:
                    continue

                if not display_name or not install_loc:
                    continue

                if match_environment_from_displayname(display_name) != env_name:
                    continue

                path = Path(install_loc)
                config_path = find_matching_config_file(path, config_files)
                if not config_path:
                    continue


                installs.append({
                    "name": env_name,
                    "display_name": display_name.strip(),
                    "root": path,
                    "apps_json": meta["apps_json"](path),
                    "config_file": config_path,
                })

        # ------------------------ Direct Registry Key Detection ------------------------
        if meta["registry_direct"]:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, meta["registry_direct"])
                install_path, _ = winreg.QueryValueEx(key, "")
                install_path = Path(install_path)
                
                config_path = find_matching_config_file(install_path, config_files)
                if config_path:
                    installs.append({
                        "name": env_name,
                        "display_name": env_name,
                        "root": install_path,
                        "apps_json": meta["apps_json"](install_path),
                        "config_file": config_path,
                    })

            except OSError:
                pass

    return installs
