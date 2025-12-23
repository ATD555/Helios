import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from io import BytesIO
import urllib.parse

import requests
from PIL import Image

from steam.steam import (
    SteamEnvironment,
    SteamUserManager,
    SteamAppLibrary,
    SteamAssetManager,
)
import epic.epic as epic
from environment.environment import find_environment_installs, EnvironmentAppsJSON


# =====================================================================
#                           Helper Functions
# =====================================================================

def is_valid_png(path: Path) -> bool:
    """
    Validates that a file at a specific path is a valid PNG image.
    Used to ensure cover art integrity.
    """
    try:
        with Image.open(path) as im:
            return im.format == "PNG"
    except Exception:
        return False


def parse_uuid_args(arg_list):
    """
    Parse CLI arguments that may contain comma‑separated UUIDs.
    Accepts formats like:
        ["uuid1,uuid2"]
        ["uuid1, uuid2"]
    Returns a flat list of UUID strings.
    """
    uuids = []
    for part in arg_list or []:
        uuids.extend(u.strip() for u in part.split(",") if u.strip())
    return uuids


def resolve_apps_by_input(all_libraries: dict, input_str: str) -> list[dict]:
    """
    Resolve user input into matching app objects.
    Supports:
      - Exact UUID matches
      - Fuzzy name matches (case‑insensitive substring search)

    Returns a list of unique app dictionaries.
    """
    search_terms = [s.strip() for s in input_str.split(",") if s.strip()]
    matches: list[dict] = []

    for term in search_terms:
        # First try exact UUID match
        app = all_libraries.get(term)
        if app:
            matches.append(app)
            continue

        # Otherwise perform fuzzy name matching
        term_l = term.lower()
        matches.extend(
            a for a in all_libraries.values()
            if term_l in a.get("name", "").lower()
        )

    # Deduplicate by UUID to avoid duplicates in output
    return list({a["uuid"]: a for a in matches}.values())


def check_admin_write(file_path: Path) -> bool:
    """
    Checks if the script has write permissions for the target file or directory.
    Crucial because environments are often installed in Program Files, which
    requires Admin rights.
    """
    if file_path.exists():
        if not os.access(file_path, os.W_OK):
            print(f"ERROR: Cannot write to {file_path}. Run this script as Administrator.")
            return False
    else:
        if not os.access(file_path.parent, os.W_OK):
            print(f"ERROR: Cannot create {file_path}. Run this script as Administrator.")
            return False
    return True


def verify_helios_covers_dir(verbose: bool = False) -> None:
    """
    Ensure Helios covers directory exists in LocalAppData.
    This is where processed PNGs are stored.
    """
    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    if not covers_dir.exists():
        covers_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(f"Created Helios covers directory at {covers_dir}")


def get_steam_library() -> dict:
    """
    Fetches installed Steam games and added Non-Steam shortcuts.
    Normalizes them into a standard dictionary format.
    """
    env = SteamEnvironment()
    user_mgr = SteamUserManager(env)
    app_lib = SteamAppLibrary(env)
    asset_mgr = SteamAssetManager(env)

    # Get the active Steam user to find specific shortcuts
    users = user_mgr.get_users()
    steam_user = next(iter(users.values()))

    installed_steam_apps = app_lib.get_installed_steam_apps()
    nonsteam_apps = app_lib.get_nonsteam_apps(steam_user)

    # Add library_capsule (cover art) path for native Steam apps
    for app in installed_steam_apps.values():
        app_id = app.get("appID")
        assets = asset_mgr.get_steam_assets(int(app_id)) if app_id else {}
        for asset_name, asset_value in assets.items():
            if asset_value:
                app[asset_name] = str(Path(asset_value))
        app["source"] = "steam"

    # Add library_capsule for non-Steam shortcuts added to Steam
    for app in nonsteam_apps.values():
        app_id = app.get("appID")
        assets = asset_mgr.get_nonsteam_assets(app_id, steam_user) if app_id else {}
        for asset_name, asset_value in assets.items():
            if asset_value:
                app[asset_name] = str(Path(asset_value))
        app["source"] = "nonsteam"

    # Merge both dictionaries
    merged_apps = {**installed_steam_apps, **nonsteam_apps}
    return merged_apps


def get_installed_epic_games() -> dict:
    """
    Fetches installed Epic Games Store apps using the Manifests and Catalog Cache.
    """
    MANIFESTS = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
    CATCACHE_BIN = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Catalog\catcache.bin")
    epic_lib = epic.EpicLibrary(MANIFESTS, CATCACHE_BIN)

    epic_apps: dict[str, dict] = {}
    for app in epic_lib.games():
        epic_apps[app.uuid] = app.to_app_dict()
    return epic_apps


def get_environment_apps(preferred: tuple[str, ...] = ("Apollo", "Sunshine")) -> EnvironmentAppsJSON:
    """
    Loads the apps.json for the first available environment in preferred order.
    """
    installs = find_environment_installs("all")
    if not installs:
        print("No supported environment installation found.")
        sys.exit(1)

    # Pick the first preferred environment that exists
    install = None
    for name in preferred:
        install = next((i for i in installs if i["name"] == name), None)
        if install:
            break

    if not install:
        install = installs[0]  # fallback

    root = install["root"]
    apps_json_path = install["apps_json"]

    return EnvironmentAppsJSON(apps_json_path, root)


