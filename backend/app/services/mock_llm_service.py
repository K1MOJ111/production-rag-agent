class MockLLMService:
    """Template-based answer generator used before connecting a real LLM."""

    def generate_answer(self, question: str, sources: list[dict], prompt: str) -> str:
        if not sources:
            return (
                "我没有在知识库中检索到足够相关的资料，"
                "所以不能基于企业文档回答这个问题。"
            )

        best_source = sources[0]
        evidence = best_source["content"][:220]
        return (
            f"根据知识库中与“{question}”最相关的资料，答案可以参考："
            f"{evidence}。依据主要来自《{best_source['filename']}》"
            f"的片段 {best_source['chunk_id']}。"
        )
