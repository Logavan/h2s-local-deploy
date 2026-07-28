# licensing/__main__.py
"""Allow `python -m licensing <cmd>` invocation."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())