def print_library_info(
    library_name: str,
    steam_apps: dict | None = None,
    epic_apps: dict | None = None,
    environment: EnvironmentAppsJSON | None = None,
    show_sample: bool = True,
) -> None:
    """
    Print detailed info about a specific library (Apollo, Steam, Epic, Sunshine)
    in a fast, clean format. Used for the --status <library> command.
    """
    print_line = lambda: print("=" * 50)

    # ------------------------ Sunshine/Apollo environment ------------------------
    if library_name in ("apollo", "sunshine"):
        installs = find_environment_installs(library_name)
        install = installs[0] if installs else None

        if not install:
            print(f"{library_name.title()} installation not found.")
            return

        print_line()
        print(f"{library_name.title()} Installation Info".center(50))
        print_line()
        print(f"{'Name:':20} {install['name']}")
        print(f"{'Display Name:':20} {install['display_name']}")
        print(f"{'Root:':20} {install['root']}")
        print(f"{'Config File:':20} {install['config_file']}")
        print(f"{'Apps File:':20} {install['apps_json']}")

        # Load apps.json
        environment = EnvironmentAppsJSON(install["apps_json"], install["root"])
        apps = environment.list_apps()

        print(f"{'Total Apps:':20} {len(apps)}")

        if show_sample:
            count = min(len(apps), 5)
            print(f"\nFirst {count} apps:")
            for app in apps[:count]:
                print(f"   - {app.get('name', 'Unknown')}")

        print_line()
        print()
        return

    # ------------------------ Steam / Non-Steam ------------------------
    if library_name in ("steam", "nonsteam"):
        if steam_apps is None:
            print("Steam library not loaded.")
            return

        steam_count = 0
        nonsteam_count = 0

        for app in steam_apps.values():
            src = app.get("source")
            if src == "steam":
                steam_count += 1
            elif src == "nonsteam":
                nonsteam_count += 1

        print_line()
        print(f"{library_name.capitalize()} Library Info".center(50))
        print_line()

        total_count = steam_count + nonsteam_count

        if library_name == "steam":
            print(f"Total library apps: {total_count}")
            print(f"Total Steam apps:   {steam_count}")
            print(f"Total Non-Steam:    {nonsteam_count}")

            if show_sample:
                print("\nFirst 5 apps:")
                for app in list(steam_apps.values())[:5]:
                    print(f"   - {app.get('name', 'Unknown')}")

        elif library_name == "nonsteam":
            print(f"Total Non-Steam apps: {nonsteam_count}")

            if show_sample:
                nonsteam_only = [
                    app for app in steam_apps.values()
                    if app.get("source") == "nonsteam"
                ]
                print("\nFirst 5 apps:")
                for app in nonsteam_only[:5]:
                    print(f"   - {app.get('name', 'Unknown')}")

        print_line()
        print()
        return

    # ------------------------ Epic ------------------------
    if library_name == "epic":
        if epic_apps is None:
            print("Epic library not loaded.")
            return

        print_line()
        print("Epic Library Info".center(50))
        print_line()
        print(f"Total Epic apps: {len(epic_apps)}")

        if show_sample:
            print("\nFirst 5 apps:")
            for app in list(epic_apps.values())[:5]:
                print(f"   - {app.get('name', 'Unknown')}")

        print_line()
        print()
        return


def mark_helios_managed_apps(library: dict, environment: EnvironmentAppsJSON) -> dict:
    """
    Iterates through all discovered apps (Steam/Epic) and checks if they exist
    inside the active environment (Apollo/Sunshine/etc.).
    Adds a 'managed_by_helios' boolean flag to the app dictionary.
    """
    for app_uuid, app_data in library.items():
        app_data["managed_by_helios"] = bool(environment.get_app_by_uuid(app_uuid))
    return library


