# src 目录各模块功能说明

> 当前主线为 **MinerU → Markdown → 分块 → FAISS → RAG 问答**（DashScope / Qwen）。Docling 旧线相关模块已移除。

---

## 1. api_requests.py
与大模型 API 交互。默认 **DashScope（Qwen）**；仍保留 openai/ibm/gemini 适配层，主流程未启用。

## 2. ingestion.py
- `BM25Ingestor`：构建 BM25 索引
- `VectorDBIngestor`：Embedding + FAISS 向量库

## 3. pdf_mineru.py
MinerU 云端 PDF → Markdown（当前建库解析入口）。

## 4. pipeline.py
主流程编排：路径、`RunConfig`、转 MD、分块、建库、批量/单问答。

## 5. prompts.py
LLM 提示词与结构化输出 Schema。

## 6. questions_processing.py
公司名抽取、检索、RAG 上下文、LLM 问答、页码校验。

## 7. reranking.py
检索结果 LLM 重排（默认 qwen-flash）。

## 8. retrieval.py
向量 / BM25 / Hybrid 检索。

## 9. text_splitter.py
Markdown / 报告文本分块。

---

# 模块关系与整体流程
1. **PDF → Markdown**：`pdf_mineru.py`（经 `pipeline.export_reports_to_markdown`）
2. **文本分块**：`text_splitter.py`
3. **向量库构建**：`ingestion.py`
4. **问答**：`questions_processing.py` + `retrieval.py` + `reranking.py` + `api_requests.py` + `prompts.py`
5. **编排**：`pipeline.py`（CLI：`main.py`；Web：`app_streamlit.py`）
