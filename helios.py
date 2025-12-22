import os
import sys
import json
import argparse
from PIL import Image   
from pathlib import Path
from steam.steam import SteamEnvironment, SteamUserManager, SteamAppLibrary, SteamAssetManager
import epic.epic as epic
from apollo.apollo import find_apollo_install
from apollo.apollo import ApolloAppsJSON
import shutil
from datetime import datetime
import requests
from io import BytesIO
import urllib.parse

# ----------------------------- Helpers ----------------------------- #
def is_valid_png(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            return im.format == "PNG"
    except Exception:
        return False


def parse_uuid_args(arg_list):
    """
    Accepts: ['uuid1,uuid2'] or ['uuid1, uuid2']
    Returns: ['uuid1', 'uuid2']
    """
    uuids = []
    for part in arg_list or []:
        uuids.extend(
            u.strip() for u in part.split(",") if u.strip()
        )
    return uuids


def resolve_apps_by_input(all_libraries, input_str: str):
    """
    Resolve UUIDs and fuzzy name matches like add/remove logic,
    but without Apollo side-effects.
    """
    search_terms = [s.strip() for s in input_str.split(",") if s.strip()]
    matches = []

    for term in search_terms:
        # Exact UUID
        app = all_libraries.get(term)
        if app:
            matches.append(app)
            continue

        # Fuzzy name match
        term_l = term.lower()
        matches.extend(
            a for a in all_libraries.values()
            if term_l in a.get("name", "").lower()
        )

    # De-duplicate by UUID
    return list({a["uuid"]: a for a in matches}.values())

def check_admin_write(file_path: Path) -> bool:
    """
    Returns True if the file can be written to (or created).
    Otherwise, prints an error and returns False.
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

def verify_helios_covers_dir(verbose=False):
    """
    Ensure Helios covers directory exists.
    Does NOT regenerate images, only ensures the directory exists.
    """
    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"

    if not covers_dir.exists():
        covers_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(f"Created Helios covers directory at {covers_dir}")

def get_steam_library() -> dict:
    env = SteamEnvironment()
    user_mgr = SteamUserManager(env)
    app_lib = SteamAppLibrary(env)
    asset_mgr = SteamAssetManager(env)

    users = user_mgr.get_users()
    steam_user = next(iter(users.values()))

    installed_steam_apps = app_lib.get_installed_steam_apps()
    nonsteam_apps = app_lib.get_nonsteam_apps(steam_user)

    



    # Add library_capsule for Steam apps
    for app in installed_steam_apps.values():
        app_id = app.get("appID")
        assets = asset_mgr.get_steam_assets(int(app_id)) if app_id else {}
        for asset_name, asset_value in assets.items():
            if asset_value:
                app[asset_name] = str(Path(asset_value))
        app["source"] = "steam"

    # Add library_capsule for non-Steam apps
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
    MANIFESTS = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
    CATCACHE_BIN = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Catalog\catcache.bin")
    epic_lib = epic.EpicLibrary(MANIFESTS, CATCACHE_BIN)

    epic_apps = {}
    for app in epic_lib.games():
        epic_apps[app.uuid] = app.to_app_dict()
    return epic_apps

def get_apollo_apps() -> ApolloAppsJSON:
    installs = find_apollo_install()
    root = None
    for install in installs:
        root = install.get("root")
    if not root:
        print("Apollo installation not found.")
        sys.exit(1)

    apps_json_path = Path(root) / "Config" / "apps.json"
    apollo = ApolloAppsJSON(apps_json_path, root)
    return apollo

def print_library_info(library_name: str, steam_apps=None, epic_apps=None, apollo=None, show_sample=True):
    """Print info about a specific library in a fast, clean format."""
    
    print_line = lambda: print("=" * 50)
    
    if library_name == "apollo":
        installs = find_apollo_install()
        if not installs:
            print("No Apollo installation found.")
            return

        for install in installs:
            root = install.get("root")
            name = install.get("name", "Unknown")
            fork = install.get("display_name", "Unknown Fork")

            print_line()
            print(f"Apollo Installation Info".center(50))
            print_line()
            print(f"{'Name:':20} {name}")
            print(f"{'Fork:':20} {fork}")
            print(f"{'Root:':20} {root}")

            apps_json_path = Path(root) / "Config" / "apps.json"
            print(f"{'Apps JSON:':20} {apps_json_path}")

            if apps_json_path.exists():
                with open(apps_json_path, "r", encoding="utf-8") as f:
                    apps_data = json.load(f)
                total_apps = len(apps_data.get("apps", []))
                last_modified = datetime.fromtimestamp(apps_json_path.stat().st_mtime)
                print(f"{'Total Apps:':20} {total_apps}")
                print(f"{'Last Modified:':20} {last_modified:%Y-%m-%d %H:%M:%S}")
                if show_sample:
                    first_apps = apps_data.get("apps", [])[:5]
                    print()
                    print("First 5 apps:")
                    for app in first_apps:
                        print(f"  - {app.get('name','Unknown')}")
            else:
                print("Apps JSON not found.")
            print_line()
            print()

    elif library_name in ("steam", "nonsteam"):
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
            print(f"Total Steam apps: {steam_count}")
            print(f"Total Non-Steam apps: {nonsteam_count}")

            if show_sample:
                
                sample_apps = list(steam_apps.values())[:5]
                print()
                print("First 5 apps:")
                for app in sample_apps:
                    print(f"  - {app.get('name','Unknown')}")
        elif library_name in "nonsteam":
            print(f"Total Non-Steam apps: {nonsteam_count}")

            if show_sample:
                # Filter only non-steam apps
                nonsteam_apps = [app for app in steam_apps.values() if app.get("source") == "nonsteam"]
                
                # Take first 5
                sample_apps = nonsteam_apps[:5]
                print()
                print("First 5 apps:")
                # Print them
                for app in sample_apps:
                    print(f"  - {app.get('name','Unknown')}")

        print_line()
        print()

    elif library_name == "epic":
        if epic_apps is None:
            print("Epic library not loaded.")
            return
        print_line()
        print("Epic Library Info".center(50))
        print_line()
        print(f"Total Epic apps: {len(epic_apps)}")
        if show_sample:
            sample_apps = list(epic_apps.values())[:5]
            print()
            print("First 5 apps:")
            for app in sample_apps:
                print(f"  - {app.get('name','Unknown')}")
        print_line()
        print()


def mark_helios_managed_apps(library: dict, apollo: ApolloAppsJSON) -> dict:
    for app_uuid, app_data in library.items():
        app_data["managed_by_helios"] = bool(apollo.get_app_by_uuid(app_uuid))
    return library


def update_helios_cache(libraries: dict, selection: str, verbose=False):
    HELIOS_CACHE_FILE = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "apps.json"
    HELIOS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    if HELIOS_CACHE_FILE.exists():
        with open(HELIOS_CACHE_FILE, "r", encoding="utf-8") as f:
            current_cache = json.load(f)
    else:
        current_cache = {}

    if selection == "steam":
        apps_to_include = {k:v for k,v in libraries.items() if v.get("source") == "steam"}
    elif selection == "nonsteam":
        apps_to_include = {k:v for k,v in libraries.items() if v.get("source") == "nonsteam"}
    elif selection == "epic":
        apps_to_include = {k:v for k,v in libraries.items() if v.get("source") == "epic"}
    else:
        apps_to_include = libraries

    if selection == "all":
        current_cache.clear()
    else:
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


def verify_helios_cache(all_libraries: dict):
    """
    Ensure Helios cache exists. If missing, rebuild it automatically for all libraries.
    """
    HELIOS_CACHE_FILE = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "apps.json"
    if not HELIOS_CACHE_FILE.exists():
        print("Helios cache missing, rebuilding for all libraries...")
        update_helios_cache(all_libraries, "all")

def handle_cache_option(all_libraries: dict, cache_selection: str):
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


def list_apps(
    libraries: dict,
    sort_key: str = None,
    managed_only: bool = False,
    show_type: bool = True,
    show_index: bool = False,
    show_managed: bool = True,
):
    apps = libraries
    if managed_only:
        apps = {k: v for k, v in libraries.items() if v.get("managed_by_helios")}

    if not apps:
        print("No apps to display.")
        return

    sorted_apps = sort_apps(apps, sort_key)

    # ---- dynamic column widths ----
    name_width = max(len(a.get("name", "")) for a in sorted_apps)
    name_width = max(name_width, 20)  # minimum

    source_width = max(len(a.get("source", "")) for a in sorted_apps)
    source_width = max(source_width, 8)

    uuid_width = max(len(a.get("uuid") or a.get("appID") or "") for a in sorted_apps)
    uuid_width = max(uuid_width, 36)

    type_width = max(len(str(a.get("type",""))) for a in sorted_apps) if show_type else 0
    type_width = max(type_width, 6) if show_type else 0

    option_width = max(len(str(len(sorted_apps))), 6) if show_index else 0

    managed_width = max(len("Managed"), max(len("Yes") if app.get("managed_by_helios") else len("No") for app in sorted_apps)) if show_managed else 0

    # ---- header ----
    header_parts = []
    if show_index:
        header_parts.append(f"{'Option':<{option_width}}")



    header_parts.extend([
        f"{'Name':<{name_width}}",
        f"{'Source':<{source_width}}"
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
        row_parts = []

        if show_index:
            row_parts.append(f"{idx:<{option_width}}")




        row_parts.extend([
            f"{app.get('name',''):<{name_width}}",
            f"{str(app.get('source','')).title():<{source_width}}",
        ])

        if show_type:
            row_parts.append(f"{str(app.get('type','')).title():<{type_width}}")

        if show_managed:
            row_parts.append(f"{'Yes' if app.get('managed_by_helios') else 'No':<{managed_width}}")

        row_parts.append(f"{(app.get('uuid') or app.get('appID') or ''):<{uuid_width}}")



        print(" | ".join(row_parts))

def print_apps_with_status(apps: list[dict], status_map: dict[str, str]):
    """
    Print a list of apps in a table style with a status column, only if the app has a status.
    """
    # Filter apps that actually have a status
    apps_with_status = [app for app in apps if status_map.get(app.get("uuid") or app.get("appID"))]

    if not apps_with_status:
        print("No apps with status to display.")
        return

    # Determine column widths dynamically
    name_width = max(max(len(a.get("name", "")) for a in apps_with_status), 20)
    source_width = max(max(len(a.get("source", "")) for a in apps_with_status), 8)
    uuid_width = max(max(len(a.get("uuid") or a.get("appID") or "") for a in apps_with_status), 36)
    status_width = max(len("Status"), max(len(status_map.get(a.get("uuid") or a.get("appID"))) for a in apps_with_status))

    # Header
    header = f"{'Name':<{name_width}} | {'Source':<{source_width}} | {'Helios ID (UUID)':<{uuid_width}} | {'Status':<{status_width}}"
    print(header)
    print("-" * len(header))

    # Rows
    for app in apps_with_status:
        uuid = app.get("uuid") or app.get("appID") or ""
        status = status_map.get(uuid)
        row = f"{app.get('name',''):<{name_width}} | {str(app.get('source','')).title():<{source_width}} | {uuid:<{uuid_width}} | {status:<{status_width}}"
        print(row)

def verify_managed_covers(
    all_libraries: dict,
    apollo,
    *,
    cleanup: bool = False,
    verbose: bool = False
):
    """
    Ensure every Helios-managed app has a cover image.

    - Restores missing or invalid covers
    - Optionally removes orphaned covers (cleanup=True)
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

        apollo_app = apollo.get_app_by_uuid(uuid)
        if not apollo_app:
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
                if not image_path.is_absolute() and apollo.root:
                    image_path = apollo.root / image_path

        # Fallback: Apollo original image
        if not image_path:
            image_path = apollo.get_image_path(app["uuid"])
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
            apollo_root=apollo.root,
            verbose=verbose,
        )

        if saved:
            restored += 1
            if apollo_app.get("image-path") != saved:
                apollo_app["image-path"] = saved
                apollo.save()
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
    apollo_root: Path | None = None,
    verbose=False
) -> str | None:
    """
    Save library capsule to Helios covers as PNG.
    Ensures the saved file is a valid PNG.
    Supports:
      - http(s) URLs
      - file:// URLs
      - absolute paths
      - relative paths from Apollo apps.json
    Returns local PNG path or None.
    """

    if not isinstance(url_or_path, str) or not url_or_path.strip():
        return None

    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    dest = covers_dir / f"{uuid}.png"

    # Already exists → verify PNG
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
        else:
            img_path = Path(url_or_path)
            if not img_path.is_absolute() and apollo_root:
                img_path = apollo_root / img_path
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



