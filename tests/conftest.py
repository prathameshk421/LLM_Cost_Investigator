"""Test-wide settings: never touch the real warehouse file or call log."""

import os
import tempfile

os.environ.setdefault("LLMCI_WAREHOUSE", ":memory:")
os.environ.setdefault("LLM_CALL_LOG", os.path.join(tempfile.gettempdir(), "llmci-test-calls.jsonl"))
