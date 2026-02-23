"""
Logging setup for CubeVi C1 STL Viewer.
Writes to cubevi_viewer.log (overwritten each run).
Also captures stderr so traceback info lands in the log.
"""

import os
import sys
import logging
from datetime import datetime

_LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'cubevi_viewer.log'
)


def setup_logging():
    """
    Configure logging to write to cubevi_viewer.log.
    Returns a redirect object — assign to sys.stdout/stderr if desired.
    """
    # Create logger
    logger = logging.getLogger('cubevi')
    logger.setLevel(logging.DEBUG)

    # File handler — overwrite each run
    fh = logging.FileHandler(_LOG_FILE, mode='w', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s  %(message)s', datefmt='%H:%M:%S')
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # Write header
    logger.info(f"CubeVi C1 STL Viewer — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Python {sys.version}")
    logger.info("")

    return logger


class _LogRedirect:
    """Redirect print() / sys.stdout writes to the logger."""
    def __init__(self, logger, level=logging.INFO):
        self._logger = logger
        self._level = level
        self._buf = ''

    def write(self, msg):
        if msg and msg.strip():
            for line in msg.rstrip().splitlines():
                self._logger.log(self._level, line)

    def flush(self):
        pass


def redirect_stdio(logger):
    """Redirect stdout and stderr to the log file."""
    sys.stdout = _LogRedirect(logger, logging.INFO)
    sys.stderr = _LogRedirect(logger, logging.ERROR)