def update_helios_cache(libraries: dict, selection: str, verbose: bool = False) -> None:
    """
    Maintains a local JSON cache of discovered apps in LocalAppData/Helios.
    Speeds up future operations by not needing to re-parse massive library
    files every time.
    """
    HELIOS_CACHE_FILE = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "apps.json"
    HELIOS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if HELIOS_CACHE_FILE.exists():
        with open(HELIOS_CACHE_FILE, "r", encoding="utf-8") as f:
            current_cache = json.load(f)
    else:
        current_cache = {}

    if selection == "steam":
        apps_to_include = {
            k: v for k, v in libraries.items()
            if v.get("source") == "steam"
        }
    elif selection == "nonsteam":
        apps_to_include = {
            k: v for k, v in libraries.items()
            if v.get("source") == "nonsteam"
        }
    elif selection == "epic":
        apps_to_include = {
            k: v for k, v in libraries.items()
            if v.get("source") == "epic"
        }
    else:
        apps_to_include = libraries

    if selection == "all":
        current_cache.clear()
    else:
        # Clear only the selected source from cache before rebuilding
        for uuid, app_data in list(current_cache.items()):
            if app_data.get("source") == selection:
                current_cache.pop(uuid)

    # Merge new apps into cache
    for uuid, app_data in apps_to_include.items():
        old_metadata = current_cache.get(uuid, {})
        current_cache[uuid] = {**old_metadata, **app_data}

    with open(HELIOS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(current_cache, f, indent=4)

    if verbose:
        print(f"Helios cache updated ({len(apps_to_include)} apps) for {selection} at {HELIOS_CACHE_FILE}")


def verify_helios_cache(all_libraries: dict) -> None:
    """
    Ensure Helios cache exists. If missing, rebuild it automatically for all libraries.
    """
    HELIOS_CACHE_FILE = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "apps.json"
    if not HELIOS_CACHE_FILE.exists():
        print("Helios cache missing, rebuilding for all libraries...")
        update_helios_cache(all_libraries, "all")


def handle_cache_option(all_libraries: dict, cache_selection: str) -> None:
    """
    Update Helios cache based on user --cache selection
    """
    if not cache_selection:
        return

    if cache_selection not in ("steam", "epic", "nonsteam", "all"):
        print(f"Invalid cache selection: {cache_selection}")
        return

    print(f"Updating Helios cache for: {cache_selection}...")
    update_helios_cache(all_libraries, cache_selection)


def sort_apps(apps: dict, sort_key: str | None) -> list[dict]:
    """
    Return a sorted list of app dicts based on the provided key (name, source, uuid, managed).
    """
    if not sort_key:
        return list(apps.values())

    if sort_key == "name":
        return sorted(apps.values(), key=lambda a: a.get("name", "").lower())

    if sort_key == "source":
        return sorted(apps.values(), key=lambda a: a.get("source", "").lower())

    if sort_key == "uuid":
        return sorted(
            apps.values(),
            key=lambda a: (a.get("uuid") or a.get("appID") or "").lower()
        )

    if sort_key == "managed":
        # Managed first, then unmanaged
        return sorted(
            apps.values(),
            key=lambda a: a.get("managed_by_helios", False),
            reverse=True
        )

    return list(apps.values())


def list_apps(
    libraries: dict,
    sort_key: str | None = None,
    managed_only: bool = False,
    show_type: bool = True,
    show_index: bool = False,
    show_managed: bool = True,
) -> None:
    """
    Render a formatted ASCII table of applications.
    Column widths are dynamically calculated based on content.
    """
    apps = libraries
    if managed_only:
        apps = {k: v for k, v in libraries.items() if v.get("managed_by_helios")}

    if not apps:
        print("No apps to display.")
        return

    sorted_apps = sort_apps(apps, sort_key)

    # ---- dynamic column widths ----
    name_width = max(max(len(a.get("name", "")) for a in sorted_apps), 20)

    source_width = max(max(len(a.get("source", "")) for a in sorted_apps), 8)

    uuid_width = max(
        max(len(a.get("uuid") or a.get("appID") or "") for a in sorted_apps),
        36,
    )

    if show_type:
        type_width = max(
            max(len(str(a.get("type", ""))) for a in sorted_apps),
            6,
        )
    else:
        type_width = 0

    option_width = max(len(str(len(sorted_apps))), 6) if show_index else 0

    if show_managed:
        managed_width = max(
            len("Managed"),
            max(
                len("Yes") if app.get("managed_by_helios") else len("No")
                for app in sorted_apps
            ),
        )
    else:
        managed_width = 0

    # ---- header ----
    header_parts: list[str] = []
    if show_index:
        header_parts.append(f"{'Option':<{option_width}}")

    header_parts.extend([
        f"{'Name':<{name_width}}",
        f"{'Source':<{source_width}}",
    ])

    if show_type:
        header_parts.append(f"{'Type':<{type_width}}")

    if show_managed:
        header_parts.append(f"{'Managed':<{managed_width}}")

    header_parts.append(f"{'Helios ID (UUID)':<{uuid_width}}")

    header = " | ".join(header_parts)
    print(header)
    print("-" * len(header))

    # ---- rows ----
    for idx, app in enumerate(sorted_apps, 1):
        row_parts: list[str] = []

        if show_index:
            row_parts.append(f"{idx:<{option_width}}")

        row_parts.extend([
            f"{app.get('name', ''):<{name_width}}",
            f"{str(app.get('source', '')).title():<{source_width}}",
        ])

        if show_type:
            row_parts.append(f"{str(app.get('type', '')).title():<{type_width}}")

        if show_managed:
            row_parts.append(f"{'Yes' if app.get('managed_by_helios') else 'No':<{managed_width}}")

        row_parts.append(f"{(app.get('uuid') or app.get('appID') or ''):<{uuid_width}}")

        print(" | ".join(row_parts))


def print_apps_with_status(apps: list[dict], status_map: dict[str, str]) -> None:
    """
    Print a list of apps in a table style with a specific status column.
    Used for showing results of Add/Remove operations (e.g., "Already added").
    """
    # Filter apps that actually have a status
    apps_with_status = [
        app for app in apps
        if status_map.get(app.get("uuid") or app.get("appID"))
    ]

    if not apps_with_status:
        print("No apps with status to display.")
        return

    # Determine column widths dynamically
    name_width = max(max(len(a.get("name", "")) for a in apps_with_status), 20)
    source_width = max(max(len(a.get("source", "")) for a in apps_with_status), 8)
    uuid_width = max(
        max(len(a.get("uuid") or a.get("appID") or "") for a in apps_with_status),
        36,
    )
    status_width = max(
        len("Status"),
        max(
            len(status_map.get(a.get("uuid") or a.get("appID")))
            for a in apps_with_status
        ),
    )

    # Header
    header = (
        f"{'Name':<{name_width}} | "
        f"{'Source':<{source_width}} | "
        f"{'Helios ID (UUID)':<{uuid_width}} | "
        f"{'Status':<{status_width}}"
    )
    print(header)
    print("-" * len(header))

    # Rows
    for app in apps_with_status:
        uuid = app.get("uuid") or app.get("appID") or ""
        status = status_map.get(uuid)
        row = (
            f"{app.get('name', ''):<{name_width}} | "
            f"{str(app.get('source', '')).title():<{source_width}} | "
            f"{uuid:<{uuid_width}} | "
            f"{status:<{status_width}}"
        )
        print(row)


def verify_managed_covers(
    all_libraries: dict,
    environment: EnvironmentAppsJSON,
    *,
    cleanup: bool = False,
    verbose: bool = False,
) -> None:
    """
    Ensure every Helios-managed app has a cover image.
    - Restores missing or invalid covers by checking the source library (Steam/Epic).
    - Optionally removes orphaned covers (files in folder but not in apps.json) if cleanup=True.
    """
    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    restored = 0
    failed = 0
    removed = 0

    managed_uuids = {
        uuid for uuid, app in all_libraries.items()
        if app.get("managed_by_helios")
    }

    for uuid in managed_uuids:
        dest = covers_dir / f"{uuid}.png"

        # Skip if file exists and is a valid PNG
        if dest.exists() and is_valid_png(dest):
            if verbose:
                print(f"[SKIP] Valid cover already exists for {all_libraries[uuid].get('name')}")
            continue

        app = all_libraries.get(uuid)
        if not app:
            continue

        environment_app = environment.get_app_by_uuid(uuid)
        if not environment_app:
            continue

        # Prefer Helios library capsule
        image_source = app.get("library_capsule")
        image_path = None

        if image_source:
            parsed = urllib.parse.urlparse(image_source)
            if parsed.scheme in ("http", "https"):
                image_path = image_source  # HTTP URL
            else:
                image_path = Path(image_source)
                if not image_path.is_absolute() and environment.root:
                    image_path = environment.root / image_path

        # Fallback: environment original image
        if not image_path:
            image_path = environment.get_image_path(app["uuid"])
            if image_path and image_path.exists():
                image_path = str(image_path)
            elif image_path and image_path.is_absolute():
                image_path = str(image_path)

        # Validate final image path
        valid = False
        if image_path:
            parsed = urllib.parse.urlparse(str(image_path))
            if parsed.scheme in ("http", "https"):
                valid = True
            elif Path(image_path).exists():
                valid = True

        if not valid:
            failed += 1
            if verbose:
                print(f"[WARN] No valid image to restore for {app.get('name')}")
            continue

        saved = save_library_capsule(
            uuid,
            image_source,
            environment_root=environment.root,
            verbose=verbose,
        )

        if saved:
            restored += 1
            if environment_app.get("image-path") != saved:
                environment_app["image-path"] = saved
                environment.save()
            if verbose:
                print(f"[RESTORED] Cover art for {app.get('name')}")
        else:
            failed += 1

    # Cleanup orphaned covers
    if cleanup:
        for file in covers_dir.iterdir():
            if file.suffix.lower() != ".png":
                continue
            if file.stem not in managed_uuids:
                try:
                    file.unlink()
                    removed += 1
                except Exception as e:
                    if verbose:
                        print(f"[CLEANUP ERROR] {file.name}: {e}")

    if verbose:
        print(
            f"Cover verification complete: "
            f"{restored} restored, {failed} failed"
            + (f", {removed} orphaned removed" if cleanup else "")
        )


def save_library_capsule(
    uuid: str,
    url_or_path: str | None,
    environment_root: Path | None = None,
    verbose: bool = False,
) -> str | None:
    """
    Save library capsule to Helios covers as PNG.
    Ensures the saved file is a valid PNG.
    Supports:
      - http(s) URLs
      - file:// URLs
      - absolute paths
      - relative paths from environment apps.json
    Returns local PNG path or None.
    """
    if not isinstance(url_or_path, str) or not url_or_path.strip():
        return None

    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    dest = covers_dir / f"{uuid}.png"

    # Already exists -> verify PNG
    if dest.exists():
        try:
            with Image.open(dest) as im:
                if im.format != "PNG":
                    if verbose:
                        print(f"[INFO] {dest.name} is not PNG, restoring.")
                    dest.unlink(missing_ok=True)
                else:
                    return str(dest)
        except Exception:
            if verbose:
                print(f"[INFO] {dest.name} corrupted, restoring.")
            dest.unlink(missing_ok=True)

    try:
        # -------- HTTP / HTTPS --------
        if url_or_path.lower().startswith(("http://", "https://")):
            resp = requests.get(url_or_path, timeout=15)
            resp.raise_for_status()
            im = Image.open(BytesIO(resp.content))

        # -------- file:// URL --------
        elif url_or_path.lower().startswith("file://"):
            img_path = Path(url_or_path[7:])
            im = Image.open(img_path)

        # -------- Local path --------
        else:
            img_path = Path(url_or_path)
            if not img_path.is_absolute() and environment_root:
                img_path = environment_root / img_path
            if not img_path.exists():
                return None
            im = Image.open(img_path)

        # Convert to PNG if not already
        if im.format != "PNG":
            im = im.convert("RGBA")
        im.save(dest, format="PNG")
        return str(dest)

    except Exception as e:
        if dest.exists():
            dest.unlink(missing_ok=True)
        if verbose:
            print(f"[COVER ERROR] {uuid}: {e}")
        return None


def add_games(
    environment: EnvironmentAppsJSON,
    all_libraries: dict,
    input_str: str,
    verbose: bool = False,
) -> None:
    """
    Interactive logic to add games.
    Resolves input -> Finds matches -> Filters for already added -> Prompts user -> Adds.
    """
    search_terms = [s.strip() for s in input_str.split(",") if s.strip()]
    all_matches: list[dict] = []

    # -------- Resolve UUIDs + name matches --------
    for term in search_terms:
        # Exact UUID match first
        app = all_libraries.get(term)
        if app:
            all_matches.append(app)
            continue

        # Fuzzy name match
        matches = [
            a for a in all_libraries.values()
            if term.lower() in a.get("name", "").lower()
        ]
        all_matches.extend(matches)

    # De-duplicate by UUID
    all_matches = list({a["uuid"]: a for a in all_matches}.values())

    if not all_matches:
        print("No apps found matching your input.")
        return

    installs = find_environment_installs("all")
    environment_name = installs[0].get("display_name") if installs else "Unknown"

    # -------- Split into already-added vs unmanaged --------
    already_added = [
        a for a in all_matches
        if environment.get_app_by_uuid(a["uuid"])
    ]
    unmanaged_matches = [
        a for a in all_matches
        if not environment.get_app_by_uuid(a["uuid"])
    ]

    # -------- Already added (informational) --------
    if already_added and verbose:
        status_map = {
            a["uuid"]: f"Already added to {environment_name} by Helios"
            for a in already_added
        }
        print(f"These apps are already added to {environment_name} by Helios:")
        print_apps_with_status(already_added, status_map)
        print()

    # -------- Nothing eligible to add --------
    if not unmanaged_matches:
        print("No unmanaged apps found to add.")
        return

    # -------- Select unmanaged apps to add --------
    print("Unmanaged apps matching your input:")
    list_apps(
        {a["uuid"]: a for a in unmanaged_matches},
        show_index=True,
        show_managed=False,
    )

    selection = input(
        "Enter numbers to add (comma-separated), 'all', or 'exit' / 'q': "
    ).strip().lower()

    if selection in {"q", "quit", "exit", ""}:
        print("Selection cancelled.")
        return

    if selection == "all":
        selected_apps = unmanaged_matches
    else:
        try:
            indices = {
                int(x) - 1
                for x in selection.split(",")
                if x.strip().isdigit()
            }
        except ValueError:
            print("Invalid input, aborting.")
            return

        selected_apps = [
            unmanaged_matches[i]
            for i in indices
            if 0 <= i < len(unmanaged_matches)
        ]

    if not selected_apps:
        print("No valid selections made.")
        return

    # -------- Add --------
    for app in selected_apps:
        _add_game(environment, all_libraries, app, verbose=verbose)


def _add_game(
    environment: EnvironmentAppsJSON,
    all_libraries: dict,
    app_data: dict,
    verbose: bool = False,
) -> bool:
    """
    Writes the game entry to the environment's apps.json and saves the cover image.
    Requires Admin privileges.
    """
    uuid = app_data["uuid"]

    if environment.get_app_by_uuid(uuid):
        if verbose:
            print(f"{app_data['name']} ({uuid}) is already in apps.json, skipping add.")
        return False

    if not check_admin_write(environment.apps_json_path):
        return False

    # Save library capsule
    image_path = save_library_capsule(
        uuid,
        app_data.get("library_capsule"),
        environment_root=environment.root,
        verbose=verbose,
    )

    app_entry = {
        "uuid": uuid,
        "name": app_data["name"],
        "cmd": app_data.get("launch") or "",
        "image-path": image_path or "",
    }

    environment.apps.append(app_entry)
    environment.by_uuid[uuid] = app_entry
    environment.save()

    if verbose:
        installs = find_environment_installs("all")
        environment_name = installs[0].get("display_name") if installs else "Environment"
        print(f"Added {app_data['name']} ({uuid}) to {environment_name} and updated managed flags.")
    return True


def _remove_game(
    environment: EnvironmentAppsJSON,
    all_libraries: dict,
    game_data: dict,
    verbose: bool = False,
) -> bool:
    """
    Removes the game entry from the environment's apps.json and deletes the cover image.
    Requires Admin privileges.
    """
    uuid = game_data["uuid"]

    if not environment.get_app_by_uuid(uuid):
        if verbose:
            print(f"{game_data['name']} ({uuid}) not found in apps.json, skipping removal.")
        return False

    if not check_admin_write(environment.apps_json_path):
        return False

    # Remove from environment
    environment.apps = [app for app in environment.apps if app.get("uuid") != uuid]
    environment.by_uuid.pop(uuid, None)
    environment.data["apps"] = environment.apps
    environment.save()

    # Remove cover
    cover_path = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers" / f"{uuid}.png"
    if cover_path.exists():
        cover_path.unlink(missing_ok=True)

    if verbose:
        print(f"Removed {game_data['name']} ({uuid}) from apps.json")
    return True


def print_helios_status(steam_apps: dict, epic_apps: dict, all_libraries: dict) -> None:
    """
    Prints a high-level summary of managed games vs total discovered games.
    """
    managed_apps = [v for v in all_libraries.values() if v.get("managed_by_helios")]

    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    cover_files: list[Path] = []
    if covers_dir.exists():
        cover_files = [f for f in covers_dir.iterdir() if f.suffix.lower() == ".png"]

    managed_cover_uuids = {
        app["uuid"] for app in managed_apps
        if "uuid" in app
    }

    orphaned_covers = [
        f for f in cover_files if f.stem not in managed_cover_uuids
    ]

    print("\nHelios Status")
    print("=" * 13)
    print(f"Steam apps discovered:      {len(steam_apps)}")
    print(f"Epic apps discovered:       {len(epic_apps)}")
    print(f"Total discovered apps:      {len(all_libraries)}\n")

    print(f"Managed by Helios:          {len(managed_apps)}")
    print(f"Unmanaged apps:             {len(all_libraries) - len(managed_apps)}\n")

    print("Helios covers:")
    print(f"  Total covers on disk:     {len(cover_files)}")
    print(f"  Managed covers:           {len(cover_files) - len(orphaned_covers)}")
    print(f"  Orphaned covers:          {len(orphaned_covers)}\n")


def get_helios_type(helios: dict, uuid: str) -> str | None:
    """
    Retrieve the Helios-defined 'type' field for a given UUID.
    """
    app = helios.get(uuid)
    if not app:
        return None
    return app.get("type")


# =====================================================================
#                               CLI ENTRY
# =====================================================================

def main() -> None:
    """
    Main command-line interface entry point for the Helios tool.

    Handles:
        - Argument parsing
        - Lazy-loading of Steam/Epic
        - Apollo/Sunshine environment detection
        - Cache verification
        - Add/remove workflows
        - Listing, searching, filtering
        - Status reporting
        - Cover cleanup
    """
    parser = argparse.ArgumentParser(
        prog="helios",
        description="Helios (defaults to --status)",
    )

    # ------------------------ CLI Arguments ------------------------
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List installed games",
    )
    parser.add_argument(
        "--sort",
        nargs="?",
        const="name",
        help="Sort output by field",
    )
    parser.add_argument(
        "--cache",
        choices=["steam", "epic", "nonsteam", "all"],
        help="Update Helios cache",
    )
    parser.add_argument(
        "--managed",
        action=argparse.BooleanOptionalAction,
        help="Filter to Helios-managed apps only",
    )
    parser.add_argument(
        "-r", "--remove",
        nargs="*",
        type=str,
        metavar="UUIDS",
        help="Comma-separated UUIDs to remove",
    )
    parser.add_argument(
        "-a", "--add",
        nargs="*",
        type=str,
        metavar="UUIDS",
        help="Comma-separated UUIDs to add",
    )
    parser.add_argument(
        "--search",
        nargs="+",
        metavar="NAME",
        help="Search for apps by name",
    )
    parser.add_argument(
        "--source",
        nargs="+",
        metavar="NAME",
        help="Filter to specified source(s)",
    )
    parser.add_argument(
        "-s", "--status",
        metavar="LIBRARY",
        help="Show status/info of a specific library",
    )
    parser.add_argument(
        "--cleanup-covers",
        action="store_true",
        help="Remove orphaned Helios covers",
    )
    parser.add_argument(
        "--show-sample",
        action="store_true",
        help="Show sample apps",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--type",
        help="Filter apps by Helios type",
    )

    args = parser.parse_args()

    # ------------------------ Default Behavior ------------------------
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    # ------------------------ Conflict Checks ------------------------
    if args.list and (args.add is not None or args.remove is not None):
        parser.error("Error: --list cannot be used together with --add or --remove.")
        return

    if args.show_sample and not args.status:
        parser.error("--show-sample can only be used together with --status <library>")
        return



    # Detect installed environments (Apollo/Sunshine/etc.)
    environment_install = find_environment_installs("all")
    environment_name = environment_install[0].get("display_name") if environment_install else "Unknown"

        # ------------------------ Load environment (always required) ------------------------
    environment = get_environment_apps()

    # Lazy-loaded libraries
    steam_apps: dict | None = None
    epic_apps: dict | None = None

    def ensure_steam_loaded() -> dict:
        nonlocal steam_apps
        if steam_apps is None:
            try:
                steam_apps = get_steam_library()
            except Exception:
                steam_apps = {}
        return steam_apps

    def ensure_epic_loaded() -> dict:
        nonlocal epic_apps
        if epic_apps is None:
            try:
                epic_apps = get_installed_epic_games()
            except Exception:
                epic_apps = {}
        return epic_apps

    def get_all_libraries() -> dict:
        libs: dict = {}
        libs.update(ensure_steam_loaded())
        libs.update(ensure_epic_loaded())
        return libs


    # ------------------------ Verify Setup ------------------------
    all_libraries = get_all_libraries()
    all_libraries = mark_helios_managed_apps(all_libraries, environment)

    verify_helios_cache(all_libraries)
    verify_helios_covers_dir(verbose=args.verbose)
    verify_managed_covers(all_libraries, environment, verbose=args.verbose)
    mark_helios_managed_apps(all_libraries, environment)
    update_helios_cache(all_libraries, selection="all", verbose=False)

    # ------------------------ Handle --cache ------------------------
    if args.cache:
        handle_cache_option(all_libraries, args.cache)

    # =================================================================
    #                           ADD WORKFLOW
    # =================================================================
    if args.add is not None:
        all_libraries = get_all_libraries()
        all_libraries = mark_helios_managed_apps(all_libraries, environment)

        unmanaged_apps = [
            app for app in all_libraries.values()
            if not app.get("managed_by_helios")
        ]

        explicit_apps: list[dict] = []
        interactive_pool = unmanaged_apps.copy()
        status_map: dict[str, str] = {}

        explicit_uuids = parse_uuid_args(args.add)

        for uuid in explicit_uuids:
            app = next((a for a in unmanaged_apps if a["uuid"] == uuid), None)

            if app:
                explicit_apps.append(app)
            else:
                app_full = all_libraries.get(uuid)
                if app_full:
                    status_map[uuid] = f"Already added to {environment_name} and managed by Helios"
                else:
                    status_map[uuid] = "UUID not found"

        explicit_uuid_set = {a["uuid"] for a in explicit_apps}
        interactive_pool = [
            a for a in interactive_pool if a["uuid"] not in explicit_uuid_set
        ]

        filters_present = any([args.search, args.source, args.type])
        has_explicit_uuids = bool(args.add)

        if filters_present or not has_explicit_uuids:
            if args.search:
                search_terms = [
                    term.strip().lower()
                    for part in args.search
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(term in a.get("name", "").lower() for term in search_terms)
                ]

            if args.type:
                type_terms = [
                    term.strip().lower()
                    for part in args.type
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(
                        term in str(get_helios_type(all_libraries, a["uuid"]) or "").lower()
                        for term in type_terms
                    )
                ]

            if args.source:
                source_terms = {
                    s.strip().lower()
                    for part in args.source
                    for s in part.split(",")
                    if s.strip()
                }
                interactive_pool = [
                    a for a in interactive_pool
                    if a.get("source", "").lower() in source_terms
                ]
        else:
            interactive_pool = []

        selected_apps: list[dict] = []

        if interactive_pool:
            print("Unmanaged apps matching your filters:")
            list_apps(
                {a["uuid"]: a for a in interactive_pool},
                show_index=True,
                show_managed=False,
            )

            selection = input(
                "Enter numbers to add (comma-separated), 'all', or 'q': "
            ).strip().lower()

            if selection not in {"", "q", "quit", "exit"}:
                if selection == "all":
                    selected_apps = interactive_pool
                else:
                    indices = {
                        int(x) - 1
                        for x in selection.split(",")
                        if x.strip().isdigit()
                    }
                    selected_apps = [
                        interactive_pool[i]
                        for i in indices
                        if 0 <= i < len(interactive_pool)
                    ]

        all_added: list[dict] = []

        for app in explicit_apps + selected_apps:
            _add_game(environment, all_libraries, app, verbose=args.verbose)
            status_map[app["uuid"]] = f"Added to {environment_name} and managed by Helios"
            all_added.append(app)

        if all_added:
            all_libraries = get_all_libraries()
            mark_helios_managed_apps(all_libraries, environment)
            update_helios_cache(all_libraries, selection="all", verbose=False)

        if status_map:
            print("\nAdd results:")
            apps_for_status = [
                all_libraries.get(uuid)
                for uuid in status_map
                if all_libraries.get(uuid)
            ]
            print_apps_with_status(apps_for_status, status_map)

    # =================================================================
    #                          REMOVE WORKFLOW
    # =================================================================
    if args.remove is not None:
        all_libraries = get_all_libraries()
        all_libraries = mark_helios_managed_apps(all_libraries, environment)

        managed_apps = [
            app for app in all_libraries.values()
            if app.get("managed_by_helios")
        ]

        explicit_apps: list[dict] = []
        interactive_pool = managed_apps.copy()
        status_map: dict[str, str] = {}

        explicit_uuids = parse_uuid_args(args.remove)

        for uuid in explicit_uuids:
            app = next((a for a in managed_apps if a["uuid"] == uuid), None)

            if app:
                explicit_apps.append(app)
            else:
                app_full = all_libraries.get(uuid)
                if app_full:
                    status_map[uuid] = "Not currently managed by Helios"
                else:
                    status_map[uuid] = "UUID not found"

        explicit_uuid_set = {a["uuid"] for a in explicit_apps}
        interactive_pool = [
            a for a in interactive_pool if a["uuid"] not in explicit_uuid_set
        ]

        filters_present = any([args.search, args.source, args.type])
        has_explicit_uuids = bool(args.remove)

        if filters_present or not has_explicit_uuids:
            if args.search:
                search_terms = [
                    term.strip().lower()
                    for part in args.search
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(term in a.get("name", "").lower() for term in search_terms)
                ]

            if args.type:
                type_terms = [
                    term.strip().lower()
                    for part in args.type
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(
                        term in str(get_helios_type(all_libraries, a["uuid"]) or "").lower()
                        for term in type_terms
                    )
                ]

            if args.source:
                source_terms = {
                    s.strip().lower()
                    for part in args.source
                    for s in part.split(",")
                    if s.strip()
                }
                interactive_pool = [
                    a for a in interactive_pool
                    if a.get("source", "").lower() in source_terms
                ]
        else:
            interactive_pool = []

        selected_apps: list[dict] = []

        if interactive_pool:
            print("Managed apps matching your filters:")
            list_apps(
                {a["uuid"]: a for a in interactive_pool},
                show_index=True,
                show_managed=False,
            )

            selection = input(
                "Enter numbers to remove (comma-separated), 'all', or 'q': "
            ).strip().lower()

            if selection not in {"", "q", "quit", "exit"}:
                if selection == "all":
                    selected_apps = interactive_pool
                else:
                    indices = {
                        int(x) - 1
                        for x in selection.split(",")
                        if x.strip().isdigit()
                    }
                    selected_apps = [
                        interactive_pool[i]
                        for i in indices
                        if 0 <= i < len(interactive_pool)
                    ]

        all_removed: list[dict] = []

        for app in explicit_apps + selected_apps:
            _remove_game(environment, all_libraries, app, verbose=args.verbose)
            status_map[app["uuid"]] = f"Removed from {environment_name} by Helios"
            all_removed.append(app)

        if all_removed:
            all_libraries = get_all_libraries()
            mark_helios_managed_apps(all_libraries, environment)
            update_helios_cache(all_libraries, selection="all", verbose=False)

        if status_map:
            print("\nRemoval results:")
            apps_for_status = [
                all_libraries.get(uuid)
                for uuid in status_map
                if all_libraries.get(uuid)
            ]
            print_apps_with_status(apps_for_status, status_map)

    # =================================================================
    #                       COVER CLEANUP WORKFLOW
    # =================================================================
    if args.cleanup_covers:
        all_libraries = get_all_libraries()
        verify_managed_covers(
            all_libraries,
            environment,
            cleanup=True,
            verbose=True,
        )

    # =================================================================
    #                        STATUS / INFO WORKFLOW
    # =================================================================
    if args.status:
        library = args.status.lower()
        show_sample = args.show_sample

        # Always load Steam/Epic for status
        steam = ensure_steam_loaded()
        epic = ensure_epic_loaded()

        if library in ("apollo", "sunshine"):
            print_library_info(
                library,
                steam_apps=steam,
                epic_apps=epic,
                environment=environment,
                show_sample=show_sample,
            )
            return

        if library == "steam":
            print_library_info("steam", steam_apps=steam, show_sample=show_sample)
            return

        if library == "nonsteam":
            print_library_info("nonsteam", steam_apps=steam, show_sample=show_sample)
            return

        if library == "epic":
            print_library_info("epic", epic_apps=epic, show_sample=show_sample)
            return

        if library in ("helios", ""):
            all_libs = get_all_libraries()
            print_helios_status(steam, epic, all_libs)
            return

        print(f"Unknown library: {library}")
        return

    # =================================================================
    #                           LIST WORKFLOW
    # =================================================================
    if args.list and args.add is None and args.remove is None:
        all_libraries = get_all_libraries()
        all_libraries = mark_helios_managed_apps(all_libraries, environment)

        apps_to_show = list(all_libraries.values())

        if args.search:
            search_terms = [
                term.strip().lower()
                for part in args.search
                for term in part.split(",")
                if term.strip()
            ]
            apps_to_show = [
                a for a in apps_to_show
                if any(term in a.get("name", "").lower() for term in search_terms)
            ]

        if args.type:
            type_terms = [
                term.strip().lower()
                for part in args.type
                for term in part.split(",")
                if term.strip()
            ]
            apps_to_show = [
                a for a in apps_to_show
                if any(
                    term in str(get_helios_type(all_libraries, a["uuid"]) or "").lower()
                    for term in type_terms
                )
            ]

        if args.source:
            source_terms = {
                s.strip().lower()
                for part in args.source
                for s in part.split(",")
                if s.strip()
            }
            apps_to_show = [
                a for a in apps_to_show
                if a.get("source", "").lower() in source_terms
            ]

        if args.managed is True:
            apps_to_show = [a for a in apps_to_show if a.get("managed_by_helios")]
        elif args.managed is False:
            apps_to_show = [a for a in apps_to_show if not a.get("managed_by_helios")]

        if not apps_to_show:
            print("No apps found matching your filters.")
            return

        list_apps(
            {a["uuid"]: a for a in apps_to_show},
            sort_key=args.sort,
        )


# =====================================================================
#                               ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    main()
