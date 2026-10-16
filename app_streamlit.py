import streamlit as st
from pathlib import Path
import importlib
import json
from datetime import datetime
import html as html_lib

# 热重载业务模块，避免 Streamlit 进程缓存旧的分数/页码逻辑
import src.reranking as _reranking
import src.retrieval as _retrieval
import src.text_splitter as _text_splitter
import src.questions_processing as _questions_processing
import src.pipeline as _pipeline
importlib.reload(_reranking)
importlib.reload(_retrieval)
importlib.reload(_text_splitter)
importlib.reload(_questions_processing)
importlib.reload(_pipeline)
from src.pipeline import Pipeline, max_config

# 相对本文件定位数据目录，项目文件夹改名或移动后仍能找到
root_path = Path(__file__).resolve().parent / "data" / "stock_data"
pipeline = Pipeline(root_path, run_config=max_config)

st.set_page_config(page_title="企业财报问答RAG", layout="wide")

# 会话状态
if "process_logs" not in st.session_state:
    st.session_state.process_logs = []
if "retrieval_chunks" not in st.session_state:
    st.session_state.retrieval_chunks = []
if "answer_payload" not in st.session_state:
    st.session_state.answer_payload = None
if "user_question" not in st.session_state:
    st.session_state.user_question = ""

# pdf_reports 中的公司及年报年份，用于生成快捷测试问题
_REPORT_COMPANIES = [
    ("中芯国际", "2024"),
    ("云浦微电", "2024"),
    ("云鸢动力", "2025"),
    ("伏曦光伏", "2025"),
    ("宴丰食品", "2025"),
    ("察微装备", "2025"),
    ("峰锆功率", "2025"),
    ("擎岳液压", "2025"),
    ("晶蓝化学", "2025"),
    ("曜石半导体", "2025"),
    ("曜锂能源", "2025"),
    ("氢澜动力", "2025"),
    ("沧澜海工", "2025"),
    ("泰诺制药", "2025"),
    ("泰赫智联", "2025"),
    ("泽佑生物", "2025"),
    ("澈微电子", "2025"),
    ("澜芯制造", "2024"),
    ("玖弘数控", "2025"),
    ("璞兰诊断", "2025"),
    ("皓发光电", "2025"),
    ("硅普材料", "2025"),
    ("磁岭材料", "2025"),
    ("磐石机器人", "2025"),
    ("禾光传媒", "2025"),
    ("竞跃体育", "2025"),
    ("翠微文旅", "2025"),
    ("迅波通信", "2025"),
    ("醇安医疗", "2025"),
    ("铖风传动", "2025"),
    ("铨核科技", "2025"),
    ("锦蔚新材", "2025"),
    ("黛荷生物", "2025"),
]


