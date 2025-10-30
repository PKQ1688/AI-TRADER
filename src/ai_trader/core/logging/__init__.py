"""AI Trader 统一日志封装，基于 Rich 输出。"""

from __future__ import annotations

import logging
from logging import Logger
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from . import config

_is_configured = False
_console: Optional[Console] = None
_handler: Optional[RichHandler] = None


def _configure_once() -> None:
    """配置根日志，只执行一次，确保保持简洁。"""

    global _is_configured, _console, _handler
    if _is_configured:
        return

    console = Console()
    handler = RichHandler(
        console=console,
        show_path=config.SHOW_PATH,
        rich_tracebacks=config.RICH_TRACEBACKS,
        markup=config.MARKUP,
        enable_link_path=config.ENABLE_LINK_PATH,
    )
    handler.setFormatter(
        logging.Formatter(config.MESSAGE_FORMAT, datefmt=config.DATE_FORMAT)
    )

    root_logger = logging.getLogger(config.ROOT_LOGGER_NAME)
    root_logger.setLevel(config.DEFAULT_LEVEL)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.propagate = False

    _console = console
    _handler = handler
    _is_configured = True


def get_logger(name: Optional[str] = None) -> Logger:
    """获取统一配置的 Logger。"""

    _configure_once()

    if not name:
        return logging.getLogger(config.ROOT_LOGGER_NAME)

    if not name.startswith(config.ROOT_LOGGER_NAME):
        full_name = f"{config.ROOT_LOGGER_NAME}.{name}"
    else:
        full_name = name

    return logging.getLogger(full_name)


__all__ = ["get_logger"]

