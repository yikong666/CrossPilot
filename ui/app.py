"""CrossPilot's browser-local, single-page Streamlit analysis workbench."""

import asyncio
from collections.abc import AsyncIterator, Mapping, MutableMapping
from typing import Any, cast

import streamlit as st

from ui.api_client import APIClientError, fetch_graph, resume_analysis, stream_analysis
from ui.components import (
    apply_sse_event,
    begin_new_analysis,
    build_analysis_payload,
    initialize_session_state,
    render_missing_fields_form,
    render_product_controls,
)
from ui.graph_view import render_graph_evidence

st.set_page_config(page_title="CrossPilot", page_icon="✈️", layout="wide")
session_state = cast(MutableMapping[str, Any], st.session_state)
initialize_session_state(session_state)

product = render_product_controls()
st.title("CrossPilot 跨境商品分析")
st.caption("仅支持美国 Amazon；页面中的商品与市场数据均为离线仿真数据。")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


async def _consume_events(events: AsyncIterator[Any], status: Any, answer: Any) -> bool:
    requires_input = False
    async for event in events:
        event_data = event.model_dump()
        apply_sse_event(session_state, event_data)
        status.write(st.session_state.progress[-1])
        if event.event_type == "answer_chunk":
            answer.markdown(st.session_state.answer_buffer)
        if event.event_type == "workflow_failed":
            status.update(label="分析未完成", state="error", expanded=True)
            st.error("分析未能完成。请检查输入后重试，或稍后再试。")
            return False
        if event.event_type == "input_required":
            requires_input = True
        if event.event_type == "workflow_completed":
            status.update(label="分析完成", state="complete", expanded=False)
            return True
    if requires_input:
        status.update(label="等待补充信息", state="running", expanded=True)
        return False
    status.update(label="分析连接已结束", state="error", expanded=True)
    st.error("分析连接已结束，请稍后重试。")
    return False


def _run_stream(events: AsyncIterator[Any]) -> None:
    status = st.status("正在连接分析服务", expanded=True)
    answer = st.empty()
    try:
        completed = asyncio.run(_consume_events(events, status, answer))
    except APIClientError as exc:
        status.update(label="分析连接异常", state="error", expanded=True)
        st.error(str(exc))
    except Exception:
        status.update(label="分析暂时不可用", state="error", expanded=True)
        st.error("分析暂时不可用，请稍后重试。")
    else:
        thread_id = st.session_state.thread_id
        if completed and isinstance(thread_id, str) and thread_id:
            try:
                st.session_state.graph_data = asyncio.run(fetch_graph(thread_id))
            except APIClientError:
                st.info("图谱依据暂时无法加载，不影响本次分析答案。")


question = st.chat_input("例如：这款产品的市场和竞品情况如何？")
if question:
    if not product["name"].strip() or not product["purchase_cost_cny"].strip():
        st.error("请先填写商品名称和采购成本，再提交分析问题。")
    else:
        request = build_analysis_payload(product, question)
        begin_new_analysis(session_state, request["product"])
        st.session_state.messages.append({"role": "user", "content": question})
        _run_stream(stream_analysis(request))

missing_fields = st.session_state.missing_fields
if isinstance(missing_fields, list):
    supplemental_fields = render_missing_fields_form(missing_fields)
    if supplemental_fields is not None:
        thread_id = st.session_state.thread_id
        if not isinstance(thread_id, str) or not thread_id:
            st.error("当前任务已失效，请重新提交分析问题。")
        elif not supplemental_fields:
            st.error("请填写需要补充的信息后继续。")
        else:
            st.session_state.progress = []
            st.session_state.answer_buffer = ""
            _run_stream(resume_analysis(thread_id, supplemental_fields))

with st.expander("图谱依据", expanded=False):
    graph_data = st.session_state.graph_data
    if isinstance(graph_data, Mapping) and graph_data.get("nodes"):
        render_graph_evidence(graph_data)
    else:
        st.caption("完成包含图谱查询的分析后，这里会显示本次任务涉及的相关节点和关系。")

st.caption("合规信息仅用于资料准备和风险提示，不构成法律意见或认证结论。")
