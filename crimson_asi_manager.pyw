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
  - необязательно: 7-Zip в PATH для .7z/.rar архивов
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
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
SUPPORTED_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
IGNORED_TOP_NAMES = {
    STATE_DIR_NAME.lower(),
    ASIBAK_DIR_NAME.lower(),
    CONFLICT_BACKUP_DIR_NAME.lower(),
    DUPLICATE_BACKUP_DIR_NAME.lower(),
}


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


class State:
    def __init__(self, target_dir: Path):
        self.target_dir = target_dir.resolve()
        self.manager_dir = self.target_dir / STATE_DIR_NAME
        self.state_path = self.manager_dir / STATE_FILE_NAME
        self.asibak_dir = self.target_dir / ASIBAK_DIR_NAME
        self.data = self._load()

    def _default(self) -> dict:
        return {
            "version": 1,
            "target_dir": str(self.target_dir),
            "mods": {},
        }

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                data.setdefault("mods", {})
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
        self._sort_reverse: dict[tuple[int, str], bool] = {}

        self._build_ui()
        self._load_last_target()
        self._register_dnd()
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)

    def close_app(self) -> None:
        """Корректно закрывает окно и завершает процесс без зависшей bat-консоли."""
        try:
            self.save_note()
        except Exception:
            pass
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
        ttk.Button(top, text="Выбрать папку игры/bin64", command=self.choose_target_dir).pack(side=RIGHT)

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

        right_top = ttk.Frame(right)
        right_top.pack(fill=X, pady=(0, 6))
        ttk.Button(right_top, text="Открыть выбранный файл", command=self.open_selected_file).pack(side=LEFT)
        ttk.Button(right_top, text="Открыть .ini", command=self.open_first_ini).pack(side=LEFT, padx=(6, 0))
        ttk.Button(right_top, text="Открыть папку", command=self.open_target_dir).pack(side=LEFT, padx=(6, 0))
        ttk.Button(right_top, text="Сохранить заметку", command=self.save_note).pack(side=RIGHT)

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
        mod_menu.add_command(label="Открыть .ini", command=self.open_first_ini)
        mod_menu.add_command(label="Открыть выбранный файл", command=self.open_selected_file)
        mod_menu.add_command(label="Сохранить заметку", command=self.save_note)
        menu.add_cascade(label="Мод", menu=mod_menu)
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
        except Exception:
            pass

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
        self.state = State(target)
        self._cleanup_old_unpacked_duplicate_backups()
        self.target_var.set(str(target))
        save_app_settings({"target_dir": str(target)})
        self.rescan(silent=True)
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
            self.install_paths([Path(p) for p in paths])

    def on_drop(self, event) -> None:  # noqa: ANN001
        state = self.require_state()
        if not state:
            return
        raw = event.data
        try:
            parts = self.root.tk.splitlist(raw)
        except Exception:
            parts = raw.split()
        paths = [Path(p) for p in parts if p]
        self.install_paths(paths)

    def install_paths(self, paths: list[Path]) -> None:
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
                installed += self._install_one(path)
            except Exception as exc:
                errors.append(f"{path}: {exc}")
        state.save()
        self.refresh_mods()
        msg = f"Установлено/обновлено модов: {installed}."
        if errors:
            msg += " Ошибки: " + "; ".join(errors[:4])
            messagebox.showwarning(APP_NAME, msg)
        self.status_var.set(msg)

    def _install_one(self, source: Path) -> int:
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
            self._unpack_archive(archive, tmp)
            base = single_top_folder_base(tmp)
            return self._install_from_folder(base, archive.stem, f"archive:{archive.name}")

    def _unpack_archive(self, archive: Path, destination: Path) -> None:
        name = archive.name.lower()
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(destination)
            return
        if tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tf:
                tf.extractall(destination)
            return
        if name.endswith((".7z", ".rar")):
            seven_zip = shutil.which("7z") or shutil.which("7za") or shutil.which("7zz")
            if not seven_zip:
                raise RuntimeError("Для .7z/.rar нужен установленный 7-Zip в PATH. ZIP работает без него.")
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
            shutil.unpack_archive(str(archive), str(destination))
        except Exception as exc:
            raise RuntimeError(f"Архив не поддержан: {archive.suffix}") from exc

    def _install_from_folder(self, folder: Path, mod_name: str, source_label: str) -> int:
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

    def _copy_files_to_target(self, base: Path, files: list[Path], mod_name: str, source_label: str) -> int:
        state = self.require_state()
        if not state:
            return 0
        mod_name = safe_name(mod_name)
        incoming_has_asi = any(file.suffix.lower() == ".asi" for file in files)
        mod_id = self._find_existing_mod_for_source_or_name(source_label, mod_name)
        duplicate_backup: Path | None = None
        if not mod_id:
            mod_id = unique_mod_id(state.mods, mod_name)
            state.mods[mod_id] = {
                "name": mod_name,
                "enabled": True,
                "files": [],
                "source": source_label,
                "installed_at": now_iso(),
                "notes": "",
            }
        elif incoming_has_asi:
            duplicate_backup = self._archive_and_move_previous_mod_files(mod_id)
            state.mods[mod_id]["files"] = []
        mod = state.mods[mod_id]
        existing_rels = {item["rel"] for item in mod.get("files", [])}
        copied_rels: list[str] = []

        for file in files:
            rel = normalize_rel(file.relative_to(base))
            if not rel or rel.startswith("../"):
                continue
            target = state.enabled_path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._backup_if_conflict(target, mod_id, rel)
            shutil.copy2(file, target)
            copied_rels.append(rel)
            if rel not in existing_rels:
                mod.setdefault("files", []).append({"rel": rel, "added_at": now_iso()})
                existing_rels.add(rel)

        mod["enabled"] = True
        mod["updated_at"] = now_iso()
        mod["source"] = source_label
        if duplicate_backup:
            mod.setdefault("duplicate_backups", []).append(str(duplicate_backup))
        self._sort_mod_files(mod)
        return 1 if copied_rels else 0

    def _archive_and_move_previous_mod_files(self, mod_id: str) -> Path | None:
        """Перед заменой дубликатом архивирует старую версию мода и удаляет старые файлы."""
        state = self.require_state()
        if not state:
            return None
        mod = state.mods.get(mod_id, {})
        files = list(mod.get("files", []))
        if not files:
            return None

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        mod_name = safe_name(str(mod.get("name", mod_id)))
        duplicate_dir = state.target_dir / DUPLICATE_BACKUP_DIR_NAME / f"{timestamp}_{mod_name}"
        duplicate_dir.mkdir(parents=True, exist_ok=True)
        archive_path = duplicate_dir / "previous_files.zip"

        manifest = {
            "mod_id": mod_id,
            "mod_name": mod.get("name", mod_id),
            "created_at": now_iso(),
            "reason": "duplicate_before_replace",
            "files": [],
        }

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
                    manifest["files"].append({"rel": rel, "status": "missing"})
                    continue

                zf.write(src, logical_rel)
                manifest["files"].append({"rel": rel, "archived_as": logical_rel})
                src.unlink()

            zf.writestr("_asi_manager_duplicate_info.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        self._remove_empty_dirs(state.asibak_dir / mod_id)
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
        backup_root = state.target_dir / CONFLICT_BACKUP_DIR_NAME / datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)

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
        self.refresh_mods()
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

    def save_note(self) -> None:
        state = self.require_state()
        if not state or not self.selected_mod_id:
            return
        mod = state.mods.get(self.selected_mod_id)
        if not mod:
            return
        mod["notes"] = self.notes_text.get("1.0", END).rstrip("\n")
        mod["updated_at"] = now_iso()
        state.save()
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
            moved += 1
        mod["enabled"] = False
        mod["updated_at"] = now_iso()
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
            moved += 1
        mod["enabled"] = True
        mod["updated_at"] = now_iso()
        self.status_var.set(f"Включено: {mod.get('name', mod_id)}. Возвращено файлов: {moved}.")

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
            open_with_default_app(path)
        except Exception as exc:
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
            open_with_default_app(ini_candidates[0])
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def open_target_dir(self) -> None:
        state = self.require_state()
        if not state:
            return
        try:
            open_with_default_app(state.target_dir)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = AsiManagerApp()
    app.run()


if __name__ == "__main__":
    main()
