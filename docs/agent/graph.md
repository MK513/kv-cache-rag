# 그래프 (A4)

`build_graph().get_graph().draw_mermaid()` 출력. 실선은 고정 엣지, 점선은 조건부 엣지
(`dispatch`·`route_after_eval`·`route_after_review`).

- `orchestrator -.-> worker` 는 `Send` 로 계획의 Task 수만큼 Worker 를 띄운다.
- `human_review` 는 선택 단계다(`--human-review`). 그래프는 이 노드 뒤에서 멈추고(`interrupt_after`),
  `app.py --resume <run_id>` 가 `apply_review` 부터 잇는다.
- `quality_eval -.-> report` 에는 publish guard 구버전 재생성도 포함된다. 횟수는
  `guard_retry_count`, 상한 `orchestrator.max_guard_retries`(2). 닿으면 publish 가 failed 로 끝낸다.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	setup(setup)
	research(research)
	orchestrator(orchestrator)
	collect_evidence(collect_evidence)
	synthesis(synthesis)
	report(report)
	quality_eval(quality_eval)
	human_review(human_review<hr/><small><em>__interrupt = after</em></small>)
	apply_review(apply_review)
	publish(publish)
	worker(worker)
	__end__([<p>__end__</p>]):::last
	__start__ --> setup;
	apply_review -.-> publish;
	apply_review -.-> synthesis;
	collect_evidence --> synthesis;
	human_review --> apply_review;
	orchestrator -.-> publish;
	orchestrator -.-> worker;
	quality_eval -.-> human_review;
	quality_eval -.-> orchestrator;
	quality_eval -.-> publish;
	quality_eval -.-> report;
	quality_eval -.-> synthesis;
	report --> quality_eval;
	research --> orchestrator;
	setup --> research;
	synthesis --> report;
	worker --> collect_evidence;
	publish --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```
