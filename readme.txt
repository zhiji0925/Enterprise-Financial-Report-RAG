企业财报问答RAG
================

基于年报 PDF 的检索增强问答。解析用 MinerU，向量库存 FAISS，作答用通义千问（DashScope）。
界面是 Streamlit，左侧提问，右侧同时看检索过程、检索页面和最终答案。

当前数据在 data/stock_data，pdf_reports 中每份财报对应一个向量库（stock_10001.faiss 起）。
公司名和向量库文件名的对应关系写在 data/stock_data/subset.csv。提问时必须带上公司名，系统只在该公司的库里检索。


怎么跑
------

在项目根目录执行：

    streamlit run app_streamlit.py

浏览器打开后，在左侧输入问题，Ctrl+Enter 或点击「生成答案」。
快捷测试问题分职务、数值、开放三类，点击后会直接开始生成。

环境变量（不要写进代码）：

    DASHSCOPE_API_KEY    向量化、重排、作答
    MINERU_API_KEY       新 PDF 解析

依赖见 requirements.txt。运行时还需要 streamlit、dashscope、langchain-text-splitters、tenacity、numpy。


问答时实际做了什么
------------------

1. 从问题里匹配 subset.csv 的公司名。多家公司则按比较题处理。
2. 用 text-embedding-v1 把问题向量化，在该公司的 FAISS 库（IndexFlatIP，内积）里召回候选切片。向量按切片建，索引里没有整页。
3. 用切片上的页码把同一页的切片拼回整页，并去掉重复页。
4. 用 qwen-flash 对这些页面打 0 到 1 的相关分，再和向量相似度合成综合分，取得分最高的若干页。
5. 把页面和问题交给 qwen-plus。默认按开放叙述（string）作答，输出分步推理、摘要、相关页码和最终答案。
6. 相关页码会回查这些页面，避免引用不存在的页。

界面上的综合分大约在 0 到 1，越大越相关。页码来自 MinerU 的 content_list（page_idx 加 1）。中芯国际没有这份解析结果，页码仍是旧分块里的估计值。


新财报怎么入库
--------------

把 PDF 放到 data/stock_data/pdf_reports，文件名保持：

    【财报】公司名：公司名20xx年年度报告.pdf

然后在项目根目录执行：

    python -c "from pathlib import Path; from src.pipeline import Pipeline, max_config; Pipeline(Path('data/stock_data'), run_config=max_config).ingest_pdf_reports()"

这一步会：MinerU 解析为 markdown、按页分块、补 subset.csv、为尚无向量库的报告生成 faiss。
分块按页处理：一页不长就整页一块；页太长则按行切窗口，表格保持完整。向量化时超过 2048 字的文本只截断进向量，库里仍保存全文。
已有 markdown 的 PDF 不会重复解析，已有同名 faiss 的报告不会重复向量化。


目录
----

app_streamlit.py                         问答界面
src/pipeline.py                          解析、分块、建库、单问/批量问答
src/pdf_mineru.py                        MinerU 本地 PDF 上传与结果下载
src/text_splitter.py                     按页分块，页码来自 MinerU content_list
src/ingestion.py                         DashScope 向量化，每份报告一个 faiss
src/retrieval.py                         按公司检索，HybridRetriever 负责向量召回加重排
src/reranking.py                         LLM 相关分与向量分融合
src/questions_processing.py              抽公司、检索、作答、校验页码
src/api_requests.py                      按题型选择提示词并调用模型
src/prompts.py                           提示词与答案 JSON 结构
main.py                                  命令行：process-reports / process-questions
data/stock_data/pdf_reports              原始 PDF
data/stock_data/debug_data/03_reports_markdown
                                         MinerU 产出的 markdown
data/stock_data/databases/chunked_reports
                                         分块 JSON
data/stock_data/databases/vector_dbs     每家公司一个 .faiss
data/stock_data/subset.csv               sha1、文件名、公司名
data/stock_data/questions.json           批量问题
docs/*.html                              各模块逻辑说明（含 Mermaid 流程图）


配置
----

界面使用 pipeline.py 里的 max_config：开启向量库、按页扩回、LLM 重排，作答模型 qwen-plus。
批量问题可用：

    python main.py process-questions --config max

界面和 main.py 都按脚本所在位置定位 data/stock_data，项目文件夹改名或移动后不用改路径。
