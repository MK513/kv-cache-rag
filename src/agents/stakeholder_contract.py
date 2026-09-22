"""R3 LLM draft validation only; shared src/schema.py remains owned by R1.

These are input drafts, not a competing State/Assessment schema. Python builds
provenance and IDs after checking drafts against this run's fetched evidence.
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Technology = Literal['TurboQuant', 'ITME']
Group = Literal['competitors', 'adopters', 'developers', 'investors']


class ClaimDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    text: str = Field(min_length=1)
    technology: Technology
    kind: Literal['fact', 'inference', 'hypothesis']
    evidence_ids: list[str] = Field(min_length=1)
    explanation: str = ''
    stakeholder_group: Group
    actor: str = Field(min_length=1)
    statement_date: str | None = None
    context: str = Field(min_length=1)

    @model_validator(mode='after')
    def premise_explanation(self):
        if self.kind != 'fact' and not self.explanation:
            raise ValueError('inference/hypothesis requires premise explanation')
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError('duplicate evidence IDs')
        return self


class GapDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    technology: Technology
    item: Group
    reason: str = Field(min_length=1)


class StakeholderDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    claims: list[ClaimDraft]
    gaps: list[GapDraft]
