#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crimson Desert ASI Manager
Простой менеджер ASI-модов: установка из файлов/папок/архивов, включение/отключение,
просмотр файлов, заметки и открытие ini-конфигов.

Зависимости:
  - Python 3.10+
  - tkinter входит в обычную сборку Python для Windows
  - необязательно: pip install tkinterdnd2 для drag-and-drop
  - .7z поддерживается через встроенный py7zr в EXE-сборке или через найденный 7-Zip
  - .rar поддерживается через найденный 7-Zip без требования PATH
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, Y, BooleanVar, StringVar, Tk, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    DND_AVAILABLE = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False

APP_NAME = "Crimson Desert ASI Manager"
STATE_DIR_NAME = "ASIModManager"
STATE_FILE_NAME = "state.json"
ASIBAK_DIR_NAME = "asibak"
CONFLICT_BACKUP_DIR_NAME = "asimanager_conflict_backups"
DUPLICATE_BACKUP_DIR_NAME = "asiduplicates"
LOADER_ARCHIVE_NAME = "asi_loaders_archive.zip"
UASI_RELEASES_API = "https://api.github.com/repos/ThirteenAG/Ultimate-ASI-Loader/releases"
UASI_ACTIVE_DLL_NAMES_X64 = [
    "dinput8.dll",
    "dsound.dll",
    "version.dll",
    "winmm.dll",
    "winhttp.dll",
    "wininet.dll",
    "d3d9.dll",
    "d3d10.dll",
    "d3d11.dll",
    "d3d12.dll",
    "binkw64.dll",
    "bink2w64.dll",
    "xinput1_1.dll",
    "xinput1_2.dll",
    "xinput1_3.dll",
    "xinput1_4.dll",
    "xinput9_1_0.dll",
    "xinputuap.dll",
]
UASI_KNOWN_DLL_NAMES = set(UASI_ACTIVE_DLL_NAMES_X64) | {
    "d3d8.dll",
    "ddraw.dll",
    "dinput.dll",
    "msacm32.dll",
    "msvfw32.dll",
    "xlive.dll",
    "binkw32.dll",
    "bink2w32.dll",
    "vorbisfile.dll",
}
SUPPORTED_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
IGNORED_TOP_NAMES = {
    STATE_DIR_NAME.lower(),
    ASIBAK_DIR_NAME.lower(),
    CONFLICT_BACKUP_DIR_NAME.lower(),
    DUPLICATE_BACKUP_DIR_NAME.lower(),
}
APP_VERSION = "v9-delete-without-archives"
SAVE_BACKUP_DIR_NAME = "save_backups"
DELETED_MOD_BACKUP_DIR_NAME = "deleted_mods"
GAME_PROCESS_NAMES = [
    "CrimsonDesert.exe",
    "CrimsonDesert-Win64-Shipping.exe",
    "CrimsonDesertClient.exe",
    "CD.exe",
]


def setup_logging() -> logging.Logger:
    """Настраивает логирование для консольного запуска.

    В .pyw-запуске консоли обычно нет, поэтому там используется NullHandler.
    В обычном .py/.bat-запуске сообщения идут прямо в stdout.
    """
    logger = logging.getLogger("crimson_asi_manager")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    stdout = getattr(sys, "stdout", None)
    if stdout and hasattr(stdout, "write"):
        handler = logging.StreamHandler(stdout)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        logger.addHandler(logging.NullHandler())
    return logger


LOGGER = setup_logging()


@dataclass
class InstalledFile:
    rel: str
    added_at: str


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fmt_ts(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except FileNotFoundError:
        return "нет файла"


def normalize_rel(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def safe_name(name: str) -> str:
    name = re.sub(r"[^0-9A-Za-zА-Яа-я_. -]+", "_", name.strip())
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return name or "mod"


def slug(name: str) -> str:
    value = safe_name(name).lower()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^0-9a-zа-я_.-]+", "_", value)
    return value.strip("._-") or "mod"


def unique_mod_id(existing: dict, base_name: str) -> str:
    base = slug(base_name)
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}_{index}"
        index += 1
    return candidate


def app_settings_path() -> Path:
    if platform.system() == "Windows":
        root = os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / "CrimsonASIManager" / "settings.json"
    return Path.home() / ".config" / "CrimsonASIManager" / "settings.json"


def load_app_settings() -> dict:
    path = app_settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_app_settings(data: dict) -> None:
    path = app_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def update_app_settings(**values) -> None:  # noqa: ANN003
    data = load_app_settings()
    data.update(values)
    save_app_settings(data)


def runtime_dir() -> Path:
    """Папка, где лежит EXE при PyInstaller-сборке, или папка .py-файла при обычном запуске."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _candidate_7zip_paths() -> list[Path]:
    """Ищет 7-Zip там, где обычный пользователь реально его оставит.

    PATH всё ещё поддерживается, но больше не является обязательным ритуалом.
    """
    settings = load_app_settings()
    candidates: list[Path] = []

    saved = str(settings.get("seven_zip_path", "")).strip()
    if saved:
        candidates.append(Path(saved))

    env_path = os.environ.get("CRIMSON_ASI_MANAGER_7Z", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    for exe_name in ("7z", "7za", "7zz"):
        found = shutil.which(exe_name)
        if found:
            candidates.append(Path(found))

    base = runtime_dir()
    candidates.extend([
        base / "7z.exe",
        base / "7za.exe",
        base / "7zz.exe",
        base / "7-Zip" / "7z.exe",
        base / "7zip" / "7z.exe",
        base / "tools" / "7zip" / "7z.exe",
        base / "tools" / "7-Zip" / "7z.exe",
    ])

    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if not root:
            continue
        candidates.extend([
            Path(root) / "7-Zip" / "7z.exe",
            Path(root) / "Programs" / "7-Zip" / "7z.exe",
        ])

    # Убираем дубликаты без потери порядка.
    result: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def find_7zip_executable(parent: tk.Misc | None = None, ask_user: bool = False) -> str | None:
    """Возвращает путь к 7z.exe/7za.exe/7zz.exe.

    Сначала ищет автоматически: рядом с EXE, в tools\\7zip, в Program Files,
    в сохранённом пользовательском пути и только потом в PATH. Если ask_user=True,
    предлагает указать 7z.exe вручную и запоминает выбор.
    """
    for path in _candidate_7zip_paths():
        try:
            if path.exists() and path.is_file():
                LOGGER.info("Найден 7-Zip: %s", path)
                return str(path)
        except OSError:
            continue

    if not ask_user:
        LOGGER.warning("7-Zip не найден автоматически")
        return None

    selected = filedialog.askopenfilename(
        parent=parent,
        title="Укажите 7z.exe из папки 7-Zip",
        filetypes=[("7-Zip executable", "7z.exe 7za.exe 7zz.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
    )
    if not selected:
        LOGGER.warning("Пользователь не выбрал 7-Zip")
        return None

    path = Path(selected)
    if not path.exists() or not path.is_file():
        LOGGER.warning("Выбранный путь 7-Zip не существует: %s", path)
        return None

    settings = load_app_settings()
    settings["seven_zip_path"] = str(path)
    save_app_settings(settings)
    LOGGER.info("Сохранён путь к 7-Zip: %s", path)
    return str(path)


def detect_asi_target(selected: Path) -> Path:
    """Если пользователь выбрал корень игры, используем bin64. Если выбрал bin64, используем его."""
    selected = selected.resolve()
    if selected.name.lower() == "bin64":
        return selected
    bin64 = selected / "bin64"
    if bin64.exists() and bin64.is_dir():
        return bin64.resolve()
    return selected


def list_files_recursive(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file():
            rel_parts = [p.lower() for p in path.relative_to(root).parts]
            if rel_parts and rel_parts[0] in IGNORED_TOP_NAMES:
                continue
            files.append(path)
    return files


def single_top_folder_base(root: Path) -> Path:
    items = [p for p in root.iterdir() if p.name not in {"__MACOSX"}]
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return root


def open_with_default_app(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    system = platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def local_appdata_dir() -> Path:
    """Возвращает %LOCALAPPDATA% для Windows или домашнюю папку как безопасный fallback."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata)
    return Path.home() / "AppData" / "Local"


def locallow_appdata_dir() -> Path:
    local = local_appdata_dir()
    # Обычно LocalLow лежит рядом с Local: AppData\LocalLow.
    if local.name.lower() == "local":
        return local.parent / "LocalLow"
    return local / ".." / "LocalLow"


def existing_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            resolved = str(path.expanduser().resolve())
        except Exception:
            resolved = str(path)
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            result.append(path)
    return result


def crimson_save_candidates() -> list[Path]:
    """Известные варианты папки сохранений Crimson Desert на Windows.

    У игры встречаются разные написания папок Pearl Abyss/PearlAbyss и CD/CrimsonDesert,
    поэтому менеджер проверяет несколько вариантов, а не делает вид, что ПК пользователя
    обязан совпадать с чужим гайдом из интернета.
    """
    local = local_appdata_dir()
    return [
        local / "Pearl Abyss" / "CD" / "save",
        local / "PearlAbyss" / "CD" / "save",
        local / "PearlAbyss" / "CrimsonDesert" / "SaveGames",
        local / "Pearl Abyss" / "CrimsonDesert" / "SaveGames",
        local / "CrimsonDesert" / "SaveGames",
        local / "CrimsonDesert" / "Saved" / "SaveGames",
        local / "Pearl Abyss" / "CD",
        local / "PearlAbyss" / "CrimsonDesert",
    ]


def shader_cache_candidates() -> list[tuple[str, Path]]:
    local = local_appdata_dir()
    locallow = locallow_appdata_dir()
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return [
        ("NVIDIA DXCache", local / "NVIDIA" / "DXCache"),
        ("NVIDIA PerDriverCache DXCache", local / "NVIDIA" / "PerDriverCache" / "DXCache"),
        ("NVIDIA LocalLow DXCache", locallow / "NVIDIA" / "DXCache"),
        ("NVIDIA LocalLow PerDriverVersion DXCache", locallow / "NVIDIA" / "PerDriverVersion" / "DXCache"),
        ("NVIDIA ComputeCache", appdata / "NVIDIA" / "ComputeCache"),
        ("AMD DXCache", local / "AMD" / "DXCache"),
        ("AMD DxcCache", local / "AMD" / "DxcCache"),
        ("AMD GLCache", local / "AMD" / "GLCache"),
        ("AMD VkCache", local / "AMD" / "VkCache"),
        ("Intel ShaderCache", local / "Intel" / "ShaderCache"),
        ("Intel DXCache", local / "Intel" / "DXCache"),
        ("Windows D3DSCache", local / "D3DSCache"),
        ("Windows DirectX Shader Cache", local / "Microsoft" / "DirectX Shader Cache"),
        ("Temp DXCache", local / "Temp" / "DXCache"),
    ]


