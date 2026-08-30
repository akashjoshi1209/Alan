"""Mock vector store for testing."""

from typing import Any, AsyncGenerator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock


class MockVectorStore:
    """Mock vector store for testing."""

    def __init__(
        self,
        dimension: int = 768,
        should_fail: bool = False,
    ):
        self.dimension = dimension
        self.should_fail = should_fail
        self.collections: Dict[str, List[Dict[str, Any]]] = {}
        self.call_count = 0

    async def create_collection(
        self,
        name: str,
        dimension: Optional[int] = None,
        metadata: Optional[Dict] = None,
    ) -> bool:
        self.call_count += 1
        if self.should_fail:
            raise Exception("Mock vector store failure")
        self.collections[name] = []
        return True

    async def delete_collection(self, name: str) -> bool:
        self.call_count += 1
        if name in self.collections:
            del self.collections[name]
            return True
        return False

    async def list_collections(self) -> List[str]:
        return list(self.collections.keys())

    async def add(
        self,
        collection: str,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: Optional[List[Dict]] = None,
    ) -> List[str]:
        self.call_count += 1

        if self.should_fail:
            raise Exception("Mock vector store failure")

        if collection not in self.collections:
            self.collections[collection] = []

        for i, (id_, emb, doc) in enumerate(zip(ids, embeddings, documents)):
            metadata = metadatas[i] if metadatas else {}
            self.collections[collection].append({
                "id": id_,
                "embedding": emb,
                "document": doc,
                "metadata": metadata,
            })

        return ids

    async def search(
        self,
        collection: str,
        query_embedding: List[float],
        n_results: int = 10,
        where: Optional[Dict] = None,
        where_document: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        self.call_count += 1

        if self.should_fail:
            raise Exception("Mock vector store failure")

        if collection not in self.collections:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        items = self.collections[collection]

        # Simple similarity: use first few items
        results = items[:n_results]

        return {
            "ids": [[r["id"] for r in results]],
            "documents": [[r["document"] for r in results]],
            "metadatas": [[r["metadata"] for r in results]],
            "distances": [[0.1 * i for i in range(len(results))]],
        }

    async def get(
        self,
        collection: str,
        ids: List[str],
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self.call_count += 1

        if collection not in self.collections:
            return {"ids": [], "documents": [], "metadatas": [], "embeddings": []}

        items = [r for r in self.collections[collection] if r["id"] in ids]

        include = include or ["documents", "metadatas"]
        result = {"ids": [r["id"] for r in items]}

        if "documents" in include:
            result["documents"] = [r["document"] for r in items]
        if "metadatas" in include:
            result["metadatas"] = [r["metadata"] for r in items]
        if "embeddings" in include:
            result["embeddings"] = [r["embedding"] for r in items]

        return result

    async def delete(
        self,
        collection: str,
        ids: List[str],
    ) -> bool:
        self.call_count += 1

        if collection not in self.collections:
            return False

        self.collections[collection] = [
            r for r in self.collections[collection] if r["id"] not in ids
        ]
        return True

    async def count(self, collection: str) -> int:
        return len(self.collections.get(collection, []))

    async def health_check(self) -> bool:
        return not self.should_fail

    def reset(self) -> None:
        """Reset the mock store."""
        self.collections.clear()
        self.call_count = 0


def create_mock_vector_store(**kwargs) -> MockVectorStore:
    """Create mock vector store."""
    return MockVectorStore(**kwargs)