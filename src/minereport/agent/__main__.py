"""``python -m minereport.agent`` entry point (same main as the console script)."""

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())

