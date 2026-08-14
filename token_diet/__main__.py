"""python3 -m token_diet — тот же единый CLI, что и `token-diet`."""

import sys

from token_diet.cli import main

if __name__ == "__main__":
    sys.exit(main())
