# Working on the project

This is the map of the code and the rules that keep existing installs working.
For using the project, see [README.md](README.md).

## How the pieces fit

```
Badge Arcade Manager.bat ─> manager.py ──────────── the window (tkinter)
                              ├─ manager_ui/         one module per tab, mixed into manager.Manager
                              │    └─ helper_tabs.py ─> helper/gui/   Badges, Machine editor, Custom weeks
                              │                          └─ helper/bahelper/   the file formats and the work
                              ├─ install.py          setup steps and the Setup tab's checklist
                              ├─ update.py           checking for and installing new versions
                              ├─ spotpass-letter/    SpotPass tools (serve.py, custom_week.py, letters...)
                              └─ starts, as background processes (manager_ui/services.py):
                                   server/badge_arcade/   python -m badge_arcade   (the server)
                                   server/mitm/           start_mitm.py            (the proxy) and hotspot.py
Setup.bat ─> update.py, install.py
```

Every tool also runs on its own from the command line; the manager only calls them.

| Folder | What goes there |
|---|---|
| `manager.py`, `manager_ui/` | The manager window. A new tab is a mixin module `manager_ui/<name>_tab.py` with a `build_<name>_tab(frame)` method: add it to `Manager`'s bases and to `TABS` and `builders` in `manager.py`. Shared look and widgets go in `manager_ui/widgets.py`, the server and proxy processes in `manager_ui/services.py`. |
| `install.py` | Setup steps (packages, config, proxy) and `checklist()`, what the Setup tab shows. New setup steps go here, never in the `.bat` files. |
| `update.py` | Updating. Standard library only: it has to run before the packages are installed. |
| `server/badge_arcade/` | The server package. NEX protocols in `protocols/`, HTTP (login, DataStore, SpotPass files) in `http_server.py`, saves in `storage.py`, settings in `config.py`. |
| `server/mitm/` | The proxy addon, its setup, and hotspot mode. |
| `spotpass-letter/` | Scripts for SpotPass files: serving weeks, custom weeks, free plays, letters. They import each other as top-level modules (the folder name has a hyphen, so it isn't a package). |
| `helper/bahelper/` | Your own badges and machines: file formats (`formats.py`, `sarc.py`, `yaz0.py`, `msbt.py`, `textures.py`), building weeks (`week.py`), the workspace. No tkinter here. |
| `helper/gui/` | The helper's tabs. `common.py` holds what they share; the Machine editor is `machines_tab.py` with its dialogs in `machine_dialogs.py`. |

The manager finds the other folders through `sys.path`: importing `manager_ui` adds
`spotpass-letter/`, `server/`, `server/mitm/` and `helper/`, which is why its tab modules
can `import serve`, `import hotspot`, `from badge_arcade...` and `from gui...`.

## Rules that keep installs working

Installs update by downloading `main` (see *Updating* in the README), so a change on `main`
reaches everyone. Keep these:

- **Don't move or rename anything people keep files in.** `other/`, `server/config.json`,
  `server/data/`, `server/nex-keys.txt`, `spotpass-letter/boot9.bin` and the key,
  `helper/workspace/` and the settings files stay where they are. They are listed in
  `.gitignore` and in `PROTECTED` in `update.py`; a new place for user files goes in both.
- **Never change `Setup.bat` or `Badge Arcade Manager.bat`** (a test checks). Windows keeps
  reading a running `.bat` from the same position, so an update that changed one would run
  half-lines of the new file. Put new steps in `install.py` or `manager.py`.
- **Keep `manager.py`, `install.py` and `update.py` at the top**, with `install.py --packages`
  working: the `.bat` files and older updaters run them.
- Moving or renaming the project's own code is fine: an update removes the files a new
  version no longer has.
- **Nothing from Nintendo** (SpotPass files, dumps, the game's code, keys) goes in the
  repository.

## Releasing

Raise the number in both `version.txt` and `VERSION` (a test checks they match) in the
change that goes to `main`. Installs pick it up at their next check, so merge the rest of
the change first or together.

## Tests and style

```sh
python install.py --packages
pip install -r server/requirements-dev.txt
python -m pytest     # every suite (server, SpotPass tools, helper)
ruff check .
coverage run -m pytest && coverage report   # which lines the tests run
```

A single suite also runs from its folder, as CI does: `cd server && python -m pytest tests`.
The helper's tests that need Nintendo's archived files and a console's key skip themselves
without them. CI (`.github/workflows/tests.yml`) runs on Windows, macOS and Linux with Python
3.12, 3.13 and 3.14, and shows the coverage on each run's summary page. ruff checks for syntax
errors, unused names and likely bugs (`pyproject.toml` lists the rules); a false alarm gets a
`# noqa: <rule>` with the reason.

The code is indented with tabs, lines are up to 140 characters (`.editorconfig` sets this
up in most editors), and needs Python 3.12. Comments and docstrings say what something is
for in plain words, like the rest of the code.
