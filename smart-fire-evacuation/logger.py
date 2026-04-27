"""
logger.py
---------
Configures a single application-wide logger that writes to both
stdout (console) and a rotating file under /logs/.
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from config.settings import LOG_DIR


def get_logger(name: str = "fire_evacuation") -> logging.Logger:
    """
    Returns a configured logger instance.
    Call once at startup; subsequent calls reuse the same logger.
    """
    logger = logging.getLogger(name)

    # Prevent duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Console handler ───────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # ── Rotating file handler (5 MB × 5 backups) ─────────────────────
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, "evacuation.log")
    file_handler = RotatingFileHandler(log_file, maxBytes=5_242_880, backupCount=5)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
