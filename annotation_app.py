"""Blind Streamlit interface for genuine human rubric annotation."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from hy3_api_review_evaluator.annotation import (
    blinded_order,
    build_copy_bundle,
    display_id,
    load_annotation_protocol,
    read_completed,
    save_annotation,
    weighted_total,
)
from hy3_api_review_evaluator.models import ReviewReport
from hy3_api_review_evaluator.rubric import DIMENSION_ORDER, load_rubric

ROOT = Path(__file__).parent
MANIFEST = ROOT / "datasets" / "manifest.jsonl"
PROTOCOL = ROOT / "datasets" / "annotation_protocol.json"
LOCAL_ANNOTATIONS = ROOT / "datasets" / "annotations" / "human_scores.local.csv"


def _records() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _render_report(report: ReviewReport) -> None:
    st.write(report.executive_summary)
    if not report.findings:
        st.warning("这份报告没有 finding。请仍按 Rubric 评分。")
    for finding in report.findings:
        with st.expander(f"[{finding.severity.upper()}] {finding.title}"):
            st.write(f"**类别:** {finding.category}")
            st.write(f"**位置:** `{finding.location}`")
            st.write(f"**原因:** {finding.rationale}")
            st.write(f"**建议:** {finding.suggestion}")
            for evidence in finding.evidence:
                st.write(f"**证据:** `{evidence.pointer}`")
                st.code(evidence.quote, language=None)


def _render_scoring_help() -> None:
    with st.expander("第一次标注？先看 5 步判断法", expanded=True):
        st.markdown(
            """
1. **先数 finding**：报告有几项实质性结论，就逐项核查几项；重复表述只算一项。
2. **查事实与位置**：左侧是否真的存在它说的接口、方法、字段或响应？JSON Pointer 是否指向
   最小相关节点？其中 `~1` 代表 `/`，例如 `#/paths/~1items/get` 就是 `/items` 的 GET。
3. **查证据**：quote 的值是否能在该 pointer 对应节点找到，并且足以证明结论？只写一个
   pointer、伪造原文或引用无关节点都不算有效证据。
4. **查严重度与建议**：等级是否符合实际影响？建议是否同时写了修改动作、修改对象、目标状态
   和必要约束？“加强安全”“完善文档”本身不能直接实施。
5. **选最高的完全满足档**：不要凭总体印象取中间分。若 4 分条件有任何一项不满足，就检查
   3 分；继续向下，直到找到所有条件都满足的档位。不确定时选较低档，并在备注说明。
"""
        )
        st.markdown("**比例速算（仍要同时满足该维度的安全例外条件）：**")
        st.table(
            {
                "可验证比例": ["100%", "90%～不足100%", "60%～不足90%", "大于0%～不足60%", "0%"],
                "通常起始档": [4, 3, 2, 1, 0],
            }
        )
        st.markdown(
            """
**严重度速查：** `critical` 只用于证据明确、可造成灾难性安全或数据影响的问题；`high` 是
真实且影响重大的安全/契约错误；`medium` 多为会造成集成、可靠性或 SDK 问题的缺陷；`low`
多为文档或治理质量缺口；`info` 不是实际缺陷。边界拿不准可以允许相差一级，但要按比例条件
评分并写备注。

