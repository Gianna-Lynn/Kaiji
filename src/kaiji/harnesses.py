"""定义只切换可靠性机制的四种受控单 Agent Harness。"""

from __future__ import annotations

from enum import StrEnum


class HarnessVariant(StrEnum):
    """列出实验允许的受控 Harness 变体。"""

    BOUNDED_BASELINE = "bounded_baseline"
    NO_RETRY = "no_retry"
    NO_DUPLICATE_GUARD = "no_duplicate_guard"
    FULL_KAIJI = "full_kaiji"

    # | Harness | Retry | Duplicate guard | max_steps |
    # |---|---:|---:|---:|
    # | `bounded_baseline` | 关 | 关 | 保留 |
    # | `no_retry` | 关 | 开 | 保留 |
    # | `no_duplicate_guard` | 开 | 关 | 保留 |
    # | `full_kaiji` | 开 | 开 | 保留 |

    @property   # 这个装饰器允许调用时使用这样的写法: variant.retry_enabled, 这样写会自动调用 retry_enabled()这个方法.
    def retry_enabled(self) -> bool:
        """仅 full 与 no_duplicate_guard 启用一次有界工具重试。"""
        # TODO 15a（L3）：只根据受控消融矩阵返回 retry 开关。
        return self in {
            HarnessVariant.NO_DUPLICATE_GUARD,
            HarnessVariant.FULL_KAIJI,
        }

    @property
    def duplicate_guard(self) -> bool:
        """仅 full 与 no_retry 启用重复调用保护。"""
        # TODO 15a（L3）：所有变体仍保留 max_steps；这里只切换重复保护。
        return self in{
            HarnessVariant.BOUNDED_BASELINE,
            HarnessVariant.NO_RETRY,
        }
