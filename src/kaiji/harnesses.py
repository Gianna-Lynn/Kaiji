"""定义只切换可靠性机制的四种受控单 Agent Harness。"""

from __future__ import annotations

from enum import StrEnum


class HarnessVariant(StrEnum):
    """列出实验允许的受控 Harness 变体。"""

    BOUNDED_BASELINE = "bounded_baseline"
    NO_RETRY = "no_retry"
    NO_DUPLICATE_GUARD = "no_duplicate_guard"
    FULL_KAIJI = "full_kaiji"

    @property
    def retry_enabled(self) -> bool:
        """仅 full 与 no_duplicate_guard 启用一次有界工具重试。"""
        # TODO 15a（L3）：只根据受控消融矩阵返回 retry 开关。
        raise NotImplementedError("TODO 15a：定义 Harness retry 矩阵")

    @property
    def duplicate_guard(self) -> bool:
        """仅 full 与 no_retry 启用重复调用保护。"""
        # TODO 15a（L3）：所有变体仍保留 max_steps；这里只切换重复保护。
        raise NotImplementedError("TODO 15a：定义 Harness duplicate guard 矩阵")