**特别情况：** 报告完全没有 finding，而左侧明显有问题时，事实、定位、严重度、证据和建议
不能因为“没有说错话”而得高分；幻觉控制可单独判断它是否编造了内容。
"""
        )


def main() -> None:
    st.set_page_config(page_title="人工盲标 - Hy3 API Review Evaluator", layout="wide")
    st.title("OpenAPI 审查报告人工盲标")
    st.info("页面不会显示 good/medium/bad 构造档次。请只依据 OpenAPI、报告和 Rubric 独立评分。")
    _render_scoring_help()
    annotator = st.text_input(
        "标注者代号",
        help="可使用自定义代号，不需要填写真实姓名。相同代号会得到稳定的样本顺序。",
    ).strip()
    if not annotator:
        st.write("请先输入标注者代号。")
        return

    manifest_records = _records()
    protocol = load_annotation_protocol(PROTOCOL, manifest_records)
    selected_ids = set(protocol["selected_record_ids"])
    records = blinded_order(
        [record for record in manifest_records if record["record_id"] in selected_ids], annotator
    )
    all_completed = read_completed(LOCAL_ANNOTATIONS)
    completed = {
        record_id: row for record_id, row in all_completed.items() if record_id in selected_ids
    }
    pending = [record for record in records if record["record_id"] not in completed]
    progress = len(completed) / len(records) if records else 0
    st.progress(progress, text=f"已完成 {len(completed)} / {len(records)}")
    st.caption(
        "本轮人工协议已冻结：保留既有标注并只补充低重复、高覆盖记录。"
        "建议每轮只完成 8～10 条，之后会从断点继续。"
    )
    if not pending:
        st.success(f"协议规定的 {len(records)} 条记录已全部完成。请运行合并和指标脚本。")
        st.download_button(
            "下载本地标注 CSV",
            LOCAL_ANNOTATIONS.read_bytes(),
            file_name="human_scores.local.csv",
            mime="text/csv",
        )
        return

    record = pending[0]
    sample_id = display_id(str(record["record_id"]), annotator)
    st.subheader(f"当前样本: {sample_id}")
    st.caption("先逐项核查报告中的 finding，再填写下方六个维度；不要根据报告长短、术语或语气打分。")
    spec_text = (ROOT / str(record["spec_path"])).read_text(encoding="utf-8")
    report = ReviewReport.model_validate_json(
        (ROOT / str(record["report_path"])).read_text(encoding="utf-8")
    )
    copy_bundle = build_copy_bundle(sample_id, spec_text, report)
    left, right, copy_column = st.columns([1, 1, 0.85])
    with left:
        st.markdown("### OpenAPI 文档")
        st.code(spec_text, language="yaml" if str(record["spec_path"]).endswith("yaml") else "json")
    with right:
        st.markdown("### 待评审报告")
        _render_report(report)
    with copy_column:
        st.markdown("### 复制材料")
        st.caption("代码框右上角可复制全部内容；仅供个人核对，不能交给其他 AI 代替人工评分。")
        st.code(copy_bundle, language=None)
        st.download_button(
            "下载当前材料 TXT",
            copy_bundle.encode("utf-8"),
            file_name=f"{sample_id}-annotation-material.txt",
            mime="text/plain",
            use_container_width=True,
        )

    rubric = load_rubric()
    st.markdown("### 六维评分")
    selections: dict[str, int | None] = {}
    for name in DIMENSION_ORDER:
        definition = rubric["dimensions"][name]
        with st.expander(
            f"{definition['label_zh']}（权重 {definition['weight']}%）", expanded=True
        ):
            selections[name] = st.selectbox(
                "选择 0～4 分",
                [None, 0, 1, 2, 3, 4],
                format_func=lambda value: "请选择" if value is None else f"{value} 分",
                key=f"{sample_id}-{name}",
            )
            st.caption(
                " | ".join(
                    f"{score}分: {definition['criteria'][score]}" for score in (4, 3, 2, 1, 0)
                )
            )
    notes = st.text_area("可选备注", max_chars=2_000)
    complete = all(value is not None for value in selections.values())
    if complete:
        materialized = {name: int(value) for name, value in selections.items() if value is not None}
        st.metric("人工加权总分", weighted_total(materialized))
    if st.button("保存本条并进入下一条", type="primary", disabled=not complete):
        scores = {name: int(value) for name, value in selections.items() if value is not None}
        save_annotation(
            LOCAL_ANNOTATIONS,
            record_id=str(record["record_id"]),
            sample_id=sample_id,
            annotator_alias=annotator,
            scores=scores,
            notes=notes,
        )
        st.rerun()


if __name__ == "__main__":
    main()
