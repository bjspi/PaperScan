"""Logging for GUI and CLI: a daily rotating log file plus (for the CLI) the console.

The log lives in ``~/.PaperScan/logs/PaperScan.log``; one file per day
is kept for :data:`LOG_RETENTION_DAYS` days. Failures are logged with their
traceback so problems can be analysed later.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from paperscan import __version__
from paperscan.app_paths import LOG_FILE

LOG_RETENTION_DAYS = 14
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
#: Third-party loggers that are noisy at INFO level.
QUIET_LOGGERS = ("PIL", "urllib3", "httpx", "openai")


def setup_logging(console_handler: logging.Handler | None = None, console_level: int = logging.WARNING) -> None:
    """Log INFO and above to the rotating file; optionally also to ``console_handler`` at ``console_level``."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = TimedRotatingFileHandler(LOG_FILE, when="midnight", backupCount=LOG_RETENTION_DAYS, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    file_handler.setLevel(logging.DEBUG if console_level <= logging.DEBUG else logging.INFO)
    handlers: list[logging.Handler] = [file_handler]
    if console_handler is not None:
        console_handler.setLevel(console_level)
        handlers.append(console_handler)
    logging.basicConfig(level=logging.DEBUG, handlers=handlers, force=True)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    sys.excepthook = _log_uncaught
    logging.getLogger(__name__).info("PaperScan %s started (%s)", __version__, " ".join(sys.argv))


def _log_uncaught(exc_type: type[BaseException], exc: BaseException, traceback: object) -> None:
    """Write crashes to the log before Python prints them."""
    logging.getLogger("paperscan").critical("Uncaught exception", exc_info=(exc_type, exc, traceback))  # type: ignore[arg-type]
    sys.__excepthook__(exc_type, exc, traceback)  # type: ignore[arg-type]
