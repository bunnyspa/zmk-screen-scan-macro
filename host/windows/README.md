# `windows/`

Everything that's Windows/desktop-exclusive today: Win32 API calls
(`ctypes`/`win32_focus.py`), PyQt5 (overlays, the `pywebview` GUI shell),
the Windows Graphics Capture-based `window_capture.py`, and the USB Raw
HID transport (`hid_link.py` + `hidapi.dll`).

Flat, not a Python package, same convention as `../shared/` - modules
import their siblings by plain top-level name. `main.py` adds
`../shared/` to `sys.path` at startup (see its own docstring); everything
in this directory is already implicitly importable by name once it's
running, since Python puts a script's own directory on `sys.path`
automatically.

Run with `python host/windows/main.py` from the repo root (not from
`host/` or `host/windows/` - see `main.py`'s `PROFILES_ROOT`, which is
`cwd`-relative).

| File | What it is |
|---|---|
| `main.py` | Entrypoint - `pywebview`'s `qt` GUI backend hosting `../shared/webui/`. |
| `win32_focus.py` | Raw `ctypes` bindings to `user32`/`dwmapi` - window lookup, focus, DWM extended frame bounds. |
| `hid_link.py` + `hidapi.dll` | The open Raw HID connection to the Nice Nano (reads: trigger detection; writes: action commands). |
| `cursor.py` | Click-targeting - gain-adaptive cursor convergence, monitor-crossing, the click-execution loop. |
| `focus.py` | Thin `win32_focus.py` re-export for `runner.py`'s focus-policy consumers. |
| `monitors.py` | Real multi-monitor geometry (`EnumDisplayMonitors`). |
| `window_capture.py` | Live per-window capture via the Windows Graphics Capture API. |
| `window_resolve.py` | Resolves a profile's target window by owning executable name. |
| `runner.py` | `MacroRunner` - walks a graph, drives capture → branch → action. |
| `pick_controller.py` / `run_controller.py` | Bridge-callable orchestration for region-picking and Run/Stop/Confirm. |
| `overlays.py` | PyQt5 click-through/always-on-top overlay widgets. |
| `bridge.py` | The `pywebview` `js_api` bridge - the only module (besides `main.py`) allowed to import `webview`. |

See `../shared/README.md` for why this split exists.
