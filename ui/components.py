"""Business-facing Streamlit components and request shaping helpers."""

from collections.abc import Mapping, MutableMapping
from typing import Any

import streamlit as st

AGENT_LABELS = {
    "market": "市场",
    "competitor": "竞品",
    "pricing": "定价",
    "compliance": "合规资料",
}

MISSING_FIELD_LABELS = {
    "selling_price_usd": "售价（美元）",
    "fx_cny_per_usd": "人民币兑美元汇率",
    "inbound_shipping_usd": "头程物流费（美元）",
    "fba_fee_usd": "FBA 配送费（美元）",
    "commission_rate": "Amazon 佣金率",
    "ad_rate": "广告费率",
    "return_loss_rate": "退货损耗率",
    "target_margin": "目标利润率",
    "weight_kg": "重量（kg）",
    "dimensions_cm": "尺寸（cm，长×宽×高）",
    "material": "材质",
    "has_battery": "是否含电池",
    "intended_age": "适用年龄",
    "provided_documents": "已有资料",
}

CATEGORY_OPTIONS = ("consumer_electronics", "children_toys", "home_goods")
CATEGORY_LABELS = {
    "consumer_electronics": "消费电子配件",
    "children_toys": "儿童玩具",
    "home_goods": "家居用品",
}


def initialize_session_state(state: MutableMapping[str, Any]) -> None:
    """Set up the state needed for one browser-local analysis session."""
    defaults: dict[str, Any] = {
        "thread_id": None,
        "trace_id": None,
        "recent_product": None,
        "messages": [],
        "missing_fields": [],
        "graph_data": {"nodes": [], "edges": []},
        "progress": [],
        "answer_buffer": "",
    }
    for key, value in defaults.items():
        state.setdefault(key, value)


def begin_new_analysis(state: MutableMapping[str, Any], product: Mapping[str, Any]) -> None:
    """Clear transient evidence from a previous task before starting a new analysis."""
    state["thread_id"] = None
    state["trace_id"] = None
    state["recent_product"] = dict(product)
    state["missing_fields"] = []
    state["graph_data"] = {"nodes": [], "edges": []}
    state["progress"] = []
    state["answer_buffer"] = ""


def apply_sse_event(state: MutableMapping[str, Any], event: Mapping[str, Any]) -> None:
    """Update only this page's session state from one validated SSE event."""
    thread_id = event.get("thread_id")
    trace_id = event.get("trace_id")
    if isinstance(thread_id, str) and thread_id:
        state["thread_id"] = thread_id
    if isinstance(trace_id, str) and trace_id:
        state["trace_id"] = trace_id

    progress = state.get("progress")
    if isinstance(progress, list):
        progress.append(business_event_message(event))

    event_type = event.get("event_type")
    payload = event.get("payload")
    event_payload = payload if isinstance(payload, Mapping) else {}
    if event_type == "input_required":
        missing_fields = event_payload.get("missing_fields")
        if isinstance(missing_fields, list):
            state["missing_fields"] = [field for field in missing_fields if isinstance(field, str)]
    elif event_type == "answer_chunk":
        message = event.get("message")
        if isinstance(message, str):
            state["answer_buffer"] = f"{state.get('answer_buffer', '')}{message}"
    elif event_type == "workflow_completed":
        final_answer = event_payload.get("final_answer")
        answer = final_answer if isinstance(final_answer, str) else state.get("answer_buffer", "")
        if isinstance(answer, str) and answer:
            messages = state.get("messages")
            if isinstance(messages, list):
                messages.append({"role": "assistant", "content": answer})
        state["answer_buffer"] = ""
        state["missing_fields"] = []


def business_event_message(event: Mapping[str, Any]) -> str:
    """Map technical SSE records to concise business progress messages."""
    event_type = str(event.get("event_type", ""))
    payload = event.get("payload")
    event_payload = payload if isinstance(payload, Mapping) else {}
    agent = str(event_payload.get("agent", ""))
    agent_label = AGENT_LABELS.get(agent, "相关")
    messages = {
        "workflow_started": "正在启动分析任务",
        "intent_identified": "正在识别您的分析需求",
        "cypher_retry": "正在调整图谱查询",
        "input_required": "需要补充信息后继续分析",
        "validation_completed": "正在校验分析结果",
        "answer_chunk": "正在生成分析建议",
        "workflow_completed": "分析完成",
        "workflow_failed": "分析暂未完成，请检查输入后重试。",
    }
    if event_type == "agents_selected":
        agents = event_payload.get("agents")
        if isinstance(agents, list):
            selected = "、".join(AGENT_LABELS.get(str(item), "相关") for item in agents)
            return f"已选择{selected}分析" if selected else "已选择分析模块"
        return "已选择分析模块"
    if event_type == "agent_started":
        return f"正在分析{agent_label}数据"
    if event_type == "agent_completed":
        return f"{agent_label}分析已完成"
    return messages.get(event_type, "正在处理分析请求")


def _category_label(category: str) -> str:
    return CATEGORY_LABELS[category]


