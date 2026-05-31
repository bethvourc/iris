from __future__ import annotations

from pathlib import Path
import glob
import os
import sys


def main() -> int:
    root = Path(os.environ.get("IRIS_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
    src = root / "src"
    site_packages = glob.glob(str(root / ".venv" / "lib" / "python*" / "site-packages"))

    # The normal venv site startup is intermittently killed on this machine while
    # processing .pth hooks. Start with python -S, then add only the paths Iris
    # needs so imports work without running those hooks.
    sys.path.insert(0, str(src))
    for path in site_packages:
        if path not in sys.path:
            sys.path.append(path)

    from iris.cli import main as cli_main

    return int(cli_main(sys.argv[1:]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