class State:
    def __init__(self, target_dir: Path):
        self.target_dir = target_dir.resolve()
        self.manager_dir = self.target_dir / STATE_DIR_NAME
        self.state_path = self.manager_dir / STATE_FILE_NAME
        self.asibak_dir = self.target_dir / ASIBAK_DIR_NAME
        self.data = self._load()

    def _default(self) -> dict:
        return {
            "version": 2,
            "target_dir": str(self.target_dir),
            "mods": {},
            "loader": {
                "selected_name": "dinput8.dll",
                "current_name": "",
                "source": "",
                "version": "",
                "updated_at": "",
                "archive": LOADER_ARCHIVE_NAME,
            },
        }

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                data.setdefault("mods", {})
                loader = data.setdefault("loader", {})
                loader.setdefault("selected_name", "dinput8.dll")
                loader.setdefault("current_name", "")
                loader.setdefault("source", "")
                loader.setdefault("version", "")
                loader.setdefault("updated_at", "")
                loader.setdefault("archive", LOADER_ARCHIVE_NAME)
                data["target_dir"] = str(self.target_dir)
                return data
            except Exception:
                broken = self.state_path.with_suffix(f".broken-{int(time.time())}.json")
                try:
                    self.state_path.rename(broken)
                except Exception:
                    pass
        return self._default()

    def save(self) -> None:
        self.manager_dir.mkdir(parents=True, exist_ok=True)
        self.data["target_dir"] = str(self.target_dir)
        self.state_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def mods(self) -> dict:
        return self.data.setdefault("mods", {})

    def disabled_path(self, mod_id: str, rel: str) -> Path:
        return self.asibak_dir / mod_id / f"{rel}.bak"

    def enabled_path(self, rel: str) -> Path:
        return self.target_dir / rel

    @property
    def loader_archive_path(self) -> Path:
        return self.manager_dir / LOADER_ARCHIVE_NAME

    @property
    def save_backup_dir(self) -> Path:
        return self.manager_dir / SAVE_BACKUP_DIR_NAME

    @property
    def deleted_mod_backup_dir(self) -> Path:
        return self.manager_dir / DELETED_MOD_BACKUP_DIR_NAME

    def actual_path(self, mod_id: str, rel: str) -> Path | None:
        enabled = self.enabled_path(rel)
        if enabled.exists():
            return enabled
        disabled = self.disabled_path(mod_id, rel)
        if disabled.exists():
            return disabled
        return None

    def mod_status(self, mod_id: str) -> str:
        mod = self.mods.get(mod_id, {})
        files = mod.get("files", [])
        if not files:
            return "пусто"
        enabled_count = 0
        disabled_count = 0
        missing_count = 0
        for item in files:
            rel = item["rel"]
            if self.enabled_path(rel).exists():
                enabled_count += 1
            elif self.disabled_path(mod_id, rel).exists():
                disabled_count += 1
            else:
                missing_count += 1
        if enabled_count and not disabled_count and not missing_count:
            return "включен"
        if disabled_count and not enabled_count and not missing_count:
            return "отключен"
        if missing_count == len(files):
            return "файлы потеряны"
        return "частично"

    def latest_mtime(self, mod_id: str) -> str:
        mod = self.mods.get(mod_id, {})
        latest = None
        for item in mod.get("files", []):
            path = self.actual_path(mod_id, item["rel"])
            if path and path.exists():
                mtime = path.stat().st_mtime
                latest = mtime if latest is None else max(latest, mtime)
        if latest is None:
            return "нет файла"
        return datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M")


