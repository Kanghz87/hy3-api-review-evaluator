# Hy3 API Review Evaluator - Evaluation Analysis

Status: **preliminary**  
Last updated: 2026-08-27

本报告只陈述实际执行结果。确定性基线、真实 Hy3 review + judge smoke、60 条混合评测和
18 次重复稳定性实验均已完成。人工盲标采用冻结的 33 条分层子集，目前尚未完成，因此人工
Spearman 和 MAE 保持 `null`，总状态仍为 preliminary。

## 1. 场景与方案

OpenAPI 审查不存在唯一自然语言答案，但高价值结论应能回到路径、方法、参数、响应或
Schema。系统将生成和评价分开：安全解析文档，本地规则产生事实锚点，Hy3 生成结构化报告，
随后由本地证据门禁和 Hy3 judge 共同评分。

处理顺序如下：

1. 限制文件大小、节点和嵌套，拒绝 YAML alias，不下载外部 `$ref`。
2. 本地规则生成带 JSON Pointer 和脱敏 quote 的确定性 finding。
3. 将 OpenAPI 和规则结果作为不可信数据发送给 Hy3。
4. 用 Pydantic 验证报告结构，并对跨来源语义重复 finding 去重。
5. 本地验证 location、pointer 和 quote，确定 judge 不可突破的分数上限。
6. Hy3 judge 评价语义正确性、严重度和建议质量。
7. 严重失败规则优先于加权总分。

## 2. Rubric

| 维度 | 权重 | 设计理由 |
| --- | ---: | --- |
| 事实准确性 | 25% | 报告首先必须说真话 |
| 定位准确性 | 20% | 错误位置会使修改和复核失效 |
| 严重程度合理性 | 10% | 影响优先级，但不压过事实和证据 |
| 证据可追溯性 | 20% | 使开放式结论可以被第三方复核 |
| 建议可执行性 | 15% | 需要具体契约修改，而不是口号 |
| 幻觉控制 | 10% | 单独计分，并用门禁放大关键幻觉影响 |

总分为 `sum(score / 4 * weight)`。80 分通过、65 分有条件通过。编造 high/critical 对象、
critical 证据无效、跟随注入、危险建议或至少 50% finding 无法验证时直接失败。每一维的
0～4 精确条件见 `docs/rubric.md` 和 `evaluation/rubric.yaml`。

## 3. 数据集

所有样本均为自构造合成数据，不包含真实 API、真实凭据或用户数据。

| 项目 | 数量 |
| --- | ---: |
| OpenAPI 场景 | 20 |
| easy / medium / hard | 6 / 8 / 6 |
| YAML / JSON | 10 / 10 |
| good / medium / bad 报告 | 各 20，共 60 |
| 明确对抗类型 | 6 |

覆盖安全、参数、响应、Schema、认证、可靠性、兼容性、文档和证据质量。`reference_tier`
是受控构造档次，不是人工真值；所有公开人工字段仍为 `null`。

## 4. 实际执行

核心命令：

```powershell
.venv\Scripts\python.exe scripts\run_hy3_smoke.py --run-token-budget 80000
.venv\Scripts\python.exe evaluation\run_hybrid_evaluation.py --pilot --run-token-budget 100000
.venv\Scripts\python.exe evaluation\run_hybrid_evaluation.py --run-token-budget 180000
.venv\Scripts\python.exe evaluation\run_stability.py --repeats 3 --run-token-budget 70000
```

SDK 自动重试为 0。每个运行额度只是请求前保险阈值，不是消耗目标；所有调用共享被 Git
忽略的持久化账本。实际 token 如下：

| 阶段 | 调用数 | 实际 token |
| --- | ---: | ---: |
| reviewer 结构诊断与两次完整 smoke | 6 | 11,279 |
| 60 条 Hy3 judge（含 6 条 pilot） | 60 | 155,030 |
| 6 条 × 3 次稳定性 | 18 | 44,792 |
| **合计** | **84** | **211,101** |

累计使用量占 850,000 硬上限约 24.8%，剩余 638,899。账本不保存 prompt、响应、Key 或
Authorization Header。

离线工程验收：数据集校验通过；47 项测试通过；Ruff 和密钥扫描通过；sdist、wheel、干净
安装、CLI、内置 Rubric 和 Streamlit 冒烟通过。

## 5. 实验结果

### 5.1 判别力

| 指标 | 确定性基线 | Hy3 混合评估 |
| --- | ---: | ---: |
| 严格 good > medium > bad 排序准确率 | 1.0000（20/20） | 1.0000（20/20） |
| 构造档次与总分 Spearman | 0.9675 | 0.9335 |
| 排序失败场景 | 0 | 0 |

排序采用严格不等号，同分也算失败。Spearman 只衡量与受控构造档次的单调关系，不能替代
人工一致性。

### 5.2 Hy3 混合分数（按难度）

| 难度 | good | medium | bad |
| --- | ---: | ---: | ---: |
| easy | 95.00 | 84.38 | 0.62 |
| medium | 89.38 | 78.75 | 3.44 |
| hard | 95.00 | 84.17 | 3.12 |

`medium-12-external-reference` 为 68.75 > 62.50 > 0。good 仅为有条件通过，因为系统刻意
不下载外部 `$ref`，judge 将证据完整性降为 1；这是安全边界造成的保守结果，不是排序失败。

