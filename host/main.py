import os

# Must happen before any NodeGraphQt/Qt.py/PyQt import - two environment
# quirks discovered getting this running, neither specific to this app:
#
# 1. Qt.py (NodeGraphQt's binding shim) auto-detects whichever Qt binding is
#    installed, in its own priority order - if PyQt6 is ALSO installed
#    alongside PyQt5 (common on a dev machine with multiple projects), it
#    can pick PyQt6, which crashes on the first NodeGraphQt import (see
#    docs/design-decisions.md's "PyQt5, not PyQt6/PySide6" section - that's
#    the exact error). Force PyQt5 explicitly rather than hoping only one
#    binding is ever installed.
# 2. NodeGraphQt 0.6.44 imports the stdlib `distutils` module directly,
#    which Python 3.12 removed entirely. `setuptools` still vendors a
#    shim, but only registers itself into sys.modules['distutils'] if
#    imported first, and only when SETUPTOOLS_USE_DISTUTILS=local.
#
# setdefault() so an explicit override (a different Qt binding on purpose,
# a shell-level env var already set) isn't clobbered.
os.environ.setdefault('QT_PREFERRED_BINDING', 'PyQt5')
os.environ.setdefault('SETUPTOOLS_USE_DISTUTILS', 'local')
import setuptools  # noqa: E402,F401 - see above, must precede the NodeGraphQt import chain

import logging  # noqa: E402
import sys  # noqa: E402

from PyQt5 import QtWidgets  # noqa: E402

from app.main_window import MainWindow  # noqa: E402

# Without this, the root logger sits at its default WARNING level, so the
# per-iteration logger.info() calls in engine/cursor.py and engine/runner.py
# (move requested vs. actual delta, focus wait/resume decisions) are
# silently dropped - only ERROR-level failures would ever be seen.
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
