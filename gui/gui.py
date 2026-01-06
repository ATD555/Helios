# =============================================================================
# Helios Library Manager - customtkinter GUI
# Two-pane managed/unmanaged view with filters and cover thumbnails
# =============================================================================

import os
from pathlib import Path
import threading
import io
import urllib.request

import customtkinter as ctk
from PIL import Image, ImageChops
from tkinter import filedialog, Menu

import helios  # backend module


# =============================================================================
# Paths & Globals
# =============================================================================

LOCALAPPDATA = Path(os.getenv("LOCALAPPDATA") or "")
HELIOS_COVERS_DIR = LOCALAPPDATA / "Helios" / "covers"
TEMP_COMPARE_DIR = LOCALAPPDATA / "Helios" / "temp_original_covers"

THUMBNAIL_SIZE = (40, 60)
THUMB_CACHE: dict[str, ctk.CTkImage] = {}  # uuid → CTkImage cache


# =============================================================================
# Helper functions
# =============================================================================

def ensure_covers_dir() -> None:
    HELIOS_COVERS_DIR.mkdir(parents=True, exist_ok=True)


def ensure_temp_compare_dir() -> None:
    TEMP_COMPARE_DIR.mkdir(parents=True, exist_ok=True)


def get_cached_original_path(uuid: str) -> Path:
    return TEMP_COMPARE_DIR / f"{uuid}.png"


def load_cover_thumbnail(environment, app: dict, size=THUMBNAIL_SIZE) -> ctk.CTkImage:
    """
    Load or generate a CTkImage thumbnail for an app.
    Uses Helios to generate the cover if missing.
    """
    uuid = app.get("uuid")
    if not uuid:
        img = Image.new("RGB", size, "#333333")
        return ctk.CTkImage(light_image=img, size=size)

    # Cache hit
    if uuid in THUMB_CACHE:
        return THUMB_CACHE[uuid]

    ensure_covers_dir()
    cover_path = HELIOS_COVERS_DIR / f"{uuid}.png"

    # Generate cover if missing
    if not cover_path.exists():
        try:
            helios.save_library_capsule(
                uuid,
                app.get("library_capsule"),
                environment_root=environment.root if environment else None,
                dry_run=False,
            )
        except Exception:
            pass

    # Load or placeholder
    try:
        img = Image.open(cover_path)
    except Exception:
        img = Image.new("RGB", size, "#333333")

    ctk_img = ctk.CTkImage(light_image=img, size=size)
    THUMB_CACHE[uuid] = ctk_img
    return ctk_img


# =============================================================================
# GUI widgets
# =============================================================================

class AppListItem(ctk.CTkFrame):
    """
    A single row in the managed/unmanaged lists.
    """

    def __init__(self, parent, app_data: dict, app_controller, environment):
        super().__init__(parent)
        self.app_data = app_data
        self.app_controller = app_controller
        self.environment = environment

        self.grid_columnconfigure(2, weight=1)

        # Checkbox
        self.checkbox = ctk.CTkCheckBox(
            self,
            text="",
            width=20,
            command=self.on_checkbox_changed,
        )
        self.checkbox.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        # Thumbnail
        self.cover_image = load_cover_thumbnail(self.environment, self.app_data)
        self.image_label = ctk.CTkLabel(self, image=self.cover_image, text="")
        self.image_label.grid(row=0, column=1, padx=5, pady=5)

        # Title
        source = self.app_data.get("source")
        if isinstance(source, str):
            source = "Non-Steam" if source.lower() == "nonsteam" else source.title()
        else:
            source = "Unknown"

        title_text = f"{self.app_data.get('name', 'Unknown')} [{source}]"
        self.title_label = ctk.CTkLabel(self, text=title_text)
        self.title_label.grid(row=0, column=2, padx=5, pady=5, sticky="w")

        # Click bindings
        for widget in (self, self.image_label, self.title_label):
            widget.bind("<Button-1>", self.on_click)
            widget.bind("<Button-3>", self.on_right_click)

    def on_click(self, event) -> None:
        self.app_controller.highlight_item(self)
        self.app_controller.on_item_selected(self.app_data)

    def on_checkbox_changed(self) -> None:
        self.app_controller.update_button_states()

    def set_checked(self, value: bool) -> None:
        self.checkbox.select() if value else self.checkbox.deselect()

    def is_checked(self) -> bool:
        return bool(self.checkbox.get())

    def on_right_click(self, event) -> None:
        self.app_controller.highlight_item(self)
        self.app_controller.on_item_selected(self.app_data)

        menu = Menu(self, tearoff=0)
        env_name = self.app_controller.get_environment_display_name()

        # Add/Remove
        if self.app_data.get("managed_by_helios"):
            menu.add_command(
                label=f"Remove Game from {env_name}",
                command=lambda: self.app_controller.on_remove_single(self.app_data),
            )
        else:
            menu.add_command(
                label=f"Add Game to {env_name}",
                command=lambda: self.app_controller.on_add_single(self.app_data),
            )

        # Cover submenu (only for managed apps)
        if self.app_data.get("managed_by_helios"):
            cover_menu = Menu(menu, tearoff=0)
            cover_menu.add_command(
                label="Change Cover…",
                command=lambda: self.app_controller.on_change_cover(self.app_data),
            )

            if self.app_controller.cover_is_modified(self.app_data):
                cover_menu.add_command(
                    label="Refresh/Restore Original Cover",
                    command=lambda: self.app_controller.on_restore_cover(self.app_data),
                )

            menu.add_cascade(label="Cover", menu=cover_menu)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


