from __future__ import annotations

import sys
from pathlib import Path

# `lambda` is a Python keyword so the lambda/ directory can't be imported as a
# package with dotted notation. Add it to sys.path so modules are imported
# directly by name (e.g. `import models`, `import scorer`).
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))
