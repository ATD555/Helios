import os
import sys
import json
import argparse
from PIL import Image
from pathlib import Path
from datetime import datetime
import requests
from io import BytesIO
import urllib.parse

# External modules for interacting with Steam, Epic, and Apollo environments
from steam.steam import SteamEnvironment, SteamUserManager, SteamAppLibrary, SteamAssetManager
import epic.epic as epic
from apollo.apollo import find_apollo_install
from apollo.apollo import ApolloAppsJSON


# =====================================================================
#                           Helper Functions
# =====================================================================

def is_valid_png(path: Path) -> bool:
    """
    Check whether a file is a valid PNG image.
    Used to ensure cover art files are intact and readable.
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


def resolve_apps_by_input(all_libraries, input_str: str):
    """
    Resolve user input into matching app objects.
    Supports:
      - Exact UUID matches
      - Fuzzy name matches (case‑insensitive substring search)

    Returns a list of unique app dictionaries.
    """
    search_terms = [s.strip() for s in input_str.split(",") if s.strip()]
    matches = []

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
    Verify that the script has permission to write to a file or its parent directory.
    This is important because Apollo is often installed in protected locations
    such as Program Files, which require elevated privileges.

    Returns:
        True  – write access is available
        False – write access is denied (and an error message is printed)
    """
    if file_path.exists():
        # File exists → check direct write permission
        if not os.access(file_path, os.W_OK):
            print(f"ERROR: Cannot write to {file_path}. Run this script as Administrator.")
            return False
    else:
        # File does not exist → check permission to create it in the parent directory
        if not os.access(file_path.parent, os.W_OK):
            print(f"ERROR: Cannot create {file_path}. Run this script as Administrator.")
            return False

    return True


def verify_helios_covers_dir(verbose=False):
    """
    Ensure that the Helios cover-art directory exists under LocalAppData.
    This directory stores all processed PNG cover images for Helios-managed apps.
    """
    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"

    if not covers_dir.exists():
        covers_dir.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(f"Created Helios covers directory at {covers_dir}")


def get_steam_library() -> dict:
    """
    Discover installed Steam games and Steam-added Non-Steam shortcuts.
    Normalizes all entries into a consistent dictionary format.

    Returns:
        A dictionary keyed by UUID/appID containing metadata for each app.
    """
    env = SteamEnvironment()
    user_mgr = SteamUserManager(env)
    app_lib = SteamAppLibrary(env)
    asset_mgr = SteamAssetManager(env)

    # Determine the active Steam user (needed for shortcut metadata)
    users = user_mgr.get_users()
    steam_user = next(iter(users.values()))

    # Retrieve installed Steam titles and Non-Steam shortcuts
    installed_steam_apps = app_lib.get_installed_steam_apps()
    nonsteam_apps = app_lib.get_nonsteam_apps(steam_user)

    # Attach Steam asset paths (library capsules, icons, etc.) to Steam apps
    for app in installed_steam_apps.values():
        app_id = app.get("appID")
        assets = asset_mgr.get_steam_assets(int(app_id)) if app_id else {}

        for asset_name, asset_value in assets.items():
            if asset_value:
                app[asset_name] = str(Path(asset_value))

        app["source"] = "steam"

    # Attach asset paths for Non-Steam shortcuts added to Steam
    for app in nonsteam_apps.values():
        app_id = app.get("appID")
        assets = asset_mgr.get_nonsteam_assets(app_id, steam_user) if app_id else {}

        for asset_name, asset_value in assets.items():
            if asset_value:
                app[asset_name] = str(Path(asset_value))

        app["source"] = "nonsteam"

    # Merge Steam and Non-Steam entries into a single dictionary
    merged_apps = {**installed_steam_apps, **nonsteam_apps}
    return merged_apps


def get_installed_epic_games() -> dict:
    """
    Discover installed Epic Games Store titles by reading Epic manifest files
    and the catalog cache.

    Returns:
        A dictionary keyed by Epic UUID containing metadata for each installed app.
    """
    MANIFESTS = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")
    CATCACHE_BIN = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Catalog\catcache.bin")

    epic_lib = epic.EpicLibrary(MANIFESTS, CATCACHE_BIN)

    epic_apps = {}
    for app in epic_lib.games():
        epic_apps[app.uuid] = app.to_app_dict()

    return epic_apps


