# 实验复现指南

返回[项目首页](../README.md) · [配置说明](configuration.md) · [分析报告](../reports/analysis.md)

本项目区分两类复现：**从保存数据重算指标**不调用模型；**重新采集模型评分**会调用 Hy3，
产生新的 token 用量，结果也可能因模型波动而改变。复现指标不要求重新支付模型调用费用。

以下命令从仓库根目录执行，使用 Windows PowerShell 的解释器路径；macOS / Linux 将
`.venv\Scripts\python.exe` 替换为 `.venv/bin/python`。

## 1. 离线校验与复算

```powershell
.venv\Scripts\python.exe scripts\validate_dataset.py
.venv\Scripts\python.exe scripts\validate_results.py
.venv\Scripts\python.exe evaluation\run_evaluation.py
.venv\Scripts\python.exe evaluation\run_boundary_checks.py
.venv\Scripts\python.exe evaluation\run_human_agreement.py --check
```

数据集校验检查场景、档次、预期证据和人工子集；结果校验检查模型输出、覆盖范围、排序和
用量汇总。当前实现 0.2.1 的确定性评测写入 `results/0.2.1/`，不重写历史基线；人工一致性 `--check` 重算 v1.0
人工对照并比较保存结果，
不会修改文件。对仓库保存的输入，应复现 20/20 严格排序和 N=33 的人工一致性结果。

`run_human_agreement.py` 专用于冻结的 v1.0 结果，不会将新版分数拼入旧实验。
`run_hybrid_evaluation.py --summary-only` 现在仅刷新 `results/0.2.1/` 中已采集的当前实现记录；
没有新版记录时应先停止，不要复制 v1.0 的模型结果到新版目录充数。

## 2. 结果文件

| 文件 | 内容 |
| --- | --- |
| `results/preliminary-local-records.csv` | 60 条确定性基线及六维分数 |
| `results/preliminary-local-summary.json` | 基线排序、对抗性和分组汇总 |
| `results/hybrid-records.jsonl` | 60 条真实 Hy3 judge 结果、证据校验与用量 |
| `results/hybrid-summary.json` | 混合评估指标与人工子集状态 |
| `results/stability-records.jsonl` | 6 条报告各 3 次的重复评分记录 |
| `results/stability-summary.json` | 组内总体标准差与稳定性汇总 |
| `datasets/annotations/human_scores.csv` | 33 条匿名化人工评分 |
| `results/human-agreement-records.csv` | 人工、基线与混合分数的逐条对照 |
| `results/human-agreement-summary.json` | 相关性、误差、分组结果和来源文件指纹 |
| `results/v1.1/local-records.csv`、`local-summary.json` | 0.2.0 实现对原始 60 条报告的本地分数及变更列表 |
| `results/v1.1/boundary-checks.json` | 12 个补充边界检查，没有新人工评分 |
| `results/v1.1/*-smoke.json` | 选定补充样本的真实 Hy3 端到端结果 |
| `results/0.2.1/local-records.csv`、`local-summary.json` | 0.2.1 实现的实际离线回归结果 |
| `results/0.2.1/boundary-checks.json` | 0.2.1 下的 12 个补充边界检查 |

直接位于 `results/` 根目录的上述结果均是历史 v1.0 实验。历史快照可能保留生成时的 `preliminary` 状态。历史人工结果以
`human-agreement-summary.json` 的明确范围为准；不能把 33 条子集描述为 60 条全量人工标注。

## 3. 真实 Hy3 调用

先按[配置说明](configuration.md)提供 API Key。下列在线命令均使用 **results/0.2.1/ 独立输出路径**，
若该目录没有对应结果就会真实调用模型。旧版 60 条完成记录不会使新版脚本免于收费。
脚本具有断点或缓存行为；若复用了缓存，不能将其描述为一次新实验。

输出包含 `implementation_version`，不能将 0.2.0 的旧模型结果作为 0.2.1 的缓存继续使用。
目前 0.2.1 没有已采集的真实模型结果，执行下列在线命令会收费。

### 端到端验证