def _build_quick_questions():
    """职务 / 数值 / 开放各 30 条，问法错开，并覆盖全部公司。"""
    companies = _REPORT_COMPANIES
    total = len(companies)
    # 职务：不同岗位、名单类、是否兼任，避免只问“某某是谁”
    duty_templates = [
        lambda n, y: f"{n}的董事长是谁？",
        lambda n, y: f"{n}现任总经理（或总裁、首席执行官）是谁？",
        lambda n, y: f"{n}董事会秘书由谁担任？",
        lambda n, y: f"{n}的财务负责人是谁？",
        lambda n, y: f"{n}独立董事有哪些人？",
        lambda n, y: f"{n}年报披露的高级管理人员有哪几位，分别任何职？",
        lambda n, y: f"{n}监事会主席是谁？",
        lambda n, y: f"{n}副总经理有哪几位？",
        lambda n, y: f"{n}董事会成员名单是什么？",
        lambda n, y: f"{n}的实际控制人或控股股东是谁？",
        lambda n, y: f"{n}法定代表人是谁？是否与董事长为同一人？",
        lambda n, y: f"{n}审计委员会的召集人是谁？",
        lambda n, y: f"{n}有没有职工董事或职工监事？是谁？",
        lambda n, y: f"{n}证券事务代表是谁？",
        lambda n, y: f"{n}核心技术人员里，哪些人同时属于高管？",
        lambda n, y: f"{n}董事长和总经理是否由同一人兼任？",
        lambda n, y: f"{n}非独立董事有哪些？",
        lambda n, y: f"{n}报告期内新聘或离任的董事、高管有哪些？",
        lambda n, y: f"{n}薪酬与考核委员会成员有哪些？",
        lambda n, y: f"{n}提名委员会负责人是谁？",
        lambda n, y: f"{n}监事有哪几位？",
        lambda n, y: f"{n}负责信息披露的高管是谁？",
        lambda n, y: f"{n}管理层里分管财务、销售、生产的分别是谁？",
        lambda n, y: f"{n}年报“董事、监事和高级管理人员”一节列出了哪些人？",
        lambda n, y: f"{n}独立董事占董事会人数的比例大概是多少，具体是哪几位？",
        lambda n, y: f"{n}有无联席总裁、常务副总或总工程师？是谁？",
        lambda n, y: f"{n}控股股东委派了哪些董事？",
        lambda n, y: f"{n}{y}年末在任的高层管理人员姓名和职务是什么？",
        lambda n, y: f"{n}董事会下设了哪些专门委员会，负责人分别是谁？",
        lambda n, y: f"{n}年报中出现的核心管理团队主要有哪些岗位？",
    ]
    # 数值：不同报表科目和口径，不只问营业收入
    numeric_templates = [
        lambda n, y: f"{n}{y}年营业收入是多少，同比变动多少？",
        lambda n, y: f"{n}{y}年归属于上市公司股东的净利润是多少？",
        lambda n, y: f"{n}{y}年扣除非经常性损益后的净利润是多少？",
        lambda n, y: f"{n}{y}年经营活动产生的现金流量净额是多少？",
        lambda n, y: f"{n}{y}年毛利率是多少？",
        lambda n, y: f"{n}{y}年末资产负债率是多少？",
        lambda n, y: f"{n}{y}年基本每股收益是多少？",
        lambda n, y: f"{n}{y}年研发投入金额及占营业收入的比例是多少？",
        lambda n, y: f"{n}{y}年末资产总计和归母净资产分别是多少？",
        lambda n, y: f"{n}{y}年拟分配的现金股利是多少？",
        lambda n, y: f"{n}{y}年末员工人数是多少？",
        lambda n, y: f"{n}{y}年前五名客户合计销售额占年度销售总额的比例是多少？",
        lambda n, y: f"{n}{y}年境外或海外收入是多少，占比如何？",
        lambda n, y: f"{n}{y}年末存货和应收账款账面价值分别是多少？",
        lambda n, y: f"{n}{y}年加权平均净资产收益率是多少？",
        lambda n, y: f"{n}{y}年营业成本是多少？",
        lambda n, y: f"{n}{y}年销售费用、管理费用、财务费用分别是多少？",
        lambda n, y: f"{n}{y}年利润总额和所得税费用是多少？",
        lambda n, y: f"{n}{y}年末货币资金余额是多少？",
        lambda n, y: f"{n}{y}年末短期借款和长期借款合计约多少？",
        lambda n, y: f"{n}{y}年营业收入增长率是多少？",
        lambda n, y: f"{n}{y}年末未分配利润是多少？",
        lambda n, y: f"{n}{y}年末总股本是多少？",
        lambda n, y: f"{n}{y}年投资活动现金流量净额是多少？",
        lambda n, y: f"{n}{y}年前五名供应商采购额占年度采购总额的比例是多少？",
        lambda n, y: f"{n}{y}年综合毛利额或毛利润大约是多少？",
        lambda n, y: f"{n}{y}年末商誉账面价值是多少？",
        lambda n, y: f"{n}{y}年政府补助计入当期损益的金额是多少？",
        lambda n, y: f"{n}{y}年分产品或分地区的收入金额分别是多少？",
        lambda n, y: f"{n}{y}年末流动比率或速动比率披露的数值是多少？",
    ]
    # 开放：战略、风险、客户、研发、治理等不同侧面
    open_templates = [
        lambda n, y: f"{n}的主营业务是什么，在所处行业里处于什么位置？",
        lambda n, y: f"{n}面临的主要风险有哪些，公司准备如何应对？",
        lambda n, y: f"{n}{y}年经营业绩和收入结构相比上年有什么变化？",
        lambda n, y: f"{n}的核心竞争力是什么？",
        lambda n, y: f"{n}{y}年的研发方向和技术布局是怎样的？",
        lambda n, y: f"{n}的客户结构和供应商集中度如何？",
        lambda n, y: f"{n}未来几年的发展战略和经营计划是什么？",
        lambda n, y: f"{n}主要在建项目或产能扩张计划有哪些？",
        lambda n, y: f"{n}如何看待行业政策和竞争格局对自身的影响？",
        lambda n, y: f"{n}在环境保护、社会责任方面做了哪些工作？",
        lambda n, y: f"{n}的分红政策和股东回报安排是怎样的？",
        lambda n, y: f"{n}报告期内有哪些重要的关联交易或对外担保？",
        lambda n, y: f"{n}的销售渠道和区域布局是怎样的？",
        lambda n, y: f"{n}现金流状况能否支撑当前的扩张？",
        lambda n, y: f"{n}成本主要由哪些部分构成，有什么控费措施？",
        lambda n, y: f"{n}产品或服务结构最近有什么调整？",
        lambda n, y: f"{n}公司治理和内部控制方面有哪些变化？",
        lambda n, y: f"{n}对原材料价格或下游需求波动是否敏感？",
        lambda n, y: f"{n}重要子公司有哪些，各自承担什么业务？",
        lambda n, y: f"{n}人才引进和股权激励是如何安排的？",
        lambda n, y: f"{n}是否涉及重大诉讼、仲裁或监管处罚？",
        lambda n, y: f"{n}海外业务或出口情况怎么样？",
        lambda n, y: f"{n}募集资金或重大投资的使用进度如何？",
        lambda n, y: f"{n}与同行业相比，管理层强调了哪些优势和短板？",
        lambda n, y: f"{n}{y}年毛利率变化的原因是什么？",
        lambda n, y: f"{n}数字化、智能化或新业务拓展进展如何？",
        lambda n, y: f"{n}报告期内产能利用率或产销情况如何？",
        lambda n, y: f"{n}品牌、渠道或大客户依赖方面有什么风险？",
        lambda n, y: f"{n}对{y}年业绩变动的管理层讨论重点是什么？",
        lambda n, y: f"如果只根据年报，{n}最值得关注的三个经营要点是什么？",
    ]
    duty, numeric, open_ended = [], [], []
    for i in range(30):
        name, year = companies[i % total]
        duty.append(duty_templates[i](name, year))
        name, year = companies[(i + 11) % total]
        numeric.append(numeric_templates[i](name, year))
        name, year = companies[(i + 22) % total]
        open_ended.append(open_templates[i](name, year))
    return {
        "职务类": duty,
        "数值类": numeric,
        "开放性问题": open_ended,
    }


