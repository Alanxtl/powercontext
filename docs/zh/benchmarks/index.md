---
template: benchmark.html
page_type: benchmark
title: Benchmark
description: PowerContext 在长程对话记忆与真实仓库软件工程评测中的表现。
hide:
  - navigation
  - toc
benchmark:
  hero:
    label: Benchmark 评测证据
    title:
      - 把上下文能力，
      - 放进压力测试。
    lead: 两项公开评测，分别检验长程记忆与真实仓库软件工程，并给出可核对的结果。
    actions_label: 跳转到具体评测
    actions:
      - label: 查看 LoCoMo
        target: locomo
      - label: 查看 SWE-bench
        target: swe-bench
    visual_label: LoCoMo 与 SWE-bench Pro 评测的关键结果
    results:
      - name: LoCoMo
        value: 90.78
        decimals: 2
        suffix: "%"
        display: 90.78%
        accessible: 问答准确率 90.78%
        metric: 问答准确率
      - name: SWE-bench Pro
        value: 86.73
        decimals: 2
        suffix: "%"
        display: 86.73%
        accessible: 任务解决率 86.73%
        metric: 开启 PowerContext 后的任务解决率
  orientation:
    title: 两个 Benchmark，回答两个不同问题。
    lead: Agent 先要找回发生过什么，再要把上下文用于真实工作。两项评测分别检验这两层能力。
    tests:
      - name: LoCoMo
        question: 系统能记住一段长期对话吗？
        answer: 评测直接回忆、时间推理、多步推理，以及结合对话证据的开放域回答。
        target: locomo
        link: 查看记忆评测
      - name: SWE-bench Pro
        question: Agent 能把上下文变成可用补丁吗？
        answer: 给 Codex 一个真实仓库和 Issue，再用可执行测试评判最终补丁。
        target: swe-bench
        link: 查看编码评测
  locomo:
    title: LoCoMo 检验长程记忆。
    lead: 公开数据集包含跨多个 Session 的长对话。PowerContext 在可回答问题集上接受评测，相关事实可能相隔很多轮对话。
    facts:
      - label: 长对话
        value: "10"
      - label: 计分问题
        value: 1,540
      - label: 问题类型
        value: "4"
    categories_label: PowerContext 结果覆盖的 LoCoMo 问题类型
    categories:
      - name: 单跳回忆
        count: "841"
        description: 从长对话中找回一条明确事实。
      - name: 时间推理
        count: "321"
        description: 推理跨 Session 的日期、顺序与持续时间。
      - name: 多跳推理
        count: "282"
        description: 连接多条事实后生成答案。
      - name: 开放域问答
        count: "96"
        description: 结合对话证据与通用知识回答。
    results_title: 同一次评测，三种上下文方式。
    results_lead: 切换指标，对比 PowerContext、PowerMem，以及把完整对话直接放入 Prompt 的方式。
    tabs_label: LoCoMo 结果指标
    metrics:
      - id: accuracy
        label: 准确率
        callout: +37.88 个百分点
        callout_detail: 相比完整上下文
        chart_label: 准确率对比，越高越好。
        direction: 越高越好。
        rows:
          - name: PowerContext
            display: 90.78%
            scale: 90.78
          - name: PowerMem
            display: 87.79%
            scale: 87.79
          - name: 完整上下文
            display: 52.9%
            scale: 52.9
      - id: latency
        label: 搜索 p95
        callout: 12.4 倍
        callout_detail: 完整上下文耗时更多
        chart_label: 搜索 p95 延迟对比，越低越好。
        direction: 越低越好。
        rows:
          - name: PowerContext
            display: 1.38 秒
            scale: 8.06
          - name: PowerMem
            display: 1.44 秒
            scale: 8.41
          - name: 完整上下文
            display: 17.12 秒
            scale: 100
      - id: tokens
        label: 回答 Token
        callout: 减少 93.7%
        callout_detail: 相比完整上下文
        chart_label: 单个问题的回答 Token 对比，越低越好。
        direction: 越低越好。
        rows:
          - name: PowerContext
            display: 约 1.65k
            scale: 6.35
          - name: PowerMem
            display: 约 0.9k
            scale: 3.46
          - name: 完整上下文
            display: 26k
            scale: 100
    scope_title: 结果覆盖范围
    scope: 90.78% 来自类别 1-4 的 1,540 个问题，其中答对 1,398 个。该结果不代表 LoCoMo 的事件总结或多模态对话生成任务。
  swe:
    title: SWE-bench Pro 检验上下文是否改变补丁结果。
    lead: 每个任务都从真实代码库与 Issue 开始。Codex 修改仓库，再由任务自带的正式测试判断补丁是否解决问题。
    method:
      - title: 相同任务集
        description: OFF 与 ON 都运行 public v2 的 731 个仓库问题。
      - title: 相同模型
        description: Codex 使用 gpt-5.6-sol，推理等级为 medium。
      - title: 受控开关
        description: OFF 禁用 Plugin，ON 启用已安装的 PowerContext Plugin。
    scores:
      - label: PowerContext OFF
        count: 602
        rate: 解决率 82.35%
        accessible: 关闭 PowerContext 时，731 个任务中解决 602 个
        kind: "off"
      - label: PowerContext ON
        count: 634
        rate: 解决率 86.73%
        accessible: 开启 PowerContext 时，731 个任务中解决 634 个
        kind: "on"
    delta: "+32"
    delta_label: 个任务被额外解决
    delta_accessible: 开启 PowerContext 后多解决 32 个任务
    caption: 在这次配对运行中，PowerContext ON 将任务解决率提高了 4.38 个百分点。
    scope_title: 如何理解这组结果
    scope: 这是 PowerContext 在固定 SWE-bench Pro public v2 数据集上的配对评测，不是官方排行榜提交。Agent 运行存在随机性，因此数字描述的是本次运行，而不是对所有运行的普遍保证。
  reading:
    title: 用每项结果回答它真正评测的问题。
    lead: 两项评测都与上下文有关，但输入、输出与评分方式不同，不能把两个分数直接横向比较。
    columns:
      dimension: 评测维度
    rows:
      - dimension: 评测内容
        locomo: 长程对话记忆与推理
        swe: 仓库级 Issue 修复
      - dimension: 输入
        locomo: 多 Session 对话历史与一个问题
        swe: 代码仓库、Issue 与干净任务环境
      - dimension: 输出
        locomo: 有对话依据的自然语言答案
        swe: 代码补丁
      - dimension: 主要评分
        locomo: Judge 判定的答案准确率
        swe: 正式可执行测试是否通过
  sources:
    title: 证据与评测方法
    lead: 从原始论文、数据集、评测工具与 PowerContext 发布结果核对页面中的结论。
    items:
      - type: 论文
        label: Evaluating Very Long-Term Conversational Memory of LLM Agents
        href: https://aclanthology.org/2024.acl-long.747/
        description: 定义 LoCoMo 与长程记忆任务的 ACL 2024 论文。
      - type: 数据集
        label: snap-research/locomo
        href: https://github.com/snap-research/locomo
        description: 包含十段长对话与标注的公开数据集。
      - type: Benchmark
        label: scaleapi/SWE-bench_Pro-os
        href: https://github.com/scaleapi/SWE-bench_Pro-os
        description: 公开 Benchmark 仓库与正式评测路径。
      - type: 评测工具
        label: PowerContext evaluation console
        href: https://github.com/oceanbase/powercontext/tree/master/evaluation
        description: 固定数据集、OFF 与 ON 分组、隔离 Runner 和报告约束。
      - type: 结果
        label: PowerContext 已发布的 Benchmark 数据
        href: https://github.com/oceanbase/powercontext#benchmarks
        description: 本页面使用的当前项目 README 数据。
  cta:
    title: 查看分数背后的系统。
    lead: PowerContext 完全开源，可直接检查实现、评测工具与核心契约。
    label: 前往 GitHub
    href: https://github.com/oceanbase/powercontext
---
