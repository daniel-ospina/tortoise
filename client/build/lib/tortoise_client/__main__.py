"""python -m tortoise_client — thin client CLI entry (mirrors the console script)."""
import sys

from tortoise_client.cli import main

if __name__ == "__main__":
    sys.exit(main())
