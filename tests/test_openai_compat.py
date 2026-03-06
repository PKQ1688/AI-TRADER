from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_trader.llm.chan_vlm_review import build_review_payload, load_diagnostic_context
from ai_trader.llm.openai_compat import (
    extract_assistant_text,
    image_path_to_data_url,
    normalize_base_url,
)


class OpenAICompatTest(unittest.TestCase):
    def test_normalize_base_url(self) -> None:
        self.assertEqual(normalize_base_url("https://www.packyapi.com"), "https://www.packyapi.com/v1")
        self.assertEqual(normalize_base_url("https://www.packyapi.com/"), "https://www.packyapi.com/v1")
        self.assertEqual(normalize_base_url("https://www.packyapi.com/v1"), "https://www.packyapi.com/v1")

    def test_image_path_to_data_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "sample.png"
            image_path.write_bytes(b"fake-png")
            data_url = image_path_to_data_url(image_path)
            self.assertTrue(data_url.startswith("data:image/png;base64,"))

    def test_extract_assistant_text(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "{\"decision\":\"accept\"}"},
                        ]
                    }
                }
            ]
        }
        self.assertEqual(extract_assistant_text(payload), "{\"decision\":\"accept\"}")

    def test_build_review_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            diagnostic_dir = Path(tmpdir) / "diag"
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            (diagnostic_dir / "decision.json").write_text(
                '{"symbol":"BTC/USDT","timeframe_main":"4h","timeframe_sub":"1h","action":{"decision":"hold"},"signals":[]}',
                encoding="utf-8",
            )
            context = load_diagnostic_context(diagnostic_dir)
            payload = build_review_payload(
                model="gpt-5.4",
                diagnostic_context=context,
                image_paths=[],
                extra_instruction="只判断是否需要人工复核。",
            )
            self.assertEqual(payload["model"], "gpt-5.4")
            self.assertEqual(payload["messages"][0]["role"], "system")
            self.assertEqual(payload["messages"][1]["role"], "user")
            self.assertIn("decision/agreement/confidence/reasons/risks/cn_summary", payload["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
