"""Orchestrator-Workers 조정·평가 계층.

  planner.py · dispatch.py · selection.py   담당 A (agent/ow-orchestration)
  evaluator.py · versions.py · quality_rules.py   담당 C (agent/ow-quality)

C 의 모듈은 agent/ow-quality 에서 구현했다(docs/agent/lane-c.md). A 의 모듈은 A 레인에서 새로 만든다.
"""
