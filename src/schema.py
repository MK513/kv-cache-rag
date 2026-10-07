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

# "both" 는 두 기술을 함께 다루는 Claim·Gap 용이다. 설계서 §3 의 검색 도구 계약이
# 이미 쓰는 값이며, 종합 단계가 관점별로 갈리는 지점을 따로 정리한다(§5).
Technology = Literal["TurboQuant", "ITME", "both"]
Kind = Literal["fact", "inference", "hypothesis"]
Status = Literal["completed", "partial", "failed"]
Collection = Literal["papers_core", "ecosystem", "context", "web"]
# 검색 관점 이름(R2 `perspectives`)과 맞춘다. State 필드는 domain_assessment 지만
# 역할 이름은 domain 이다. synthesis 도 종합 단계에서 Gap 을 남긴다(설계서 §7).
Role = Literal["research", "maturity", "market", "stakeholder", "domain", "synthesis"]

# ── Orchestrator-Workers 계약 상수 (계획서 §3, 코드 검토 D2·D3) ──
# 역할 이름 목록·기술 목록이 graph·report·reference·review·validate·app 에 따로 있던 것을
# 여기 하나로 둔다. 각 레인은 자기 파일의 사본을 이것으로 바꾼다.
TECHS = ["TurboQuant", "ITME"]
# State 키 이름이다(역할 이름 `domain` 이 아니라 `domain_assessment`). Worker 가 실행하는 4관점.
PERSPECTIVES = ["maturity", "market", "stakeholder", "domain_assessment"]
# research 는 Orchestrator 앞의 고정 선행 단계라 Worker 가 아니다.
ASSESSMENT_ROLES = ["research"] + PERSPECTIVES
Perspective = Literal["maturity", "market", "stakeholder", "domain_assessment"]
# 근거 공백의 원인. 커버리지는 조사 이력이 있는 evidence_gap 만 인정한다(계획서 §7-1).
GapKind = Literal["evidence_gap", "execution_gap", "invalid_evidence"]


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
    # ── Orchestrator-Workers 계약 (기본값이 있어 기존 생성 코드는 그대로 통과한다) ──
    kind: GapKind = "evidence_gap"
    attempted_queries: list[str] = []   # Worker 어댑터가 실제 실행한 질의를 기록한다(LLM 이 채우지 않는다)
    missing_info: str = ""
    impact: str = ""


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
    """관점에 따라 평가 내용이 실제로 달라지는 지점."""

    perspective: Literal["TRL", "시장성", "이해관계자", "도메인"]
    why: str = Field(description="어떤 근거 또는 조건 때문에 관점별 평가가 달라지는지. "
                                 "기술 우열이나 종합 승자를 판정하지 않는다.")


class Synthesis(BaseModel):
    """종합 결과. §5 — 점수를 합쳐 순위를 매기지 않는다."""

    agreements: list[str] = Field(default_factory=list,
                                  description="여러 관점에서 공통으로 확인되는 사실 또는 방향")
    # §5 — 상충하는 의견을 억지로 만들지 않는다. 그래서 빈 목록을 허용한다.
    conflicts: list[Conflict] = Field(default_factory=list,
                                      description="관점별 평가가 실제로 달라지는 지점")
    gaps: list[str] = Field(default_factory=list,
                            description="관점별 근거 공백과 아직 실증되지 않은 항목")
    combination_hypothesis: str = Field(
        description="TurboQuant+ITME 결합 가설. 공개 결합 실험이 없으면 추론임을 명시한다(§2·§5).")


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


# ══ Orchestrator-Workers 계약 (계획서 §3·§6·§7) ════════════════════════════════
# 0단계 계약 커밋에서 정의만 한다. 사용처는 각 레인이 연결한다.
#   A(조정): Task·WorkerResult    B(Worker): Task 입력·worker_meta 반환
#   C(출력): Agreement·GroundedConflict·GroundedSynthesis·ReportManifest·QualityEval


