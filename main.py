"""Thin compatibility shim — prefer: python -m llm_cost_investigator.cli"""

from llm_cost_investigator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
