# `shared/`

Everything in this directory has **zero OS-specific dependency** - no
`ctypes`/win32 calls, no PyQt5, no `pywebview`, no Windows-only packages.
Confirmed file-by-file, not assumed - see `zmk-config`'s
`docs/modules/zmk-screen-scan-macro/research-android-feasibility.md` for
the audit this split came out of.

Flat, not a Python package (no `__init__.py`, no subfolders for `.py`
files) - every module here imports its siblings by plain top-level name
(`import protocol as wire`, `from command import Command`, ...), the same
convention `protocol.py`/`win32_focus.py` already used pre-split. This
directory is added to `sys.path` by `../pytest.ini` (tests) and by
`../windows/main.py` (the real app) - nothing in here does its own
`sys.path` bootstrapping.

| File | What it is |
|---|---|
| `protocol.py` | Wire-format packet encoding for the command channel to the Nice Nano. |
| `matcher.py` | Masked template matching (`cv2.matchTemplate`) - Branch/Branch (Wait) node scoring. |
| `command.py` | `Command` dataclass + `CommandSink` protocol/implementations (targets `protocol.py`). |
| `branch_images.py` | Pure port/connection-rewiring algorithm for Branch/Branch (Wait)'s image list. |
| `graph_translation.py` | Translates a `GraphDocument` (from `webui/graph_editor.js`) into the engine's plain-JSON graph schema. |
| `reference_processing.py` | Cropping/alpha-masking a user-uploaded reference image (`process_masked_reference()`). |
| `profile_manager.py` / `profile_store.py` | Profile CRUD (`profiles/<name>/profile.json` + `images/`) - plain `os`/`shutil`/`json`. |
| `webui/` | The Drawflow-based graph editor (HTML/JS/CSS) - a browser can render this unmodified; only the native bridge it talks to (`windows/bridge.py`) is platform-specific. |

**Why this split exists**: `windows/` (the sibling directory) holds
everything that's Windows/desktop-exclusive today - Win32 focus/capture/
cursor APIs, PyQt5 overlays, the `pywebview` GUI shell, and the USB Raw
HID transport. Anything that could plausibly run unchanged on a different
host (a future Android build, in particular) lives here instead, so it
doesn't have to be picked back out of a monolithic tree later. This is a
**structural** move only - no behavior changed, see the commit this
landed in.