def build_analysis_payload(product: Mapping[str, Any], question: str) -> dict[str, Any]:
    """Drop empty optional controls before submitting the API contract JSON."""
    required_fields = ("name", "category", "purchase_cost_cny")
    result: dict[str, Any] = {field: product.get(field) for field in required_fields}
    for field in (
        "selling_price_usd",
        "fx_cny_per_usd",
        "inbound_shipping_usd",
        "fba_fee_usd",
        "commission_rate",
        "ad_rate",
        "return_loss_rate",
        "target_margin",
        "weight_kg",
        "material",
        "has_battery",
        "intended_age",
    ):
        value = product.get(field)
        if value is not None and value != "":
            result[field] = value

    dimensions = product.get("dimensions_cm")
    if isinstance(dimensions, (tuple, list)) and len(dimensions) == 3 and all(dimensions):
        result["dimensions_cm"] = list(dimensions)

    documents = product.get("provided_documents")
    if isinstance(documents, str):
        normalized_documents = [item.strip() for item in documents.split(",") if item.strip()]
    elif isinstance(documents, list):
        normalized_documents = [str(item).strip() for item in documents if str(item).strip()]
    else:
        normalized_documents = []
    if normalized_documents:
        result["provided_documents"] = normalized_documents

    return {"product": result, "question": question.strip()}


def render_product_controls() -> dict[str, Any]:
    """Render the complete product form in the sidebar and return raw widget values."""
    with st.sidebar:
        st.header("候选商品")
        st.caption("市场与商品数据均为离线仿真数据，仅用于演示分析。")
        name = st.text_input("商品名称 *", key="product_name")
        category = st.selectbox(
            "商品类别 *",
            options=CATEGORY_OPTIONS,
            format_func=_category_label,
            key="product_category",
        )
        purchase_cost_cny = st.text_input("采购成本（人民币） *", key="purchase_cost_cny")
        st.divider()
        st.caption("选填：成本与定价")
        selling_price_usd = st.text_input("售价（美元）", key="selling_price_usd")
        fx_cny_per_usd = st.text_input("人民币兑美元汇率", key="fx_cny_per_usd")
        inbound_shipping_usd = st.text_input("头程物流费（美元）", key="inbound_shipping_usd")
        fba_fee_usd = st.text_input("FBA 配送费（美元）", key="fba_fee_usd")
        commission_rate = st.text_input("Amazon 佣金率", key="commission_rate")
        ad_rate = st.text_input("广告费率", key="ad_rate")
        return_loss_rate = st.text_input("退货损耗率", key="return_loss_rate")
        target_margin = st.text_input("目标利润率", key="target_margin")
        st.divider()
        st.caption("选填：商品属性")
        weight_kg = st.text_input("重量（kg）", key="weight_kg")
        dimension_columns = st.columns(3)
        length_cm = dimension_columns[0].text_input("长（cm）", key="length_cm")
        width_cm = dimension_columns[1].text_input("宽（cm）", key="width_cm")
        height_cm = dimension_columns[2].text_input("高（cm）", key="height_cm")
        material = st.text_input("材质", key="material")
        battery = st.selectbox("是否含电池", ["未说明", "是", "否"], key="has_battery")
        intended_age = st.text_input("适用年龄", key="intended_age")
        provided_documents = st.text_input("已有资料（逗号分隔）", key="provided_documents")

    return {
        "name": name,
        "category": category,
        "purchase_cost_cny": purchase_cost_cny,
        "selling_price_usd": selling_price_usd,
        "fx_cny_per_usd": fx_cny_per_usd,
        "inbound_shipping_usd": inbound_shipping_usd,
        "fba_fee_usd": fba_fee_usd,
        "commission_rate": commission_rate,
        "ad_rate": ad_rate,
        "return_loss_rate": return_loss_rate,
        "target_margin": target_margin,
        "weight_kg": weight_kg,
        "dimensions_cm": (length_cm, width_cm, height_cm),
        "material": material,
        "has_battery": {"是": True, "否": False}.get(battery),
        "intended_age": intended_age,
        "provided_documents": provided_documents,
    }


def render_missing_fields_form(missing_fields: list[str]) -> dict[str, Any] | None:
    """Render only the fields requested by an interrupted workflow."""
    if not missing_fields:
        return None
    values: dict[str, Any] = {}
    with st.form("resume_analysis"):
        st.subheader("请补充以下信息")
        for field in missing_fields:
            label = MISSING_FIELD_LABELS.get(field, field)
            if field == "has_battery":
                values[field] = st.selectbox(label, ["", "是", "否"], key=f"resume_{field}")
            elif field == "provided_documents":
                values[field] = st.text_input(label, key=f"resume_{field}")
            else:
                values[field] = st.text_input(label, key=f"resume_{field}")
        submitted = st.form_submit_button("补充并继续")
    if not submitted:
        return None
    return _normalize_resume_values(values)


def _normalize_resume_values(values: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for field, value in values.items():
        if value in (None, ""):
            continue
        if field == "has_battery":
            normalized[field] = value == "是"
        elif field == "dimensions_cm" and isinstance(value, str):
            dimensions = [
                item.strip() for item in value.replace("×", ",").split(",") if item.strip()
            ]
            if len(dimensions) == 3:
                normalized[field] = dimensions
        elif field == "provided_documents" and isinstance(value, str):
            normalized[field] = [item.strip() for item in value.split(",") if item.strip()]
        else:
            normalized[field] = value
    return normalized
