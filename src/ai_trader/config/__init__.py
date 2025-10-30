"""配置子模块。

暴露设置数据结构供 SDK 直接导入使用。
"""

from __future__ import annotations

from .settings import Settings, load_settings

__all__ = ["Settings", "load_settings"]
