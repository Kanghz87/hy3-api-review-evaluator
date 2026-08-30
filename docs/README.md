# 项目文档

返回[项目首页](../README.md)。

## 使用与配置

- [配置说明](configuration.md)：服务连接、输入限制、token 预算和本地诊断。
- [演示流程](demo_script.md)：两分钟应用演示的操作步骤与讲解安排。

## 方法与实验

- [评分标准](rubric.md)：六个维度的 0～4 分判定条件及严重失败规则。
- [实验复现指南](evaluation.md)：离线复算、真实模型调用、结果文件和重新采样注意事项。
- [人工标注指南](annotation_guide.md)：盲标界面、参考判断方法及规范化导出。
- [数据卡](../datasets/DATASET_CARD.md)：合成场景、受控报告与人工子集的来源和限制。
- [实验分析](../reports/analysis.md)：实测指标、人工分歧、失败模式和能力边界。
- [Demo 前审计](../reports/pre_demo_audit.md)：最新工程复验与正式样本真实 Hy3 预检。

## 工程与安全

- [安全说明](security.md)：威胁模型、防护措施和剩余风险。
- [本地工程验收记录](../reports/release_validation.md)：构建、干净安装和数据完整性检查。
- [CI 工作流](../.github/workflows/ci.yml)：Python 3.11 / 3.12 自动化检查。
