"""全局变量容器（每 Runtime 实例独立，避免模块级单例污染）。"""

from __future__ import annotations

from .license_context import LicenseContext

__all__ = ["LicenseContext"]
