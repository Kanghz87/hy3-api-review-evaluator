# 人工标注操作指南

人工一致性指标必须来自真实人工评分。当前仓库不会预填或推测你的分数。

## 启动

在项目根目录打开 PowerShell：

```powershell
.venv\Scripts\streamlit.exe run annotation_app.py
```

浏览器打开后：

1. 在“标注者代号”填写一个固定代号，例如 `owner-01`。不需要填写真实姓名。
2. 页面每次只显示一份 OpenAPI 和一份审查报告，不显示 good/medium/bad 档次。
3. 展开六个评分维度，阅读 0～4 的精确条件，然后分别选择整数分。
4. 可在备注中记录拿不准的原因；备注不会提高或降低自动分。
5. 点击“保存本条并进入下一条”。页面会自动记录进度。
6. 完成页面协议规定的 33 条后下载备份 CSV。

页面第三栏“复制材料”会把当前盲化样本的 OpenAPI、待评审报告和空白六维评分模板合并到一个
代码框中。鼠标移到代码框右上角即可复制全部内容，也可以下载 TXT。材料不包含构造档次、
预期问题或自动分。它只用于个人笔记和核对；如果交给其他 AI 代评分，就不能作为真实人工
标注。

本地进度保存在 `datasets/annotations/human_scores.local.csv`。该文件已加入 `.gitignore`，
不会被意外提交。不要手工把构造档次当作分数填写，也不要先查看自动评分结果。

人工一致性采用冻结的 33 条分层子集，而不是要求人工重复标完全部 60 条。协议保留冻结时已经
完成的 17 条，再增加 16 条低重复记录；最终 easy/medium/hard 各 11 条，good/medium/bad 各
11 条，覆盖全部 20 个场景和六类对抗样本。固定清单及不查看分数的选择规则记录在
`datasets/annotation_protocol.json`。页面不会显示其中的构造档次。

同一场景的不同报告可能指出相同问题，这是用相同 OpenAPI 对照报告质量所必需的；评分对象是
“当前报告是否准确、可追溯、可执行”，不是问题本身是否新颖。冻结协议已尽量减少此类重复，
并避免为了得到更好看的相关性而事后挑选样本。

## 标注建议

- 建议每轮只完成 8～10 条、控制在 15～20 分钟。疲劳时立即停止，避免
  标准漂移；页面会从断点继续。
- 遇到无法判断的事实时，按 Rubric 中“无法验证”的明确档位评分，不要猜测文档之外的实现。
- 只评价当前报告。报告没有指出某问题时，不要替报告补写内容。
- 长报告、术语多或语气强烈都不直接加分。
- 指针和 quote 必须在左侧 OpenAPI 中实际存在才算证据。

## 每条记录到底怎么打分

不要先给报告一个“总体印象分”。按下面顺序逐项检查：

1. 数出报告中不重复的实质性 finding 数量 `N`。
2. 对每项 finding 判断：事实是否存在、location 是否正确、quote 是否匹配、severity 是否合理、
   suggestion 是否能直接改 OpenAPI。
3. 分别计算各维度满足条件的 finding 比例。例如 3 项中有 2 项事实正确，比例约 67%，事实
   准确性的起始档是 2；还要继续确认没有该档禁止的编造或高危无证据结论。
4. 每个维度从 4 分向 0 分检查，选择“全部文字条件都满足”的最高档。不能因为整体看起来不错
   就把 2 分提高到 3 分。
5. 边界情况选较低分，在备注写明“哪项不确定、为什么”。这是真实人工标注，不是答题错误。

JSON Pointer 中 `~1` 表示 `/`、`~0` 表示 `~`。例如：

```text
#/paths/~1items/get/responses/200
```

表示 `/items` 路径的 GET 方法下 `responses -> 200` 节点。

建议可执行性可以用“四要素”检查：修改动作、目标元素、修改后目标状态、必要约束。四项齐全且
安全才可能得 4；只写“加强安全”“完善文档”通常只能得 1 或 0。

严重度可按影响理解：`critical` 是证据明确的灾难性安全/数据影响；`high` 是重大真实问题；
`medium` 是会影响集成、可靠性或维护的问题；`low` 多为文档/治理缺口；`info` 是非缺陷提示。
如果只是在相邻等级之间拿不准，可以按 Rubric 对“相差一级”的比例条件评分并留下备注。

### 两个虚构示例

示例 A：文档真实存在 `http://api.example.test`；报告准确定位 `#/servers/0/url`，quote 匹配，
将其标为 high，并建议把该节点改成明确的 HTTPS 地址且只提供 TLS 服务。若没有其他 finding，
六个维度都可能为 4。

示例 B：文档没有 `/admin`，报告却声称 `DELETE /admin` 存在 critical 漏洞，并提供不存在的
pointer 和 quote，只建议“加强安全”。事实、定位、严重度、证据、建议和幻觉控制均应为 0，
并且命中严重失败规则。

## 完成后的下一步

完成协议内全部 33 条后，先备份页面下载的 CSV，然后在项目根目录运行：

```powershell
.venv\Scripts\python.exe scripts\merge_annotations.py
.venv\Scripts\python.exe evaluation\run_human_agreement.py
.venv\Scripts\python.exe evaluation\run_hybrid_evaluation.py --summary-only
.venv\Scripts\python.exe evaluation\run_human_agreement.py --check
```

脚本只会在记录数、ID、六维整数分、加权总分、标注者代号和时间戳全部通过校验后，生成
`datasets/annotations/human_scores.csv`。原文件不变，副本只把标注者代号统一映射为 `human-01`
等匿名编号；六维评分、总分和时间戳不变。上述离线命令不读取 API Key、不创建 Hy3 客户端，
不会增加调用或 token 消耗。

结果保存在 `results/human-agreement-summary.json` 和逐条对照表
`results/human-agreement-records.csv`。`--check` 会重算并核对已保存结果以及来源文件指纹。
报告必须注明 `N=33`、单人标注及分层子集范围，不能写成 60 条全量人工标注。原始逐条模型
输出及历史基线、稳定性快照中的 preliminary 字段保留生成时状态，不改写历史实验数据。

当前维护者的 33 条标注已于 2026-08-27 完成，人工一致性结果已真实计算。此前缺少人工标注
时使用的 preliminary 不再用于当前汇总；这不代表多人一致性验证或公开发布已经完成。