class AsiManagerApp:
    def __init__(self):
        root_cls = TkinterDnD.Tk if DND_AVAILABLE else Tk
        self.root = root_cls()
        self.root.title(APP_NAME)
        self.root.geometry("1100x680")
        self.root.minsize(900, 560)

        self.state: State | None = None
        self.selected_mod_id: str | None = None
        self.target_var = StringVar(value="Папка ASI не выбрана")
        self.status_var = StringVar(value="Выберите папку игры или bin64.")
        self.show_disabled_var = BooleanVar(value=True)
        self.loader_name_var = StringVar(value="dinput8.dll")
        self.github_version_var = StringVar(value="")
        self.loader_status_var = StringVar(value="Загрузчик: папка не выбрана")
        settings = load_app_settings()
        self.backup_saves_var = BooleanVar(value=bool(settings.get("backup_saves_enabled", False)))
        self.backup_count_var = StringVar(value=str(settings.get("backup_saves_keep", 10)))
        self.github_releases: list[dict] = []
        self._last_loader_warning_key: tuple[str, ...] | None = None
        self._sort_reverse: dict[tuple[int, str], bool] = {}
        self._game_was_running = False
        self._monitor_after_id: str | None = None
        self._last_save_backup_at = 0.0

        LOGGER.info("Запуск %s %s. Drag-and-drop: %s", APP_NAME, APP_VERSION, "доступен" if DND_AVAILABLE else "недоступен")
        self._build_ui()
        self._load_last_target()
        self._register_dnd()
        self._schedule_game_monitor()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

    def close_app(self) -> None:
        """Корректно закрывает окно и завершает процесс без зависшей bat-консоли."""
        LOGGER.info("Закрытие программы")
        try:
            self.save_note(silent=True)
        except Exception:
            pass
        if self._monitor_after_id:
            try:
                self.root.after_cancel(self._monitor_after_id)
            except Exception:
                pass
            self._monitor_after_id = None
        try:
            self.root.destroy()
        finally:
            # Если запускали из .bat без pause, процесс завершится, а консоль закроется.
            # При запуске из уже открытого терминала сам терминал, естественно, не убиваем.
            sys.exit(0)

    def _build_ui(self) -> None:
        self._build_menu()

        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill=X)
        ttk.Label(top, text="Папка ASI:").pack(side=LEFT)
        ttk.Label(top, textvariable=self.target_var).pack(side=LEFT, padx=(6, 12))
        top_buttons = ttk.Frame(top)
        top_buttons.pack(side=RIGHT)
        ttk.Button(top_buttons, text="Выбрать папку игры/bin64", command=self.choose_target_dir).pack(side=LEFT)
        ttk.Button(top_buttons, text="Открыть папку", command=self.open_target_dir).pack(side=LEFT, padx=(6, 0))

        main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=BOTH, expand=True, padx=8, pady=4)

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=2)
        main.add(right, weight=3)

        toolbar = ttk.Frame(left)
        toolbar.pack(fill=X, pady=(0, 6))
        ttk.Button(toolbar, text="Добавить", command=self.add_via_dialog).pack(side=LEFT)
        ttk.Button(toolbar, text="Вкл/выкл", command=self.toggle_selected_mod).pack(side=LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="Сканировать", command=self.rescan).pack(side=LEFT, padx=(6, 0))
        ttk.Button(toolbar, text="Удалить", command=self.delete_selected_mod).pack(side=LEFT, padx=(6, 0))
        ttk.Checkbutton(toolbar, text="показывать отключенные", variable=self.show_disabled_var, command=self.refresh_mods).pack(side=RIGHT)

        self.mods_tree = ttk.Treeview(
            left,
            columns=("status", "files", "mtime", "source"),
            show="tree headings",
            selectmode="browse",
        )
        self.mods_tree.heading("#0", text="Мод", command=lambda: self.sort_tree(self.mods_tree, "#0"))
        self.mods_tree.heading("status", text="Статус", command=lambda: self.sort_tree(self.mods_tree, "status"))
        self.mods_tree.heading("files", text="Файлы", command=lambda: self.sort_tree(self.mods_tree, "files"))
        self.mods_tree.heading("mtime", text="Изменён", command=lambda: self.sort_tree(self.mods_tree, "mtime"))
        self.mods_tree.heading("source", text="Источник", command=lambda: self.sort_tree(self.mods_tree, "source"))
        self.mods_tree.column("#0", width=230, minwidth=160)
        self.mods_tree.column("status", width=95, anchor=W)
        self.mods_tree.column("files", width=55, anchor=W)
        self.mods_tree.column("mtime", width=130, anchor=W)
        self.mods_tree.column("source", width=130, anchor=W)
        self.mods_tree.pack(fill=BOTH, expand=True)
        self.mods_tree.bind("<<TreeviewSelect>>", self.on_mod_selected)
        self.mods_tree.bind("<Double-1>", lambda _e: self.toggle_selected_mod())

        self.loader_frame = ttk.LabelFrame(right, text="Ultimate ASI Loader")
        self.loader_frame.pack(fill=X, pady=(0, 8))
        loader_row1 = ttk.Frame(self.loader_frame)
        loader_row1.pack(fill=X, padx=6, pady=(6, 3))
        ttk.Label(loader_row1, text="DLL:").pack(side=LEFT)
        self.loader_combo = ttk.Combobox(
            loader_row1,
            textvariable=self.loader_name_var,
            values=UASI_ACTIVE_DLL_NAMES_X64,
            state="readonly",
            width=18,
        )
        self.loader_combo.pack(side=LEFT, padx=(6, 8))
        self.loader_combo.bind("<<ComboboxSelected>>", self.on_loader_choice_changed)
        ttk.Button(loader_row1, text="Применить", command=self.apply_selected_loader).pack(side=LEFT)
        ttk.Button(loader_row1, text="Добавить", command=self.add_loader_via_dialog).pack(side=LEFT, padx=(6, 0))
        ttk.Button(loader_row1, text="Чистка", command=self.archive_extra_loaders).pack(side=LEFT, padx=(6, 0))

        loader_row2 = ttk.Frame(self.loader_frame)
        loader_row2.pack(fill=X, padx=6, pady=(3, 6))
        ttk.Label(loader_row2, text="GitHub версия:").pack(side=LEFT)
        self.github_combo = ttk.Combobox(loader_row2, textvariable=self.github_version_var, values=[], state="readonly", width=18)
        self.github_combo.pack(side=LEFT, padx=(6, 8))
        ttk.Button(loader_row2, text="Обновить список", command=self.refresh_github_releases).pack(side=LEFT)
        ttk.Button(loader_row2, text="Скачать и поставить", command=self.download_selected_loader_from_github).pack(side=LEFT, padx=(6, 0))
        self.loader_status_label = ttk.Label(self.loader_frame, textvariable=self.loader_status_var, wraplength=580)
        self.loader_status_label.pack(fill=X, padx=6, pady=(0, 6))

        right_top = ttk.Frame(right)
        right_top.pack(fill=X, pady=(0, 6))
        ttk.Button(right_top, text="Открыть .ini", command=self.open_first_ini).pack(side=LEFT)
        ttk.Button(right_top, text="Сохранения", command=self.open_save_folder).pack(side=LEFT, padx=(6, 0))
        ttk.Button(right_top, text="Кэш шейдеров", command=self.open_shader_cache_folder).pack(side=LEFT, padx=(6, 0))

        backup_row = ttk.Frame(right)
        backup_row.pack(fill=X, pady=(0, 6))
        ttk.Checkbutton(
            backup_row,
            text="Бэкап сохранений после закрытия игры",
            variable=self.backup_saves_var,
            command=self.on_backup_settings_changed,
        ).pack(side=LEFT)
        ttk.Label(backup_row, text="хранить:").pack(side=LEFT, padx=(10, 4))
        self.backup_count_spin = ttk.Spinbox(backup_row, from_=1, to=999, width=5, textvariable=self.backup_count_var, command=self.on_backup_settings_changed)
        self.backup_count_spin.pack(side=LEFT)
        self.backup_count_spin.bind("<FocusOut>", lambda _e: self.on_backup_settings_changed())
        self.backup_count_spin.bind("<Return>", lambda _e: self.on_backup_settings_changed())

        self.files_tree = ttk.Treeview(right, columns=("file", "mtime", "place"), show="headings", selectmode="browse")
        self.files_tree.heading("file", text="Файл", command=lambda: self.sort_tree(self.files_tree, "file"))
        self.files_tree.heading("mtime", text="Изменён", command=lambda: self.sort_tree(self.files_tree, "mtime"))
        self.files_tree.heading("place", text="Где лежит", command=lambda: self.sort_tree(self.files_tree, "place"))
        self.files_tree.column("file", width=420, minwidth=250, anchor=W)
        self.files_tree.column("mtime", width=140, anchor=W)
        self.files_tree.column("place", width=120, anchor=W)
        self.files_tree.pack(fill=BOTH, expand=True)
        self.files_tree.bind("<Double-1>", lambda _e: self.open_selected_file())

        notes_frame = ttk.LabelFrame(right, text="Заметка")
        notes_frame.pack(fill=BOTH, expand=False, pady=(8, 0))
        notes_toolbar = ttk.Frame(notes_frame)
        notes_toolbar.pack(fill=X, padx=6, pady=(6, 0))
        ttk.Button(notes_toolbar, text="Сохранить заметку", command=self.save_note).pack(side=RIGHT)
        self.notes_text = tk.Text(notes_frame, height=7, wrap="word", undo=True)
        self.notes_text.pack(fill=BOTH, expand=True, padx=6, pady=6)

        bottom = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        bottom.pack(fill=X)
        ttk.Label(bottom, textvariable=self.status_var).pack(side=LEFT)

        if not DND_AVAILABLE:
            self.status_var.set("Drag-and-drop отключён: установите tkinterdnd2. Меню добавления работает.")

    def sort_tree(self, tree: ttk.Treeview, column: str) -> None:
        """Сортировка Treeview по нажатию на заголовок."""
        key = (id(tree), column)
        reverse = self._sort_reverse.get(key, False)
        items = list(tree.get_children(""))
        items.sort(key=lambda item: self._tree_sort_value(tree, item, column), reverse=reverse)
        for index, item in enumerate(items):
            tree.move(item, "", index)
        self._sort_reverse[key] = not reverse

    def _tree_sort_value(self, tree: ttk.Treeview, item: str, column: str):  # noqa: ANN001
        if column == "#0":
            value = tree.item(item, "text")
        else:
            columns = list(tree["columns"])
            values = list(tree.item(item, "values"))
            try:
                index = columns.index(column)
                value = values[index] if index < len(values) else ""
            except ValueError:
                value = ""
        return self._sortable_value(column, value)

    @staticmethod
    def _sortable_value(column: str, value: object):  # noqa: ANN001
        text = str(value)
        if text in {"нет файла", ""}:
            return (9, text.lower())
        if column == "files":
            try:
                return (0, int(text))
            except ValueError:
                pass
        try:
            return (1, datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp())
        except ValueError:
            pass
        parts = re.split(r"(\d+)", text.lower())
        natural = tuple(int(part) if part.isdigit() else part for part in parts)
        return (2, natural)

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Выбрать папку игры/bin64...", command=self.choose_target_dir)
        file_menu.add_separator()
        file_menu.add_command(label="Добавить моды...", command=self.add_via_dialog)
        file_menu.add_command(label="Сканировать папку", command=self.rescan)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.close_app)
        menu.add_cascade(label="Файл", menu=file_menu)

        mod_menu = tk.Menu(menu, tearoff=False)
        mod_menu.add_command(label="Включить/отключить", command=self.toggle_selected_mod)
        mod_menu.add_command(label="Удалить", command=self.delete_selected_mod)
        mod_menu.add_command(label="Открыть .ini", command=self.open_first_ini)
        mod_menu.add_command(label="Сохранить заметку", command=self.save_note)
        menu.add_cascade(label="Мод", menu=mod_menu)

        loader_menu = tk.Menu(menu, tearoff=False)
        loader_menu.add_command(label="Добавить загрузчик из файла/архива...", command=self.add_loader_via_dialog)
        loader_menu.add_command(label="Применить выбранный загрузчик", command=self.apply_selected_loader)
        loader_menu.add_command(label="Чистка лишних загрузчиков", command=self.archive_extra_loaders)
        loader_menu.add_separator()
        loader_menu.add_command(label="Обновить список версий GitHub", command=self.refresh_github_releases)
        loader_menu.add_command(label="Скачать выбранную версию с GitHub", command=self.download_selected_loader_from_github)
        menu.add_cascade(label="Загрузчик", menu=loader_menu)

        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label="Открыть папку сохранений", command=self.open_save_folder)
        tools_menu.add_command(label="Сделать бэкап сохранений сейчас", command=self.backup_crimson_saves_now)
        tools_menu.add_command(label="Открыть кэш шейдеров автоматически", command=self.open_shader_cache_folder)
        tools_menu.add_separator()
        tools_menu.add_command(label="Открыть NVIDIA DXCache", command=lambda: self.open_shader_cache_folder("nvidia"))
        tools_menu.add_command(label="Открыть AMD DXCache", command=lambda: self.open_shader_cache_folder("amd"))
        tools_menu.add_command(label="Открыть Intel ShaderCache", command=lambda: self.open_shader_cache_folder("intel"))
        tools_menu.add_command(label="Открыть Windows D3DSCache", command=lambda: self.open_shader_cache_folder("windows"))
        menu.add_cascade(label="Инструменты", menu=tools_menu)
        self.root.config(menu=menu)

    def _register_dnd(self) -> None:
        if not DND_AVAILABLE:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.on_drop)
            self.mods_tree.drop_target_register(DND_FILES)
            self.mods_tree.dnd_bind("<<Drop>>", self.on_drop)
            self.files_tree.drop_target_register(DND_FILES)
            self.files_tree.dnd_bind("<<Drop>>", self.on_drop)
            for widget in self._walk_widgets(self.loader_frame):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self.on_loader_drop)
                except Exception:
                    pass
        except Exception:
            pass

    def _walk_widgets(self, widget):  # noqa: ANN001
        yield widget
        for child in widget.winfo_children():
            yield from self._walk_widgets(child)

    def _backup_keep_count(self) -> int:
        try:
            value = int(str(self.backup_count_var.get()).strip())
        except ValueError:
            value = 10
        value = max(1, min(999, value))
        self.backup_count_var.set(str(value))
        return value

    def on_backup_settings_changed(self) -> None:
        keep = self._backup_keep_count()
        enabled = bool(self.backup_saves_var.get())
        update_app_settings(backup_saves_enabled=enabled, backup_saves_keep=keep)
        LOGGER.info("Настройки бэкапа сохранений: enabled=%s keep=%d", enabled, keep)
        self.status_var.set(
            f"Бэкап сохранений {'включён' if enabled else 'выключен'}. Хранить архивов: {keep}."
        )

    def _schedule_game_monitor(self) -> None:
        try:
            self._monitor_after_id = self.root.after(5000, self._monitor_game_for_save_backup)
        except Exception:
            self._monitor_after_id = None

    def _monitor_game_for_save_backup(self) -> None:
        try:
            if not self.backup_saves_var.get():
                self._game_was_running = False
                return

            running = self._is_crimson_desert_running()
            if running and not self._game_was_running:
                LOGGER.info("Обнаружен запуск Crimson Desert. Буду ждать закрытия для бэкапа сохранений.")
                self.status_var.set("Игра запущена. После закрытия будет создан бэкап сохранений.")
            if self._game_was_running and not running:
                LOGGER.info("Crimson Desert закрыт. Запускаю бэкап сохранений.")
                # Защита от двойного срабатывания, если tasklist на секунду вернул пустой результат.
                if time.time() - self._last_save_backup_at > 30:
                    self.backup_crimson_saves(reason="game_closed", show_messages=False)
                    self._last_save_backup_at = time.time()
            self._game_was_running = running
        except Exception:
            LOGGER.exception("Ошибка мониторинга процесса игры")
        finally:
            self._schedule_game_monitor()

    @staticmethod
    def _is_crimson_desert_running() -> bool:
        if platform.system() != "Windows":
            return False
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                timeout=5,
            )
        except Exception:
            return False
        output = result.stdout.lower()
        if any(name.lower() in output for name in GAME_PROCESS_NAMES):
            return True
        return "crimson" in output and "desert" in output

    @staticmethod
    def _find_save_folder_for_backup() -> Path | None:
        existing = existing_paths(crimson_save_candidates())
        if not existing:
            return None
        preferred = [p for p in existing if p.name.lower() in {"save", "saves", "savegames"}]
        if preferred:
            return preferred[0]
        return existing[0]

    def backup_crimson_saves_now(self) -> None:
        self.backup_crimson_saves(reason="manual", show_messages=True)

    def backup_crimson_saves(self, reason: str = "manual", show_messages: bool = True) -> Path | None:
        state = self.require_state()
        if not state:
            return None
        save_dir = self._find_save_folder_for_backup()
        if not save_dir or not save_dir.exists():
            message = "Папка сохранений Crimson Desert не найдена. Бэкап не создан."
            LOGGER.warning(message)
            if show_messages:
                messagebox.showwarning(APP_NAME, message)
            self.status_var.set(message)
            return None

        files = [p for p in save_dir.rglob("*") if p.is_file()]
        if not files:
            message = f"Папка сохранений пуста: {save_dir}. Бэкап не создан."
            LOGGER.warning(message)
            if show_messages:
                messagebox.showwarning(APP_NAME, message)
            self.status_var.set(message)
            return None

        state.save_backup_dir.mkdir(parents=True, exist_ok=True)
        archive_path = state.save_backup_dir / f"saves_{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        manifest = {
            "created_at": now_iso(),
            "reason": reason,
            "source": str(save_dir),
            "file_count": len(files),
        }
        LOGGER.info("Создание бэкапа сохранений: %s -> %s", save_dir, archive_path)
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file in files:
                    zf.write(file, normalize_rel(file.relative_to(save_dir)))
                zf.writestr("_crimson_asi_manager_save_backup.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        except Exception as exc:
            LOGGER.exception("Ошибка создания бэкапа сохранений")
            if archive_path.exists():
                try:
                    archive_path.unlink()
                except Exception:
                    pass
            if show_messages:
                messagebox.showerror(APP_NAME, f"Не удалось создать бэкап сохранений:\n{exc}")
            self.status_var.set("Не удалось создать бэкап сохранений.")
            return None

        self._prune_save_backups(state)
        message = f"Создан бэкап сохранений: {archive_path.name}"
        LOGGER.info(message)
        self.status_var.set(message)
        if show_messages:
            messagebox.showinfo(APP_NAME, message)
        return archive_path

    def _prune_save_backups(self, state: State) -> None:
        keep = self._backup_keep_count()
        archives = sorted(
            state.save_backup_dir.glob("saves_*.zip"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        for old in archives[keep:]:
            try:
                old.unlink()
                LOGGER.info("Удалён старый бэкап сохранений по лимиту: %s", old)
            except Exception:
                LOGGER.exception("Не удалось удалить старый бэкап сохранений: %s", old)

    def _load_last_target(self) -> None:
        settings = load_app_settings()
        target = settings.get("target_dir")
        if target and Path(target).exists():
            self.set_target_dir(Path(target))

    def require_state(self) -> State | None:
        if not self.state:
            messagebox.showwarning(APP_NAME, "Сначала выберите папку игры или папку bin64.")
            return None
        return self.state

    def set_target_dir(self, path: Path) -> None:
        target = detect_asi_target(path)
        target.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Выбрана рабочая папка ASI: %s", target)
        self.state = State(target)
        loader_state = self.state.data.setdefault("loader", {})
        selected_loader = str(loader_state.get("selected_name") or "dinput8.dll").lower()
        if selected_loader in UASI_KNOWN_DLL_NAMES:
            self.loader_name_var.set(selected_loader)
        self._cleanup_old_unpacked_duplicate_backups()
        self.target_var.set(str(target))
        update_app_settings(target_dir=str(target))
        self.rescan(silent=True)
        self.refresh_loader_status(show_warning=True)
        self.status_var.set(f"Рабочая папка: {target}")

    def choose_target_dir(self) -> None:
        selected = filedialog.askdirectory(title="Выберите папку игры Crimson Desert или bin64")
        if selected:
            self.set_target_dir(Path(selected))

    def add_via_dialog(self) -> None:
        state = self.require_state()
        if not state:
            return
        paths = filedialog.askopenfilenames(
            title="Выберите архивы или файлы модов",
            filetypes=(
                ("Моды и архивы", "*.asi *.ini *.dll *.json *.txt *.zip *.7z *.rar *.tar *.gz *.tgz"),
                ("Все файлы", "*.*"),
            ),
        )
        if paths:
            LOGGER.info("Добавление через диалог: %s", "; ".join(paths))
            self.install_paths([Path(p) for p in paths])

    def _paths_from_drop_event(self, event) -> list[Path]:  # noqa: ANN001
        raw = event.data
        try:
            parts = self.root.tk.splitlist(raw)
        except Exception:
            parts = raw.split()
        return [Path(p) for p in parts if p]

    def on_drop(self, event) -> None:  # noqa: ANN001
        state = self.require_state()
        if not state:
            return
        paths = self._paths_from_drop_event(event)
        LOGGER.info("Добавление drag-and-drop: %s", "; ".join(str(p) for p in paths))
        self.install_paths(paths)

    def on_loader_drop(self, event) -> None:  # noqa: ANN001
        state = self.require_state()
        if not state:
            return
        paths = self._paths_from_drop_event(event)
        LOGGER.info("Добавление загрузчика drag-and-drop: %s", "; ".join(str(p) for p in paths))
        self.install_loader_paths(paths)

    def install_paths(self, paths: list[Path]) -> None:
        state = self.require_state()
        if not state:
            return
        LOGGER.info("Начата установка: %d объект(ов)", len(paths))
        installed = 0
        errors: list[str] = []
        for path in paths:
            try:
                if not path.exists():
                    errors.append(f"Не найдено: {path}")
                    continue
                installed += self._install_one(path)
            except Exception as exc:
                LOGGER.exception("Ошибка установки %s", path)
                errors.append(f"{path}: {exc}")
        state.save()
        self.refresh_mods()
        msg = f"Установлено/обновлено модов: {installed}."
        if errors:
            msg += " Ошибки: " + "; ".join(errors[:4])
            messagebox.showwarning(APP_NAME, msg)
        LOGGER.info(msg)
        self.status_var.set(msg)

    def on_loader_choice_changed(self, _event=None) -> None:  # noqa: ANN001
        selected = self.loader_name_var.get().strip().lower()
        if selected not in UASI_KNOWN_DLL_NAMES:
            selected = "dinput8.dll"
            self.loader_name_var.set(selected)
        state = self.state
        if state:
            state.data.setdefault("loader", {})["selected_name"] = selected
            state.save()
        LOGGER.info("Выбран тип ASI-загрузчика: %s", selected)
        self.refresh_loader_status(show_warning=False)

    def add_loader_via_dialog(self) -> None:
        state = self.require_state()
        if not state:
            return
        paths = filedialog.askopenfilenames(
            title="Выберите DLL или архив Ultimate ASI Loader",
            filetypes=(
                ("Загрузчики и архивы", "*.dll *.zip *.7z *.rar *.tar *.gz *.tgz"),
                ("Все файлы", "*.*"),
            ),
        )
        if paths:
            self.install_loader_paths([Path(p) for p in paths])

    def add_loader_folder_via_dialog(self) -> None:
        state = self.require_state()
        if not state:
            return
        selected = filedialog.askdirectory(title="Выберите папку с DLL Ultimate ASI Loader")
        if selected:
            self.install_loader_paths([Path(selected)])

    def install_loader_paths(self, paths: list[Path]) -> None:
        state = self.require_state()
        if not state:
            return
        installed = 0
        errors: list[str] = []
        for path in paths:
            try:
                if not path.exists():
                    errors.append(f"Не найдено: {path}")
                    continue
                self._install_loader_one(path, f"local:{path}")
                installed += 1
            except Exception as exc:
                LOGGER.exception("Ошибка установки ASI-загрузчика %s", path)
                errors.append(f"{path}: {exc}")
        self.refresh_loader_status(show_warning=True)
        msg = f"Установлено загрузчиков: {installed}."
        if errors:
            msg += " Ошибки: " + "; ".join(errors[:4])
            messagebox.showwarning(APP_NAME, msg)
        LOGGER.info(msg)
        self.status_var.set(msg)

    def _install_loader_one(self, source: Path, source_label: str) -> None:
        LOGGER.info("Обработка ASI-загрузчика: %s", source)
        if source.is_dir():
            self._install_loader_from_folder(source, source_label)
            return
        if source.suffix.lower() == ".dll":
            self._install_loader_file(source, source_label=source_label, version="")
            return
        name = source.name.lower()
        if source.suffix.lower() in SUPPORTED_ARCHIVE_EXTS or name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
            self._install_loader_from_archive(source, source_label)
            return
        raise RuntimeError("Нужен .dll, архив или папка с DLL загрузчика.")

    def _install_loader_from_archive(self, archive: Path, source_label: str, version: str = "") -> None:
        with tempfile.TemporaryDirectory(prefix="crimson_asi_loader_") as tmp_str:
            tmp = Path(tmp_str)
            LOGGER.info("Распаковка архива загрузчика: %s", archive)
            self._unpack_archive(archive, tmp)
            self._install_loader_from_folder(tmp, source_label, version=version)

    def _install_loader_from_folder(self, folder: Path, source_label: str, version: str = "") -> None:
        candidates = [p for p in folder.rglob("*.dll") if p.is_file()]
        if not candidates:
            raise RuntimeError("В папке/архиве не найдено DLL-файлов загрузчика.")
        selected_name = self.loader_name_var.get().strip().lower()
        source_dll = self._choose_loader_dll(candidates, selected_name)
        self._install_loader_file(source_dll, source_label=source_label, version=version)

    def _choose_loader_dll(self, candidates: list[Path], selected_name: str) -> Path:
        selected_name = selected_name.lower()
        exact = [p for p in candidates if p.name.lower() == selected_name]
        if exact:
            return exact[0]
        known = [p for p in candidates if p.name.lower() in UASI_KNOWN_DLL_NAMES]
        if len(candidates) > 1:
            names = ", ".join(sorted(p.name for p in candidates)[:12])
            LOGGER.warning("В источнике несколько DLL загрузчика: %s. Будет использован первый подходящий.", names)
            messagebox.showwarning(
                APP_NAME,
                "В источнике несколько DLL. Менеджер возьмёт первую подходящую и переименует её в выбранный тип загрузчика.\n\n"
                f"Выбранный тип: {selected_name}\nНайдено: {names}",
            )
        if known:
            return known[0]
        return candidates[0]

    def _install_loader_file(self, source_dll: Path, source_label: str, version: str = "") -> None:
        state = self.require_state()
        if not state:
            return
        selected_name = self.loader_name_var.get().strip().lower() or "dinput8.dll"
        if selected_name not in UASI_KNOWN_DLL_NAMES:
            raise RuntimeError(f"Неподдерживаемое имя загрузчика: {selected_name}")
        target = state.target_dir / selected_name
        LOGGER.info("Установка ASI-загрузчика %s как %s", source_dll, target)

        same_file = False
        try:
            same_file = source_dll.resolve() == target.resolve() or source_dll.samefile(target)
        except Exception:
            same_file = False

        # Если источник лежит прямо в bin64 под другим именем, его может удалить архивация лишних.
        # Поэтому заранее делаем временную копию. Файлы, как выяснилось, не любят исчезать до копирования.
        temp_holder = None
        copy_source = source_dll
        if not same_file:
            temp_holder = tempfile.TemporaryDirectory(prefix="crimson_asi_loader_copy_")
            copy_source = Path(temp_holder.name) / source_dll.name
            shutil.copy2(source_dll, copy_source)

        try:
            self._archive_other_loader_candidates(keep_name=selected_name)
            if target.exists() and not same_file:
                self._archive_loader_file(target, reason="replace_selected_loader")
            if not same_file:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copy_source, target)
        finally:
            if temp_holder is not None:
                temp_holder.cleanup()
        loader_state = state.data.setdefault("loader", {})
        loader_state["selected_name"] = selected_name
        loader_state["current_name"] = selected_name
        loader_state["source"] = source_label
        loader_state["version"] = version
        loader_state["updated_at"] = now_iso()
        loader_state["archive"] = LOADER_ARCHIVE_NAME
        state.save()
        LOGGER.info("ASI-загрузчик установлен: %s", target)
        self.refresh_loader_status(show_warning=True)
        self.status_var.set(f"Установлен ASI-загрузчик: {selected_name}")

    def _detect_loader_candidates(self) -> list[Path]:
        state = self.state
        if not state:
            return []
        result: list[Path] = []
        for path in state.target_dir.iterdir():
            if not path.is_file():
                continue
            if path.name.lower() in UASI_KNOWN_DLL_NAMES:
                result.append(path)
        return sorted(result, key=lambda p: p.name.lower())

    def refresh_loader_status(self, show_warning: bool = False) -> None:
        state = self.state
        if not state:
            self.loader_status_var.set("Загрузчик: папка не выбрана")
            return
        selected = self.loader_name_var.get().strip().lower()
        candidates = self._detect_loader_candidates()
        names = [p.name for p in candidates]
        active = [p for p in candidates if p.name.lower() == selected]
        extras = [p for p in candidates if p.name.lower() != selected]
        archive_note = ""
        if state.loader_archive_path.exists():
            try:
                archive_note = f" | архив: {state.loader_archive_path.name} ({state.loader_archive_path.stat().st_size // 1024} КБ)"
            except OSError:
                archive_note = f" | архив: {state.loader_archive_path.name}"
        if not candidates:
            text = f"Текущий загрузчик: не найден. Выбран для установки: {selected}{archive_note}"
        elif active and not extras:
            text = f"Текущий загрузчик: {selected}{archive_note}"
        elif active and extras:
            text = f"Текущий: {selected}. Лишние в bin64: {', '.join(p.name for p in extras)}{archive_note}"
        else:
            text = f"Выбранный {selected} не найден. В bin64 есть: {', '.join(names)}{archive_note}"
        self.loader_status_var.set(text)
        try:
            self.loader_status_label.configure(foreground=("red" if len(candidates) > 1 or (candidates and not active) else ""))
        except Exception:
            pass

        key = tuple(p.name.lower() for p in candidates)
        if show_warning and len(candidates) > 1 and key != self._last_loader_warning_key:
            self._last_loader_warning_key = key
            messagebox.showwarning(
                APP_NAME,
                "В bin64 найдено несколько DLL с именами, которые использует Ultimate ASI Loader.\n\n"
                f"Выбранный: {selected}\nНайдено: {', '.join(names)}\n\n"
                "Оставь один активный загрузчик. Кнопка 'Чистка' уберёт остальные в ZIP-архив менеджера.",
            )

    def _archive_loader_file(self, path: Path, reason: str) -> None:
        state = self.require_state()
        if not state:
            return
        if not path.exists() or not path.is_file():
            return
        archive_path = state.loader_archive_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:8]
        safe_reason = slug(reason)
        arc_dir = f"{stamp}_{safe_reason}_{unique}"
        arc_name = f"{arc_dir}/{path.name}"
        info_name = f"{arc_dir}/_loader_info.json"
        info = {
            "file": path.name,
            "archived_at": now_iso(),
            "reason": reason,
            "original_path": str(path),
            "size": path.stat().st_size,
        }
        LOGGER.info("Архивация загрузчика: %s -> %s:%s", path, archive_path, arc_name)
        with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(path, arc_name)
            zf.writestr(info_name, json.dumps(info, ensure_ascii=False, indent=2))
        path.unlink()
        LOGGER.info("Загрузчик заархивирован и удалён из bin64: %s", path)

    def _archive_other_loader_candidates(self, keep_name: str) -> int:
        keep_name = keep_name.lower()
        count = 0
        for path in self._detect_loader_candidates():
            if path.name.lower() == keep_name:
                continue
            self._archive_loader_file(path, reason=f"extra_loader_keep_{keep_name}")
            count += 1
        return count

    def archive_extra_loaders(self) -> None:
        state = self.require_state()
        if not state:
            return
        selected = self.loader_name_var.get().strip().lower()
        count = self._archive_other_loader_candidates(keep_name=selected)
        state.save()
        self.refresh_loader_status(show_warning=False)
        self.status_var.set(f"Чистка завершена. Заархивировано лишних загрузчиков: {count}.")
        LOGGER.info("Заархивировано лишних загрузчиков: %d", count)

    def apply_selected_loader(self) -> None:
        state = self.require_state()
        if not state:
            return
        selected = self.loader_name_var.get().strip().lower()
        active = state.target_dir / selected
        if active.exists():
            archived = self._archive_other_loader_candidates(keep_name=selected)
            state.data.setdefault("loader", {})["selected_name"] = selected
            state.data.setdefault("loader", {})["current_name"] = selected
            state.data.setdefault("loader", {})["updated_at"] = now_iso()
            state.save()
            self.refresh_loader_status(show_warning=False)
            self.status_var.set(f"Активен загрузчик: {selected}. Заархивировано лишних: {archived}.")
            return
        restored = self._restore_loader_from_archive(selected)
        if restored:
            self.status_var.set(f"Восстановлен загрузчик из архива: {selected}")
            return
        messagebox.showinfo(
            APP_NAME,
            f"В bin64 и архиве менеджера нет {selected}. Добавь локальный DLL/архив или скачай версию с GitHub.",
        )

    def _restore_loader_from_archive(self, selected_name: str) -> bool:
        state = self.require_state()
        if not state:
            return False
        archive_path = state.loader_archive_path
        if not archive_path.exists():
            return False
        selected_name = selected_name.lower()
        with tempfile.TemporaryDirectory(prefix="crimson_asi_loader_restore_") as tmp_str:
            tmp = Path(tmp_str)
            with zipfile.ZipFile(archive_path, "r") as zf:
                match = None
                for info in reversed(zf.infolist()):
                    if info.is_dir():
                        continue
                    if Path(info.filename).name.lower() == selected_name:
                        match = info
                        break
                if not match:
                    return False
                extracted = Path(zf.extract(match, tmp))
            self._install_loader_file(extracted, source_label=f"archive:{archive_path.name}", version="")
        return True

    def refresh_github_releases(self) -> None:
        self.status_var.set("Запрос списка версий Ultimate ASI Loader с GitHub...")
        self.root.update_idletasks()
        LOGGER.info("Запрос релизов GitHub: %s", UASI_RELEASES_API)
        try:
            req = urllib.request.Request(UASI_RELEASES_API, headers={"User-Agent": "CrimsonASIManager"})
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = response.read().decode("utf-8")
            releases = json.loads(payload)
        except Exception as exc:
            LOGGER.exception("Не удалось получить список релизов GitHub")
            messagebox.showerror(APP_NAME, f"Не удалось получить список релизов GitHub:\n{exc}")
            self.status_var.set("Не удалось получить список релизов GitHub.")
            return
        parsed: list[dict] = []
        for release in releases:
            tag = str(release.get("tag_name") or release.get("name") or "").strip()
            if not tag:
                continue
            assets = []
            for asset in release.get("assets", []) or []:
                name = str(asset.get("name") or "")
                url = str(asset.get("browser_download_url") or "")
                if name.lower().endswith(".zip") and url:
                    assets.append({"name": name, "url": url})
            if assets:
                parsed.append({"tag": tag, "name": release.get("name") or tag, "assets": assets})
        self.github_releases = parsed
        tags = [item["tag"] for item in parsed]
        self.github_combo.configure(values=tags)
        if tags and not self.github_version_var.get():
            self.github_version_var.set(tags[0])
        self.status_var.set(f"Найдено версий GitHub: {len(tags)}.")
        LOGGER.info("Найдено релизов GitHub с ZIP-ассетами: %d", len(tags))

    def _selected_github_release(self) -> dict | None:
        tag = self.github_version_var.get().strip()
        for release in self.github_releases:
            if release.get("tag") == tag:
                return release
        return self.github_releases[0] if self.github_releases else None

    @staticmethod
    def _preferred_x64_asset(release: dict) -> dict | None:
        assets = release.get("assets", []) or []
        if not assets:
            return None
        for asset in assets:
            if str(asset.get("name", "")).lower() == "ultimate-asi-loader_x64.zip":
                return asset
        for asset in assets:
            if "x64" in str(asset.get("name", "")).lower():
                return asset
        return assets[0]

    def download_selected_loader_from_github(self) -> None:
        state = self.require_state()
        if not state:
            return
        if not self.github_releases:
            self.refresh_github_releases()
        release = self._selected_github_release()
        if not release:
            messagebox.showerror(APP_NAME, "Список релизов GitHub пуст.")
            return
        asset = self._preferred_x64_asset(release)
        if not asset:
            messagebox.showerror(APP_NAME, "У выбранного релиза нет ZIP-ассета.")
            return
        tag = str(release.get("tag", ""))
        asset_name = str(asset.get("name", "Ultimate-ASI-Loader_x64.zip"))
        url = str(asset.get("url", ""))
        self.status_var.set(f"Скачивание {asset_name} {tag}...")
        self.root.update_idletasks()
        LOGGER.info("Скачивание ASI-загрузчика с GitHub: %s %s", tag, url)
        try:
            with tempfile.TemporaryDirectory(prefix="crimson_asi_loader_dl_") as tmp_str:
                tmp = Path(tmp_str)
                archive = tmp / asset_name
                req = urllib.request.Request(url, headers={"User-Agent": "CrimsonASIManager"})
                with urllib.request.urlopen(req, timeout=60) as response:
                    archive.write_bytes(response.read())
                self._install_loader_from_archive(archive, source_label=f"github:{tag}:{asset_name}", version=tag)
        except Exception as exc:
            LOGGER.exception("Не удалось скачать/установить ASI-загрузчик с GitHub")
            messagebox.showerror(APP_NAME, f"Не удалось скачать/установить загрузчик:\n{exc}")
            self.status_var.set("Не удалось скачать/установить ASI-загрузчик.")
            return
        self.status_var.set(f"Скачан и установлен ASI-загрузчик {self.loader_name_var.get()} из {tag}.")

    def _install_one(self, source: Path) -> int:
        LOGGER.info("Обработка источника: %s", source)
        if source.is_dir():
            base = source
            return self._install_from_folder(base, source.name, f"folder:{source}")
        ext = source.suffix.lower()
        if ext in SUPPORTED_ARCHIVE_EXTS or source.name.lower().endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
            return self._install_from_archive(source)
        return self._install_files_as_group([source], source.stem, f"file:{source}")

    def _install_from_archive(self, archive: Path) -> int:
        with tempfile.TemporaryDirectory(prefix="crimson_asi_mod_") as tmp_str:
            tmp = Path(tmp_str)
            LOGGER.info("Распаковка архива: %s", archive)
            self._unpack_archive(archive, tmp)
            base = single_top_folder_base(tmp)
            return self._install_from_folder(base, archive.stem, f"archive:{archive.name}")

    def _unpack_archive(self, archive: Path, destination: Path) -> None:
        name = archive.name.lower()
        if zipfile.is_zipfile(archive):
            LOGGER.info("Архив распознан как ZIP")
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(destination)
            return
        if tarfile.is_tarfile(archive):
            LOGGER.info("Архив распознан как TAR")
            with tarfile.open(archive) as tf:
                tf.extractall(destination)
            return
        if name.endswith(".7z"):
            seven_zip = find_7zip_executable(parent=self.root, ask_user=False)
            if seven_zip:
                LOGGER.info("Архив распознан как 7z, используется найденный 7-Zip: %s", seven_zip)
                result = subprocess.run(
                    [seven_zip, "x", "-y", f"-o{destination}", str(archive)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "7-Zip не распаковал архив")
                return

            try:
                import py7zr  # type: ignore
            except Exception:
                py7zr = None

            if py7zr is not None:
                LOGGER.info("Архив распознан как 7z, используется встроенная библиотека py7zr")
                with py7zr.SevenZipFile(archive, mode="r") as zf:
                    zf.extractall(path=destination)
                return

            seven_zip = find_7zip_executable(parent=self.root, ask_user=True)
            if not seven_zip:
                raise RuntimeError(
                    "Не найден 7-Zip для .7z. Установите 7-Zip в обычное место "
                    "C:\\Program Files\\7-Zip или положите 7z.exe рядом с CrimsonASIManager.exe "
                    "либо в tools\\7zip. PATH больше не обязателен."
                )
            result = subprocess.run(
                [seven_zip, "x", "-y", f"-o{destination}", str(archive)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "7-Zip не распаковал архив")
            return

        if name.endswith(".rar"):
            seven_zip = find_7zip_executable(parent=self.root, ask_user=True)
            LOGGER.info("Архив распознан как RAR, используется внешний 7-Zip: %s", seven_zip)
            if not seven_zip:
                raise RuntimeError(
                    "Для .rar нужен 7-Zip, но PATH не нужен. Установите 7-Zip в обычное место "
                    "C:\\Program Files\\7-Zip или положите 7z.exe рядом с CrimsonASIManager.exe "
                    "либо в tools\\7zip."
                )
            result = subprocess.run(
                [seven_zip, "x", "-y", f"-o{destination}", str(archive)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "7-Zip не распаковал RAR-архив")
            return
        try:
            shutil.unpack_archive(str(archive), str(destination))
        except Exception as exc:
            raise RuntimeError(f"Архив не поддержан: {archive.suffix}") from exc

    def _install_from_folder(self, folder: Path, mod_name: str, source_label: str) -> int:
        LOGGER.info("Установка из папки: %s", folder)
        files = list_files_recursive(folder)
        if not files:
            raise RuntimeError("в папке/архиве нет файлов")
        asi_files = [p for p in files if p.suffix.lower() == ".asi"]
        if len(asi_files) == 1:
            mod_name = asi_files[0].stem
        return self._copy_files_to_target(folder, files, mod_name, source_label)

    def _install_files_as_group(self, files: list[Path], mod_name: str, source_label: str) -> int:
        with tempfile.TemporaryDirectory(prefix="crimson_asi_group_") as tmp_str:
            tmp = Path(tmp_str)
            staged: list[Path] = []
            for file in files:
                dst = tmp / file.name
                shutil.copy2(file, dst)
                staged.append(dst)
            asi_files = [p for p in staged if p.suffix.lower() == ".asi"]
            if len(asi_files) == 1:
                mod_name = asi_files[0].stem
            return self._copy_files_to_target(tmp, staged, mod_name, source_label)

    def _duplicate_ini_choice(self, mod_id: str, base: Path, files: list[Path]) -> str:
        """Возвращает replace/keep/cancel для INI при замене уже установленного мода."""
        state = self.require_state()
        if not state:
            return "replace"
        mod = state.mods.get(mod_id, {})
        old_ini_rels = []
        for item in mod.get("files", []):
            rel = normalize_rel(item.get("rel", ""))
            if not rel.lower().endswith(".ini"):
                continue
            if state.enabled_path(rel).exists() or state.disabled_path(mod_id, rel).exists():
                old_ini_rels.append(rel)

        new_ini_rels = []
        for file in files:
            try:
                rel = normalize_rel(file.relative_to(base))
            except ValueError:
                continue
            if rel.lower().endswith(".ini"):
                new_ini_rels.append(rel)

        if not old_ini_rels or not new_ini_rels:
            return "replace"

        def compact(items: list[str]) -> str:
            shown = items[:6]
            text = "\n".join(f"  • {item}" for item in shown)
            if len(items) > len(shown):
                text += f"\n  • ... ещё {len(items) - len(shown)}"
            return text

        message = (
            f"Мод «{mod.get('name', mod_id)}» уже установлен, и у старой и новой версии есть .ini-файлы.\n\n"
            "Да — заменить старый .ini новым из добавляемого мода.\n"
            "Нет — оставить старый .ini; новые .ini из добавляемого мода не копировать.\n"
            "Отмена — отменить добавление мода.\n\n"
            f"Старые .ini:\n{compact(old_ini_rels)}\n\n"
            f"Новые .ini:\n{compact(new_ini_rels)}"
        )
        choice = messagebox.askyesnocancel("INI при замене мода", message, parent=self.root)
        if choice is None:
            LOGGER.info("Добавление дубликата отменено пользователем на выборе INI")
            return "cancel"
        if choice is False:
            LOGGER.info("Пользователь выбрал оставить старые INI для мода %s", mod_id)
            return "keep"
        LOGGER.info("Пользователь выбрал заменить INI для мода %s", mod_id)
        return "replace"

    def _existing_ini_rels(self, mod_id: str) -> set[str]:
        state = self.require_state()
        if not state:
            return set()
        mod = state.mods.get(mod_id, {})
        result: set[str] = set()
        for item in mod.get("files", []):
            rel = normalize_rel(item.get("rel", ""))
            if not rel.lower().endswith(".ini"):
                continue
            if state.enabled_path(rel).exists() or state.disabled_path(mod_id, rel).exists():
                result.add(rel)
        return result

    def _copy_files_to_target(self, base: Path, files: list[Path], mod_name: str, source_label: str) -> int:
        state = self.require_state()
        if not state:
            return 0
        mod_name = safe_name(mod_name)
        LOGGER.info("Подготовка установки мода: %s, файлов: %d, источник: %s", mod_name, len(files), source_label)
        incoming_has_asi = any(file.suffix.lower() == ".asi" for file in files)
        mod_id = self._find_existing_mod_for_source_or_name(source_label, mod_name)
        duplicate_backup: Path | None = None
        keep_old_ini_rels: set[str] = set()
        skip_new_ini = False

        if not mod_id:
            mod_id = unique_mod_id(state.mods, mod_name)
            LOGGER.info("Создаётся новая запись мода: %s (%s)", mod_name, mod_id)
            state.mods[mod_id] = {
                "name": mod_name,
                "enabled": True,
                "files": [],
                "source": source_label,
                "installed_at": now_iso(),
                "notes": "",
            }
        elif incoming_has_asi:
            ini_choice = self._duplicate_ini_choice(mod_id, base, files)
            if ini_choice == "cancel":
                self.status_var.set("Добавление отменено: выбор INI отменён.")
                return 0
            if ini_choice == "keep":
                keep_old_ini_rels = self._existing_ini_rels(mod_id)
                skip_new_ini = bool(keep_old_ini_rels)

            LOGGER.info(
                "Найден дубликат мода: %s (%s). Старая версия будет заархивирована; keep_old_ini=%s",
                mod_name,
                mod_id,
                sorted(keep_old_ini_rels),
            )
            duplicate_backup = self._archive_and_move_previous_mod_files(mod_id, keep_rels=keep_old_ini_rels)
            kept_items = [
                {"rel": rel, "added_at": now_iso(), "kept_from_previous_ini": True}
                for rel in sorted(keep_old_ini_rels)
                if state.enabled_path(rel).exists()
            ]
            state.mods[mod_id]["files"] = kept_items
        mod = state.mods[mod_id]
        existing_rels = {item["rel"] for item in mod.get("files", [])}
        copied_rels: list[str] = []
        skipped_ini_rels: list[str] = []

        for file in files:
            rel = normalize_rel(file.relative_to(base))
            if not rel or rel.startswith("../"):
                continue
            if skip_new_ini and rel.lower().endswith(".ini"):
                skipped_ini_rels.append(rel)
                LOGGER.info("Новый INI пропущен, сохранён старый INI: %s", rel)
                continue
            target = state.enabled_path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._backup_if_conflict(target, mod_id, rel)
            shutil.copy2(file, target)
            LOGGER.info("Скопирован файл: %s -> %s", file, target)
            copied_rels.append(rel)
            if rel not in existing_rels:
                mod.setdefault("files", []).append({"rel": rel, "added_at": now_iso()})
                existing_rels.add(rel)

        mod["enabled"] = True
        mod["updated_at"] = now_iso()
        mod["source"] = source_label
        if skipped_ini_rels:
            mod["ini_policy"] = {
                "last_duplicate_choice": "keep_old",
                "kept_old_ini": sorted(keep_old_ini_rels),
                "skipped_new_ini": skipped_ini_rels,
                "updated_at": now_iso(),
            }
        elif incoming_has_asi:
            mod["ini_policy"] = {"last_duplicate_choice": "replace_or_no_ini_conflict", "updated_at": now_iso()}
        if duplicate_backup:
            mod.setdefault("duplicate_backups", []).append(str(duplicate_backup))
        self._sort_mod_files(mod)
        return 1 if copied_rels or keep_old_ini_rels else 0

    def _archive_and_move_previous_mod_files(self, mod_id: str, keep_rels: set[str] | None = None) -> Path | None:
        """Перед заменой дубликатом архивирует старую версию мода и удаляет старые файлы.

        Важно: распакованные .asi/.ini/.dll не оставляем внутри bin64/asiduplicates,
        потому что некоторые ASI-загрузчики и игры могут просматривать подпапки.
        """
        state = self.require_state()
        if not state:
            return None
        keep_rels = {normalize_rel(rel) for rel in (keep_rels or set())}
        mod = state.mods.get(mod_id, {})
        files = list(mod.get("files", []))
        if not files:
            return None

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        mod_name = safe_name(str(mod.get("name", mod_id)))
        duplicate_dir = state.manager_dir / DUPLICATE_BACKUP_DIR_NAME / f"{timestamp}_{mod_name}"
        duplicate_dir.mkdir(parents=True, exist_ok=True)
        archive_path = duplicate_dir / "previous_files.zip"

        manifest = {
            "mod_id": mod_id,
            "mod_name": mod.get("name", mod_id),
            "created_at": now_iso(),
            "reason": "duplicate_before_replace",
            "files": [],
        }

        LOGGER.info("Архивация старой версии мода %s в %s", mod.get("name", mod_id), archive_path)
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in files:
                rel = normalize_rel(item.get("rel", ""))
                if not rel:
                    continue
                enabled = state.enabled_path(rel)
                disabled = state.disabled_path(mod_id, rel)
                if enabled.exists():
                    src = enabled
                    logical_rel = f"bin64/{rel}"
                elif disabled.exists():
                    src = disabled
                    logical_rel = f"{ASIBAK_DIR_NAME}/{mod_id}/{rel}.bak"
                else:
                    LOGGER.warning("Файл старой версии не найден и не будет архивирован: %s", rel)
                    manifest["files"].append({"rel": rel, "status": "missing"})
                    continue

                zf.write(src, logical_rel)
                file_manifest = {"rel": rel, "archived_as": logical_rel}
                if rel in keep_rels:
                    target = state.enabled_path(rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if src == enabled:
                        file_manifest["kept"] = True
                        file_manifest["kept_as"] = f"bin64/{rel}"
                        LOGGER.info("Архивирован и оставлен старый INI: %s", src)
                    else:
                        shutil.move(str(src), str(target))
                        file_manifest["kept"] = True
                        file_manifest["kept_as"] = f"bin64/{rel}"
                        LOGGER.info("Архивирован и восстановлен старый INI из asibak: %s -> %s", src, target)
                else:
                    src.unlink()
                    file_manifest["deleted_after_archive"] = True
                    LOGGER.info("Архивирован и удалён старый файл: %s", src)
                manifest["files"].append(file_manifest)

            zf.writestr("_asi_manager_duplicate_info.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        self._remove_empty_dirs(state.asibak_dir / mod_id)
        LOGGER.info("Архив старой версии готов: %s", archive_path)
        return archive_path

    def _cleanup_old_unpacked_duplicate_backups(self) -> None:
        """Удаляет распакованные дубликаты, оставленные старыми версиями менеджера.

        Удаляется только папка asiduplicates/*/files, если рядом есть previous_files.zip.
        Сам zip-архив не трогаем.
        """
        state = self.state
        if not state:
            return
        duplicates_root = state.target_dir / DUPLICATE_BACKUP_DIR_NAME
        if not duplicates_root.exists():
            return
        for duplicate_dir in duplicates_root.iterdir():
            if not duplicate_dir.is_dir():
                continue
            archive_path = duplicate_dir / "previous_files.zip"
            unpacked = duplicate_dir / "files"
            if archive_path.exists() and unpacked.exists() and unpacked.is_dir():
                LOGGER.info("Удалена старая распакованная папка дубликата: %s", unpacked)
                shutil.rmtree(unpacked, ignore_errors=True)

    @staticmethod
    def _remove_empty_dirs(root: Path) -> None:
        if not root.exists() or not root.is_dir():
            return
        for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            root.rmdir()
        except OSError:
            pass

    def _backup_if_conflict(self, target: Path, mod_id: str, rel: str) -> None:
        state = self.require_state()
        if not state or not target.exists():
            return
        owner = self._owner_of_rel(rel)
        if owner == mod_id:
            return
        backup_root = state.manager_dir / CONFLICT_BACKUP_DIR_NAME / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        LOGGER.warning("Конфликт файла %s: резервная копия создана в %s", rel, backup)

    def _owner_of_rel(self, rel: str) -> str | None:
        state = self.require_state()
        if not state:
            return None
        for mid, mod in state.mods.items():
            for item in mod.get("files", []):
                if item.get("rel") == rel:
                    return mid
        return None

    def _find_existing_mod_for_source_or_name(self, source_label: str, mod_name: str) -> str | None:
        state = self.require_state()
        if not state:
            return None
        for mid, mod in state.mods.items():
            if mod.get("source") == source_label:
                return mid
            if str(mod.get("name", "")).lower() == mod_name.lower():
                return mid
        return None

    def _sort_mod_files(self, mod: dict) -> None:
        mod["files"] = sorted(mod.get("files", []), key=lambda item: item.get("rel", "").lower())

    def rescan(self, silent: bool = False) -> None:
        state = self.require_state()
        if not state:
            return
        found = 0
        for asi in sorted(state.target_dir.glob("*.asi")):
            if asi.name.lower().endswith(".bak"):
                continue
            rel = normalize_rel(asi.relative_to(state.target_dir))
            if self._owner_of_rel(rel):
                continue
            mod_name = asi.stem
            mod_id = unique_mod_id(state.mods, mod_name)
            related = self._detect_related_files_for_existing_asi(asi)
            LOGGER.info("Сканирован новый ASI-мод: %s", asi.name)
            state.mods[mod_id] = {
                "name": safe_name(mod_name),
                "enabled": True,
                "files": [{"rel": normalize_rel(p.relative_to(state.target_dir)), "added_at": now_iso()} for p in related],
                "source": "scan",
                "installed_at": now_iso(),
                "updated_at": now_iso(),
                "notes": "",
            }
            self._sort_mod_files(state.mods[mod_id])
            found += 1
        state.save()
        LOGGER.info("Сканирование завершено. Новых ASI-модов: %d", found)
        self.refresh_mods()
        self.refresh_loader_status(show_warning=not silent)
        if not silent:
            self.status_var.set(f"Сканирование завершено. Найдено новых ASI-модов: {found}.")

    def _detect_related_files_for_existing_asi(self, asi: Path) -> list[Path]:
        state = self.require_state()
        if not state:
            return [asi]
        stem = asi.stem.lower()
        related: list[Path] = [asi]
        for file in state.target_dir.iterdir():
            if not file.is_file() or file == asi:
                continue
            if file.suffix.lower() == ".bak":
                continue
            if file.stem.lower() == stem or file.name.lower().startswith(stem + "."):
                related.append(file)
        same_named_dir = state.target_dir / asi.stem
        if same_named_dir.exists() and same_named_dir.is_dir():
            related.extend(list_files_recursive(same_named_dir))
        return sorted(set(related), key=lambda p: normalize_rel(p.relative_to(state.target_dir)).lower())

    def refresh_mods(self) -> None:
        state = self.state
        self.mods_tree.delete(*self.mods_tree.get_children())
        self.files_tree.delete(*self.files_tree.get_children())
        self.notes_text.delete("1.0", END)
        if not state:
            return
        selected_to_restore = self.selected_mod_id
        for mod_id, mod in sorted(state.mods.items(), key=lambda pair: pair[1].get("name", "").lower()):
            status = state.mod_status(mod_id)
            if not self.show_disabled_var.get() and status == "отключен":
                continue
            files = mod.get("files", [])
            values = (status, str(len(files)), state.latest_mtime(mod_id), mod.get("source", ""))
            self.mods_tree.insert("", END, iid=mod_id, text=mod.get("name", mod_id), values=values)
        if selected_to_restore and self.mods_tree.exists(selected_to_restore):
            self.mods_tree.selection_set(selected_to_restore)
            self.mods_tree.focus(selected_to_restore)
            self.load_mod_details(selected_to_restore)

    def on_mod_selected(self, _event=None) -> None:  # noqa: ANN001
        selected = self.mods_tree.selection()
        if not selected:
            return
        self.selected_mod_id = selected[0]
        self.load_mod_details(self.selected_mod_id)

    def load_mod_details(self, mod_id: str) -> None:
        state = self.require_state()
        if not state:
            return
        mod = state.mods.get(mod_id)
        if not mod:
            return
        self.files_tree.delete(*self.files_tree.get_children())
        for item in mod.get("files", []):
            rel = item["rel"]
            enabled = state.enabled_path(rel)
            disabled = state.disabled_path(mod_id, rel)
            if enabled.exists():
                place = "bin64"
                mtime = fmt_ts(enabled)
            elif disabled.exists():
                place = "asibak"
                mtime = fmt_ts(disabled)
            else:
                place = "нет файла"
                mtime = "нет файла"
            self.files_tree.insert("", END, iid=rel, values=(rel, mtime, place))
        self.notes_text.delete("1.0", END)
        self.notes_text.insert("1.0", mod.get("notes", ""))

    def save_note(self, silent: bool = False) -> None:
        state = self.state
        if not state or not self.selected_mod_id:
            return
        mod = state.mods.get(self.selected_mod_id)
        if not mod:
            return
        mod["notes"] = self.notes_text.get("1.0", END).rstrip("\n")
        mod["updated_at"] = now_iso()
        state.save()
        if not silent:
            self.status_var.set("Заметка сохранена.")

    def toggle_selected_mod(self) -> None:
        state = self.require_state()
        if not state or not self.selected_mod_id:
            return
        mod_id = self.selected_mod_id
        status = state.mod_status(mod_id)
        try:
            if status == "отключен":
                self.enable_mod(mod_id)
            else:
                self.disable_mod(mod_id)
            state.save()
            self.refresh_mods()
            self.load_mod_details(mod_id)
        except Exception as exc:
            LOGGER.exception("Ошибка переключения мода %s", mod_id)
            messagebox.showerror(APP_NAME, str(exc))

    def disable_mod(self, mod_id: str) -> None:
        state = self.require_state()
        if not state:
            return
        mod = state.mods[mod_id]
        moved = 0
        for item in mod.get("files", []):
            rel = item["rel"]
            src = state.enabled_path(rel)
            if not src.exists():
                continue
            dst = state.disabled_path(mod_id, rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
            LOGGER.info("Отключение файла: %s -> %s", src, dst)
            moved += 1
        mod["enabled"] = False
        mod["updated_at"] = now_iso()
        LOGGER.info("Отключено: %s. Перемещено файлов: %d", mod.get("name", mod_id), moved)
        self.status_var.set(f"Отключено: {mod.get('name', mod_id)}. Перемещено файлов: {moved}.")

    def enable_mod(self, mod_id: str) -> None:
        state = self.require_state()
        if not state:
            return
        mod = state.mods[mod_id]
        moved = 0
        for item in mod.get("files", []):
            rel = item["rel"]
            src = state.disabled_path(mod_id, rel)
            if not src.exists():
                continue
            dst = state.enabled_path(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            self._backup_if_conflict(dst, mod_id, rel)
            if dst.exists():
                dst.unlink()
            shutil.move(str(src), str(dst))
            LOGGER.info("Включение файла: %s -> %s", src, dst)
            moved += 1
        mod["enabled"] = True
        mod["updated_at"] = now_iso()
        LOGGER.info("Включено: %s. Возвращено файлов: %d", mod.get("name", mod_id), moved)
        self.status_var.set(f"Включено: {mod.get('name', mod_id)}. Возвращено файлов: {moved}.")

    def delete_selected_mod(self) -> None:
        state = self.require_state()
        if not state or not self.selected_mod_id:
            messagebox.showinfo(APP_NAME, "Выберите мод для удаления.")
            return
        mod_id = self.selected_mod_id
        mod = state.mods.get(mod_id)
        if not mod:
            return
        mod_name = str(mod.get("name", mod_id))
        files_count = len(mod.get("files", []))
        if not messagebox.askyesno(
            APP_NAME,
            f"Удалить мод «{mod_name}»?\n\n"
            f"Будет удалено файлов: {files_count}.\n"
            "Архивная копия НЕ создаётся. Действие нельзя отменить через менеджер.",
        ):
            return
        try:
            deleted_count = self._delete_mod_files(mod_id)
            state.mods.pop(mod_id, None)
            state.save()
            self.selected_mod_id = None
            self.refresh_mods()
            self.status_var.set(f"Мод удалён: {mod_name}. Удалено файлов: {deleted_count}.")
        except Exception as exc:
            LOGGER.exception("Ошибка удаления мода %s", mod_id)
            messagebox.showerror(APP_NAME, f"Не удалось удалить мод:\n{exc}")

    def _delete_mod_files(self, mod_id: str) -> int:
        """Удаляет файлы мода без создания ZIP-архива.

        Отключенные файлы ищутся в asibak/<mod_id>/*.bak, включенные — прямо в bin64.
        """
        state = self.require_state()
        if not state:
            return 0
        mod = state.mods.get(mod_id, {})
        files = list(mod.get("files", []))
        deleted_count = 0
        LOGGER.info("Удаление мода без архива: %s", mod.get("name", mod_id))
        for item in files:
            rel = normalize_rel(item.get("rel", ""))
            if not rel:
                continue
            candidates = [state.enabled_path(rel), state.disabled_path(mod_id, rel)]
            for src in candidates:
                if not src.exists() or not src.is_file():
                    continue
                src.unlink()
                deleted_count += 1
                LOGGER.info("Удалён файл мода: %s", src)
        self._remove_empty_dirs(state.asibak_dir / mod_id)
        return deleted_count

    def selected_file_path(self) -> Path | None:
        state = self.require_state()
        if not state or not self.selected_mod_id:
            return None
        selected = self.files_tree.selection()
        if not selected:
            return None
        rel = selected[0]
        return state.actual_path(self.selected_mod_id, rel)

    def open_selected_file(self) -> None:
        path = self.selected_file_path()
        if not path:
            messagebox.showinfo(APP_NAME, "Выберите файл мода в списке.")
            return
        try:
            LOGGER.info("Открытие файла: %s", path)
            open_with_default_app(path)
        except Exception as exc:
            LOGGER.exception("Ошибка открытия файла %s", path)
            messagebox.showerror(APP_NAME, str(exc))

    def open_first_ini(self) -> None:
        state = self.require_state()
        if not state or not self.selected_mod_id:
            return
        mod = state.mods.get(self.selected_mod_id, {})
        ini_candidates: list[Path] = []
        for item in mod.get("files", []):
            rel = item["rel"]
            if rel.lower().endswith(".ini"):
                path = state.actual_path(self.selected_mod_id, rel)
                if path:
                    ini_candidates.append(path)
        if not ini_candidates:
            messagebox.showinfo(APP_NAME, "У этого мода не найден .ini файл.")
            return
        try:
            LOGGER.info("Открытие ini-файла: %s", ini_candidates[0])
            open_with_default_app(ini_candidates[0])
        except Exception as exc:
            LOGGER.exception("Ошибка открытия ini-файла %s", ini_candidates[0])
            messagebox.showerror(APP_NAME, str(exc))

    def open_target_dir(self) -> None:
        state = self.require_state()
        if not state:
            return
        try:
            LOGGER.info("Открытие рабочей папки: %s", state.target_dir)
            open_with_default_app(state.target_dir)
        except Exception as exc:
            LOGGER.exception("Ошибка открытия рабочей папки %s", state.target_dir)
            messagebox.showerror(APP_NAME, str(exc))

    def open_save_folder(self) -> None:
        """Открывает папку сохранений Crimson Desert, если она найдена."""
        candidates = crimson_save_candidates()
        found = existing_paths(candidates)
        if found:
            target = found[0]
            try:
                LOGGER.info("Открытие папки сохранений: %s", target)
                open_with_default_app(target)
                self.status_var.set(f"Открыта папка сохранений: {target}")
                return
            except Exception as exc:
                LOGGER.exception("Ошибка открытия папки сохранений %s", target)
                messagebox.showerror(APP_NAME, str(exc))
                return

        local = local_appdata_dir()
        message = (
            "Папка сохранений Crimson Desert не найдена.\n\n"
            "Проверялись варианты:\n"
            + "\n".join(f"• {p}" for p in candidates[:6])
            + "\n\nОткрою %LOCALAPPDATA%, чтобы можно было найти папку вручную."
        )
        LOGGER.warning("Папка сохранений не найдена. Открываю LOCALAPPDATA: %s", local)
        messagebox.showwarning(APP_NAME, message)
        try:
            open_with_default_app(local)
        except Exception as exc:
            LOGGER.exception("Ошибка открытия LOCALAPPDATA %s", local)
            messagebox.showerror(APP_NAME, str(exc))

    def open_shader_cache_folder(self, vendor: str = "auto") -> None:
        """Открывает папку кэша шейдеров.

        auto сначала пытается открыть NVIDIA DXCache, потому что это путь, который чаще всего
        нужен для текущего запроса. Если NVIDIA нет, берёт AMD/Intel/системные варианты.
        """
        vendor = (vendor or "auto").lower()
        all_candidates = shader_cache_candidates()
        if vendor == "auto":
            preferred = [item for item in all_candidates if item[0] == "NVIDIA DXCache"]
            fallback = [item for item in all_candidates if item not in preferred]
            candidates = preferred + fallback
        elif vendor == "nvidia":
            candidates = [item for item in all_candidates if item[0].lower().startswith("nvidia")]
        elif vendor == "amd":
            candidates = [item for item in all_candidates if item[0].lower().startswith("amd")]
        elif vendor == "intel":
            candidates = [item for item in all_candidates if item[0].lower().startswith("intel")]
        elif vendor == "windows":
            candidates = [item for item in all_candidates if item[0].lower().startswith("windows") or item[0].lower().startswith("temp")]
        else:
            candidates = all_candidates

        for label, path in candidates:
            if path.exists():
                try:
                    LOGGER.info("Открытие папки кэша шейдеров [%s]: %s", label, path)
                    open_with_default_app(path)
                    self.status_var.set(f"Открыт кэш шейдеров: {label} — {path}")
                    return
                except Exception as exc:
                    LOGGER.exception("Ошибка открытия кэша шейдеров %s: %s", label, path)
                    messagebox.showerror(APP_NAME, str(exc))
                    return

        # Для NVIDIA показываем именно тот путь, который ожидает пользователь, но папку не создаём.
        display_candidates = candidates or all_candidates
        message = (
            "Подходящая папка кэша шейдеров не найдена.\n\n"
            "Проверялись варианты:\n"
            + "\n".join(f"• {label}: {path}" for label, path in display_candidates[:8])
            + "\n\nПапку не создаю: если драйвер её не использует, пустая папка только создаст иллюзию порядка."
        )
        LOGGER.warning("Папка кэша шейдеров не найдена для режима %s", vendor)
        messagebox.showwarning(APP_NAME, message)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    LOGGER.info("Старт %s %s", APP_NAME, APP_VERSION)
    app = AsiManagerApp()
    app.run()


if __name__ == "__main__":
    main()
