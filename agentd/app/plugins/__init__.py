"""受信任内置运维插件。

Phase 3 不扫描目录、不导入用户代码；只有这里显式组装的插件能够进入
Agent Tool 列表。这种“保守插件化”便于演示扩展点，同时不破坏安全边界。
"""

from .kubernetes import KubernetesPlugin
from .prometheus import PrometheusPlugin
from .registry import PluginRegistry, build_builtin_registry

__all__ = [
    "KubernetesPlugin",
    "PluginRegistry",
    "PrometheusPlugin",
    "build_builtin_registry",
]
