import sys
from pathlib import Path

# Both roots the source tree is split into (see host/shared/README.md and
# host/windows/README.md) - kept here in addition to pytest.ini's own
# `pythonpath` setting (belt-and-suspenders: this file's insert also
# covers any tool that imports conftest.py without going through pytest's
# own pythonpath bootstrap).
_HOST_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOST_ROOT / 'shared'))
sys.path.insert(0, str(_HOST_ROOT / 'windows'))
