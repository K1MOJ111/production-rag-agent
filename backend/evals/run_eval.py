import argparse
import asyncio
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from langchain_core.embeddings import Embeddings

from app.config import Settings
from app.services.agent_service import LangGraphAgentService
from app.services.business_adapter import InMemoryBusinessAdapter
from app.services.document_service import DocumentService
from app.services.mock_embedding_service import MockEmbeddingService
from app.services.mock_llm_service import MockLLMService
from app.services.prompt_service import build_prompt, has_valid_citations
from app.services.vector_store import InMemoryVectorStore


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DATASET_PATH = Path(__file__).with_name("m9_cases.json")
REQUIRED_CATEGORIES = {
    "exact",
    "paraphrase",
    "keyword",
    "cross_document",
    "irrelevant",
    "adversarial",
}
LOW_CONFIDENCE_ANSWER = (
    "我没有在知识库中检索到足够相关的资料，"
    "所以不能基于企业文档回答这个问题。"
)


class TimedMockEmbedding(MockEmbeddingService):
    def __init__(self) -> None:
        self.last_query_ms = 0.0

    def embed(self, text: str) -> dict[str, float]:
        started = perf_counter()
        value = super().embed(text)
        self.last_query_ms = (perf_counter() - started) * 1000
        return value


class TimedEmbeddings(Embeddings):
    def __init__(self, service: Embeddings) -> None:
        self.service = service
        self.last_query_ms = 0.0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.service.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        started = perf_counter()
        value = self.service.embed_query(text)
        self.last_query_ms = (perf_counter() - started) * 1000
        return value


def load_dataset(path: Path = DATASET_PATH) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    cases = dataset.get("cases")
    if not isinstance(cases, list) or len(cases) < 30:
        raise ValueError("Eval dataset must contain at least 30 cases")
    if {case.get("category") for case in cases} != REQUIRED_CATEGORIES:
        raise ValueError("Eval dataset categories are incomplete")
    ids = [case.get("id") for case in cases]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("Eval case IDs must be present and unique")
    for case in cases:
        expected = case.get("expected_filenames")
        refused = case.get("expected_refusal")
        if not isinstance(expected, list) or not isinstance(refused, bool):
            raise ValueError("Eval cases require expected_filenames and expected_refusal")
        if refused == bool(expected):
            raise ValueError("Refusal cases must have no expected sources")
    return dataset


def _build_stack(mode: str):
    if mode == "mock":
        settings = Settings.from_env({"RAG_MODE": "mock"})
        embedder = TimedMockEmbedding()
        return (
            settings,
            embedder,
            InMemoryVectorStore(),
            None,
            MockLLMService(),
        )
    if os.getenv("RUN_REAL_EVAL") != "1":
        raise RuntimeError("set RUN_REAL_EVAL=1 to allow paid model calls")
    settings = Settings.from_env()
    if settings.rag_mode != "real":
        raise RuntimeError("real Eval requires RAG_MODE=real")

    from app.services.dashscope_embedding_service import DashScopeEmbeddingService
    from app.services.dashscope_rerank_service import DashScopeRerankService
    from app.services.deepseek_llm_service import DeepSeekLLMService
    from app.services.postgres_vector_store import PostgresVectorStore

    embedder = TimedEmbeddings(
        DashScopeEmbeddingService(
            settings.dashscope_api_key,
            settings.dashscope_base_url,
            settings.embedding_model,
            settings.embedding_dimension,
        )
    )
    return (
        settings,
        embedder,
        PostgresVectorStore(
            settings.database_url, embedder, settings.embedding_dimension
        ),
        DashScopeRerankService(
            settings.dashscope_api_key,
            settings.dashscope_base_url,
            settings.rerank_model,
        ),
        DeepSeekLLMService(
            settings.deepseek_api_key,
            settings.deepseek_base_url,
            settings.deepseek_model,
        ),
    )


def _latency_summary(cases: list[dict]) -> dict:
    summary = {}
    for stage in ("embedding", "retrieval", "rerank", "llm"):
        values = sorted(case["latency_ms"][stage] for case in cases)
        summary[stage] = {
            "avg": round(sum(values) / len(values), 3),
            "p95": round(values[math.ceil(len(values) * 0.95) - 1], 3),
            "max": round(values[-1], 3),
        }
    return summary


