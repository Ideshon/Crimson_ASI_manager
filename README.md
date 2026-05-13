# Crimson Desert ASI Manager
==========================

## EN
What does:
- Selects the game folder or bin64. If the game folder is selected and bin64 is inside, bin64 is used.
- Adds mods from files, folders and archives.
- Drag-and-drop works with tkinterdnd2 installed: pip install tkinterdnd2
- Without tkinterdnd2, adding works via the File -> Add Mods menu.
- ZIP/TAR archives are unpacked using Python tools.
- 7Z/RAR require an installed 7-Zip in the PATH.
- Groups mod files: everything from one archive/folder is considered one mod.
- When scanning already installed ones .asi searches for files with the same name nearby, for example Mod.asi + Mod.ini + Mod.dll .
- Disabling transfers the mod files to bin64/asibak/<mod_id>/ and adds .bak to the file name.
- Enabling returns files back.
- Notes are stored in bin64/ASIModManager/state.json.
- You can open the .ini or the selected file through the default editor/application.
- When replacing a duplicate, the old mod files are added to previous_files.zip

**Important:**
- Make a backup copy of bin64 before using it for the first time, because mods like to break, as if it were their spiritual practice.
- After adding the old .asi/.ini/.dll/other files to the archive, they are deleted from bin64/asiduplicates and from the working folder.

Launch:
1. Install Python 3.10+ for Windows.
2. For drag-and-drop:
      py -3 -m pip install tkinterdnd2
3. Launch:
   1) Normal startup:
      run_crimson_asi_manager.bat
The console will close after the program window is closed. In this version, pause is removed.

   2) Launch without a console:
      run_crimson_asi_manager_no_console.bat
or double-click on crimson_asi_manager.pyw.

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
- При замене дубликата старые файлы мода добавляются в previous_files.zip

**Важно:**
- Перед первым использованием сделайте резервную копию bin64, потому что моды любят ломаться, как будто это их духовная практика.
- После добавления в архив старые .asi/.ini/.dll/прочие файлы удаляются из bin64/asiduplicates и из рабочей папки.

Запуск:
1. Установите Python 3.10+ для Windows.
2. Для drag-and-drop:
      py -3 -m pip install tkinterdnd2
3. Запуск:
   1) Обычный запуск:
      run_crimson_asi_manager.bat
      Консоль закроется после закрытия окна программы. В этой версии pause убран.

   2) Запуск без консоли:
      run_crimson_asi_manager_no_console.bat
      или двойной клик по crimson_asi_manager.pyw.
