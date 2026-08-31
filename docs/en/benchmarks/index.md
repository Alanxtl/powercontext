---
template: benchmark.html
page_type: benchmark
title: Benchmarks
description: How PowerContext performs on long-term conversational memory and repository-level software engineering evaluations.
hide:
  - navigation
  - toc
benchmark:
  hero:
    label: Benchmark evidence
    title:
      - Context,
      - under pressure.
    lead: Two public evaluations test long-term recall and repository-level software engineering with measurable outcomes.
    actions_label: Jump to a benchmark
    actions:
      - label: See LoCoMo
        target: locomo
      - label: See SWE-bench
        target: swe-bench
    visual_label: Key results from the LoCoMo and SWE-bench Pro evaluations
    results:
      - name: LoCoMo
        value: 90.78
        decimals: 2
        suffix: "%"
        display: 90.78%
        accessible: 90.78 percent accuracy
        metric: question-answer accuracy
      - name: SWE-bench Pro
        value: 86.73
        decimals: 2
        suffix: "%"
        display: 86.73%
        accessible: 86.73 percent task resolution
        metric: tasks resolved with PowerContext on
  orientation:
    title: Two benchmarks. Two different questions.
    lead: "Memory quality matters twice: first when an agent must recover what happened, then when it must use context to finish real work."
    tests:
      - name: LoCoMo
        question: Can the system remember a long-running conversation?
        answer: It measures direct recall, temporal reasoning, multi-step reasoning, and context grounded open-domain answers.
        target: locomo
        link: Explore the memory test
      - name: SWE-bench Pro
        question: Can an agent turn context into a working patch?
        answer: It gives Codex a real repository and issue, then grades the resulting patch with executable tests.
        target: swe-bench
        link: Explore the coding test
  locomo:
    title: LoCoMo tests long-term recall.
    lead: The public dataset contains long, multi-session conversations. PowerContext is evaluated on the answerable question set, where facts can be separated by many sessions.
    facts:
      - label: Conversations
        value: "10"
      - label: Scored questions
        value: 1,540
      - label: Question types
        value: "4"
    categories_label: LoCoMo question categories used in the PowerContext result
    categories:
      - name: Single-hop
        count: "841"
        description: Recover one fact from the conversation history.
      - name: Temporal
        count: "321"
        description: Reason about dates, order, and duration across sessions.
      - name: Multi-hop
        count: "282"
        description: Connect several facts before producing an answer.
      - name: Open-domain
        count: "96"
        description: Combine conversation evidence with general knowledge.
    results_title: One run, three ways to carry context.
    results_lead: Switch metrics to compare PowerContext, PowerMem, and placing the entire conversation in the prompt.
    tabs_label: LoCoMo result metric
    metrics:
      - id: accuracy
        label: Accuracy
        callout: +37.88 points
        callout_detail: above full context
        chart_label: Accuracy comparison. Higher values are better.
        direction: Higher is better.
        rows:
          - name: PowerContext
            display: 90.78%
            scale: 90.78
          - name: PowerMem
            display: 87.79%
            scale: 87.79
          - name: Full context
            display: 52.9%
            scale: 52.9
      - id: latency
        label: Search p95
        callout: 12.4x
        callout_detail: full context took longer
        chart_label: Search p95 latency comparison. Lower values are better.
        direction: Lower is better.
        rows:
          - name: PowerContext
            display: 1.38 s
            scale: 8.06
          - name: PowerMem
            display: 1.44 s
            scale: 8.41
          - name: Full context
            display: 17.12 s
            scale: 100
      - id: tokens
        label: Answer tokens
        callout: 93.7% fewer
        callout_detail: than full context
        chart_label: Answer tokens per question comparison. Lower values are better.
        direction: Lower is better.
        rows:
          - name: PowerContext
            display: about 1.65k
            scale: 6.35
          - name: PowerMem
            display: about 0.9k
            scale: 3.46
          - name: Full context
            display: 26k
            scale: 100
    scope_title: What this result covers
    scope: The 90.78% result is 1,398 correct answers from 1,540 questions in categories 1-4. It does not claim results for LoCoMo event summarization or multimodal dialogue generation.
  swe:
    title: SWE-bench Pro tests whether context changes the patch.
    lead: Each task starts from a real codebase and issue. Codex edits the repository, and the official task tests decide whether the patch resolves the problem.
    method:
      - title: Same task set
        description: 731 public v2 repository issues in both arms.
      - title: Same model
        description: gpt-5.6-sol with medium reasoning in Codex.
      - title: Controlled switch
        description: OFF disables plugins. ON enables the installed PowerContext plugin.
    scores:
      - label: PowerContext OFF
        count: 602
        rate: 82.35% resolved
        accessible: 602 of 731 tasks resolved with PowerContext off
        kind: "off"
      - label: PowerContext ON
        count: 634
        rate: 86.73% resolved
        accessible: 634 of 731 tasks resolved with PowerContext on
        kind: "on"
    delta: "+32"
    delta_label: more tasks resolved
    delta_accessible: PowerContext on resolved 32 more tasks
    caption: In the reported paired run, PowerContext ON improved task resolution by 4.38 percentage points.
    scope_title: How to read this result
    scope: This is a PowerContext paired evaluation on a pinned SWE-bench Pro public v2 dataset, not an official leaderboard submission. Agent runs are stochastic, so the numbers describe this run rather than a universal guarantee.
  reading:
    title: Read each result for the question it answers.
    lead: The two evaluations share a context theme, but their inputs, outputs, and graders are intentionally different.
    columns:
      dimension: Evaluation dimension
    rows:
      - dimension: What is tested
        locomo: Long-term conversational recall and reasoning
        swe: Repository-level issue resolution
      - dimension: Input
        locomo: Multi-session dialogue history and a question
        swe: A repository, an issue, and a clean task environment
      - dimension: Output
        locomo: A grounded natural-language answer
        swe: A code patch
      - dimension: Primary score
        locomo: Judge-rated answer accuracy
        swe: Official executable tests passed
  sources:
    title: Evidence and methodology
    lead: Follow the dataset, paper, harness, and published PowerContext figures from the original sources.
    items:
      - type: Paper
        label: Evaluating Very Long-Term Conversational Memory of LLM Agents
        href: https://aclanthology.org/2024.acl-long.747/
        description: The ACL 2024 paper that defines LoCoMo and its long-term memory tasks.
      - type: Dataset
        label: snap-research/locomo
        href: https://github.com/snap-research/locomo
        description: The public ten-conversation dataset and annotations.
      - type: Benchmark
        label: scaleapi/SWE-bench_Pro-os
        href: https://github.com/scaleapi/SWE-bench_Pro-os
        description: The public benchmark repository and official evaluation path.
      - type: Harness
        label: PowerContext evaluation console
        href: https://github.com/oceanbase/powercontext/tree/master/evaluation
        description: The pinned dataset, OFF and ON arms, isolated runner, and reporting contracts.
      - type: Results
        label: Published PowerContext benchmark figures
        href: https://github.com/oceanbase/powercontext#benchmarks
        description: The current project README values used on this page.
  cta:
    title: Inspect the system behind the scores.
    lead: PowerContext is open source. Review the implementation, evaluation harness, and contracts directly.
    label: View on GitHub
    href: https://github.com/oceanbase/powercontext
---
