import json
from urllib.request import Request, urlopen


class DashScopeRerankService:
    def __init__(self, api_key: str, base_url: str, model: str, opener=urlopen) -> None:
        host = base_url.rstrip("/").removesuffix("/compatible-mode/v1")
        self.url = f"{host}/compatible-api/v1/reranks"
        self.api_key = api_key
        self.model = model
        self.opener = opener

    def rerank(self, question: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates:
            return []
        payload = json.dumps(
            {
                "model": self.model,
                "query": question,
                "documents": [item["content"] for item in candidates],
                "top_n": min(top_k, len(candidates)),
            }
        ).encode("utf-8")
        request = Request(
            self.url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.opener(request, timeout=30) as response:
            results = json.loads(response.read())["results"]
        return [
            {
                **candidates[int(item["index"])],
                "score": round(float(item["relevance_score"]), 4),
            }
            for item in results
        ]