def add_games(apollo, all_libraries, input_str: str, verbose=False):
    search_terms = [s.strip() for s in input_str.split(",") if s.strip()]
    all_matches = []

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

    installs = find_apollo_install()
    fork_name = installs[0].get("display_name") if installs else "Apollo"

    # -------- Split into already-added vs unmanaged --------
    already_added = [
        a for a in all_matches
        if apollo.get_app_by_uuid(a["uuid"])
    ]
    unmanaged_matches = [
        a for a in all_matches
        if not apollo.get_app_by_uuid(a["uuid"])
    ]

    # -------- Already added (informational) --------
    if already_added and verbose:
        status_map = {
            a["uuid"]: f"Already added to {fork_name} by Helios"
            for a in already_added
        }
        print(f"These apps are already added to {fork_name} by Helios:")
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
        show_managed=False
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
        _add_game(apollo, all_libraries, app, verbose=verbose)

def _add_game(apollo, all_libraries, app_data, verbose=False):
    
    uuid = app_data['uuid']

    if apollo.get_app_by_uuid(uuid):
        if verbose:
            print(f"{app_data['name']} ({uuid}) is already in apps.json, skipping add.")
        return False

    if not check_admin_write(apollo.apps_json_path):
        return False

    # Save library capsule
    image_path = save_library_capsule(
        uuid,
        app_data.get('library_capsule'),
        apollo_root=apollo.root,
        verbose=verbose
    )

    app_entry = {
        "uuid": uuid,
        "name": app_data["name"],
        "cmd": app_data.get("launch") or "",
        "image-path": image_path or ""
    }

    apollo.apps.append(app_entry)
    apollo.by_uuid[uuid] = app_entry
    apollo.save()

    if verbose:
        print(f"Added {app_data['name']} ({uuid}) to Apollo and updated managed flags.")
    return True


