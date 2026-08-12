"""提供可替换的真实模型 Provider 实现。"""

from kaiji.providers.openai_compatible import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)

__all__ = ["OpenAICompatibleConfig", "OpenAICompatibleProvider"]
