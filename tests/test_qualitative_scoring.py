import unittest
import json
from unittest.mock import patch

from cis_fin.qualitative_scoring import RUBRIC, score_qualitative_evidence
from cis_fin.settings import Settings


class QualitativeScoringTests(unittest.TestCase):
    def test_rubric_covers_zero_to_five(self):
        self.assertEqual(set(RUBRIC), set(range(6)))

    def test_scoring_disables_thinking_and_parses_structured_response(self):
        response_payload = {
            "done_reason": "stop",
            "message": {
                "content": json.dumps(
                    {
                        "score": 4,
                        "rationale": "Governance and scaled use are established.",
                        "evidence_quote": "board-approved AI governance program",
                        "confidence": 0.9,
                    }
                )
            },
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode()

        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode())
            return FakeResponse()

        with patch("cis_fin.qualitative_scoring.urlopen", side_effect=fake_urlopen):
            result = score_qualitative_evidence(
                Settings.from_env({}), "AI governance?", "Board-approved AI governance program.", 2, "technology"
            )

        self.assertFalse(captured["body"]["think"])
        self.assertGreaterEqual(captured["body"]["options"]["num_predict"], 512)
        self.assertEqual(result["score"], 4)

    def test_empty_token_limited_responses_raise_actionable_error(self):
        response_payload = {"done_reason": "length", "message": {"content": "", "thinking": "..."}}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode()

        with patch("cis_fin.qualitative_scoring.urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(ValueError, "output token limit"):
                score_qualitative_evidence(
                    Settings.from_env({}), "AI governance?", "Board-approved AI governance program.", 2, "technology"
                )


if __name__ == "__main__":
    unittest.main()
