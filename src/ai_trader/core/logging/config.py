"""日志系统配置项，保持最小但可扩展。"""

from __future__ import annotations

import logging
import os

# 基础日志级别
DEFAULT_LEVEL = logging.INFO

# 从环境变量读取详细级别设置
VERBOSE_LOGGING = os.getenv("AI_TRADER_VERBOSE", "false").lower() == "true"
DEBUG_LOGGING = os.getenv("AI_TRADER_DEBUG", "false").lower() == "true"

# 根据环境变量调整日志级别
if DEBUG_LOGGING:
    DEFAULT_LEVEL = logging.DEBUG
elif VERBOSE_LOGGING:
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

