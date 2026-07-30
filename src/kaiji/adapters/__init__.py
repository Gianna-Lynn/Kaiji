"""集中导出 Kaiji 与 Azusa 对接所需的适配器类型。"""

from kaiji.adapters.azusa import (
    AzusaAdapter,
    AzusaAdapterError,
    AzusaHttpAdapter,
    LegacyAzusaVectorHttpAdapter,
    MockAzusaAdapter,
)

__all__ = [
    "AzusaAdapter",
    "AzusaAdapterError",
    "AzusaHttpAdapter",
    "LegacyAzusaVectorHttpAdapter",
    "MockAzusaAdapter",
]