def _usage_summary(usages: list[dict], mode: str) -> dict:
    if mode == "mock":
        return {
            "status": "not_applicable_in_mock_mode",
            "embedding": "not_applicable_in_mock_mode",
            "rerank": "not_applicable_in_mock_mode",
        }
    if not usages:
        return {
            "status": "unavailable_from_provider_response",
            "embedding": "unavailable_from_provider_response",
            "rerank": "unavailable_from_provider_response",
        }

    totals = {
        key: sum(item[key] for item in usages)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    input_rate = os.getenv("DEEPSEEK_INPUT_COST_PER_MILLION", "").strip()
    output_rate = os.getenv("DEEPSEEK_OUTPUT_COST_PER_MILLION", "").strip()
    result = {
        "status": "llm_tokens_recorded",
        **totals,
        "estimated_cost": None,
        "embedding": "unavailable_from_provider_response",
        "rerank": "unavailable_from_provider_response",
    }
    if not input_rate or not output_rate:
        result["cost_status"] = "unavailable_pricing_not_configured"
        return result
    input_price, output_price = float(input_rate), float(output_rate)
    if input_price < 0 or output_price < 0:
        raise ValueError("cost rates must not be negative")
    result["estimated_cost"] = round(
        totals["prompt_tokens"] * input_price / 1_000_000
        + totals["completion_tokens"] * output_price / 1_000_000,
        8,
    )
    result["cost_status"] = "estimated_from_configured_rates"
    result["currency"] = os.getenv("DEEPSEEK_COST_CURRENCY", "CNY")
    return result


def _evaluate_rag(dataset: dict, mode: str) -> tuple[dict, dict]:
    settings, embedder, store, reranker, llm = _build_stack(mode)
    existing = {item["document_id"] for item in store.list_documents()}
    loaded = DocumentService(store, embedder).load_sample_documents(
        PROJECT_ROOT / "sample_docs"
    )
    created = [item["document_id"] for item in loaded if item["document_id"] not in existing]
    cases = []
    usages = []
    top_k = int(dataset["top_k"])

    try:
        for spec in dataset["cases"]:
            search_started = perf_counter()
            candidates = store.search(spec["question"], top_k * 4, embedder)
            search_ms = (perf_counter() - search_started) * 1000
            embedding_ms = embedder.last_query_ms

            rerank_started = perf_counter()
            sources = (
                reranker.rerank(spec["question"], candidates, top_k)
                if reranker
                else candidates[:top_k]
            )
            rerank_ms = (perf_counter() - rerank_started) * 1000 if reranker else 0.0
            sources = [
                {**source, "citation_id": index}
                for index, source in enumerate(sources, start=1)
            ]
            refused = not sources or sources[0]["score"] < settings.min_similarity_score
            top_score = sources[0]["score"] if sources else None
            returned = [source["filename"] for source in sources]
            llm_started = perf_counter()
            usage = None
            if refused:
                answer = LOW_CONFIDENCE_ANSWER
                citation_valid = None
            else:
                prompt = build_prompt(spec["question"], sources)
                answer, usage = llm.generate_answer_with_usage(
                    spec["question"], sources, prompt
                )
                citation_valid = has_valid_citations(answer, sources)
            llm_ms = (perf_counter() - llm_started) * 1000 if not refused else 0.0
            if usage:
                usages.append(usage)

            expected = spec["expected_filenames"]
            found = len(set(expected) & set(returned))
            recall = found / len(expected) if expected else None
            relevant_ranks = [
                rank for rank, filename in enumerate(returned, start=1)
                if filename in expected
            ]
            reciprocal_rank = 1 / min(relevant_ranks) if relevant_ranks else 0.0
            refusal_correct = refused == spec["expected_refusal"]
            source_match = set(expected) <= set(returned) if expected else refused
            passed = refusal_correct and source_match and (
                citation_valid is not False
            )
            cases.append(
                {
                    "id": spec["id"],
                    "category": spec["category"],
                    "question": spec["question"],
                    "expected_filenames": expected,
                    "returned_filenames": returned,
                    "expected_refusal": spec["expected_refusal"],
                    "actual_refusal": refused,
                    "top_score": top_score,
                    "recall_at_3": round(recall, 4) if recall is not None else None,
                    "reciprocal_rank_at_3": round(reciprocal_rank, 4),
                    "citation_valid": citation_valid,
                    "source_match": source_match,
                    "passed": passed,
                    "latency_ms": {
                        "embedding": round(embedding_ms, 3),
                        "retrieval": round(max(0.0, search_ms - embedding_ms), 3),
                        "rerank": round(rerank_ms, 3),
                        "llm": round(llm_ms, 3),
                    },
                }
            )
    finally:
        for document_id in created:
            store.delete_document(document_id)
        if mode == "real":
            asyncio.run(store.close())

    relevant = [case for case in cases if case["expected_filenames"]]
    answered = [case for case in cases if case["citation_valid"] is not None]
    metrics = {
        "retrieval_recall_at_3": round(
            sum(case["recall_at_3"] for case in relevant) / len(relevant), 4
        ),
        "mrr_at_3": round(
            sum(case["reciprocal_rank_at_3"] for case in relevant) / len(relevant),
            4,
        ),
        "refusal_accuracy": round(
            sum(case["actual_refusal"] == case["expected_refusal"] for case in cases)
            / len(cases),
            4,
        ),
        "citation_validity": round(
            sum(case["citation_valid"] is True for case in answered) / len(answered), 4
        ) if answered else None,
        "source_match_rate": round(
            sum(case["source_match"] for case in relevant) / len(relevant), 4
        ),
        "pass_rate": round(sum(case["passed"] for case in cases) / len(cases), 4),
    }
    model_config = {
        "embedding": settings.embedding_model if mode == "real" else "mock-token-frequency",
        "retrieval": "pgvector+pg_trgm+RRF" if mode == "real" else "mock-cosine",
        "rerank": settings.rerank_model if mode == "real" else "not_applicable",
        "llm": settings.deepseek_model if mode == "real" else "mock-grounded-answer",
        "min_similarity_score": settings.min_similarity_score,
        "top_k": top_k,
    }
    return (
        {
            "case_count": len(cases),
            "passed": sum(case["passed"] for case in cases),
            "metrics": metrics,
            "latency_ms": _latency_summary(cases),
            "failure_ids": [case["id"] for case in cases if not case["passed"]],
            "cases": cases,
        },
        {"model_config": model_config, "usage": _usage_summary(usages, mode)},
    )


class _AgentCompletions:
    def create(self, **kwargs):
        last = kwargs["messages"][-1]
        if last["role"] == "user":
            content = last["content"]
            if "取消" in content:
                name, arguments = "draft_order_cancellation", {
                    "order_id": "ORD-1003" if "批准" in content else "ORD-1002",
                    "reason": "评测请求",
                }
            elif "库存" in content:
                name, arguments = "get_inventory", {"sku": "SKU-A100"}
            elif "制度" in content:
                name, arguments = "knowledge_search", {"question": content}
            else:
                name, arguments = "get_order", {"order_id": "ORD-1001"}
            message = SimpleNamespace(
                content="",
                tool_calls=[
                    SimpleNamespace(
                        id="m9-call",
                        function=SimpleNamespace(
                            name=name,
                            arguments=json.dumps(arguments, ensure_ascii=False),
                        ),
                    )
                ],
            )
        else:
            message = SimpleNamespace(content=last["content"], tool_calls=[])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _evaluate_agent() -> dict:
    business = InMemoryBusinessAdapter(
        {
            "ORD-1001": {"status": "已发货", "sku": "SKU-A100", "quantity": 2},
            "ORD-1002": {"status": "待处理", "sku": "SKU-A100", "quantity": 1},
            "ORD-1003": {"status": "待处理", "sku": "SKU-A100", "quantity": 1},
        },
        {"SKU-A100": {"available": 18, "warehouse": "深圳仓"}},
    )
    service = LangGraphAgentService(
        "mock-agent",
        lambda question: {"found": True, "question": question},
        SimpleNamespace(chat=SimpleNamespace(completions=_AgentCompletions())),
        business,
    )
    owner = "m9-owner"
    cases = []
    for case_id, question, tool in (
        ("agent-route-knowledge", "查询报销制度", "knowledge_search"),
        ("agent-route-order", "查询订单", "get_order"),
        ("agent-route-inventory", "查询库存", "get_inventory"),
    ):
        result = service.run(owner, case_id, question)
        cases.append(
            {"id": case_id, "passed": result.get("used_tools") == [tool]}
        )

    pending = service.run(owner, "agent-confirm", "取消订单")
    cases.append(
        {
            "id": "agent-draft-requires-confirmation",
            "passed": pending["status"] == "needs_confirmation"
            and business.get_order("ORD-1002")["status"] == "待处理",
        }
    )
    unauthorized = False
    try:
        service.confirm("other-user", "agent-confirm", True)
    except PermissionError:
        unauthorized = True
    cases.append(
        {
            "id": "agent-confirm-owner-enforced",
            "passed": unauthorized and business.get_order("ORD-1002")["status"] == "待处理",
        }
    )
    service.confirm(owner, "agent-confirm", False)
    cases.append(
        {
            "id": "agent-rejection-has-no-write",
            "passed": business.get_order("ORD-1002")["status"] == "待处理",
        }
    )
    service.run(owner, "agent-approve", "取消订单并批准")
    service.confirm(owner, "agent-approve", True)
    cases.append(
        {
            "id": "agent-approval-writes-after-confirmation",
            "passed": business.get_order("ORD-1003")["status"] == "已取消",
        }
    )
    service.close()
    return {
        "mode": "deterministic_mock",
        "case_count": len(cases),
        "passed": sum(case["passed"] for case in cases),
        "pass_rate": round(sum(case["passed"] for case in cases) / len(cases), 4),
        "cases": cases,
    }


def _markdown(report: dict) -> str:
    metrics = report["rag"]["metrics"]
    lines = [
        "# M9 Eval 报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 模式：{report['mode']}",
        f"- 数据集：{report['dataset_version']}（{report['rag']['case_count']}题）",
        f"- 模型配置：`{json.dumps(report['model_config'], ensure_ascii=False)}`",
        "",
        "## RAG 指标",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        *[f"| {key} | {value} |" for key, value in metrics.items()],
        "",
        "## 分阶段耗时（毫秒）",
        "",
        "| 阶段 | 平均 | P95 | 最大 |",
        "|---|---:|---:|---:|",
        *[
            f"| {stage} | {values['avg']} | {values['p95']} | {values['max']} |"
            for stage, values in report["rag"]["latency_ms"].items()
        ],
        "",
        "## Token 与成本",
        "",
        f"`{json.dumps(report['usage'], ensure_ascii=False)}`",
        "",
        "## Agent 场景",
        "",
        f"通过 {report['agent']['passed']}/{report['agent']['case_count']}。",
        "",
        "## 失败案例",
        "",
    ]
    rag_failures = [case for case in report["rag"]["cases"] if not case["passed"]]
    lines.extend(
        [
            f"- {case['id']}：期望拒答={case['expected_refusal']}，"
            f"实际拒答={case['actual_refusal']}，最高分={case['top_score']}"
            for case in rag_failures
        ]
    )
    lines.extend(
        f"- {case['id']}：Agent 场景未通过"
        for case in report["agent"]["cases"]
        if not case["passed"]
    )
    if not rag_failures and report["agent"]["passed"] == report["agent"]["case_count"]:
        lines.append("- 无")
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "Mock 报告不调用外部模型，耗时仅代表当前机器上的确定性本地适配器。真实模式才记录供应商返回的 LLM Token；Embedding 和 Rerank 接口未返回的用量不推测。",
            "",
        ]
    )
    return "\n".join(lines)


def run_evaluation(mode: str, output_dir: Path | None = None) -> tuple[dict, tuple[Path, Path]]:
    if mode not in {"mock", "real"}:
        raise ValueError("mode must be mock or real")
    dataset = load_dataset()
    rag, details = _evaluate_rag(dataset, mode)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "dataset_version": dataset["dataset_version"],
        "model_config": details["model_config"],
        "rag": rag,
        "agent": _evaluate_agent(),
        "usage": details["usage"],
    }
    output_dir = output_dir or DATASET_PATH.parent / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    json_path = output_dir / f"m9-{mode}-{stamp}.json"
    markdown_path = output_dir / f"m9-{mode}-{stamp}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    return report, (json_path, markdown_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the versioned RAG and Agent Eval")
    parser.add_argument("--mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    report, paths = run_evaluation(args.mode, args.output_dir)
    print(
        f"{report['mode']} Eval: RAG {report['rag']['passed']}/{report['rag']['case_count']}, "
        f"Agent {report['agent']['passed']}/{report['agent']['case_count']}"
    )
    print(paths[0])
    print(paths[1])


if __name__ == "__main__":
    main()
