"""실물 노드로 그래프를 끝까지 돌린다 — 담당 A

`tests/test_r1_graph.py` 는 mock 노드로 **배선**을 본다. 이쪽은 `DEFAULT_NODES` 와 실제
에이전트를 그대로 쓰고 검색·LLM·색인만 fixture 로 막아 **계약이 맞물리는지** 본다.
research → orchestrator 계획 → Worker(실제 에이전트) → 병합 → 종합·보고서·제출본이 한 줄로
이어져야 한다.

대역 두 가지
- `stakeholder` 에이전트: 네트워크와 실행별 저장소를 쓴다. `scripts/r3_smoke.py` 가 따로 검증한다.
- `quality_eval`: 담당 C 가 구현하기 전까지 계약 형식(schema.QualityEval)의 통과 판정을 쓴다.
  사람 검토 재개 경로는 A4 에서 선택 단계(--human-review)로 다시 붙이며 그때 테스트를 쓴다.
"""

import json

import pytest

from src.agents import domain, market, maturity, research
from src.graph import build_graph, invoke
from tests import mock_nodes
from src.output import pdf
from src.schema import Assessment

# 노드별 허용 컬렉션이 다르다(§3). 시장 근거를 papers_core 에서 주면 검증이 막는다.
PAPERS = [
    {"chunk_id": "aaaaaaaaaaaa", "source_id": "turboquant-paper", "collection": "papers_core",
     "page": 3, "text": "TurboQuant reports 3.5-bit KV cache quantization.",
     "applies_to": ["TurboQuant"], "scope": "direct", "cosine_score": 0.7},
    {"chunk_id": "bbbbbbbbbbbb", "source_id": "itme-paper", "collection": "papers_core",
     "page": 7, "text": "ITME reports 1.80x throughput over an NVMe-oF baseline.",
     "applies_to": ["ITME"], "scope": "direct", "cosine_score": 0.6},
]
ECOSYSTEM = [
    {"chunk_id": "cccccccccccc", "source_id": "cxl-report", "collection": "ecosystem",
     "page": 2, "text": "CXL memory modules shipped by multiple vendors.",
     "applies_to": ["ITME"], "scope": "ecosystem", "cosine_score": 0.5},
    {"chunk_id": "dddddddddddd", "source_id": "quant-report", "collection": "ecosystem",
     "page": 4, "text": "Serving frameworks announce KV cache quantization support.",
     "applies_to": ["TurboQuant"], "scope": "ecosystem", "cosine_score": 0.5},
]
CHUNKS = PAPERS + ECOSYSTEM

PAPERS_TEXT = (
    "## TurboQuant\nTurboQuant 는 3.5비트 양자화를 보고한다 [aaaaaaaaaaaa].\n\n"
    "## ITME\nITME 는 NVMe-oF 대비 1.80배 처리량을 보고한다 [bbbbbbbbbbbb].\n\n"
    "근거 공백: 결합 실측 자료 없음"
)
MARKET_TEXT = (
    "## ① 시장 규모/성장성\n정량 리포트 미확인 [dddddddddddd].\n\n"
    "## ② 상용화/채택 현황\n발표 단계, 배포 확인 미확인 [cccccccccccc].\n\n"
    "## ③ 생태계 지지\n일반 CXL 근거이며 추론 특화 여부 미확인 [cccccccccccc].\n\n"
    "근거 공백: TurboQuant 운영 배포 사례 미확인"
)


class FakeSearch:
    def __init__(self, chunks):
        self.chunks = chunks

    def invoke(self, payload):
        return self.chunks


def stakeholder_stub(state):
    """R3 노드 자리. 형식만 같은 합성 Assessment 를 돌려준다."""
    run_id = state["run_id"]
    return {
        "stakeholder": {
            "status": "partial",
            "claims": [{"claim_id": "claim-stakeholder", "text": "개발자 통합 논의 확인",
                        "technology": "TurboQuant", "kind": "fact",
                        "evidence_ids": ["e-web-1"]}],
            "evidence": [{"evidence_id": "e-web-1", "source_id": "web-1", "run_id": run_id,
                          "collection": "web", "quote": "합성 인터뷰", "location": "paragraph:1",
                          "allowed_uses": ["stakeholder"]}],
            "sources": [{"source_id": "web-1", "run_id": run_id, "collection": "web",
                         "allowed_uses": ["stakeholder"], "title": "합성 출처",
                         "url": "https://example.org/synthetic", "published_at": None,
                         "retrieved_at": "2026-09-22"}],
            "gaps": [{"role": "stakeholder", "technology": "ITME", "item": "investors",
                      "reason": "원문 본문 근거 미확보"}],
        },
        "trace": [{"node": "stakeholder", "status": "partial"}],
    }


class FakeSynthesis:
    """synthesis 의 구조화 출력 LLM 자리."""

    def with_structured_output(self, schema):
        return self

    def invoke(self, prompt):
        from src.schema import Synthesis
        return Synthesis(
            agreements=["두 기술 모두 KV cache 메모리 병목을 완화한다."],
            conflicts=[{"perspective": "TRL", "why": "실증 환경의 범위가 다르다"}],
            gaps=["두 기술의 결합 실측 자료 미확인"],
            combination_hypothesis="개별 근거에서 도출한 추론이며 실측 결과가 아니다.",
        )