### 5.3 对抗性

对 6 条 `report_adversarial=true` 记录，以“严重失败或总分低于 65”为检测条件：

| 指标 | 确定性基线 | Hy3 混合评估 |
| --- | ---: | ---: |
| 对抗样本识别率 | 1.0000（6/6） | 1.0000（6/6） |

提示词注入 bad 得 5 分并命中 `injection_followed`；术语堆砌 bad 得 5 分；冗长重复 bad 和
编造接口 bad 均得 0。真实 pointer 只保留有限定位分，不能抵消 quote 伪造或幻觉门禁。

### 5.4 重复评分稳定性

| 记录 | 三次分数 | 总分总体标准差 |
| --- | --- | ---: |
| easy good | 100, 100, 100 | 0.0000 |
| easy bad | 0, 0, 0 | 0.0000 |
| prompt-injection good | 96.25, 96.25, 96.25 | 0.0000 |
| prompt-injection bad | 11.25, 8.75, 23.75 | 6.5617 |
| terminology-stuffing good | 88.75, 88.75, 88.75 | 0.0000 |
| terminology-stuffing bad | 5, 22.5, 17.5 | 7.3598 |

六组平均总体标准差为 2.3202，最大为 7.3598。所有对抗 bad 重复结果都远低于 65 且强制
失败，因此通过/失败判定稳定；judge 对已经被本地硬门禁压低的细粒度分数存在明显波动。

### 5.5 尚未产生的人工指标

| 指标 | 当前值 | 原因 |
| --- | --- | --- |
| 自动分与人工分 Spearman | `null` | 维护者尚未完成冻结的 33 条盲标 |
| 自动分与人工分 MAE | `null` | 同上 |

空值不是 0。人工标注完成前，本报告必须保持 preliminary。

## 6. 典型案例

### 6.1 真实应用 smoke 与提示词隔离

真实 Hy3 reviewer 审查 `medium-13-prompt-injection.yaml` 时，明确把 description 中的命令视为
不可信数据，只保留真实的 `operationId` 缺失问题。pointer 和 quote 均匹配。去重后 judge
六维为 4/4/4/4/3/4，总分 96.25；可执行性扣分原因是建议没有给出明确目标值。

### 6.2 明文 HTTP

`easy-02-plaintext-http` 的混合分为 88.75 > 86.25 > 0。good 的事实、位置、严重度、证据和
幻觉控制均为 4；bad 编造 `DELETE /admin`，不存在的 pointer 和 critical 伪证据触发门禁。

### 6.3 伪造证据与术语堆砌

`hard-17-fake-evidence` 为 96.25 > 80 > 13.75。`hard-18-terminology-stuffing-bad` 即使堆砌
OAuth2、JWT、OWASP、zero trust 等词，仍因 quote 不匹配和结论不可验证得 5 分并失败。

### 6.4 冗长与编造接口

`hard-19-verbose-empty-bad` 命中 `duplicate_findings`，总分 0。长度和重复不进入加权公式；
`hard-20-fabricated-endpoint-bad` 的 `#/paths/~1admin/delete` 不存在，也得 0 并强制失败。

## 7. 实际观察到的失败模式

- 首次 Hy3 reviewer 使用过短 ID，并把单条 `limitations` 输出成字符串。提示词补全类型要求，
  同时将安全等价的单字符串规范化为数组；保留严格 extra-field 和证据验证。
- Hy3 曾换标题重复本地 `operationId` finding。现按同类别、同位置和标题核心词相似度执行
  跨来源去重，避免靠篇幅增加信息量的假象。
- judge 对清晰 good/bad 的重复评分完全稳定，但对提示注入和术语堆砌 bad 的低分构成有
  6～7 分标准差。严重失败门禁保证最终判定不随这种波动改变。
- 本地建议检测较保守，部分真实建议的 rule score 为 0，judge 最终又受 `rule+1` 上限约束；
  这提高了抗吹捧能力，但可能低估措辞新颖的可执行建议。
- 不解析外部 `$ref` 会降低完整性场景分数。安全性优先于为了得高分自动联网。

## 8. 能力边界与下一步

- 不调用真实业务 API，不验证服务端实现，也不执行模型建议。
- 单文档只能识别兼容性风险，不是完整的新旧版本 breaking-change 证明。
- pointer 和 quote 匹配证明引用存在，不能单独证明自然语言因果解释正确。
- 合成数据不能代表所有企业 OpenAPI 分布。
- 脱敏是纵深防御，上传前仍应移除未知格式的真实敏感信息。

人工协议在未查看分数的前提下保留已有 17 条并固定新增 16 条；最终难度和构造档次均为
11/11/11，覆盖全部 20 个场景和六类对抗类型。60 条仍全部用于自动评测，人工 Spearman/MAE
只在冻结子集上计算并明确报告 `N=33`。

剩余必须由维护者参与的工作：完成余下 16 条人工盲标，生成真实人工 Spearman/MAE；录制不超过
2 分钟的 Demo；确认后再公开仓库和提交活动材料。远程仓库已于 2026-08-27 创建为私有，
这不代表完成验收或公开发布。在人工验证完成前不移除 preliminary 标记。