QUICK_QUESTIONS = _build_quick_questions()


def _parse_answer_payload(answer):
    """解析 pipeline 返回为统一展示结构"""
    if isinstance(answer, str):
        try:
            answer_dict = json.loads(answer)
        except Exception:
            return {
                "step_by_step": "-",
                "reasoning_summary": "-",
                "relevant_pages": [],
                "final_answer": str(answer),
                "chunks": [],
            }
    else:
        answer_dict = answer if isinstance(answer, dict) else {}

    content = answer_dict.get("content", answer_dict)
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = {
                "step_by_step_analysis": answer_dict.get("step_by_step_analysis", "-"),
                "reasoning_summary": answer_dict.get("reasoning_summary", "-"),
                "relevant_pages": answer_dict.get("relevant_pages", []),
                "final_answer": content,
            }
    if not isinstance(content, dict):
        content = {}

    return {
        "step_by_step": content.get("step_by_step_analysis") or answer_dict.get("step_by_step_analysis") or "-",
        "reasoning_summary": content.get("reasoning_summary") or answer_dict.get("reasoning_summary") or "-",
        "relevant_pages": content.get("relevant_pages") or answer_dict.get("relevant_pages") or [],
        "final_answer": content.get("final_answer") or answer_dict.get("final_answer") or "-",
        "chunks": answer_dict.get("_retrieval_chunks") or [],
    }


