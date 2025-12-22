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

    def __init__(self, path: Path, library: EpicLibrary | None = None):
        self.path = path
        self.data = self._load()
        self.library = library
        # Generate a UUID from catalog_item_id + app_name
        uid_str = f"{self.catalog_item_id}|{self.app_name}"
        self.uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, uid_str)).upper()

    def _load(self) -> dict:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @property
    def name(self) -> str:
        return self.data.get("DisplayName", "")

    @property
    def app_name(self) -> str:
        return self.data.get("AppName", "")

    @property
    def catalog_namespace(self) -> str:
        return self.data.get("CatalogNamespace", "")

    @property
    def catalog_item_id(self) -> str:
        return self.data.get("CatalogItemId", "")

    @property
    def install_location(self) -> Optional[Path]:
        path = self.data.get("InstallLocation")
        return Path(path) if path else None

    @property
    def executable(self) -> Optional[Path]:
        exe = self.data.get("LaunchExecutable")
        if exe and self.install_location:
            return self.install_location / exe
        return None

    @property
    def header_image_url(self) -> Optional[str]:
        """Return the DieselGameBox (wide header) URL if available."""
        if self.library:
            return self.library.get_image_url(self, "DieselGameBox")
        return None


    @property
    def launch_uri(self) -> Optional[str]:
        if all((self.catalog_namespace, self.catalog_item_id, self.app_name)):
            return (
                "com.epicgames.launcher://apps/"
                f"{self.catalog_namespace}%3A"
                f"{self.catalog_item_id}%3A"
                f"{self.app_name}"
                "?action=launch&silent=true"
            )
        return None

    @property
    def is_game(self) -> bool:
        return "games" in self.data.get("AppCategories", [])

    @property
    def is_installed(self) -> bool:
        return not self.data.get("bIsIncompleteInstall", True)

    @property
    def image_url(self) -> Optional[str]:
        """Return the DieselGameBoxTall URL if available via the library's .bin data."""
        if self.library:
            return self.library.get_image_url(self)
        return None

    @property
    def item_type(self) -> str:
        tech_type = self.data.get("TechnicalType", "").lower()
        if "game" in tech_type:
            return "game"
        elif "software" in tech_type:
            return "application"
        return "unknown"

    def to_app_dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "launch": self.launch_uri,
            "exe": str(self.executable) if self.executable else None,
            "install_dir": str(self.install_location) if self.install_location else None,
            "source": "epic",
            "library_capsule": self.image_url,
            "library_header": self.header_image_url,  # 👈 ADD THIS
            "type": self.item_type,
        }


    def __repr__(self) -> str:
        return f"<EpicItem name={self.name!r}>"


# ----------------------------- Epic Library ----------------------------- #
class EpicLibrary:
    """Represents a collection of Epic Games installed items, plus optional .bin data."""

    def __init__(self, manifests_dir: Path = MANIFESTS, bin_path: Path = CATCACHE_BIN):
        self.manifests_dir = manifests_dir
        self.bin_path = bin_path
        self.catcache_data = self._load_bin() if bin_path.exists() else {}

        # Build a dict keyed by each EpicItem's own UUID
        self.items_by_uuid: dict[str, EpicItem] = {
            item.uuid: item for item in self.iter_items()
        }

    def _load_bin(self) -> dict:
        """Load the .bin file as JSON into a dict keyed by 'id'."""
        with open(self.bin_path, "rb") as f:
            encoded_data = f.read()
        decoded_bytes = base64.b64decode(encoded_data)
        decoded_str = decoded_bytes.decode("utf-8")
        data_list = json.loads(decoded_str)
        return {entry["id"]: entry for entry in data_list if "id" in entry}

    def iter_items(self) -> Iterator[EpicItem]:
        for path in self.manifests_dir.glob("*.item"):
            try:
                yield EpicItem(path, library=self)
            except Exception:
                continue

    def games(self) -> list[EpicItem]:
        return [
            item for item in self.iter_items()
            if item.is_game and item.is_installed
        ]

    def get_image_url(self, item: EpicItem, image_type: str = "DieselGameBoxTall") -> Optional[str]:
        catalog_id = item.catalog_item_id
        entry = self.catcache_data.get(catalog_id)
        if not entry:
            return None
        key_images = entry.get("keyImages", [])
        for img in key_images:
            if img.get("type") == image_type:
                return img.get("url")
        return None

    def get_by_uuid(self, uuid_str: str) -> Optional[EpicItem]:
        return self.items_by_uuid.get(uuid_str)