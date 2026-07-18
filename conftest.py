# conftest.py
"""
pytest 根级配置。

将项目根目录加入 sys.path，使测试可以用 `from src.utils.xxx import ...`
方式直接导入源码（与 mypy --strict 使用的导入路径一致）。
"""
import os
import sys

# 项目根目录（conftest.py 所在目录）
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
