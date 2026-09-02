import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.run_eval import DATASET_PATH, load_dataset, run_evaluation


class M9EvalTest(unittest.TestCase):
    def test_dataset_has_required_coverage(self) -> None:
        dataset = load_dataset(DATASET_PATH)

        self.assertEqual(dataset["dataset_version"], "m9-v1")
        self.assertGreaterEqual(len(dataset["cases"]), 30)
        self.assertEqual(
            {case["category"] for case in dataset["cases"]},
            {
                "exact",
                "paraphrase",
                "keyword",
                "cross_document",
                "irrelevant",
                "adversarial",
            },
        )

    def test_mock_eval_writes_computed_rag_and_agent_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report, paths = run_evaluation("mock", Path(directory))
            saved = json.loads(paths[0].read_text(encoding="utf-8"))
            markdown = paths[1].read_text(encoding="utf-8")

        self.assertEqual(saved, report)
        self.assertEqual(report["mode"], "mock")
        self.assertEqual(report["dataset_version"], "m9-v1")
        self.assertEqual(report["rag"]["case_count"], len(report["rag"]["cases"]))
        self.assertEqual(report["agent"]["passed"], report["agent"]["case_count"])
        self.assertIn("retrieval_recall_at_3", report["rag"]["metrics"])
        self.assertIn("mrr_at_3", report["rag"]["metrics"])
        self.assertIn("refusal_accuracy", report["rag"]["metrics"])
        self.assertIn("citation_validity", report["rag"]["metrics"])
        self.assertIn("source_match_rate", report["rag"]["metrics"])
        self.assertEqual(
            set(report["rag"]["latency_ms"]),
            {"embedding", "retrieval", "rerank", "llm"},
        )
        self.assertEqual(report["usage"]["status"], "not_applicable_in_mock_mode")
        self.assertIn("m9-v1", markdown)
        self.assertIn("失败案例", markdown)

    def test_real_eval_requires_explicit_paid_call_switch(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RUN_REAL_EVAL=1"):
                run_evaluation("real")


if __name__ == "__main__":
    unittest.main()