def _remove_game(apollo, all_libraries, game_data, verbose=False):
    uuid = game_data["uuid"]

    if not apollo.get_app_by_uuid(uuid):
        if verbose:
            print(f"{game_data['name']} ({uuid}) not found in apps.json, skipping removal.")
        return False

    if not check_admin_write(apollo.apps_json_path):
        return False

    # Remove from Apollo
    apollo.apps = [app for app in apollo.apps if app.get("uuid") != uuid]
    apollo.by_uuid.pop(uuid, None)
    apollo.data["apps"] = apollo.apps
    apollo.save()

    # Remove cover
    cover_path = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers" / f"{uuid}.png"
    if cover_path.exists():
        cover_path.unlink(missing_ok=True)
  
    if verbose:
        print(f"Removed {game_data['name']} ({uuid}) from apps.json")
    return True


def print_helios_status(steam_apps: dict, epic_apps: dict, all_libraries: dict):
    managed_apps = [v for v in all_libraries.values() if v.get("managed_by_helios")]



    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    cover_files = []
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


def sort_apps(apps: dict, sort_key: str) -> list:
    """
    Return a sorted list of app dicts
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

def get_helios_type(helios, uuid):
    app = helios.get(uuid)
    if not app:
        return None
    return app.get("type")

# ----------------------------- CLI ----------------------------- #
def main():
    parser = argparse.ArgumentParser(description="Helios Manager (defaults to --status)")
    parser.add_argument("--info", choices=["apollo","steam","nonsteam","epic"], help="Show information about a specific library")
    parser.add_argument("--list", action="store_true", help="List installed games")
    parser.add_argument("--sort", nargs="?", const="name", help="Sort output by field")
    parser.add_argument("--cache", choices=["steam","epic","nonsteam","all"], help="Update Helios cache")
    parser.add_argument("--managed", action=argparse.BooleanOptionalAction, help="Filter to Helios-managed apps only")
    parser.add_argument("--remove", nargs="*", type=str, metavar="UUIDS", help="Comma-separated UUIDs to remove")
    parser.add_argument("--add", nargs="*", type=str, metavar="UUIDS", help="Comma-separated UUIDs to add")
    parser.add_argument("--search", nargs="+", metavar="NAME", help="Search for apps by name")
    parser.add_argument("--source", nargs="+", metavar="NAME", help="Filter to specified source(s)")
    parser.add_argument("--status", action="store_true", help="Show Helios status")
    parser.add_argument("--cleanup-covers", action="store_true", help="Remove orphaned Helios covers")
    parser.add_argument("--show-sample", action="store_true", help="Show sample apps")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    parser.add_argument("--type", help="Filter apps by Helios type")
    parser.add_argument("--dry-run", action="store_true", help="Show what would change without making any modifications")

    args = parser.parse_args()

    # ---------------- Conflict check ---------------- #
    if args.list and (args.add is not None or args.remove is not None):
        print("Error: --list cannot be used together with --add or --remove.")
        return

    # ---------------- Load libraries ---------------- #
    apollo = get_apollo_apps()
    steam_apps = get_steam_library()
    epic_apps = get_installed_epic_games()
    all_libraries = {**steam_apps, **epic_apps}
    all_libraries = mark_helios_managed_apps(all_libraries, apollo)

    apollo_install = find_apollo_install()
    fork_name = apollo_install[0].get("display_name") if apollo_install else "Apollo"

    # ---------------- Verify setup ---------------- #
    verify_helios_cache(all_libraries)
    verify_helios_covers_dir(verbose=args.verbose)
    verify_managed_covers(all_libraries, apollo, verbose=args.verbose)
    mark_helios_managed_apps(all_libraries, apollo)
    update_helios_cache(all_libraries, selection="all", verbose=False)

    # ---------------- Default behavior ---------------- #
    action_flags = any([
        args.list,
        args.add is not None,
        args.remove is not None,
        args.info,
        args.cache,
    ])

    if not action_flags:
        args.status = True


    # ---------------- Handle --cache ---------------- #
    if args.cache:
        handle_cache_option(all_libraries, args.cache)


    # ---------------- Handle --add ---------------- #
    if args.add is not None:
        unmanaged_apps = [
            app for app in all_libraries.values()
            if not app.get("managed_by_helios")
        ]

        explicit_apps = []
        interactive_pool = unmanaged_apps.copy()
        status_map = {}

        # ---- 1. Parse explicit UUIDs (comma-separated) ----
        explicit_uuids = parse_uuid_args(args.add)

        for uuid in explicit_uuids:
            app = next((a for a in unmanaged_apps if a["uuid"] == uuid), None)

            if app:
                explicit_apps.append(app)
            else:
                app_full = all_libraries.get(uuid)
                if app_full:
                    status_map[uuid] = f"Already added to {fork_name} and managed by Helios"
                else:
                    status_map[uuid] = "UUID not found"

        # Remove explicit UUIDs from interactive pool
        explicit_uuid_set = {a["uuid"] for a in explicit_apps}
        interactive_pool = [
            a for a in interactive_pool if a["uuid"] not in explicit_uuid_set
        ]

        # ---- 2. Apply filters ONLY if present ----
        filters_present = any([args.search, args.source, args.type])
        has_explicit_uuids = bool(args.add)

        if filters_present or not has_explicit_uuids:
            # ----- Search filter (comma-separated) -----
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

            # ----- Type filter (comma-separated) -----
            if args.type:
                type_terms = [
                    term.strip().lower()
                    for part in args.type
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(term in str(get_helios_type(all_libraries, a["uuid"])).lower() for term in type_terms)
                ]

            # ----- Source filter (comma-separated) -----
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


        # ---- 3. Interactive selection ----
        selected_apps = []

        if interactive_pool:
            print("Unmanaged apps matching your filters:")
            list_apps(
                {a["uuid"]: a for a in interactive_pool},
                show_index=True,
                show_managed=False
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

        # ---- 4. Perform adds ----
        all_added = []

        for app in explicit_apps + selected_apps:
            _add_game(apollo, all_libraries, app, verbose=args.verbose)
            status_map[app["uuid"]] = f"Added to {fork_name} and managed by Helios"
            all_added.append(app)

        if all_added:
            mark_helios_managed_apps(all_libraries, apollo)
            update_helios_cache(all_libraries, selection="all", verbose=False)

        # ---- 5. Print ONE table ----
        if status_map:
            print("\nAdd results:")
            apps_for_status = [
                all_libraries.get(uuid)
                for uuid in status_map
                if all_libraries.get(uuid)
            ]
            print_apps_with_status(apps_for_status, status_map)



    # ---------------- Handle --remove ---------------- #
    if args.remove is not None:
        managed_apps = [
            app for app in all_libraries.values()
            if app.get("managed_by_helios")
        ]

        explicit_apps = []
        interactive_pool = managed_apps.copy()
        status_map = {}

        # ---- 1. Parse explicit UUIDs (comma-separated) ----
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

        # Remove explicit UUIDs from interactive pool
        explicit_uuid_set = {a["uuid"] for a in explicit_apps}
        interactive_pool = [
            a for a in interactive_pool if a["uuid"] not in explicit_uuid_set
        ]

        # ---- 2. Apply filters ONLY if present ----
        filters_present = any([args.search, args.source, args.type])
        has_explicit_uuids = bool(args.add)

        if filters_present or not has_explicit_uuids:
            # ----- Search filter (comma-separated) -----
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

            # ----- Type filter (comma-separated) -----
            if args.type:
                type_terms = [
                    term.strip().lower()
                    for part in args.type
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(term in str(get_helios_type(all_libraries, a["uuid"])).lower() for term in type_terms)
                ]

            # ----- Source filter (comma-separated) -----
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

        # ---- 3. Interactive selection ----
        selected_apps = []

        if interactive_pool:
            print("Managed apps matching your filters:")
            list_apps(
                {a["uuid"]: a for a in interactive_pool},
                show_index=True,
                show_managed=False
            )

            selection = input(
                "Enter numbers to remove (comma-separated), 'all', or 'q': "
            ).strip().lower()

            if selection not in {"", "q", "quit", "exit"}:
                if selection == "all":
                    selected_apps = interactive_pool
                else:
                    indices = {
                        int(x) - 1 for x in selection.split(",") if x.strip().isdigit()
                    }
                    selected_apps = [
                        interactive_pool[i]
                        for i in indices
                        if 0 <= i < len(interactive_pool)
                    ]

        # ---- 4. Perform removals ----
        all_removed = []

        for app in explicit_apps + selected_apps:
            _remove_game(apollo, all_libraries, app, verbose=args.verbose)
            status_map[app["uuid"]] = f"Removed from {fork_name} by Helios"
            all_removed.append(app)

        if all_removed:
            mark_helios_managed_apps(all_libraries, apollo)
            update_helios_cache(all_libraries, selection="all", verbose=False)

        # ---- 5. Print ONE table ----
        if status_map:
            print("\nRemoval results:")
            apps_for_status = [
                all_libraries.get(uuid)
                for uuid in status_map
                if all_libraries.get(uuid)
            ]
            print_apps_with_status(apps_for_status, status_map)




    # ---------------- Handle --cleanup-covers ---------------- #
    if args.cleanup_covers:
        verify_managed_covers(
            all_libraries,
            apollo,
            cleanup=True,
            verbose=True
        )


    # ---------------- Handle --status ---------------- #
    if args.status:
        print_helios_status(steam_apps, epic_apps, all_libraries)

    # ---------------- Handle --info ---------------- #
    if args.info:
        library = args.info.lower()
        show_sample = args.show_sample
        if library == "apollo":
            print_library_info(library, apollo=apollo, show_sample=show_sample)
        elif library in ("steam", "nonsteam"):
            print_library_info(library, steam_apps=all_libraries, show_sample=show_sample)
        elif library == "epic":
            print_library_info(library, epic_apps=epic_apps, show_sample=show_sample)
        else:
            print(f"Unknown library: {library}")

    # ---------------- Handle --list ---------------- #
    if args.list and args.add is None and args.remove is None:
        apps_to_show = list(all_libraries.values())

        # ----- Search filter (comma-separated) -----
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

        # ----- Type filter (comma-separated) -----
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
                    term in str(get_helios_type(all_libraries, a["uuid"])).lower()
                    for term in type_terms
                )
            ]

        # ----- Source filter (comma-separated) -----
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

        # ----- Managed filter (tri-state) -----
        if args.managed is True:
            apps_to_show = [a for a in apps_to_show if a.get("managed_by_helios")]
        elif args.managed is False:
            apps_to_show = [a for a in apps_to_show if not a.get("managed_by_helios")]

        if not apps_to_show:
            print("No apps found matching your filters.")
            return

        list_apps(
            {a["uuid"]: a for a in apps_to_show},
            sort_key=args.sort
        )



if __name__ == "__main__":
    main()
