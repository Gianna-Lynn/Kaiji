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

        return self in {self.NO_DUPLICATE_GUARD, self.FULL_KAIJI}

    @property
    def duplicate_guard(self) -> bool:
        """仅 full 与 no_retry 启用重复调用保护。"""

        return self in {self.NO_RETRY, self.FULL_KAIJI}
