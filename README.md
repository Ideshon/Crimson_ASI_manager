# -Crimson Desert ASI Manager
==========================

## EN
What it does:
- Selects the game folder or the bin64 folder. If the game folder is selected and it contains a bin64 folder, it uses the bin64 folder.
- Adds mods from files, folders, and archives.
- Drag-and-drop works if tkinterdnd2 is installed: pip install tkinterdnd2
- Without tkinterdnd2, you can add mods via the File -> Add Mods menu.
- ZIP/TAR archives are extracted using Python tools.
- 7Z/RAR require 7-Zip to be installed and in the PATH.
- Groups mod files: everything from a single archive/folder is considered a single mod.
- When scanning already installed .asi files, it looks for files with the same name nearby, for example Mod.asi + Mod.ini + Mod.dll.
- Disabling moves mod files to bin64/asibak/<mod_id>/ and adds .bak to the filename.
- Enabling moves the files back.
- Notes are stored in bin64/ASIModManager/state.json.
- You can open the .ini or selected file using the default editor/application.

Setup:
1. Install Python 3.10+ for Windows.
2. Run run_crimson_asi_manager.bat or execute:
   py -3 crimson_asi_manager.py

For drag-and-drop:
   py -3 -m pip install tkinterdnd2

Important:
- Before using it for the first time, make a backup of bin64, because mods love to break as if it were their spiritual practice.
- If the file already exists and the manager overwrites it, the old version is copied to bin64/asimanager_conflict_backups/.

Translated with DeepL.com (free version)

## RU
Что делает:
- Выбирает папку игры или bin64. Если выбрана папка игры и внутри есть bin64, использует bin64.
- Добавляет моды из файлов, папок и архивов.
- Drag-and-drop работает при установленном tkinterdnd2: pip install tkinterdnd2
- Без tkinterdnd2 работает добавление через меню Файл -> Добавить моды.
- ZIP/TAR архивы распаковываются средствами Python.
- 7Z/RAR требуют установленный 7-Zip в PATH.
- Группирует файлы мода: всё из одного архива/папки считается одним модом.
- При сканировании уже установленных .asi ищет рядом файлы с тем же названием, например Mod.asi + Mod.ini + Mod.dll.
- Отключение переносит файлы мода в bin64/asibak/<mod_id>/ и добавляет .bak к имени файла.
- Включение возвращает файлы обратно.
- Заметки хранятся в bin64/ASIModManager/state.json.
- Можно открыть .ini или выбранный файл через редактор/приложение по умолчанию.

Запуск:
1. Установите Python 3.10+ для Windows.
2. Запустите run_crimson_asi_manager.bat или выполните:
   py -3 crimson_asi_manager.py

Для drag-and-drop:
   py -3 -m pip install tkinterdnd2

Важно:
- Перед первым использованием сделайте резервную копию bin64, потому что моды любят ломаться, как будто это их духовная практика.
- Если файл уже существует и менеджер его перезаписывает, старая версия копируется в bin64/asimanager_conflict_backups/.
