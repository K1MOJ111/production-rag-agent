import re


def build_prompt(question: str, sources: list[dict]) -> str:
    if not sources:
        references = "未检索到相关资料。"
    else:
        references = "\n\n".join(
            (
                f"[资料 {source.get('citation_id', index)}]\n"
                f"文件：{source['filename']}\n"
                f"片段：{source['chunk_id']}\n"
                f"内容：{source['content']}"
            )
            for index, source in enumerate(sources, start=1)
        )

    return (
        "你是企业知识库问答助手。请只根据参考资料回答用户问题；"
        "如果资料不足，请明确说不知道，不要编造。\n\n"
        f"参考资料：\n{references}\n\n"
        f"用户问题：{question}\n\n"
        "请给出简洁答案；每个事实后使用 [资料 N] 标注依据。"
    )


def has_valid_citations(answer: str, sources: list[dict]) -> bool:
    cited = {int(value) for value in re.findall(r"\[资料\s*(\d+)\]", answer)}
    valid = {int(source["citation_id"]) for source in sources}
    return bool(cited) and cited <= valid
