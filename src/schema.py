"""공용 데이터 계약 — 담당: R1 (설계서 §7)

나머지 네 역할이 주고받는 dict 의 형식을 여기서 한 곳에 고정한다.

**State 에는 dict 로 넣는다.** 이 모델은 경계에서만 쓴다 — 노드 출력을 `collect_evidence`
가 한 번 검증하고, 그 뒤로는 dict 로 흐른다. LangGraph reducer·직렬화가 dict 기준이고
R3(`src/agents/stakeholder.py`)가 이미 dict 를 반환한다.

필드는 R3 실물 출력에서 역산했다 — `src/tools/web_store.py:179-194`,
`src/agents/stakeholder.py:144-188`. 웹 출처와 R2 색인 출처가 같은 모델을 통과해야 하므로
공통 필드만 필수로 두고 나머지는 선택이다.

R5 의 `src/agents/synthesis.py` 에 같은 이름의 `Synthesis`·`Conflict` 가 있다.
이 파일 것이 공용 계약이며 R5 는 여기서 import 해 쓴다(중복 정의를 남기지 않는다).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Technology = Literal["TurboQuant", "ITME"]
Kind = Literal["fact", "inference", "hypothesis"]
Status = Literal["completed", "partial", "failed"]
Collection = Literal["papers_core", "ecosystem", "context", "web"]
# 검색 관점 이름(R2 `perspectives`)과 맞춘다. State 필드는 domain_assessment 지만
# 역할 이름은 domain 이다. synthesis 도 종합 단계에서 Gap 을 남긴다(설계서 §7).
Role = Literal["research", "maturity", "market", "stakeholder", "domain", "synthesis"]


class Source(BaseModel):
    """원문 한 건. 웹(R3)·색인(R2) 두 출처가 같은 모델을 통과한다."""

    # 웹/색인 출처의 필드가 달라 extra 를 막지 않는다. 막으면 R2 색인 출처가 못 들어온다.
    model_config = ConfigDict(extra="allow")

    source_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    collection: Collection
    allowed_uses: list[Role] = Field(min_length=1)
    title: str = ""
    url: str = ""
    published_at: str | None = None   # 미확인은 null. 조회일로 대체하지 않는다.
    retrieved_at: str = ""
    sha256: str = ""
    snapshot_path: str = ""


class Evidence(BaseModel):
    """Source 원문의 특정 위치. Claim 은 반드시 이걸 거쳐 원문에 닿는다."""

    model_config = ConfigDict(extra="allow")

    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    collection: Collection
    quote: str = Field(min_length=1)
    location: str = Field(min_length=1)   # "paragraph:2:chars:0-195" | "page:7"
    allowed_uses: list[Role] = Field(min_length=1)
    snapshot_path: str = ""
    sha256: str = ""


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    technology: Technology
    kind: Kind
    evidence_ids: list[str] = Field(min_length=1)
    explanation: str = ""
    # 이해관계자 전용. maturity/market Claim 은 비운다.
    stakeholder_group: str = ""
    actor: str = ""
    statement_date: str | None = None
    context: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.kind != "fact" and not self.explanation:
            raise ValueError(f"{self.kind} 는 전제 explanation 이 필요하다: {self.claim_id}")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError(f"evidence_ids 중복: {self.claim_id}")
        return self


class Gap(BaseModel):
    """근거 공백. item 은 역할마다 형식이 달라 자유 문자열이다."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Role
    technology: Technology
    item: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Assessment(BaseModel):
    """평가 노드 하나의 산출. status=completed 는 구조 검사 통과일 뿐 내용 검토 완료가 아니다."""

    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = []
    sources: list[Source] = []
    evidence: list[Evidence] = []
    gaps: list[Gap] = []
    status: Status

    @model_validator(mode="after")
    def _linked(self):
        """인용 사슬이 자기 안에서 닫히는지 본다. collect_evidence 가 믿을 근거는 이것뿐이다."""
        source_ids = {s.source_id for s in self.sources}
        evidence_ids = {e.evidence_id for e in self.evidence}
        for e in self.evidence:
            if e.source_id not in source_ids:
                raise ValueError(f"evidence {e.evidence_id} 의 source_id 가 sources 에 없다")
        for c in self.claims:
            missing = [i for i in c.evidence_ids if i not in evidence_ids]
            if missing:
                raise ValueError(f"claim {c.claim_id} 가 없는 evidence 를 인용한다: {missing}")
        if self.status == "failed" and self.claims:
            raise ValueError("status=failed 인데 claims 가 남아 있다")
        return self


class Conflict(BaseModel):
    """한 관점에서 두 기술의 평가가 갈리는 지점."""

    perspective: Literal["TRL", "시장성", "이해관계자", "도메인"]
    favors: Literal["TurboQuant", "ITME", "판단보류"] = Field(
        description="이 관점의 근거가 상대적으로 유리하게 읽히는 쪽. 종합 승자가 아니다."
    )
    why: str = Field(description="관점이 갈리는 이유와 근거")


class Synthesis(BaseModel):
    agreements: list[str] = Field(description="네 관점이 공통으로 가리키는 사실")
    conflicts: list[Conflict] = Field(
        min_length=1, description="관점에 따라 평가가 갈리는 지점. 최소 1건 필수."
    )
    gaps: list[str] = Field(description="관점별 gaps 병합 + 결합 효과 등 실측 없는 항목")
    combination_hypothesis: str = Field(
        description="TurboQuant+ITME 결합 가설. 추론임을 명시. 본문이 아니라 시사점에만 쓴다."
    )


class Event(BaseModel):
    """trace 한 줄. node 또는 tool 중 하나는 반드시 있다."""

    model_config = ConfigDict(extra="allow")

    node: str = ""
    tool: str = ""
    status: str = "ok"
    attempt: int = 1
    timestamp: str = ""

    @model_validator(mode="after")
    def _named(self):
        if not (self.node or self.tool):
            raise ValueError("trace 한 줄에는 node 또는 tool 이 있어야 한다")
        return self
