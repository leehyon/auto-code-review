"""审查文件类型过滤的公共辅助。"""

import os
from typing import List, Optional


def supported_extensions() -> List[str]:
    """读取 SUPPORTED_EXTENSIONS，返回去掉空项的扩展名列表。

    空项必须去掉：``path.endswith('')`` 恒为真，一旦配置里出现空元素
    （例如以逗号结尾），过滤会完全失效，所有文件都会被送进模型审查。
    """
    raw = os.getenv('SUPPORTED_EXTENSIONS', '.java,.py,.php')
    return [ext.strip() for ext in raw.split(',') if ext.strip()]


def is_supported(path: str, extensions: Optional[List[str]] = None) -> bool:
    """文件路径的扩展名是否在受支持列表里。"""
    if extensions is None:
        extensions = supported_extensions()
    if not extensions:
        return False
    return any(path.endswith(ext) for ext in extensions)