class Task(BaseModel):
    """Orchestrator 가 만든 조사 계획의 한 항목. `Send("worker", state | {"task": ...})` 로 전달된다."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)          # f"r{round}-{perspective}-{tech|both}[-{n}]" — 결과 식별자
    perspective: Perspective
    technologies: list[Literal["TurboQuant", "ITME"]] = Field(min_length=1)
    focus: str = Field(min_length=1)            # 이번에 답할 하위 질문
    queries: list[str] = Field(min_length=1, max_length=3)
    rationale: str = ""                         # 왜 이 Task 가 필요한가 (research·사전 조사·품질 평가 근거)
    round: int = Field(ge=0)                    # 0 = 최초 계획, 1·2 = 재계획
    web_budget: int = Field(default=0, ge=0)    # stakeholder 만. dispatch 전에 남은 예산에서 할당


class WorkerMeta(BaseModel):
    """에이전트가 반환 dict 의 `worker_meta` 키로 돌려주는 조사 이력 (B → A)."""

    model_config = ConfigDict(extra="forbid")

    attempted_queries: list[str] = []
    retrieved: int = Field(default=0, ge=0)       # 유사도 임계값 이상 검색 결과 수
    web_searches_used: int = Field(default=0, ge=0)


class WorkerResult(BaseModel):
    """`worker` 노드가 `worker_results` 에 누적하는 한 건. 식별자는 `task.task_id`."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    round: int = Field(ge=0)                    # = task.round. 계획 버전 — 최신 결과 선택에 쓴다
    status: Status
    assessment: dict | None = None              # 대상 기술 부분, Claim ID 재부여 후 (Assessment 형식)
    error_kind: Literal["", "transient", "permanent"] = ""
    error: str = ""
    meta: WorkerMeta = WorkerMeta()
    result_path: str = ""                       # runs/<run_id>/workers/<task_id>.json

    @model_validator(mode="after")
    def _consistent(self):
        if self.round != self.task.round:
            raise ValueError("round 는 task.round 와 같아야 한다")
        if self.status == "failed" and (self.assessment is not None or not self.error_kind):
            raise ValueError("failed 결과는 assessment 가 없고 error_kind 가 있어야 한다")
        if self.status != "failed" and self.assessment is None:
            raise ValueError("성공 결과에는 assessment 가 있어야 한다")
        return self


class Agreement(BaseModel):
    """종합의 일치 항목. 근거 추적을 위해 참조한 Claim 을 남긴다(Groundedness L2)."""

    text: str = Field(min_length=1)
    claim_ids: list[str] = []


class GroundedConflict(Conflict):
    """관점별 차이 + 참조 Claim."""

    claim_ids: list[str] = []


class GroundedSynthesis(BaseModel):
    """C 가 `Synthesis` 를 옮겨 갈 목표 형식. 전환 전까지 `Synthesis` 는 그대로 쓴다."""

    agreements: list[Agreement] = Field(default_factory=list)
    conflicts: list[GroundedConflict] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    combination_hypothesis: str = ""
    synthesis_version: int = Field(default=1, ge=1)
    round: int = Field(default=0, ge=0)          # 어느 계획 라운드의 결과로 만들었나


class ManifestGap(BaseModel):
    perspective: str
    technology: Technology
    kind: GapKind
    item: str


class ReportManifest(BaseModel):
    """보고서에 **실제로 표시된** 것. Markdown 과 같은 보고서 모델 객체에서 함께 만든다."""

    report_version: int = Field(ge=1)
    based_on_synthesis_version: int = Field(ge=1)
    sections: dict[str, list[str]] = {}          # 절 이름 → 표시 Claim id
    gaps: list[ManifestGap] = []                  # 표시된 근거 공백과 종류
    cited_evidence_ids: list[str] = []
    references: list[str] = []                    # REFERENCE 에 실린 source_id
    disclosed_exceptions: list[str] = []          # 보고서에 공개한 편향 예외
    compaction: int = Field(default=0, ge=0)
    partial: bool = False


class Verdict(BaseModel):
    status: Literal["pass", "fail", "accepted_exception", "skipped"]
    reasons: list[str] = []
    target_cells: list[str] = []                  # 재계획 대상 "perspective:tech"


class QualityEval(BaseModel):
    """`quality_eval` 노드 출력. `route_after_eval` 은 `next` 만 읽는다."""

    passed: bool
    next: Literal["orchestrator", "synthesis", "report", "human_review", "publish"]
    verdicts: dict[str, Verdict] = {}             # groundedness_l1·groundedness_l2·neutrality·bias·coverage·structure
    evaluated_report_version: int = Field(ge=1)
    pdf_path: str = ""
    pdf_sha256: str = ""
    scope: dict = {}                              # Judge 검사 범위 n/N, 층별 건수
