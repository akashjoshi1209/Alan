"""Mock AI provider for testing."""

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock

from alan.ai_providers.base import BaseProvider, BaseEmbeddingProvider


class MockChatProvider(BaseProvider):
    """Mock chat provider for testing."""

    def __init__(
        self,
        response: str = "Test response",
        delay: float = 0.0,
        should_fail: bool = False,
        error_message: str = "Test error",
    ):
        self._response = response
        self._delay = delay
        self._should_fail = should_fail
        self._error_message = error_message
        self.call_count = 0
        self.last_messages = None
        self.last_kwargs = None

    @property
    def name(self) -> str:
        return "mock"

    @property
    def models(self) -> List[str]:
        return ["mock-model", "mock-model-large"]

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def supports_function_calling(self) -> bool:
        return True

    @property
    def supports_vision(self) -> bool:
        return False

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate chat completion."""
        self.call_count += 1
        self.last_messages = messages
        self.last_kwargs = kwargs

        if self._delay:
            await asyncio.sleep(self._delay)

        if self._should_fail:
            raise Exception(self._error_message)

        return {
            "content": self._response,
            "model": model or "mock-model",
            "tokens_input": sum(len(m.get("content", "")) for m in messages) // 4,
            "tokens_output": len(self._response) // 4,
            "cost_usd": 0.0,
            "finish_reason": "stop",
        }

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion."""
        self.call_count += 1
        self.last_messages = messages
        self.last_kwargs = kwargs

        if self._delay:
            await asyncio.sleep(self._delay)

        if self._should_fail:
            raise Exception(self._error_message)

        # Split response into chunks
        words = self._response.split()
        for i, word in enumerate(words):
            if i > 0:
                yield " "
            yield word
            await asyncio.sleep(0.01)


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """Mock embedding provider for testing."""

    def __init__(
        self,
        dimension: int = 768,
        delay: float = 0.0,
        should_fail: bool = False,
    ):
        self._dimension = dimension
        self._delay = delay
        self._should_fail = should_fail
        self.call_count = 0
        self.last_texts = None

    @property
    def name(self) -> str:
        return "mock-embedding"

    @property
    def models(self) -> List[str]:
        return ["mock-embedding", "mock-embedding-large"]

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
        **kwargs,
    ) -> List[List[float]]:
        """Generate embeddings."""
        self.call_count += 1
        self.last_texts = texts

        if self._delay:
            await asyncio.sleep(self._delay)

        if self._should_fail:
            raise Exception("Embedding failed")

        # Return deterministic embeddings based on text content
        embeddings = []
        for text in texts:
            # Simple hash-based embedding for determinism
            import hashlib
            hash_val = hashlib.md5(text.encode()).hexdigest()
            embedding = []
            for i in range(0, min(len(hash_val), self._dimension * 2), 2):
                val = int(hash_val[i:i+2], 16) / 255.0
                embedding.append(val)
            # Pad or truncate to dimension
            embedding = (embedding * (self._dimension // len(embedding) + 1))[:self._dimension]
            embeddings.append(embedding)

        return embeddings


class MockProviderFactory:
    """Factory for creating mock providers."""

    @staticmethod
    def create_chat_provider(**kwargs) -> MockChatProvider:
        return MockChatProvider(**kwargs)

    @staticmethod
    def create_embedding_provider(**kwargs) -> MockEmbeddingProvider:
        return MockEmbeddingProvider(**kwargs)

    @staticmethod
    def create_failing_provider(error: str = "Test error") -> MockChatProvider:
        return MockChatProvider(should_fail=True, error_message=error)

    @staticmethod
    def create_delayed_provider(delay: float = 0.5) -> MockChatProvider:
        return MockChatProvider(delay=delay)


# Pre-configured mock providers
MOCK_CHAT_PROVIDER = MockChatProvider()
MOCK_EMBEDDING_PROVIDER = MockEmbeddingProvider()
FAILING_CHAT_PROVIDER = MockChatProvider(should_fail=True)
SLOW_CHAT_PROVIDER = MockChatProvider(delay=0.1)