def get_apollo_apps() -> ApolloAppsJSON:
    """
    Locate the Apollo installation and load its apps.json configuration file.

    Returns:
        An ApolloAppsJSON instance representing the parsed apps.json file.

    Exits:
        If Apollo cannot be located, the script terminates with an error.
    """
    installs = find_apollo_install()
    root = None

    # Use the last detected installation root (if multiple exist)
    for install in installs:
        root = install.get("root")

    if not root:
        print("Apollo installation not found.")
        sys.exit(1)

    apps_json_path = Path(root) / "Config" / "apps.json"
    apollo = ApolloAppsJSON(apps_json_path, root)
    return apollo


def print_library_info(
    library_name: str,
    steam_apps=None,
    epic_apps=None,
    apollo=None,
    show_sample=True
):
    """
    Display detailed information about a specific library (Apollo, Steam, Epic).
    This is used by the --status <library> command to provide a quick overview.

    Args:
        library_name: One of "apollo", "steam", "nonsteam", or "epic".
        steam_apps:   Steam + Non-Steam app dictionary (if applicable).
        epic_apps:    Epic app dictionary (if applicable).
        apollo:       ApolloAppsJSON instance (if applicable).
        show_sample:  Whether to show the first few entries for preview.
    """

    print_line = lambda: print("=" * 50)

    # ------------------------ Apollo Info ------------------------
    if library_name == "apollo":
        if not apollo:
            print("Apollo not loaded.")
            return

        installs = find_apollo_install()
        root = apollo.root

        # Extract installation metadata
        for install in installs:
            name = install.get("name", "Apollo")
            fork = install.get("display_name", "Unknown Apollo Fork")

        print_line()
        print("Apollo Installation Info".center(50))
        print_line()

        print(f"{'Name:':20} {name}")
        print(f"{'Fork:':20} {fork}")
        print(f"{'Root:':20} {root}")

        apps_json_path = apollo.apps_json_path
        print(f"{'Apps JSON:':20} {apps_json_path}")

        # Show apps.json metadata
        if apps_json_path.exists():
            with open(apps_json_path, "r", encoding="utf-8") as f:
                apps_data = json.load(f)

            total_apps = len(apps_data.get("apps", []))
            last_modified = datetime.fromtimestamp(apps_json_path.stat().st_mtime)

            print(f"{'Total Apps:':20} {total_apps}")
            print(f"{'Last Modified:':20} {last_modified:%Y-%m-%d %H:%M:%S}")

            if show_sample:
                print("\nFirst 5 apps:")
                for app in apps_data.get("apps", [])[:5]:
                    print(f"   - {app.get('name', 'Unknown')}")
        else:
            print("Apps JSON not found.")

        print_line()
        print()
        return

    # ------------------------ Steam / Non-Steam Info ------------------------
    elif library_name in ("steam", "nonsteam"):
        if steam_apps is None:
            print("Steam library not loaded.")
            return

        steam_count = 0
        nonsteam_count = 0

        # Count Steam vs Non-Steam entries
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
                print("\nFirst 5 apps:")
                nonsteam_only = [
                    app for app in steam_apps.values()
                    if app.get("source") == "nonsteam"
                ]
                for app in nonsteam_only[:5]:
                    print(f"   - {app.get('name', 'Unknown')}")

        print_line()
        print()
        return

    # ------------------------ Epic Info ------------------------
    elif library_name == "epic":
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


def mark_helios_managed_apps(library: dict, apollo: ApolloAppsJSON) -> dict:
    """
    Mark each discovered app with a boolean flag indicating whether it is
    currently managed by Helios (i.e., present in Apollo's apps.json).

    Returns:
        The same dictionary with an added 'managed_by_helios' key per entry.
    """
    for app_uuid, app_data in library.items():
        app_data["managed_by_helios"] = bool(apollo.get_app_by_uuid(app_uuid))
    return library


