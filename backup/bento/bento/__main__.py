import sys

if sys.version_info < (3, 11):
    sys.exit("bento: Python 3.11+ required, found: %d.%d.%d" % sys.version_info[:3])

from .cli import main  # noqa: E402

sys.exit(main())
