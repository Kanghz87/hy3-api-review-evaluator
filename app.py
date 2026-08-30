"""Streamlit demo application for grounded OpenAPI review and report evaluation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import streamlit as st

from hy3_api_review_evaluator.budget import TokenBudgetLedger
from hy3_api_review_evaluator.config import Settings
from hy3_api_review_evaluator.errors import EvaluatorError
from hy3_api_review_evaluator.evaluator import evaluate_report_hybrid
from hy3_api_review_evaluator.export import build_csv_export, build_json_export
from hy3_api_review_evaluator.hy3_client import Hy3Client
from hy3_api_review_evaluator.models import EvaluationResult, Focus, ReviewReport
from hy3_api_review_evaluator.redaction import redact_text
from hy3_api_review_evaluator.reviewer import review_spec
from hy3_api_review_evaluator.rules import audit_spec
from hy3_api_review_evaluator.spec_loader import LoadedSpec, load_spec_bytes

FOCUS_LABELS = {
    "全部": Focus.ALL,
    "安全性": Focus.SECURITY,
    "设计质量": Focus.DESIGN,
    "可靠性": Focus.RELIABILITY,
    "兼容性": Focus.COMPATIBILITY,
    "开发者体验": Focus.DEVELOPER_EXPERIENCE,
}
SEVERITY_ICONS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "⚪",
}


async def _run_pipeline(
    spec: LoadedSpec,
    focus: Focus,
    settings: Settings,
    ledger: TokenBudgetLedger,
    on_stage: Callable[[str], None] | None = None,
) -> tuple[ReviewReport, EvaluationResult]:
    client = Hy3Client(settings, ledger)
    if on_stage:
        on_stage("review")
    report = await review_spec(
        spec,
        focus=focus,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    if on_stage:
        on_stage("judge")
    evaluation = await evaluate_report_hybrid(
        spec,
        report,
        max_model_chars=settings.max_model_chars,
        client=client,
    )
    return report, evaluation


def _result_key(spec_sha256: str, focus: Focus) -> tuple[str, str]:
    return spec_sha256, focus.value


def _local_table(spec: LoadedSpec) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "severity": item.severity,
                "category": item.category,
                "location": item.location,
                "title": item.title,
                "suggestion": item.suggestion,
            }
            for item in audit_spec(spec)
        ]
    )


def _render_results(
    spec: LoadedSpec,
    report: ReviewReport,
    evaluation: EvaluationResult,
    ledger: TokenBudgetLedger,
) -> None:
    st.subheader("Hy3 结构化审查报告")
    source_counts = {
        source: sum(finding.source == source for finding in report.findings)
        for source in ("deterministic", "hy3")
    }
    st.write(report.executive_summary)
    st.caption(
        f"模型：{report.model or '未报告'} · 本地规则 finding：{source_counts['deterministic']} · "
        f"Hy3 finding：{source_counts['hy3']}"
    )
    if report.limitations:
        with st.expander("报告局限性"):
            for limitation in report.limitations:
                st.write(f"- {limitation}")

    st.subheader("审查质量评估")
    score_col, verdict_col, model_col, usage_col = st.columns(4)
    score_col.metric("总分", f"{evaluation.total_score:.2f} / 100")
    verdict_col.metric(
        "结论",
        {
            "pass": "通过",
            "conditional_pass": "有条件通过",
            "fail": "不通过",
        }[evaluation.verdict],
    )
    model_col.metric("模型", report.model or "未报告")
    usage_col.metric(
        "本次 Hy3 token",
        report.usage.total_tokens + evaluation.judge_usage.total_tokens,
    )
    if evaluation.severe_failure:
        st.error("命中严重失败规则：" + "；".join(evaluation.severe_failure_reasons))

    dimension_rows = [
        {
            "维度": item.label_zh,
            "规则分": item.rule_score,
            "Hy3 judge 分": item.judge_score,
            "最终分": item.final_score,
            "权重": f"{item.weight}%",
            "依据": item.reason,
        }
        for item in evaluation.dimension_scores
    ]
    st.dataframe(pd.DataFrame(dimension_rows), use_container_width=True, hide_index=True)

    if evaluation.anti_gaming_flags:
        st.warning(
            "反作弊信号："
            + "；".join(f"{item.code}: {item.detail}" for item in evaluation.anti_gaming_flags)
        )

    st.subheader("审查发现")
    assessments = {item.finding_id: item for item in evaluation.finding_assessments}
    for finding in report.findings:
        icon = SEVERITY_ICONS[finding.severity]
        with st.expander(f"{icon} [{finding.severity.upper()}] {finding.title}"):
            source_label = "本地确定性规则" if finding.source == "deterministic" else "Hy3"
            st.write(f"**来源：** {source_label}")
            st.write(f"**类别：** {finding.category}")
            st.code(finding.location, language=None)
            st.write(f"**原因：** {finding.rationale}")
            st.write(f"**建议：** {finding.suggestion}")
            assessment = assessments.get(finding.finding_id)
            for index, evidence in enumerate(finding.evidence, start=1):
                st.write(f"**证据 {index}：** `{evidence.pointer}`")
                st.code(evidence.quote, language=None)
                if assessment and index <= len(assessment.evidence_checks):
                    check = assessment.evidence_checks[index - 1]
                    status = "✅ 匹配" if check.exists and check.quote_matches else "❌ 不匹配"
                    st.caption(f"{status} — {check.reason}")

    result_json = build_json_export(spec, report, evaluation)
    result_csv = build_csv_export(report, evaluation)
    json_col, csv_col = st.columns(2)
    json_col.download_button(
        "下载 JSON",
        result_json,
        file_name=f"{spec.sha256[:12]}-review-evaluation.json",
        mime="application/json",
        use_container_width=True,
    )
    csv_col.download_button(
        "下载 CSV",
        result_csv.encode("utf-8-sig"),
        file_name=f"{spec.sha256[:12]}-review-evaluation.csv",
        mime="text/csv",
        use_container_width=True,
    )
    with st.expander("Token 预算账本"):
        st.json(ledger.safe_snapshot())


def main() -> None:
    st.set_page_config(page_title="Hy3 API Review Evaluator", page_icon="🦏", layout="wide")
    st.title("Hy3 API Review Evaluator")
    st.caption("基于 Hy3 的 OpenAPI 智能审查与审查质量评估系统")
    st.info("本项目为 2026 腾讯犀牛鸟开源人才培养计划个人实战作品，并非腾讯官方发布的软件。")

    try:
        settings = Settings.from_env(env_file=Path(".env"))
    except EvaluatorError as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.header("审查配置")
        focus_label = st.selectbox("审查重点", list(FOCUS_LABELS))
        focus = FOCUS_LABELS[focus_label]
        st.write("Hy3 API Key：", "已配置" if settings.api_key else "未配置")
        st.write("单文件上限：", f"{settings.max_file_bytes / 1_000_000:.1f} MB")
        st.write("单次运行 token 上限：", f"{settings.default_run_token_budget:,}")

    uploaded = st.file_uploader(
        "上传 OpenAPI 3.x 文档",
        type=["yaml", "yml", "json"],
        help="文件只会被解析为数据；远程 $ref 不会被下载，文档中的命令不会被执行。",
    )
    if uploaded is None:
        st.write("请上传一份 YAML 或 JSON 格式的 OpenAPI 3.x 文档。")
        return
    if uploaded.size > settings.max_file_bytes:
        st.error(f"文件超过 {settings.max_file_bytes:,} 字节的应用限制，请压缩或拆分文档后重试。")
        return

    try:
        spec = load_spec_bytes(uploaded.getvalue(), uploaded.name, settings)
    except EvaluatorError as exc:
        st.error(str(exc))
        return

    metadata = st.columns(4)
    metadata[0].metric("标题", spec.title)
    metadata[1].metric("OpenAPI", spec.version)
    metadata[2].metric("操作数", spec.operation_count)
    metadata[3].metric("本地发现", len(audit_spec(spec)))
    if spec.external_refs:
        st.warning(
            f"检测到 {len(spec.external_refs)} 个外部 $ref；为安全起见未下载，完整性会受限。"
        )

    st.subheader("确定性检查")
    local_frame = _local_table(spec)
    if local_frame.empty:
        st.success("确定性规则未发现问题。")
    else:
        st.dataframe(local_frame, use_container_width=True, hide_index=True)

    if st.button(
        "运行 Hy3 审查并评估报告",
        type="primary",
        disabled=not settings.api_key,
        use_container_width=True,
    ):
        st.session_state.pop("latest_result", None)
        ledger = TokenBudgetLedger(
            Path("results/private/token-ledger.json"),
            total_limit=settings.total_token_budget,
            run_limit=settings.default_run_token_budget,
        )
        try:
            with st.status("正在准备 Hy3 审查……", expanded=True) as status:
                stage_labels = {
                    "review": "第 1/2 步：Hy3 正在生成结构化审查报告……",
                    "judge": "第 2/2 步：正在校验证据并由 Hy3 评价报告质量……",
                }

                def update_stage(stage: str) -> None:
                    status.update(label=stage_labels[stage], state="running", expanded=True)

                report, evaluation = asyncio.run(
                    _run_pipeline(spec, focus, settings, ledger, on_stage=update_stage)
                )
                status.update(label="Hy3 审查和质量评估已完成", state="complete", expanded=False)
        except EvaluatorError as exc:
            safe = redact_text(str(exc), exact_secrets=[settings.api_key or ""])
            st.error(safe)
        except Exception as exc:
            safe_type = redact_text(type(exc).__name__, exact_secrets=[settings.api_key or ""])
            st.error(f"应用发生已隔离的错误：{safe_type}")
        else:
            st.session_state["latest_result"] = (
                _result_key(spec.sha256, focus),
                report,
                evaluation,
                ledger,
            )

    latest = st.session_state.get("latest_result")
    if latest and latest[0] == _result_key(spec.sha256, focus):
        _render_results(spec, latest[1], latest[2], latest[3])


if __name__ == "__main__":
    main()
