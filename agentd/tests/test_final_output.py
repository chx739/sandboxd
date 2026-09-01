from __future__ import annotations

import json
import unittest

from agentd.app.graph import parse_final_diagnosis


class FinalOutputTest(unittest.TestCase):
    def test_extracts_fenced_json_after_prose(self) -> None:
        payload = {
            "summary": "container exits with code 1",
            "rootCause": "hardcoded exit 1",
            "severity": "warning",
            "evidence": ["model claimed evidence"],
            "injectionDetected": True,
            "deniedActions": ["model claimed denial"],
            "recommendation": "fix the command",
            "planId": "model-claimed-plan",
        }
        content = (
            "I have enough evidence.\n\n```json\n"
            + json.dumps(payload)
            + "\n```"
        )

        diagnosis = parse_final_diagnosis(content)

        self.assertEqual(diagnosis.summary, "container exits with code 1")
        self.assertEqual(diagnosis.root_cause, "hardcoded exit 1")
        self.assertTrue(diagnosis.injection_detected)
        self.assertEqual(diagnosis.evidence, [])
        self.assertEqual(diagnosis.denied_actions, [])
        self.assertIsNone(diagnosis.plan_id)

    def test_prefers_last_valid_diagnosis_object(self) -> None:
        content = (
            '{"summary":"draft"}\n'
            '{"summary":"final","rootCause":"exit 1","recommendation":"report"}'
        )

        diagnosis = parse_final_diagnosis(content)

        self.assertEqual(diagnosis.summary, "final")
        self.assertEqual(diagnosis.root_cause, "exit 1")

    def test_nested_evidence_does_not_replace_outer_diagnosis(self) -> None:
        content = json.dumps(
            {
                "summary": "outer summary",
                "rootCause": "outer cause",
                "severity": "warning",
                "evidence": [
                    {
                        "source": "read_file",
                        "summary": "nested evidence summary",
                    }
                ],
                "recommendation": "outer recommendation",
            }
        )

        diagnosis = parse_final_diagnosis(content)

        self.assertEqual(diagnosis.summary, "outer summary")
        self.assertEqual(diagnosis.root_cause, "outer cause")
        self.assertEqual(diagnosis.recommendation, "outer recommendation")

    def test_rejects_text_without_diagnosis_json(self) -> None:
        with self.assertRaises(ValueError):
            parse_final_diagnosis("plain prose only")


if __name__ == "__main__":
    unittest.main()
