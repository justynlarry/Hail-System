"""Which release this code is (parking-lot item 289).

A deploy writes hailsys/VERSION (scripts/write_version.sh; ignored by git, so never committed):
one line, the output of `git describe --tags --always --dirty` and the short commit, for example
"v0.9.0 abc1234" or "v0.9.0-3-gabc1234-dirty abc1234".  The web container mounts only hailsys/, so it
cannot ask git itself.  No file, or a line that is not two plain words, means a working copy that was
never stamped:  the footer says "development".
"""

import re
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
_WORD = re.compile(r"[A-Za-z0-9._-]{1,80}")


def read_version(path=VERSION_FILE):
    """{'describe', 'commit'} from the version file, or None if there is none or it is not as written."""
    try:
        line = Path(path).read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return None
    parts = line.split()
    if len(parts) != 2 or not all(_WORD.fullmatch(p) for p in parts):
        return None
    return {"describe": parts[0], "commit": parts[1]}
