import os
import re
import glob
import struct
import winreg
import vdf
import uuid
from pathlib import Path
from datetime import datetime, timezone
from functools import lru_cache
from typing import Dict, Optional
import mimetypes
from .appinfo import Appinfo

# ----------------------------- Constants ----------------------------- #

ICON_RE = re.compile(r"^[a-f0-9]{40}\.(jpg|png)$")

STEAM_ASSET_PREFIXES = {
    "library_capsule": ("library_600x900", "library_capsule"),
    "library_header": ("header",),
    "library_hero": ("library_hero",),
    "library_hero_blur": ("library_hero_blur",),
    "library_logo": ("logo",),
    "library_icon": None,
}

STEAMID_OFFSET = 76561197960265728

# ----------------------------- Steam Environment ----------------------------- #

class SteamEnvironment:
    def __init__(self):
        self.root = self.get_steam_root()

    @staticmethod
    @lru_cache(maxsize=1)
    def get_steam_root() -> str:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Valve\Steam"
            )
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            return os.path.normpath(value)
        except FileNotFoundError:
            raise RuntimeError("Steam registry key not found")

    @lru_cache(maxsize=1)
    def library_folders(self):
        folders = [os.path.join(self.root, "steamapps")]
        vdf_file = os.path.join(self.root, "steamapps", "libraryfolders.vdf")

        if os.path.exists(vdf_file):
            try:
                with open(vdf_file, "r", encoding="utf-8") as f:
                    data = vdf.load(f)

                for entry in data.get("libraryfolders", {}).values():
                    path = entry.get("path")
                    if path:
                        folders.append(os.path.join(path, "steamapps"))
            except Exception:
                pass

        return tuple(os.path.normpath(p) for p in folders if os.path.exists(p))

    def appinfo_file(self) -> Optional[str]:
        path = os.path.join(self.root, "appcache", "appinfo.vdf")
        return path if os.path.exists(path) else None

    def librarycache(self) -> Path:
        return Path(self.root) / "appcache" / "librarycache"

# ----------------------------- Steam User ----------------------------- #

class SteamUser:
    def __init__(self, username: str, steamid32: str):
        self.username = username
        self.steamid32 = steamid32

    def get(self, key: str):
        if key == "Username":
            return self.username
        if key == "ID":
            return self.steamid32
        return None

# ----------------------------- Steam User Manager ----------------------------- #

class SteamUserManager:
    def __init__(self, env: SteamEnvironment):
        self.env = env

    @lru_cache(maxsize=1)
    def _login_data(self):
        file = os.path.join(self.env.root, "config", "loginusers.vdf")
        with open(file, "r", encoding="utf-8") as f:
            return vdf.load(f)

    def get_users(self, all_users: bool = False) -> Dict[str, SteamUser]:
        data = self._login_data().get("users", {})
        users = {}

        for sid64, details in data.items():
            persona = details.get("PersonaName", "Unknown")
            sid32 = str(int(sid64) - STEAMID_OFFSET)
            users[persona] = SteamUser(persona, sid32)

        if all_users:
            return users

        for sid64, details in data.items():
            if details.get("MostRecent") == "1":
                persona = details.get("PersonaName", "Unknown")
                sid32 = str(int(sid64) - STEAMID_OFFSET)
                return {persona: SteamUser(persona, sid32)}

        return users

# ----------------------------- Steam App Library ----------------------------- #