```powershell
.venv\Scripts\python.exe scripts\run_hy3_smoke.py --run-token-budget 80000
.venv\Scripts\python.exe scripts\run_hy3_smoke.py --boundary clean-contract --run-token-budget 60000
.venv\Scripts\python.exe scripts\run_hy3_smoke.py --boundary parameter-ref --run-token-budget 60000
```

第一条验证原始注入样本，后两条验证无问题报告及参数引用，分别缓存，不必为复现而全部运行。
默认复用对应已保存的验证结果。确需重新验证时，在归档旧结果并确认实际调用费用后，可添加
`--force`；一次成功流程包含 Hy3 reviewer 和 judge 两次调用，会覆盖该脚本的结果文件。

### 混合评测

```powershell
.venv\Scripts\python.exe evaluation\run_hybrid_evaluation.py --pilot --run-token-budget 100000
.venv\Scripts\python.exe evaluation\run_hybrid_evaluation.py --run-token-budget 180000
```

固定 pilot 包含 6 条记录，覆盖三档质量、三种难度及对抗样本。脚本逐条追加结果，并按
`record_id` 跳过已完成记录；汇总会提供平均用量与剩余记录的用量投影。确认 pilot 输出和
预算后再运行后续批次，避免在格式、配置或预算不合适时扩大调用。

### 重复稳定性

```powershell
.venv\Scripts\python.exe evaluation\run_stability.py --repeats 3 --run-token-budget 70000
```

固定选择 6 条报告，按“记录 ID + 重复序号”跳过已完成项。标准差使用每组实际评分的总体
标准差，不以构造档次或人为设定值替代重复调用。

### 完整重新采样的注意事项

当前脚本使用固定输出路径，没有独立的 `--output-dir` 参数。需要从头采集新结果时：

1. 当前实现结果已与历史版本隔离；如需再次采集 0.2.1，使用独立实验副本，归档该副本的
   `results/0.2.1/hybrid-*.json*` 和 `results/0.2.1/stability-*.json*`。保留所有历史结果。
2. 保留输入文档、报告、Rubric、人工协议和标注不变，记录实验版本与配置；若改动这些条件，
   应作为新的评测设定报告，不能继续沿用旧实验结论。
3. 保留已有的私有 token 账本。独立副本不共享账本，需要额外核算跨副本累计用量。
4. 依次执行 pilot、后续混合评测和稳定性评测；调用中断后保留新结果并断点续跑。
5. 对新采集结果重新生成新版基线和汇总；旧人工评分只作跨版本描述性对照，不能解释为
   新版人工验证。新的人工作业必须另存版本、协议和记录；不要混用旧来源指纹与新评分。

归档和重新采样会改变工作副本内容，应保留原始实验文件的可恢复副本。示例运行额度只是
请求前的上限，可能因模型输出长度和上下文不同而不足；预算不足时脚本应停止，而不是清空
历史用量继续请求。

## 4. 人工验证与解释边界

人工评分工具和合并命令见[标注指南](annotation_guide.md)。`reference_tier` 是报告的受控
构造档次，不是人工真值；未标注记录不得用档次、模型评分或插值补齐。

- **排序准确率**：同一场景必须严格满足 good > medium > bad，同分算失败。
- **Spearman**：衡量自动分与人工分的秩相关，同分使用平均秩；不等于准确率。
- **MAE**：自动总分与人工总分的平均绝对差，保持 0～100 分量纲。
- **重复标准差**：描述同一报告多次评分的波动；低波动不代表判断正确。
- **对抗识别率**：以“严重失败或总分低于 65”为检测条件，仅统计标记为报告级对抗的记录。

人工子集在已有标注后冻结并保留所有既有记录，不是事前独立预注册的随机留出测试集。单人
标注、共享场景和较小分组样本限制了结论的外推范围。已有报告中的不利结果、分歧和失败
案例应一并保留，不应通过挑选样本或改写参考分数提高指标。

v1.1 尚未重新收集人工标注或重跑全量混合/稳定性实验。新版本地分数与旧标注的相关性和 MAE
在 `comparison_to_v1_0_human_labels` 中单列，不填入新版 `human_score_*` 字段，分析仍为
preliminary。无需删除或重做原先 33 条真实评分。
