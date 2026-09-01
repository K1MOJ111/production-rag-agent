from openai import OpenAI


class DeepSeekLLMService:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60,
            max_retries=2,
        )

    def generate_answer(self, question: str, sources: list[dict], prompt: str) -> str:
        if not sources:
            return "我没有在知识库中检索到足够相关的资料，所以不能基于企业文档回答这个问题。"

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "你是企业知识库助手，只能依据给定资料回答并标注资料编号。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=600,
        )
        answer = response.choices[0].message.content
        if not answer or not answer.strip():
            raise RuntimeError("DeepSeek returned an empty answer")
        return answer.strip()
