def build_prompt(question: str, sources: list[dict]) -> str:
    if not sources:
        references = "未检索到相关资料。"
    else:
        references = "\n\n".join(
            (
                f"[资料 {index}]\n"
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
        "请给出简洁答案，并说明依据来自哪些资料。"
    )
