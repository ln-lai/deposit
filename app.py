import os
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv


def _safe_float(x: Any) -> float | None:
    try:
        if x is None:
            return None
        if isinstance(x, (float, int, np.floating, np.integer)):
            return float(x)
        s = str(x).strip().replace(",", "")
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def basic_overview(df: pd.DataFrame) -> dict[str, Any]:
    rows, cols = df.shape
    missing = df.isna().sum().sort_values(ascending=False)
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    numeric_desc = df[numeric_cols].describe().T if numeric_cols else pd.DataFrame()
    return {
        "rows": rows,
        "cols": cols,
        "missing": missing,
        "numeric_cols": numeric_cols,
        "numeric_desc": numeric_desc,
    }


def rule_based_insights(df: pd.DataFrame) -> str:
    ov = basic_overview(df)
    lines: list[str] = []
    lines.append(f"- 行数: **{ov['rows']}**；列数: **{ov['cols']}**")

    missing: pd.Series = ov["missing"]
    if missing.sum() > 0:
        top_missing = missing[missing > 0].head(5)
        miss_items = ", ".join([f"{k}({int(v)})" for k, v in top_missing.items()])
        lines.append(f"- 缺失值最多的列(Top5): {miss_items}")
    else:
        lines.append("- 缺失值: **0**（很好）")

    numeric_cols: list[str] = ov["numeric_cols"]
    if numeric_cols:
        desc: pd.DataFrame = ov["numeric_desc"]
        # pick 1-3 most variable columns by std
        if "std" in desc.columns:
            var_cols = desc["std"].sort_values(ascending=False).head(3).index.tolist()
            lines.append(f"- 波动最大的数值列(按标准差): {', '.join(var_cols)}")
        # simple outlier hint
        if "max" in desc.columns and "mean" in desc.columns:
            ratios = (desc["max"] / desc["mean"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)
            ratios = ratios.dropna().sort_values(ascending=False)
            if not ratios.empty and float(ratios.iloc[0]) >= 10:
                col = str(ratios.index[0])
                lines.append(f"- 异常值提示: `{col}` 的 max/mean 很高（可能存在极端值）")
    else:
        lines.append("- 数值列: **0**（可能都是文本/类别列）")

    # detect date-like column candidates (very rough)
    date_candidates: list[str] = []
    for c in df.columns[:50]:
        if "date" in str(c).lower() or "时间" in str(c) or "日期" in str(c):
            date_candidates.append(str(c))
    if date_candidates:
        lines.append(f"- 时间字段候选: {', '.join(date_candidates[:3])}")

    lines.append("")
    lines.append("你可以试试问这些问题：")
    lines.append("- “本月/本周数据有什么异常？”（如果有日期列更好）")
    lines.append("- “哪个维度（渠道/地区/品类）表现最好？”")
    lines.append("- “给我 3 条可执行建议”")
    return "\n".join(lines)


def df_schema_text(df: pd.DataFrame) -> str:
    parts: list[str] = []
    parts.append(f"rows={len(df)}, cols={df.shape[1]}")
    col_lines = []
    for c in df.columns.tolist()[:60]:
        col_lines.append(f"- {c}: {str(df[c].dtype)}")
    parts.append("columns:\n" + "\n".join(col_lines))
    sample = df.head(30).copy()
    return "\n\n".join(parts) + "\n\nsample_rows_csv:\n" + sample.to_csv(index=False)

def get_completion(prompt: str,content: str, history=None):
    if history is None:
        history = []
    history.append({"role": "system", "content": prompt})
    history.append({"role": "user", "content": content})
    response = client.chat.asyncCompletions.create(
        model="glm-4-flash",
        messages=history,
    )
    task_id = response.id
    task_status = ''
    get_cnt = 0
    while task_status != 'SUCCESS' and task_status != 'FAILED' and get_cnt <= 40:
        result_response = client.chat.asyncCompletions.retrieve_completion_result(id=task_id)
        task_status = result_response.task_status
        time.sleep(.5)
        get_cnt += 1
    content = result_response.choices[0].message.content
    history.append({"role": "assistant", "content": content})
    return content,history

def try_ai_answer(question: str, df: pd.DataFrame) -> tuple[bool, str]:

    try:
        from zhipuai import ZhipuAI
        client = ZhipuAI(api_key="4ff7309af7b648bf9978029246a04c4d.6zQwsrUtNt2BqDoP")
        context = df_schema_text(df)
        prompt = (
            "你是数据分析助理。用户会给你一个 CSV 数据集的 schema 和部分样例行。"
            "你要用中文回答问题，给出清晰结论，并尽量给出可执行建议。"
            "如果信息不足，先说需要哪一列/哪种统计，再给出你能给的最佳推断。"
        )
        text, history = get_completion(prompt，context)
        if not text:
            return False, "AI 返回为空，已降级为非 AI 洞察。"
        return True, text
    except Exception as e:
        return False, f"AI 调用失败（已降级）：{e}"


st.set_page_config(page_title="AI 数据分析小面板 (MVP)", layout="wide")
st.title("AI 数据分析小面板（MVP）")
st.caption("上传 CSV → 基础分析 → 可选 AI 总结/问答（没 Key 也能用）")

load_dotenv(override=False)

with st.sidebar:
    st.subheader("数据")
    uploaded = st.file_uploader("上传 CSV", type=["csv"])
    st.divider()
    st.subheader("AI（可选）")
    has_key = bool(os.getenv("OPENAI_API_KEY", "").strip())
    st.write(f"OPENAI_API_KEY: {'已配置' if has_key else '未配置'}")
    st.write(f"OPENAI_MODEL: `{os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')}`")

if not uploaded:
    st.info("先在左侧上传一个 CSV 文件开始。")
    st.stop()

try:
    df = pd.read_csv(uploaded)
except Exception:
    uploaded.seek(0)
    df = pd.read_csv(uploaded, encoding_errors="ignore")

col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("数据预览")
    st.dataframe(df.head(50), use_container_width=True)

with col2:
    st.subheader("基础概览")
    ov = basic_overview(df)
    st.metric("行数", ov["rows"])
    st.metric("列数", ov["cols"])
    st.write("缺失值 Top10")
    st.dataframe(ov["missing"].head(10).rename("missing_count"), use_container_width=True)
    if not ov["numeric_desc"].empty:
        st.write("数值列统计（describe）")
        st.dataframe(ov["numeric_desc"], use_container_width=True)

st.divider()
st.subheader("自动洞察（不需要 API Key）")
st.markdown(rule_based_insights(df))

st.divider()
st.subheader("自然语言提问（可选 AI）")
q = st.text_input("你想问这份数据什么？", placeholder="例如：哪个渠道转化率最高？最近是否有异常？给我 3 条建议")
if st.button("回答", type="primary", disabled=(not q.strip())):
    ok, ans = try_ai_answer(q.strip(), df)
    if ok:
        st.success("AI 回答")
        st.write(ans)
    else:
        st.warning(ans)
        st.markdown(rule_based_insights(df))
