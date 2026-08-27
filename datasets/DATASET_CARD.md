# Hy3 API Review Evaluator Dataset Card

## 来源与许可

本数据集的 OpenAPI 文档和审查报告全部由项目维护者为评估实验自行构造，不包含真实客户
接口、真实用户数据、真实凭据或从第三方数据集中复制的内容。仓库中的 bearer 字符串明确
标为 synthetic/example，不能用于任何服务。

## 构造与覆盖

- 20 个 OpenAPI 3.x 场景，其中 YAML 10 个、JSON 10 个。
- 难度分布：简单 6 个、中等 8 个、困难 6 个。
- 每个场景有 good、medium、bad 三档报告，共 60 条评测记录。
- 覆盖文档、安全、参数、响应、Schema、认证、可靠性、兼容性和证据质量。
- 对抗类型包括提示词注入、敏感标记外传、伪造证据、术语堆砌、冗长低信息报告和编造接口。

`scenarios.json` 记录每个场景的来源、构造方法、难度、类别、预期问题和对抗类型。
`manifest.jsonl` 记录每一份报告及其对应文档。`reference_tier` 表示构造时采用的质量档次，
不是人工评分。

## 人工标注状态

所有 `manual_scores`、`manual_total`、`annotator` 和 `annotated_at` 字段初始为 `null`。
在维护者实际完成 `annotation_app.py` 标注前，不得把这些字段解释为人工真值，也不得报告
自动评分与人工评分相关性。未完成人工标注的分析必须标记为 preliminary。

2026-08-27 已完成 33 条真实维护者评分，存于 `annotations/human_scores.csv`。通过 `record_id`
与 manifest 关联；其余 27 条没有人工分数。生成器的空人工字段保持不变，不能直接把 manifest
中的 null 当成最终标注状态。原本地 CSV 保留，分析副本使用匿名标注者编号，所有分数原样保留。

人工一致性实验使用 `annotation_protocol.json` 中冻结的 33 条分层子集：冻结时已有的 17 条
全部保留，新增 16 条在不读取人工分数的前提下选定。子集覆盖全部 20 个场景和六类对抗样本，
并在难度与构造档次上分别保持 11/11/11。60 条记录仍全部用于自动判别力评测。人工相关性和
MAE 必须明确报告 `N=33`，不得称为 60 条全量人工标注。

## 生成与校验

```powershell
.venv\Scripts\python scripts\build_dataset.py
.venv\Scripts\python scripts\validate_dataset.py
```

生成器是确定性的，不调用任何模型。重新生成后应得到相同的规范内容和记录数量。

## 已知边界

- 这是合成数据，不代表真实企业 API 的完整分布。
- good/medium/bad 是受控构造标签，不能替代独立人工质量分。
- 人工数据来自一名维护者，没有多人一致性指标；33 条报告共享 20 个场景，不能视为 33 份
  独立 API 文档。没有报告总体置信区间或统计显著性，分组结果只作描述性分析。
- 确定性规则只覆盖高价值常见问题，不是完整的 OpenAPI 规范验证器。
- 兼容性样本关注单份契约可观察到的演进风险，不替代新旧版本的完整差异分析。