@pytest.fixture
def wired(monkeypatch, tmp_path):
    for module, chunks, text in ((research, PAPERS, PAPERS_TEXT),
                                 (maturity, PAPERS, PAPERS_TEXT),
                                 (domain, PAPERS, PAPERS_TEXT),
                                 (market, ECOSYSTEM, MARKET_TEXT)):
        monkeypatch.setattr(module, "bind_document_search",
                            lambda *a, _chunks=chunks, **k: FakeSearch(_chunks))
        monkeypatch.setattr(module, "run_node", lambda *a, _text=text, **k: _text)

    from src.agents import report as report_module
    from src.agents import synthesis as synthesis_module

    monkeypatch.setattr(synthesis_module, "get_llm", lambda: FakeSynthesis())
    monkeypatch.setattr(report_module, "model_name", lambda: "test-model")
    monkeypatch.setattr("src.rag.index.build", lambda: {"manifest": [{"id": "turboquant-paper"}]})
    # 원문(data/raw/)은 저작권 문제로 커밋하지 않는다. 해시 대조 자체는 test_r1_graph 가 따로 본다.
    monkeypatch.setattr("src.graph.verify_corpus", lambda manifest: [])
    monkeypatch.setattr(pdf, "render_pdf",
                        lambda md, out: (out.parent.mkdir(parents=True, exist_ok=True),
                                         out.write_bytes(b"%PDF fixture")))

    def run(state=None, **overrides):
        initial = state or {
            "run_id": "e2e-test", "run_status": "running", "trace": [],
            "run_config": {"domain": "데이터센터/클라우드", "technologies": ["TurboQuant", "ITME"],
                           "runs_dir": str(tmp_path), "started_at": "2026-09-22T00:00:00+00:00"},
        }
        overrides = {"quality_eval": mock_nodes.quality_eval("publish")} | overrides
        graph = build_graph(workers={"stakeholder": stakeholder_stub}, **overrides)
        return invoke(graph, initial), tmp_path / initial["run_id"]

    return run


def test_pipeline_produces_the_submission(wired):
    """계획 → Worker → 병합 → 종합 → 보고서 → 품질 평가(대역 통과) → 제출본."""
    final, directory = wired()

    assert "## REFERENCE" in final["report"]
    assert final["report_paths"][0].endswith("report.md")
    assert json.loads((directory / "submission.json").read_text())["generated"] is True
    assert final["validation"]["errors"] == []
    # Worker 결과는 Task 별 파일로 남는다
    assert sorted(p.name for p in (directory / "workers").iterdir()) == [
        "r0-domain_assessment-both.json", "r0-market-both.json",
        "r0-maturity-both.json", "r0-stakeholder-both.json"]


def test_every_assessment_merges_into_the_registries(wired):
    final, _ = wired()

    for node in ("research", "maturity", "market", "stakeholder", "domain_assessment"):
        Assessment.model_validate(final[node])

    # 합류 단계가 다섯 Assessment 의 출처를 모두 모은다 (§6·§9)
    assert {"turboquant-paper", "itme-paper", "cxl-report", "quant-report",
            "web-1"} <= set(final["source_registry"])
    assert final["evidence_registry"] and final["gaps"]


def test_gaps_are_merged_sequentially(wired):
    """§7 — 합류 후 수집, 종합 후 순차 병합.

    fixture 의 종합 공백("두 기술의 결합 실측 자료 미확인")은 노드가 이미 보고한
    "결합 실측 자료 없음" 을 되풀이한 것이라 걸러진다. §6 은 종합에게 공백을 *정리* 하라고
    한다 — 같은 내용을 말만 바꿔 다시 싣지 않는다.
    """
    final, _ = wired()
    roles = {gap["role"] for gap in final["gaps"]}
    items = [gap["item"] for gap in final["gaps"]]

    assert roles & {"research", "maturity", "market", "stakeholder", "domain"}
    assert "결합 실측 자료 없음" in items
    assert "두 기술의 결합 실측 자료 미확인" not in items


def test_invalid_claims_never_reach_the_report(wired):
    """claim_flags 가 invalid 인 Claim 은 종합·보고서·참고문헌에 들어가지 않는다(계획서 §5)."""
    first, _ = wired()
    rejected = [c["claim_id"] for c in first["market"]["claims"]]
    assert rejected and all(c.startswith("r0-market-both:") for c in rejected)

    # 에이전트가 run_id 로 Claim ID 를 만들므로 같은 run_id 로 다시 돌린다.
    state = {"run_id": first["run_id"], "run_status": "running", "trace": [],
             "claim_flags": {cid: {"status": "invalid", "by": "quality_eval"} for cid in rejected},
             "run_config": first["run_config"]}
    final, _ = wired(state=state)

    assert final["market"]["claims"] == []
    assert "발표 단계, 배포 확인 미확인" not in final["report"]
    kinds = {(g["role"], g["kind"]) for g in final["gaps"] if g.get("kind")}
    assert ("market", "invalid_evidence") in kinds