class SteamAppLibrary:
    def __init__(self, env: SteamEnvironment):
        self.env = env
        self.appinfo = Appinfo(env.appinfo_file())

    def get_installed_steam_apps(self) -> Dict[str, dict]:
        apps = {}

        for steamapps in self.env.library_folders():
            for manifest in glob.glob(os.path.join(steamapps, "appmanifest_*.acf")):
                match = re.search(r"appmanifest_(\d+)", manifest)
                if not match:
                    continue

                appID = match.group(1)
                try:
                    info = self.appinfo.parsedAppInfo[int(appID)]["sections"]["appinfo"]
                    name = info["common"]["name"]
                    # Generate a UUID from appID + name
                    uid_str = f"{appID}|{name}"
                    app_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, uid_str)).upper()

                    apps[app_uuid] = {
                        "uuid": app_uuid,
                        "appID": appID,
                        "name": name,
                        "source": "steam",
                        "launch": f"steam://rungameid/{appID}",
                        "type": info["common"].get("type"),
                    }
                except Exception:
                    continue

        return apps
    
    def get_nonsteam_apps(self, user: SteamUser) -> Dict[str, dict]:
        apps = {}
        shortcut_file = (
            Path(self.env.root)
            / "userdata"
            / user.steamid32
            / "config"
            / "shortcuts.vdf"
        )

        if not shortcut_file.exists():
            return apps

        def convert_nonsteam_id(appID32: int) -> int:
            return ((appID32 & 0xFFFFFFFF) << 32) | 0x02000000

        with open(shortcut_file, "rb") as f:
            shortcuts = vdf.binary_load(f)

        for entry in shortcuts.get("shortcuts", {}).values():
            name = entry.get("AppName", "Unknown Shortcut")
            raw_appID = entry.get("appid", 0)

            if isinstance(raw_appID, int):
                raw_bytes = raw_appID.to_bytes(4, "little", signed=True)
            elif isinstance(raw_appID, bytes):
                raw_bytes = raw_appID[:4].ljust(4, b"\x00")
            else:
                raw_bytes = (hash(name) & 0xFFFFFFFF).to_bytes(4, "little")

            appID32 = struct.unpack("<I", raw_bytes)[0]
            appID64 = convert_nonsteam_id(appID32)

            # Generate UUID from appID64 + name
            uid_str = f"{appID64}|{name}"
            app_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, uid_str)).upper()

            apps[app_uuid] = {
                "uuid": app_uuid,
                "name": name,
                "source": "nonsteam",
                "appID": str(appID64),
                "launch": f"steam://rungameid/{appID64}",
                "type": "game",
            }

        return apps

# ----------------------------- Steam Asset Manager ----------------------------- #

class SteamAssetManager:
    def __init__(self, env: SteamEnvironment):
        self.env = env
        self.appinfo = Appinfo(env.appinfo_file())

    @staticmethod
    def is_image_file(f: Path) -> bool:
        mime_type, _ = mimetypes.guess_type(f)
        return mime_type is not None and mime_type.startswith("image/")

    def get_steam_assets(self, appID: int) -> Dict[str, str]:
        assets: Dict[str, str] = {}
        librarycache = self.env.librarycache()
        app_dir = librarycache / str(appID)

        common = (
            self.appinfo.parsedAppInfo
            .get(appID, {})
            .get("sections", {})
            .get("appinfo", {})
            .get("common", {})
        )

        # First, include assets from the appinfo JSON
        assets_full = common.get("library_assets_full", {})
        for asset_type, data in assets_full.items():
            image = data.get("image", {}).get("english")
            if image:
                img_path = app_dir / image
                if img_path.exists() and self.is_image_file(img_path):
                    assets[asset_type] = str(img_path.resolve())

        if not app_dir.exists():
            return assets

        files = list(app_dir.iterdir())

        # Iterate over asset types and match based on prefixes
        for asset_type, prefixes in STEAM_ASSET_PREFIXES.items():
            if asset_type in assets:
                continue  # already set from appinfo

            if asset_type == "library_icon":
                # Match any image file with a 40-char hex filename
                for f in files:
                    if ICON_RE.match(f.name) and self.is_image_file(f):
                        assets[asset_type] = str(f)
                        break
                continue

            if prefixes:
                for f in files:
                    if f.name.startswith(prefixes) and self.is_image_file(f):
                        assets[asset_type] = str(f)
                        break

        return assets

    def get_nonsteam_assets(self, appID64: str, user: SteamUser) -> dict[str, str]:
        assets = {}
        grid = Path(self.env.root) / "userdata" / user.steamid32 / "config" / "grid"
        if not grid.exists():
            return assets

        appID32 = str(int(appID64) >> 32)

        # Mapping of asset keys to filename prefixes
        patterns = {
            "library_header": f"{appID32}",
            "library_capsule": f"{appID32}p",
            "library_hero": f"{appID32}_hero",
            "library_icon": f"{appID32}_icon",
            "library_logo": f"{appID32}_logo",
        }

        # Iterate all files in grid folder
        for f in grid.iterdir():
            if not f.is_file() or not self.is_image_file(f):
                continue  # skip non-images

            name = f.stem
            for asset_key, pattern in patterns.items():
                # Only set the asset if not already assigned
                if asset_key not in assets and name.startswith(pattern):
                    assets[asset_key] = str(f)

        return assets