class LoadingScreen(ctk.CTkToplevel):
    def __init__(self, parent, message: str):
        super().__init__(parent)
        self.title("")
        self.geometry("300x120")
        self.resizable(False, False)
        self.overrideredirect(True)

        # Center on screen
        self.update_idletasks()
        w, h = 300, 120
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        # Store label as attribute so we can update it later
        self.label = ctk.CTkLabel(self, text=message, font=("Segoe UI", 16))
        self.label.pack(pady=20)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.pack(fill="x", padx=20, pady=10)
        self.progress.start()


# =============================================================================
# Main application
# =============================================================================

class HeliosApp(ctk.CTk):
    def __init__(self):
        # HARD STOP: require admin before initializing the app
        if not self._is_admin():
            self._show_admin_blocker()
            raise SystemExit
        super().__init__()

        ctk.set_appearance_mode("dark")
        self.title("Helios Library Manager")
        self.geometry("1100x700")
        self.minsize(900, 600)

        # Loading overlay
        self.loading = LoadingScreen(self, f"Helios Loading {self.get_environment_display_name()} Library…")
        self.withdraw()

        # Backend state
        self.environment = None
        self.all_libraries: dict[str, dict] = {}
        self.unmanaged_apps: dict[str, dict] = {}
        self.managed_apps: dict[str, dict] = {}

        # UI state
        self.available_items: list[AppListItem] = []
        self.managed_items: list[AppListItem] = []
        self.selected_app: dict | None = None
        self.highlighted_item: AppListItem | None = None

        self.available_filter_var = ctk.StringVar()
        self.managed_filter_var = ctk.StringVar()

        # Layout
        self.grid_columnconfigure((0, 2), weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(4, weight=1)

        # Start loading
        self.after(0, self._start_loading_thread)
        self.protocol("WM_DELETE_WINDOW", self._on_close)


    def _show_admin_blocker(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()

        from tkinter import messagebox
        messagebox.showerror(
            "Administrator Required",
            "Helios Library Manager must be run as Administrator.\n\n"
            "Please restart the application with elevated permissions."
        )

        root.destroy()

    # -------------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------------

    def _on_close(self) -> None:
        self._cleanup_temp_compare_dir()
        self.destroy()

    def _cleanup_temp_compare_dir(self) -> None:
        if TEMP_COMPARE_DIR.exists():
            try:
                import shutil
                shutil.rmtree(TEMP_COMPARE_DIR)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Startup
    # -------------------------------------------------------------------------

    def _start_loading_thread(self):
        threading.Thread(target=self._startup_sequence, daemon=True).start()


    def _startup_sequence(self):
        self._load_backend_data()

        if self.loading:
            env_name = self.get_environment_display_name()
            self.loading.label.configure(
                text=f"Helios Loading {env_name} Library…"
            )

        self.after(0, self._finish_startup)


    def _finish_startup(self) -> None:
        self._build_ui()
        self._populate_lists()

        # Remove loading overlay
        if self.loading:
            self.loading.destroy()
            self.loading = None

        # Build dropdown values AFTER lists exist
        self._rebuild_dropdown_values()

        # Reset dropdowns
        self.available_source_var.set("All")
        self.managed_source_var.set("All")
        self.available_type_var.set("All")
        self.managed_type_var.set("All")

        self.deiconify()

    def _is_admin(self) -> bool:
        import ctypes
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False
    # -------------------------------------------------------------------------
    # Backend loading
    # -------------------------------------------------------------------------

    def _load_backend_data(self) -> None:
        try:
            steam = helios.get_steam_library()
        except Exception:
            steam = {}

        try:
            epic = helios.get_installed_epic_games()
            for uuid, app in epic.items():
                app["source"] = "epic"
        except Exception:
            epic = {}

        try:
            self.environment = helios.get_environment_apps()
        except Exception:
            self.environment = None

        self.all_libraries = {**steam, **epic}

        if self.environment:
            helios.mark_helios_managed_apps(self.all_libraries, self.environment)

        self._split_managed_unmanaged()
        self._rebuild_original_cover_cache()

    def _split_managed_unmanaged(self) -> None:
        self.managed_apps = {
            u: a for u, a in self.all_libraries.items()
            if a.get("managed_by_helios")
        }
        self.unmanaged_apps = {
            u: a for u, a in self.all_libraries.items()
            if not a.get("managed_by_helios")
        }

    def _show_operation_loading(self, message: str):
        self.operation_loading = LoadingScreen(self, message)
        self.operation_loading.grab_set()  # modal
        self.operation_loading.update()

    def _hide_operation_loading(self):
        if hasattr(self, "operation_loading") and self.operation_loading:
            try:
                self.operation_loading.progress.stop()
            except Exception:
                pass
            self.operation_loading.destroy()
            self.operation_loading = None



    # -------------------------------------------------------------------------
    # Original cover cache (Restore Original)
    # -------------------------------------------------------------------------

    def _rebuild_original_cover_cache(self) -> None:
        """Rebuild normalized original cover cache for managed apps."""
        ensure_temp_compare_dir()

        # Clear folder
        for f in TEMP_COMPARE_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass

        # Rebuild
        for app in self.managed_apps.values():
            self.ensure_original_cached(app)

    def _load_original_bytes(self, app_data: dict) -> bytes | None:
        """Load original cover bytes from file, URL, or environment."""
        capsule = app_data.get("library_capsule")

        # File path
        if isinstance(capsule, str) and not capsule.startswith(("http://", "https://")):
            p = Path(capsule)
            if p.exists():
                try:
                    return p.read_bytes()
                except Exception:
                    pass

        # URL
        if isinstance(capsule, str) and capsule.startswith(("http://", "https://")):
            try:
                with urllib.request.urlopen(capsule) as resp:
                    return resp.read()
            except Exception:
                pass

        # Environment fallback
        uuid = app_data.get("uuid")
        if self.environment:
            env_img = self.environment.get_image_path(uuid)
            if env_img and env_img.exists():
                try:
                    return env_img.read_bytes()
                except Exception:
                    pass

        return None

    def images_are_equal(self, path1: Path, path2: Path) -> bool:
        try:
            img1 = Image.open(path1).convert("RGB")
            img2 = Image.open(path2).convert("RGB")

            # If sizes differ, they are not equal
            if img1.size != img2.size:
                return False

            diff = ImageChops.difference(img1, img2)
            return diff.getbbox() is None  # True if visually identical
        except Exception:
            return False

    def ensure_original_cached(self, app_data: dict) -> Path | None:
        """Ensure original cover is cached as normalized PNG."""
        uuid = app_data.get("uuid")
        ensure_temp_compare_dir()
        dest = get_cached_original_path(uuid)

        if dest.exists():
            return dest

        source_bytes = self._load_original_bytes(app_data)
        if not source_bytes:
            return None

        try:
            img = Image.open(io.BytesIO(source_bytes)).convert("RGB")
            img.save(dest, "PNG")
            return dest
        except Exception:
            return None

    def cover_is_modified(self, app_data: dict) -> bool:
        uuid = app_data.get("uuid")
        if not uuid:
            return False

        cover_path = HELIOS_COVERS_DIR / f"{uuid}.png"
        if not cover_path.exists():
            return False  # No cover yet → not modified

        cached = self.ensure_original_cached(app_data)
        if not cached or not cached.exists():
            return False  # No original → treat as not modified

        return not self.images_are_equal(cover_path, cached)

    def on_change_cover(self, app_data: dict) -> None:
        self.selected_app = app_data

        file_path = filedialog.askopenfilename(
            title="Select PNG Cover",
            filetypes=[("PNG Files", "*.png")],
        )
        if not file_path:
            return

        uuid = app_data.get("uuid")
        if not uuid:
            self._set_status("Selected app has no UUID.", "red", 4000)
            return

        ensure_covers_dir()
        dest = HELIOS_COVERS_DIR / f"{uuid}.png"

        try:
            dest.write_bytes(Path(file_path).read_bytes())
            THUMB_CACHE.pop(uuid, None)
            self._set_status("Cover updated.", "green")
            self._populate_lists()
        except Exception as e:
            self._set_status(f"Failed to update cover: {e}", "red", 5000)

    def on_restore_cover(self, app_data: dict) -> None:
        uuid = app_data.get("uuid")
        if not uuid:
            self._set_status("No UUID.", "red")
            return

        cached = self.ensure_original_cached(app_data)
        if not cached or not cached.exists():
            self._set_status("Original cover not available.", "red")
            return

        ensure_covers_dir()
        dest = HELIOS_COVERS_DIR / f"{uuid}.png"

        try:
            dest.write_bytes(cached.read_bytes())
        except Exception as e:
            self._set_status(f"Failed to restore cover: {e}", "red")
            return

        THUMB_CACHE.pop(uuid, None)
        self._set_status("Cover restored.", "green")
        self._populate_lists()

    # -------------------------------------------------------------------------
    # UI building
    # -------------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ---------------- Filters row ----------------
        # Unmanaged
        avail_filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        avail_filter_frame.grid(row=0, column=0, sticky="ew", padx=(10, 5), pady=(10, 5))
        avail_filter_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(avail_filter_frame, text="Source:").grid(
            row=0, column=1, padx=(5, 0), sticky="w"
        )
        ctk.CTkLabel(avail_filter_frame, text="Application Type:").grid(
            row=0, column=2, padx=(5, 0), sticky="w"
        )

        self.available_type_var = ctk.StringVar(value="All")
        self.available_type_dropdown = ctk.CTkOptionMenu(
            avail_filter_frame,
            values=["All"],
            variable=self.available_type_var,
            command=lambda _: self.apply_filters(),
        )
        self.available_type_dropdown.grid(row=1, column=2, padx=(5, 0))

        ctk.CTkLabel(avail_filter_frame, text="Filter:").grid(
            row=0, column=0, sticky="w", padx=(0, 5)
        )
        self.available_filter_entry = ctk.CTkEntry(
            avail_filter_frame,
            textvariable=self.available_filter_var,
            placeholder_text="Search unmanaged apps...",
        )
        self.available_filter_entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.available_filter_entry.bind("<KeyRelease>", lambda e: self.apply_filters())

        self.available_clear_btn = ctk.CTkButton(
            avail_filter_frame,
            text="✕",
            width=18,
            height=18,
            fg_color="#3A3A3A",
            hover_color="#505050",
            text_color="#C8C8C8",
            corner_radius=4,
            command=lambda: self._clear_filter("available"),
        )
        self.available_clear_btn.grid(row=1, column=0, sticky="e", padx=(0, 6))

        self.available_source_var = ctk.StringVar(value="All")
        self.available_source_dropdown = ctk.CTkOptionMenu(
            avail_filter_frame,
            values=["All"],
            variable=self.available_source_var,
            command=lambda _: self.apply_filters(),
        )
        self.available_source_dropdown.grid(row=1, column=1, padx=(5, 0))

        # Managed
        managed_filter_frame = ctk.CTkFrame(self, fg_color="transparent")
        managed_filter_frame.grid(row=0, column=2, sticky="ew", padx=(5, 10), pady=(10, 5))
        managed_filter_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(managed_filter_frame, text="Source:").grid(
            row=0, column=1, padx=(5, 0), sticky="w"
        )
        ctk.CTkLabel(managed_filter_frame, text="Application Type:").grid(
            row=0, column=2, padx=(5, 0), sticky="w"
        )

        self.managed_type_var = ctk.StringVar(value="All")
        self.managed_type_dropdown = ctk.CTkOptionMenu(
            managed_filter_frame,
            values=["All"],
            variable=self.managed_type_var,
            command=lambda _: self.apply_filters(),
        )
        self.managed_type_dropdown.grid(row=1, column=2, padx=(5, 0))

        ctk.CTkLabel(managed_filter_frame, text="Filter:").grid(
            row=0, column=0, sticky="w", padx=(0, 5)
        )
        self.managed_filter_entry = ctk.CTkEntry(
            managed_filter_frame,
            textvariable=self.managed_filter_var,
            placeholder_text="Search managed apps...",
        )
        self.managed_filter_entry.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.managed_filter_entry.bind("<KeyRelease>", lambda e: self.apply_filters())

        self.managed_clear_btn = ctk.CTkButton(
            managed_filter_frame,
            text="✕",
            width=18,
            height=18,
            fg_color="#3A3A3A",
            hover_color="#505050",
            text_color="#C8C8C8",
            corner_radius=4,
            command=lambda: self._clear_filter("managed"),
        )
        self.managed_clear_btn.grid(row=1, column=0, sticky="e", padx=(0, 6))

        self.managed_source_var = ctk.StringVar(value="All")
        self.managed_source_dropdown = ctk.CTkOptionMenu(
            managed_filter_frame,
            values=["All"],
            variable=self.managed_source_var,
            command=lambda _: self.apply_filters(),
        )
        self.managed_source_dropdown.grid(row=1, column=1, padx=(5, 0))

        # ---------------- Select/Deselect rows ----------------
        available_actions = ctk.CTkFrame(self, fg_color="transparent")
        available_actions.grid(row=1, column=0, sticky="w", padx=(10, 5), pady=(0, 5))

        self.available_select_all_btn = ctk.CTkButton(
            available_actions,
            text="Select All",
            width=100,
            command=lambda: self.toggle_all("available", True),
        )
        self.available_select_all_btn.pack(side="left", padx=3)

        self.available_deselect_all_btn = ctk.CTkButton(
            available_actions,
            text="Deselect All",
            width=100,
            command=lambda: self.toggle_all("available", False),
        )
        self.available_deselect_all_btn.pack(side="left", padx=3)

        managed_actions = ctk.CTkFrame(self, fg_color="transparent")
        managed_actions.grid(row=1, column=2, sticky="e", padx=(5, 10), pady=(0, 5))

        self.managed_select_all_btn = ctk.CTkButton(
            managed_actions,
            text="Select All",
            width=100,
            command=lambda: self.toggle_all("managed", True),
        )
        self.managed_select_all_btn.pack(side="left", padx=3)

        self.managed_deselect_all_btn = ctk.CTkButton(
            managed_actions,
            text="Deselect All",
            width=100,
            command=lambda: self.toggle_all("managed", False),
        )
        self.managed_deselect_all_btn.pack(side="left", padx=3)

        # ---------------- Lists row ----------------
        self.available_frame = ctk.CTkScrollableFrame(
            self, label_text="Unmanaged Applications"
        )
        self.available_frame.grid(
            row=4, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10)
        )

        self.managed_frame = ctk.CTkScrollableFrame(
            self, label_text="Managed Applications"
        )
        self.managed_frame.grid(
            row=4, column=2, sticky="nsew", padx=(5, 10), pady=(0, 10)
        )

        # Center actions
        center_actions = ctk.CTkFrame(self, fg_color="transparent")
        center_actions.grid(row=4, column=1, sticky="ns", padx=5, pady=10)

        self.add_selected_button = ctk.CTkButton(
            center_actions,
            text="Add Selected →",
            command=self.on_add_selected,
        )
        self.add_selected_button.pack(pady=20)

        self.remove_selected_button = ctk.CTkButton(
            center_actions,
            text="← Remove Selected",
            command=self.on_remove_selected,
        )
        self.remove_selected_button.pack(pady=20)

        # ---------------- Bottom status + details ----------------
        self.status_label = ctk.CTkLabel(self, text="", anchor="center")
        self.status_label.grid(
            row=6, column=0, columnspan=3, padx=10, pady=(5, 5), sticky="ew"
        )

        self.details_box = ctk.CTkTextbox(self, height=120)
        self.details_box.grid(
            row=7, column=0, columnspan=3, padx=10, pady=(0, 10), sticky="ew"
        )
        self.details_box.configure(state="disabled")

    # -------------------------------------------------------------------------
    # Dropdown rebuilding and counts
    # -------------------------------------------------------------------------

    def _rebuild_dropdown_values(self) -> None:
        """Rebuild dropdown values for type/source filters after backend load."""
        # Types
        unmanaged_types = ["All"] + sorted({
            app.get("type", "").title()
            for app in self.unmanaged_apps.values()
            if isinstance(app.get("type"), str) and app.get("type").strip()
        })
        managed_types = ["All"] + sorted({
            app.get("type", "").title()
            for app in self.managed_apps.values()
            if isinstance(app.get("type"), str) and app.get("type").strip()
        })

        unmanaged_type_counts = self._collect_unmanaged_type_counts()
        managed_type_counts = self._collect_managed_type_counts()

        self.available_type_dropdown.configure(
            values=self._build_labeled_values(unmanaged_types, unmanaged_type_counts)
        )
        self.managed_type_dropdown.configure(
            values=self._build_labeled_values(managed_types, managed_type_counts)
        )

        # Sources
        unmanaged_sources = ["All"] + sorted({
            ("Non-Steam" if app.get("source", "").lower() == "nonsteam"
             else app.get("source", "").title())
            for app in self.unmanaged_apps.values()
            if isinstance(app.get("source"), str) and app.get("source").strip()
        })
        managed_sources = ["All"] + sorted({
            ("Non-Steam" if app.get("source", "").lower() == "nonsteam"
             else app.get("source", "").title())
            for app in self.managed_apps.values()
            if isinstance(app.get("source"), str) and app.get("source").strip()
        })

        unmanaged_source_counts = self._collect_unmanaged_source_counts()
        managed_source_counts = self._collect_managed_source_counts()

        self.available_source_dropdown.configure(
            values=self._build_labeled_values(unmanaged_sources, unmanaged_source_counts)
        )
        self.managed_source_dropdown.configure(
            values=self._build_labeled_values(managed_sources, managed_source_counts)
        )

    def _collect_unmanaged_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for app in self.unmanaged_apps.values():
            t = app.get("type")
            if isinstance(t, str) and t.strip():
                normalized = t.title()
                counts[normalized] = counts.get(normalized, 0) + 1
        return counts

    def _collect_managed_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for app in self.managed_apps.values():
            t = app.get("type")
            if isinstance(t, str) and t.strip():
                normalized = t.title()
                counts[normalized] = counts.get(normalized, 0) + 1
        return counts

    def _collect_unmanaged_source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for app in self.unmanaged_apps.values():
            s = app.get("source")
            if isinstance(s, str) and s.strip():
                normalized = "Non-Steam" if s.lower() == "nonsteam" else s.title()
                counts[normalized] = counts.get(normalized, 0) + 1
        return counts

    def _collect_managed_source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for app in self.managed_apps.values():
            s = app.get("source")
            if isinstance(s, str) and s.strip():
                normalized = "Non-Steam" if s.lower() == "nonsteam" else s.title()
                counts[normalized] = counts.get(normalized, 0) + 1
        return counts

    def _build_labeled_values(self, items: list[str], counts: dict[str, int]) -> list[str]:
        labeled: list[str] = []
        for item in items:
            if item == "All":
                labeled.append("All")
            else:
                labeled.append(f"{item} ({counts.get(item, 0)})")
        return labeled

    # -------------------------------------------------------------------------
    # List population & filters
    # -------------------------------------------------------------------------

    def _clear_filter(self, which: str) -> None:
        if which == "available":
            self.available_filter_var.set("")
        else:
            self.managed_filter_var.set("")
        self.apply_filters()

    def _populate_lists(self) -> None:
        """Build all widgets once, then rely on apply_filters() to hide/show them."""
        for frame in (self.available_frame, self.managed_frame):
            for w in frame.winfo_children():
                w.destroy()

        self.available_items = []
        self.managed_items = []
        self.highlighted_item = None
        self.selected_app = None
        self._clear_details()

        # Unmanaged
        for app in sorted(self.unmanaged_apps.values(), key=lambda a: a.get("name", "").lower()):
            item = AppListItem(self.available_frame, app, self, self.environment)
            item.pack(fill="x", padx=5, pady=2)
            self.available_items.append(item)

        # Managed
        for app in sorted(self.managed_apps.values(), key=lambda a: a.get("name", "").lower()):
            item = AppListItem(self.managed_frame, app, self, self.environment)
            item.pack(fill="x", padx=5, pady=2)
            self.managed_items.append(item)

        self.apply_filters()

    def _strip_count(self, value: str) -> str:
        """Convert 'Steam (12)' → 'Steam'."""
        return value.split(" (", 1)[0] if "(" in value else value

    def apply_filters(self) -> None:
        """Fast filtering with stable alphabetical order and correct re-packing."""
        avail_text = self.available_filter_var.get().lower().strip()
        avail_source = self.available_source_var.get()
        avail_type = self.available_type_var.get()

        managed_text = self.managed_filter_var.get().lower().strip()
        managed_source = self.managed_source_var.get()
        managed_type = self.managed_type_var.get()

        # ---------------- Unmanaged ----------------
        visible_available: list[AppListItem] = []

        for item in self.available_items:
            app = item.app_data
            name = app.get("name", "").lower()
            source = app.get("source", "").lower()

            visible = True

            # Text
            if avail_text and avail_text not in name:
                visible = False

            # Type
            app_type = app.get("type", "")
            app_type = app_type.title() if isinstance(app_type, str) else ""
            if avail_type != "All" and app_type != avail_type:
                visible = False

            # Source
            if avail_source != "All":
                selected = self._strip_count(avail_source)
                normalized = "Non-Steam" if source == "nonsteam" else source.title()
                if normalized != selected:
                    visible = False

            if visible:
                visible_available.append(item)

        for item in self.available_items:
            item.pack_forget()
        for item in visible_available:
            item.configure(fg_color="transparent")
            item.pack(fill="x", padx=5, pady=2)

        if self.highlighted_item and self.highlighted_item not in visible_available:
            self.highlighted_item = None
            self.selected_app = None
            self._clear_details()

        # ---------------- Managed ----------------
        visible_managed: list[AppListItem] = []

        for item in self.managed_items:
            app = item.app_data
            name = app.get("name", "").lower()
            source = app.get("source", "").lower()

            visible = True

            # Text
            if managed_text and managed_text not in name:
                visible = False

            # Type
            app_type = app.get("type", "")
            app_type = app_type.title() if isinstance(app_type, str) else ""
            if managed_type != "All" and app_type != managed_type:
                visible = False

            # Source
            if managed_source != "All":
                selected = self._strip_count(managed_source)
                normalized = "Non-Steam" if source == "nonsteam" else source.title()
                if normalized != selected:
                    visible = False

            if visible:
                visible_managed.append(item)

        for item in self.managed_items:
            item.pack_forget()
        for item in visible_managed:
            item.configure(fg_color="transparent")
            item.pack(fill="x", padx=5, pady=2)

        if self.highlighted_item and self.highlighted_item not in visible_managed:
            self.highlighted_item = None
            self.selected_app = None
            self._clear_details()

        self.update_button_states()

    # -------------------------------------------------------------------------
    # Highlighting & selection
    # -------------------------------------------------------------------------

    def highlight_item(self, item: AppListItem) -> None:
        if self.highlighted_item and self.highlighted_item is not item:
            self.highlighted_item.configure(fg_color="transparent")

        item.configure(fg_color="#3A3A3A")
        self.highlighted_item = item

    # -------------------------------------------------------------------------
    # Button state
    # -------------------------------------------------------------------------

    def toggle_all(self, list_type: str, should_select: bool) -> None:
        items = self.available_items if list_type == "available" else self.managed_items
        for item in items:
            item.set_checked(should_select)
        self.update_button_states()

    def update_button_states(self) -> None:
        any_available = any(i.is_checked() for i in self.available_items)
        any_managed = any(i.is_checked() for i in self.managed_items)

        self.add_selected_button.configure(state="normal" if any_available else "disabled")
        self.remove_selected_button.configure(state="normal" if any_managed else "disabled")

    # -------------------------------------------------------------------------
    # Details panel
    # -------------------------------------------------------------------------

    def on_item_selected(self, app_data: dict) -> None:
        self.selected_app = app_data
        self._show_details(app_data)

    def _clear_details(self) -> None:
        self.details_box.configure(state="normal")
        self.details_box.delete("1.0", "end")
        self.details_box.configure(state="disabled")

    def _show_details(self, app: dict) -> None:
        self.details_box.configure(state="normal")
        self.details_box.delete("1.0", "end")

        source = app.get("source")
        if isinstance(source, str):
            source = "Non-Steam" if source.lower() == "nonsteam" else source.title()
        else:
            source = "Unknown"

        app_type = app.get("type")
        app_type = app_type.title() if isinstance(app_type, str) else "Unknown"

        lines = [
            f"Name: {app.get('name')}",
            f"Helios ID: {app.get('uuid')}",
            f"Source: {source}",
            f"Type: {app_type}",
            f"Launch Command: {app.get('cmd') or app.get('launch', '')}",
        ]
        self.details_box.insert("end", "\n".join(lines))
        self.details_box.configure(state="disabled")

    def _set_status(self, text: str, color: str = "white", duration_ms: int = 3000) -> None:
        self.status_label.configure(text=text, text_color=color)
        if duration_ms > 0:
            self.after(duration_ms, lambda: self.status_label.configure(text=""))

    # -------------------------------------------------------------------------
    # Add / Remove / Cover actions
    # -------------------------------------------------------------------------

    def on_add_selected(self):
        selected_items = [i for i in self.available_items if i.is_checked()]
        env_name = self.get_environment_display_name()
        if not selected_items:
            return

        # Show loading screen on main thread
        self._show_operation_loading(f"Adding {len(selected_items)} games to {env_name}…")

        def worker():
            try:
                # BACKGROUND WORK ONLY
                for item in selected_items:
                    helios._add_game(self.environment, self.all_libraries, item.app_data, verbose=False)

                # Schedule UI updates on main thread
                self.after(0, self._load_backend_data)
                self.after(0, self._populate_lists)
                self.after(0, lambda: self._set_status(
                    f"Added {len(selected_items)} game(s).", "green"
                ))

            except Exception as e:
                self.after(0, lambda: self._set_status(f"Add failed: {e}", "red", 5000))

            finally:
                # Hide loading screen AFTER UI settles
                self.after(100, self._hide_operation_loading)

        threading.Thread(target=worker, daemon=True).start()

    def on_remove_selected(self):
        selected_items = [i for i in self.managed_items if i.is_checked()]
        env_name = self.get_environment_display_name()
        if not selected_items:
            return

        # Show loading screen on main thread
        self._show_operation_loading(f"Removing {len(selected_items)} games from {env_name}…")

        def worker():
            try:
                # BACKGROUND WORK ONLY
                for item in selected_items:
                    helios._remove_game(self.environment, self.all_libraries, item.app_data, verbose=False)

                # Schedule UI updates on main thread
                self.after(0, self._load_backend_data)
                self.after(0, self._populate_lists)
                self.after(0, lambda: self._set_status(
                    f"Removed {len(selected_items)} game(s).", "green"
                ))

            except Exception as e:
                self.after(0, lambda: self._set_status(f"Remove failed: {e}", "red", 5000))

            finally:
                # Hide loading screen AFTER UI settles
                self.after(100, self._hide_operation_loading)

        threading.Thread(target=worker, daemon=True).start()

    def on_add_single(self, app_data):
        env_name = self.get_environment_display_name()

        # Show loading screen on main thread
        self._show_operation_loading(f"Adding {app_data.get('name')} to {env_name}…")

        def worker():
            try:
                # BACKGROUND WORK ONLY
                helios._add_game(self.environment, self.all_libraries, app_data, verbose=False)

                # Schedule UI updates on main thread
                self.after(0, self._load_backend_data)
                self.after(0, self._populate_lists)
                self.after(0, lambda: self._set_status(
                    f"Added {app_data.get('name')} to {env_name}.", "green"
                ))

            except Exception as e:
                self.after(0, lambda: self._set_status(f"Add failed: {e}", "red", 5000))

            finally:
                # Hide loading screen AFTER UI settles
                self.after(100, self._hide_operation_loading)

        threading.Thread(target=worker, daemon=True).start()

    def on_remove_single(self, app_data: dict) -> None:
        env_name = self.get_environment_display_name()

        # Show loading screen on main thread
        self._show_operation_loading(f"Removing {app_data.get('name')} from {env_name}…")

        def worker():
            try:
                # BACKGROUND WORK ONLY
                helios._remove_game(self.environment, self.all_libraries, app_data, verbose=False)

                # Schedule UI updates on main thread
                self.after(0, self._load_backend_data)
                self.after(0, self._populate_lists)
                self.after(0, lambda: self._set_status(
                    f"Removed {app_data.get('name')} from {env_name}.", "green"
                ))

            except Exception as e:
                self.after(0, lambda: self._set_status(f"Remove failed: {e}", "red", 5000))

            finally:
                # Hide loading screen AFTER UI settles
                self.after(100, self._hide_operation_loading)

        threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------------------
    # Environment
    # -------------------------------------------------------------------------

    def get_environment_display_name(self) -> str:
        try:
            installs = helios.find_environment_installs("all")
            for inst in installs:
                return inst.get("display_name") or inst.get("name") or "Environment"
        except Exception:
            return "Environment"
    