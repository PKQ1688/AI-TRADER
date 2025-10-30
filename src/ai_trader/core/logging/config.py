"""日志系统配置项，保持最小但可扩展。"""

from __future__ import annotations

import logging

# 基础日志级别
DEFAULT_LEVEL = logging.INFO

# RichHandler 相关细节
SHOW_PATH = False
RICH_TRACEBACKS = True
MARKUP = True
ENABLE_LINK_PATH = False

# 格式定义：Rich 已负责级别与时间的渲染，这里仅保留消息体
MESSAGE_FORMAT = "%(message)s"
DATE_FORMAT = "[%X]"

# 统一的根日志名称，便于分层管理
ROOT_LOGGER_NAME = "ai_trader"

