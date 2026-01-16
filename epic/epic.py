from __future__ import annotations
import json
import base64
import uuid
from pathlib import Path
from typing import Iterator, Optional

# ----------------------------- Constants ----------------------------- #
MANIFESTS = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
CATCACHE_BIN = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Catalog\catcache.bin")


# ----------------------------- Epic Item ----------------------------- #
class EpicItem:
    """Represents a single Epic Games .item manifest."""

    __slots__ = (
        "path", "data", "library", "uuid",
        "_name", "_app_name", "_catalog_namespace",
        "_catalog_item_id", "_install_location",
        "_launch_exe", "_launch_uri", "_is_game",
        "_is_installed", "_tech_type"
    )

    def __init__(self, path: Path, library: EpicLibrary | None = None):
        self.path = path
        self.data = self._load()
        self.library = library

        # Cache frequently accessed fields
        d = self.data
        self._name = d.get("DisplayName", "")
        self._app_name = d.get("AppName", "")
        self._catalog_namespace = d.get("CatalogNamespace", "")
        self._catalog_item_id = d.get("CatalogItemId", "")
        self._install_location = d.get("InstallLocation")
        self._launch_exe = d.get("LaunchExecutable")
        self._is_game = "games" in d.get("AppCategories", [])
        self._is_installed = not d.get("bIsIncompleteInstall", True)
        self._tech_type = d.get("TechnicalType", "").lower()

        # Precompute UUID
        uid_str = f"{self._catalog_item_id}|{self._app_name}"
        self.uuid = uuid.uuid5(uuid.NAMESPACE_OID, uid_str).hex.upper()

        # Precompute launch URI
        if self._catalog_namespace and self._catalog_item_id and self._app_name:
            self._launch_uri = (
                "com.epicgames.launcher://apps/"
                f"{self._catalog_namespace}%3A"
                f"{self._catalog_item_id}%3A"
                f"{self._app_name}"
                "?action=launch&silent=true"
            )
        else:
            self._launch_uri = None

    def _load(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    # -------- Cached Properties -------- #

    @property
    def name(self) -> str:
        return self._name

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def catalog_namespace(self) -> str:
        return self._catalog_namespace

    @property
    def catalog_item_id(self) -> str:
        return self._catalog_item_id

    @property
    def install_location(self) -> Optional[Path]:
        loc = self._install_location
        return Path(loc) if loc else None

    @property
    def executable(self) -> Optional[Path]:
        if self._launch_exe and self._install_location:
            return Path(self._install_location) / self._launch_exe
        return None

    @property
    def header_image_url(self) -> Optional[str]:
        if self.library:
            return self.library.get_image_url(self, "DieselGameBox")
        return None

    @property
    def launch_uri(self) -> Optional[str]:
        return self._launch_uri

    @property
    def is_game(self) -> bool:
        return self._is_game

    @property
    def is_installed(self) -> bool:
        return self._is_installed

    @property
    def image_url(self) -> Optional[str]:
        if self.library:
            return self.library.get_image_url(self)
        return None

    @property
    def item_type(self) -> str:
        tech = self._tech_type
        if "game" in tech:
            return "game"
        if "software" in tech:
            return "application"
        return "unknown"

    def to_app_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "name": self._name,
            "launch": self._launch_uri,
            "exe": str(self.executable) if self.executable else None,
            "install_dir": self._install_location,
            "source": "epic",
            "library_capsule": self.image_url,
            "library_header": self.header_image_url,
            "type": self.item_type,
        }

    def __repr__(self) -> str:
        return f"<EpicItem name={self._name!r}>"


# ----------------------------- Epic Library ----------------------------- #
class EpicLibrary:
    """Represents a collection of Epic Games installed items, plus optional .bin data."""

    def __init__(self, manifests_dir: Path = MANIFESTS, bin_path: Path = CATCACHE_BIN):
        self.manifests_dir = manifests_dir
        self.bin_path = bin_path

        self.catcache_data = self._load_bin() if bin_path.exists() else {}

        # Preload all items once
        items = list(self.iter_items())
        self.items_by_uuid: dict[str, EpicItem] = {item.uuid: item for item in items}
        self._items = items  # cached list for games()

    def _load_bin(self) -> dict:
        """Load the .bin file as JSON into a dict keyed by 'id'."""
        with self.bin_path.open("rb") as f:
            decoded = base64.b64decode(f.read()).decode("utf-8")
        data_list = json.loads(decoded)
        return {entry["id"]: entry for entry in data_list if "id" in entry}

    def iter_items(self) -> Iterator[EpicItem]:
        for path in self.manifests_dir.glob("*.item"):
            try:
                yield EpicItem(path, library=self)
            except Exception:
                continue

    def games(self) -> list[EpicItem]:
        # Use cached list instead of re-reading manifests
        return [item for item in self._items if item.is_game and item.is_installed]

    def get_image_url(self, item: EpicItem, image_type: str = "DieselGameBoxTall") -> Optional[str]:
        entry = self.catcache_data.get(item.catalog_item_id)
        if not entry:
            return None

        # Faster loop: avoid repeated dict lookups
        for img in entry.get("keyImages", ()):
            if img.get("type") == image_type:
                return img.get("url")
        return None

    def get_by_uuid(self, uuid_str: str) -> Optional[EpicItem]:
        return self.items_by_uuid.get(uuid_str)