def update_helios_cache(libraries: dict, selection: str, verbose=False):
    """
    Update the Helios local cache stored in:
        %LOCALAPPDATA%/Helios/apps.json

    This cache speeds up future operations by avoiding repeated full scans
    of Steam/Epic/Apollo libraries.

    Args:
        libraries:  All discovered apps.
        selection:  Which subset to refresh ("steam", "nonsteam", "epic", "all").
        verbose:    Whether to print progress details.
    """
    HELIOS_CACHE_FILE = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "apps.json"
    HELIOS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load existing cache if present
    if HELIOS_CACHE_FILE.exists():
        with open(HELIOS_CACHE_FILE, "r", encoding="utf-8") as f:
            current_cache = json.load(f)
    else:
        current_cache = {}

    # Determine which apps to include
    if selection == "steam":
        apps_to_include = {k: v for k, v in libraries.items() if v.get("source") == "steam"}
    elif selection == "nonsteam":
        apps_to_include = {k: v for k, v in libraries.items() if v.get("source") == "nonsteam"}
    elif selection == "epic":
        apps_to_include = {k: v for k, v in libraries.items() if v.get("source") == "epic"}
    else:
        apps_to_include = libraries

    # If refreshing all, clear the entire cache
    if selection == "all":
        current_cache.clear()
    else:
        # Otherwise remove only entries belonging to the selected source
        for uuid, app_data in list(current_cache.items()):
            if app_data.get("source") == selection:
                current_cache.pop(uuid)

    # Merge updated entries into the cache
    for uuid, app_data in apps_to_include.items():
        old_metadata = current_cache.get(uuid, {})
        current_cache[uuid] = {**old_metadata, **app_data}

    # Save updated cache
    with open(HELIOS_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(current_cache, f, indent=4)

    if verbose:
        print(f"Helios cache updated ({len(apps_to_include)} apps) for {selection} at {HELIOS_CACHE_FILE}")


def verify_helios_cache(all_libraries: dict):
    """
    Ensure the Helios cache exists. If missing, rebuild it automatically.
    """
    HELIOS_CACHE_FILE = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "apps.json"

    if not HELIOS_CACHE_FILE.exists():
        print("Helios cache missing, rebuilding for all libraries...")
        update_helios_cache(all_libraries, "all")


def handle_cache_option(all_libraries: dict, cache_selection: str):
    """
    Handle the --cache <selection> CLI option.
    Valid selections: steam, epic, nonsteam, all
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
    """
    Render a formatted ASCII table of applications.
    Column widths are dynamically calculated based on content.

    Args:
        libraries:     Dictionary of apps to display.
        sort_key:      Optional sorting key ("name", "source", "uuid", "managed").
        managed_only:  If True, only show Helios-managed apps.
        show_type:     Whether to display the app's type column.
        show_index:    Whether to display numeric selection indices.
        show_managed:  Whether to display the "Managed" column.
    """
    apps = libraries

    # Filter to only Helios-managed apps if requested
    if managed_only:
        apps = {k: v for k, v in libraries.items() if v.get("managed_by_helios")}

    if not apps:
        print("No apps to display.")
        return

    sorted_apps = sort_apps(apps, sort_key)

    # ------------------------ Column Width Calculation ------------------------
    name_width = max(max(len(a.get("name", "")) for a in sorted_apps), 20)
    source_width = max(max(len(a.get("source", "")) for a in sorted_apps), 8)
    uuid_width = max(max(len(a.get("uuid") or a.get("appID") or "") for a in sorted_apps), 36)

    type_width = (
        max(max(len(str(a.get("type", ""))) for a in sorted_apps), 6)
        if show_type else 0
    )

    option_width = max(len(str(len(sorted_apps))), 6) if show_index else 0

    managed_width = (
        max(len("Managed"), max(len("Yes") if a.get("managed_by_helios") else len("No") for a in sorted_apps))
        if show_managed else 0
    )

    # ------------------------ Header Construction ------------------------
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

    # ------------------------ Row Rendering ------------------------
    for idx, app in enumerate(sorted_apps, 1):
        row_parts = []

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


def print_apps_with_status(apps: list[dict], status_map: dict[str, str]):
    """
    Print a table of apps along with a status message for each.
    Used primarily after add/remove operations to show results such as:
        - "Already added"
        - "Added successfully"
        - "Removed"
    """
    # Only include apps that actually have a status entry
    apps_with_status = [
        app for app in apps
        if status_map.get(app.get("uuid") or app.get("appID"))
    ]

    if not apps_with_status:
        print("No apps with status to display.")
        return

    # Determine dynamic column widths
    name_width = max(max(len(a.get("name", "")) for a in apps_with_status), 20)
    source_width = max(max(len(a.get("source", "")) for a in apps_with_status), 8)
    uuid_width = max(max(len(a.get("uuid") or a.get("appID") or "") for a in apps_with_status), 36)
    status_width = max(
        len("Status"),
        max(len(status_map.get(a.get("uuid") or a.get("appID"))) for a in apps_with_status)
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
    apollo,
    *,
    cleanup: bool = False,
    verbose: bool = False
):
    """
    Ensure that every Helios-managed app has a valid cover image stored locally.

    Behavior:
        - If a cover is missing or invalid, attempt to restore it from:
            1. The library capsule (Steam/Epic)
            2. The Apollo image-path fallback
        - If cleanup=True, remove orphaned covers (files with no matching app)

    Args:
        all_libraries: All discovered apps (Steam/Epic/Non-Steam).
        apollo:        ApolloAppsJSON instance.
        cleanup:       Whether to remove orphaned cover files.
        verbose:       Whether to print detailed progress.
    """
    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    restored = 0
    failed = 0
    removed = 0

    # UUIDs of apps that are managed by Helios
    managed_uuids = {
        uuid for uuid, app in all_libraries.items()
        if app.get("managed_by_helios")
    }

    for uuid in managed_uuids:
        dest = covers_dir / f"{uuid}.png"

        # If a valid PNG already exists, skip
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

        # Prefer the library capsule (Steam/Epic)
        image_source = app.get("library_capsule")
        image_path = None

        if image_source:
            parsed = urllib.parse.urlparse(image_source)

            # HTTP/HTTPS URL
            if parsed.scheme in ("http", "https"):
                image_path = image_source
            else:
                # Local file path
                image_path = Path(image_source)
                if not image_path.is_absolute() and apollo.root:
                    image_path = apollo.root / image_path

        # Fallback: Apollo's original image-path
        if not image_path:
            image_path = apollo.get_image_path(app["uuid"])
            if image_path and image_path.exists():
                image_path = str(image_path)
            elif image_path and image_path.is_absolute():
                image_path = str(image_path)

        # Validate the final image path
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

        # Attempt to save the cover
        saved = save_library_capsule(
            uuid,
            image_source,
            apollo_root=apollo.root,
            verbose=verbose,
        )

        if saved:
            restored += 1

            # Update Apollo metadata if needed
            if apollo_app.get("image-path") != saved:
                apollo_app["image-path"] = saved
                apollo.save()

            if verbose:
                print(f"[RESTORED] Cover art for {app.get('name')}")
        else:
            failed += 1

    # Optional cleanup of orphaned covers
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
    Save a library capsule (cover image) to the Helios covers directory as a PNG.

    Supports:
        - HTTP/HTTPS URLs
        - file:// URLs
        - Absolute paths
        - Relative paths (resolved against Apollo root)

    Ensures:
        - The saved file is a valid PNG
        - Existing corrupted or non-PNG files are replaced

    Returns:
        The local PNG path as a string, or None on failure.
    """
    if not isinstance(url_or_path, str) or not url_or_path.strip():
        return None

    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    dest = covers_dir / f"{uuid}.png"

    # If file exists, verify it's a valid PNG
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
        # ---------------- HTTP/HTTPS ----------------
        if url_or_path.lower().startswith(("http://", "https://")):
            resp = requests.get(url_or_path, timeout=15)
            resp.raise_for_status()
            im = Image.open(BytesIO(resp.content))

        # ---------------- file:// URL ----------------
        elif url_or_path.lower().startswith("file://"):
            img_path = Path(url_or_path[7:])
            im = Image.open(img_path)

        # ---------------- Local path ----------------
        else:
            img_path = Path(url_or_path)
            if not img_path.is_absolute() and apollo_root:
                img_path = apollo_root / img_path

            if not img_path.exists():
                return None

            im = Image.open(img_path)

        # Convert to PNG if needed
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
    """
    High‑level workflow for adding games to Apollo via Helios.

    Steps:
        1. Resolve user input into matching apps (UUID or fuzzy name).
        2. Separate matches into:
              - already added
              - unmanaged (eligible to add)
        3. Display unmanaged apps and prompt user for selection.
        4. Add selected apps to Apollo and save cover images.

    Args:
        apollo:         ApolloAppsJSON instance.
        all_libraries:  All discovered apps (Steam/Epic/Non-Steam).
        input_str:      User input string (names or UUIDs).
        verbose:        Whether to print detailed progress.
    """
    search_terms = [s.strip() for s in input_str.split(",") if s.strip()]
    all_matches = []

    # ------------------------ Resolve UUIDs + fuzzy names ------------------------
    for term in search_terms:
        # Exact UUID match
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

    # Deduplicate by UUID
    all_matches = list({a["uuid"]: a for a in all_matches}.values())

    if not all_matches:
        print("No apps found matching your input.")
        return

    installs = find_apollo_install()
    fork_name = installs[0].get("display_name") if installs else "Apollo"

    # ------------------------ Split into added vs unmanaged ------------------------
    already_added = [a for a in all_matches if apollo.get_app_by_uuid(a["uuid"])]
    unmanaged_matches = [a for a in all_matches if not apollo.get_app_by_uuid(a["uuid"])]

    # Inform user about already-added apps
    if already_added and verbose:
        status_map = {
            a["uuid"]: f"Already added to {fork_name} by Helios"
            for a in already_added
        }
        print(f"These apps are already added to {fork_name} by Helios:")
        print_apps_with_status(already_added, status_map)
        print()

    if not unmanaged_matches:
        print("No unmanaged apps found to add.")
        return

    # ------------------------ Display unmanaged apps ------------------------
    print("Unmanaged apps matching your input:")
    list_apps(
        {a["uuid"]: a for a in unmanaged_matches},
        show_index=True,
        show_managed=False
    )

    # ------------------------ User selection ------------------------
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

    # ------------------------ Add selected apps ------------------------
    for app in selected_apps:
        _add_game(apollo, all_libraries, app, verbose=verbose)

def _add_game(apollo, all_libraries, app_data, verbose=False):
    """
    Add a single game entry to Apollo's apps.json.

    Behavior:
        - Ensures the game is not already present.
        - Ensures write permissions.
        - Saves the game's cover image.
        - Writes the new entry to apps.json.

    Returns:
        True if added, False otherwise.
    """
    uuid = app_data['uuid']

    # Already present
    if apollo.get_app_by_uuid(uuid):
        if verbose:
            print(f"{app_data['name']} ({uuid}) is already in apps.json, skipping add.")
        return False

    # Check write permissions
    if not check_admin_write(apollo.apps_json_path):
        return False

    # Save cover image
    image_path = save_library_capsule(
        uuid,
        app_data.get('library_capsule'),
        apollo_root=apollo.root,
        verbose=verbose
    )

    # Construct new app entry
    app_entry = {
        "uuid": uuid,
        "name": app_data["name"],
        "cmd": app_data.get("launch") or "",
        "image-path": image_path or ""
    }

    # Update Apollo structures
    apollo.apps.append(app_entry)
    apollo.by_uuid[uuid] = app_entry
    apollo.save()

    if verbose:
        print(f"Added {app_data['name']} ({uuid}) to Apollo and updated managed flags.")
    return True

def _remove_game(apollo, all_libraries, game_data, verbose=False):
    """
    Remove a game entry from Apollo's apps.json and delete its cover image.

    Behavior:
        - Ensures the game exists in apps.json.
        - Ensures write permissions.
        - Removes entry and cover image.

    Returns:
        True if removed, False otherwise.
    """
    uuid = game_data["uuid"]

    # Not present
    if not apollo.get_app_by_uuid(uuid):
        if verbose:
            print(f"{game_data['name']} ({uuid}) not found in apps.json, skipping removal.")
        return False

    # Check write permissions
    if not check_admin_write(apollo.apps_json_path):
        return False

    # Remove from Apollo
    apollo.apps = [app for app in apollo.apps if app.get("uuid") != uuid]
    apollo.by_uuid.pop(uuid, None)
    apollo.data["apps"] = apollo.apps
    apollo.save()

    # Remove cover file
    cover_path = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers" / f"{uuid}.png"
    if cover_path.exists():
        cover_path.unlink(missing_ok=True)

    if verbose:
        print(f"Removed {game_data['name']} ({uuid}) from apps.json")
    return True

def print_helios_status(steam_apps: dict, epic_apps: dict, all_libraries: dict):
    """
    Print a summary of Helios-managed apps, discovered apps, and cover files.

    Includes:
        - Steam/Epic totals
        - Managed vs unmanaged counts
        - Cover file counts (including orphaned covers)
    """
    managed_apps = [v for v in all_libraries.values() if v.get("managed_by_helios")]

    covers_dir = Path(os.getenv("LOCALAPPDATA")) / "Helios" / "covers"
    cover_files = []

    if covers_dir.exists():
        cover_files = [f for f in covers_dir.iterdir() if f.suffix.lower() == ".png"]

    managed_cover_uuids = {app["uuid"] for app in managed_apps if "uuid" in app}

    orphaned_covers = [f for f in cover_files if f.stem not in managed_cover_uuids]

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
    Sort a dictionary of apps based on a given key.

    Supported keys:
        - name
        - source
        - uuid
        - managed (Helios-managed apps first)

    Returns:
        A sorted list of app dictionaries.
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
        return sorted(
            apps.values(),
            key=lambda a: a.get("managed_by_helios", False),
            reverse=True
        )

def get_helios_type(helios, uuid):
    """
    Retrieve the Helios-defined 'type' field for a given UUID.

    Args:
        helios: Dictionary-like object containing app metadata.
        uuid:   The UUID of the app to query.

    Returns:
        The 'type' value if present, otherwise None.
    """
    app = helios.get(uuid)
    if not app:
        return None
    return app.get("type")


# =====================================================================
#                               CLI ENTRY
# =====================================================================

def main():
    """
    Main command-line interface entry point for the Helios tool.

    Handles:
        - Argument parsing
        - Library loading
        - Cache verification
        - Add/remove workflows
        - Listing, searching, filtering
        - Status reporting
        - Cover cleanup
    """
    parser = argparse.ArgumentParser(
        prog='helios',
        description="Helios (defaults to --status)"
    )

    # ------------------------ CLI Arguments ------------------------
    parser.add_argument("-l", "--list", action="store_true",
                        help="List installed games")
    parser.add_argument("--sort", nargs="?", const="name",
                        help="Sort output by field")
    parser.add_argument("--cache", choices=["steam", "epic", "nonsteam", "all"],
                        help="Update Helios cache")
    parser.add_argument("--managed", action=argparse.BooleanOptionalAction,
                        help="Filter to Helios-managed apps only")
    parser.add_argument("-r", "--remove", nargs="*", type=str, metavar="UUIDS",
                        help="Comma-separated UUIDs to remove")
    parser.add_argument("-a", "--add", nargs="*", type=str, metavar="UUIDS",
                        help="Comma-separated UUIDs to add")
    parser.add_argument("--search", nargs="+", metavar="NAME",
                        help="Search for apps by name")
    parser.add_argument("--source", nargs="+", metavar="NAME",
                        help="Filter to specified source(s)")
    parser.add_argument("-s", "--status", metavar="LIBRARY",
                        help="Show status/info of a specific library")
    parser.add_argument("--cleanup-covers", action="store_true",
                        help="Remove orphaned Helios covers")
    parser.add_argument("--show-sample", action="store_true",
                        help="Show sample apps")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show detailed output")
    parser.add_argument("--type", help="Filter apps by Helios type")

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

    # ------------------------ Load Libraries ------------------------
    apollo = get_apollo_apps()
    steam_apps = get_steam_library()
    epic_apps = get_installed_epic_games()

    # Merge Steam + Epic into a unified dictionary
    all_libraries = {**steam_apps, **epic_apps}

    # Mark which apps are managed by Helios
    all_libraries = mark_helios_managed_apps(all_libraries, apollo)

    apollo_install = find_apollo_install()
    fork_name = apollo_install[0].get("display_name") if apollo_install else "Apollo"

    # ------------------------ Verify Setup ------------------------
    verify_helios_cache(all_libraries)
    verify_helios_covers_dir(verbose=args.verbose)
    verify_managed_covers(all_libraries, apollo, verbose=args.verbose)
    mark_helios_managed_apps(all_libraries, apollo)
    update_helios_cache(all_libraries, selection="all", verbose=False)

    # ------------------------ Handle --cache ------------------------
    if args.cache:
        handle_cache_option(all_libraries, args.cache)

    # =================================================================
    #                           ADD WORKFLOW
    # =================================================================
    if args.add is not None:
        # All apps not yet managed by Helios
        unmanaged_apps = [
            app for app in all_libraries.values()
            if not app.get("managed_by_helios")
        ]

        explicit_apps = []      # Apps explicitly requested by UUID
        interactive_pool = unmanaged_apps.copy()
        status_map = {}

        # ---- Parse explicit UUIDs ----
        explicit_uuids = parse_uuid_args(args.add)

        for uuid in explicit_uuids:
            # Check if UUID corresponds to an unmanaged app
            app = next((a for a in unmanaged_apps if a["uuid"] == uuid), None)

            if app:
                explicit_apps.append(app)
            else:
                # UUID exists but is already managed
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

        # ---- Apply filters (search, type, source) ----
        filters_present = any([args.search, args.source, args.type])
        has_explicit_uuids = bool(args.add)

        if filters_present or not has_explicit_uuids:
            # Search filter
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

            # Type filter
            if args.type:
                type_terms = [
                    term.strip().lower()
                    for part in args.type
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(term in str(get_helios_type(all_libraries, a["uuid"])).lower()
                           for term in type_terms)
                ]

            # Source filter
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

        # ---- Interactive selection ----
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

        # ---- Perform additions ----
        all_added = []

        for app in explicit_apps + selected_apps:
            _add_game(apollo, all_libraries, app, verbose=args.verbose)
            status_map[app["uuid"]] = f"Added to {fork_name} and managed by Helios"
            all_added.append(app)

        # Refresh cache after modifications
        if all_added:
            mark_helios_managed_apps(all_libraries, apollo)
            update_helios_cache(all_libraries, selection="all", verbose=False)

        # ---- Print results ----
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
        managed_apps = [
            app for app in all_libraries.values()
            if app.get("managed_by_helios")
        ]

        explicit_apps = []
        interactive_pool = managed_apps.copy()
        status_map = {}

        # ---- Parse explicit UUIDs ----
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

        # ---- Apply filters ----
        filters_present = any([args.search, args.source, args.type])
        has_explicit_uuids = bool(args.remove)

        if filters_present or not has_explicit_uuids:
            # Search filter
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

            # Type filter
            if args.type:
                type_terms = [
                    term.strip().lower()
                    for part in args.type
                    for term in part.split(",")
                    if term.strip()
                ]
                interactive_pool = [
                    a for a in interactive_pool
                    if any(term in str(get_helios_type(all_libraries, a["uuid"])).lower()
                           for term in type_terms)
                ]

            # Source filter
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

        # ---- Interactive selection ----
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

        # ---- Perform removals ----
        all_removed = []

        for app in explicit_apps + selected_apps:
            _remove_game(apollo, all_libraries, app, verbose=args.verbose)
            status_map[app["uuid"]] = f"Removed from {fork_name} by Helios"
            all_removed.append(app)

        # Refresh cache
        if all_removed:
            mark_helios_managed_apps(all_libraries, apollo)
            update_helios_cache(all_libraries, selection="all", verbose=False)

        # ---- Print results ----
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
        verify_managed_covers(
            all_libraries,
            apollo,
            cleanup=True,
            verbose=True
        )

    # =================================================================
    #                        STATUS / INFO WORKFLOW
    # =================================================================
    if args.status:
        library = args.status.lower()
        show_sample = args.show_sample

        if library == "apollo":
            print_library_info(library, apollo=apollo, show_sample=show_sample)
        elif library in ("steam", "nonsteam"):
            print_library_info(library, steam_apps=all_libraries, show_sample=show_sample)
        elif library == "epic":
            print_library_info(library, epic_apps=epic_apps, show_sample=show_sample)
        elif (library == (None or "") or library == "helios"):
            print_helios_status(steam_apps, epic_apps, all_libraries)
        else:
            print(f"Unknown library: {library}")

    # =================================================================
    #                           LIST WORKFLOW
    # =================================================================
    if args.list and args.add is None and args.remove is None:
        apps_to_show = list(all_libraries.values())

        # ---- Search filter ----
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

        # ---- Type filter ----
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

        # ---- Source filter ----
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

        # ---- Managed filter ----
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


# =====================================================================
#                               ENTRY POINT
# =====================================================================

if __name__ == "__main__":
    main()