def _render_scroll_log(logs, height_px=220):
    """渲染可滚动的实时过程日志（自动滚到底部）"""
    if not logs:
        body = '<div style="color:#888;">等待开始检索...</div>'
    else:
        lines = [
            f'<div style="padding:4px 0;border-bottom:1px solid #eee;">{html_lib.escape(str(item))}</div>'
            for item in logs
        ]
        body = "".join(lines)
    st.markdown(
        f"""
        <div id="rag-process-log" style="
            height:{height_px}px; overflow-y:auto; background:#f8fafc;
            border:1px solid #e2e8f0; border-radius:8px; padding:12px;
            font-family:Consolas,monospace; font-size:13px; color:#334155;">
            {body}
        </div>
        <script>
        (function() {{
          var el = document.getElementById('rag-process-log');
          if (el) {{ el.scrollTop = el.scrollHeight; }}
        }})();
        </script>
        """,
        unsafe_allow_html=True,
    )


def _set_quick_question(q: str):
    """填入快捷问题，并标记下一轮自动提交（无需再点生成答案）"""
    st.session_state.user_question = q
    st.session_state.auto_submit = True


# 页面标题
st.markdown("""
<div style='background: linear-gradient(90deg, #7b2ff2 0%, #f357a8 100%); padding: 20px 0; border-radius: 12px; text-align: center;'>
    <h2 style='color: white; margin: 0;'>企业财报问答RAG</h2>
    <div style='color: #fff; font-size: 16px;'>向量检索 + LLM 重排 + Qwen-Plus（DashScope）</div>
</div>
""", unsafe_allow_html=True)

# 左侧输入区：text_area 加高；表单内 Ctrl+Enter 提交（Streamlit 原生行为）
with st.sidebar:
    st.markdown(
        """
        <style>
        section[data-testid="stSidebar"] div[data-testid="stTextArea"] textarea {
            min-height: 120px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.header("查询设置")
    with st.form("ask_form", clear_on_submit=False):
        user_question = st.text_area(
            "输入问题",
            key="user_question",
            placeholder="输入后按 Ctrl+Enter 生成答案",
            height=120,
        )
        form_submitted = st.form_submit_button(
            "生成答案",
            use_container_width=True,
            type="primary",
        )

    st.divider()
    st.subheader("快捷测试问题")
    st.caption("点击快捷问题后直接开始生成。")

    for category, questions in QUICK_QUESTIONS.items():
        with st.expander(category, expanded=(category == "职务类")):
            for idx, q in enumerate(questions):
                # 按钮文案过长时截断显示，完整问题写入输入框
                label = q if len(q) <= 28 else q[:28] + "..."
                st.button(
                    label,
                    key=f"quick_{category}_{idx}",
                    use_container_width=True,
                    on_click=_set_quick_question,
                    args=(q,),
                    help=q,
                )

# 快捷问题点击后自动提交；或表单 Enter / 按钮提交
auto_submit = bool(st.session_state.pop("auto_submit", False))
submit_btn = bool(form_submitted) or auto_submit
if auto_submit:
    user_question = st.session_state.get("user_question", "") or ""

running = bool(submit_btn and str(user_question).strip())

# ---------- 区域1：实时检索过程 ----------
process_placeholder = None
st.markdown("#### 1. 实时检索过程")
with st.container(border=True):
    process_placeholder = st.empty()
    with process_placeholder.container():
        _render_scroll_log(st.session_state.process_logs)

# ---------- 区域2 + 3 ----------
col_chunks, col_answer = st.columns(2, gap="medium")

with col_chunks:
    st.markdown("#### 2. 检索页面汇总")
    with st.container(border=True):
        chunks = st.session_state.retrieval_chunks
        if not chunks:
            st.caption("生成答案后，此处汇总向量检索并扩回的页面。")
        else:
            # 答案引用页：优先展开并排到前面，避免「相关页面」和可见切片对不上
            cited_pages = set()
            if st.session_state.answer_payload:
                for p in st.session_state.answer_payload.get("relevant_pages") or []:
                    cited_pages.add(p)
            indexed = list(enumerate(chunks, 1))
            if cited_pages:
                cited_items = [x for x in indexed if x[1].get("page") in cited_pages]
                other_items = [x for x in indexed if x[1].get("page") not in cited_pages]
                display_items = cited_items + other_items
                missing = sorted(cited_pages - {c.get("page") for c in chunks})
                unit_name = "页面" if chunks and chunks[0].get("unit") == "page" else "切片"
                st.caption(
                    f"共 {len(chunks)} 条{unit_name}；答案引用页 {sorted(cited_pages)}"
                    + (f"（其中 {missing} 未出现在本批结果中）" if missing else "")
                )
            else:
                display_items = indexed
                unit_name = "页面" if chunks and chunks[0].get("unit") == "page" else "切片"
                st.caption(f"共 {len(chunks)} 条{unit_name}")

            for i, chunk in display_items:
                page = chunk.get("page", "-")
                is_cited = page in cited_pages
                # 综合分应为 0~1；若仍是上千的大数，说明是旧会话缓存，按向量内积展示
                if "combined_score" in chunk:
                    cs = chunk["combined_score"]
                    try:
                        cs_f = float(cs)
                    except (TypeError, ValueError):
                        cs_f = None
                    if cs_f is not None and 0.0 <= cs_f <= 1.0:
                        score_label = f"综合分 {cs}"
                    else:
                        score_label = f"向量内积 {chunk.get('distance', cs)}"
                elif "relevance_score" in chunk:
                    score_label = f"相关分 {chunk['relevance_score']}"
                else:
                    score_label = f"向量内积 {chunk.get('distance', '-')}"
                text = chunk.get("text", "") or ""
                preview = text if len(text) <= 400 else text[:400] + "..."
                item_name = "页面" if chunk.get("unit") == "page" else "切片"
                title = f"{item_name} {i} | 页码 {page} | {score_label}"
                if is_cited:
                    title = f"[答案引用] {title}"
                with st.expander(title, expanded=is_cited or (not cited_pages and i <= 2)):
                    # 按纯文本展示，避免 markdown 标题(#)导致字号失真
                    safe = html_lib.escape(preview)
                    st.markdown(
                        f"<pre style='white-space:pre-wrap;word-break:break-word;"
                        f"font-size:14px;line-height:1.55;margin:0;padding:8px;"
                        f"background:#f8fafc;border-radius:6px;color:#334155;"
                        f"font-family:Consolas,\"Microsoft YaHei\",sans-serif;'>{safe}</pre>",
                        unsafe_allow_html=True,
                    )

with col_answer:
    st.markdown("#### 3. 最终答案")
    with st.container(border=True):
        payload = st.session_state.answer_payload
        if not payload:
            st.caption("请在左侧输入问题并点击【生成答案】")
        else:
            st.markdown("**最终答案**")
            st.markdown(
                f"<div style='background:#f6f8fa;padding:14px;border-radius:8px;font-size:17px;line-height:1.6;'>{html_lib.escape(str(payload['final_answer']))}</div>",
                unsafe_allow_html=True,
            )
            st.markdown("**推理摘要**")
            st.success(payload["reasoning_summary"])
            st.markdown("**分步推理**")
            st.info(payload["step_by_step"])
            st.markdown("**相关页面**")
            pages = payload.get("relevant_pages") or []
            st.write("、".join(str(p) for p in pages) if pages else "无")

# ---------- 提交后执行问答 ----------
if submit_btn and user_question.strip():
    st.session_state.process_logs = []
    st.session_state.retrieval_chunks = []
    st.session_state.answer_payload = None

    logs = []

    def on_progress(msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        logs.append(line)
        st.session_state.process_logs = list(logs)
        target = process_placeholder if process_placeholder is not None else st.empty()
        with target.container():
            _render_scroll_log(logs, height_px=240)

    try:
        with st.spinner("正在检索并生成答案..."):
            answer = pipeline.answer_single_question(
                user_question,
                kind="string",
                progress_callback=on_progress,
            )
        payload = _parse_answer_payload(answer)
        st.session_state.retrieval_chunks = payload["chunks"]
        st.session_state.answer_payload = payload
        st.session_state.process_logs = list(logs)
        st.rerun()
    except Exception as e:
        on_progress(f"出错：{e}")
        st.error(f"生成答案时出错: {e}")
elif submit_btn:
    st.warning("请先输入问题")
