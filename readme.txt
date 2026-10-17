企业财报问答RAG
================

基于年报 PDF 的检索增强问答。解析用 MinerU，向量库存 FAISS，作答用通义千问（DashScope）。
界面是 Streamlit，左侧提问，右侧同时看检索过程、检索页面和最终答案。

当前数据在 data/stock_data。每份财报对应 subset.csv 里的一个编号（stock_10001 起）和一个向量库。
提问时必须带上公司名，系统只在该公司的库里检索。


怎么跑
------

在项目根目录执行：

    streamlit run app_streamlit.py

浏览器打开后，在左侧输入问题，Ctrl+Enter 或点击「生成答案」。
快捷测试问题分职务、数值、开放三类，点击后会直接开始生成。

批量问答在项目根目录执行：

    python main.py process-questions --config max

它读取 data/stock_data/questions.json，答案写到 data/stock_data/answers_qwen_plus.json。
界面和 main.py 都按脚本所在位置定位 data/stock_data，项目文件夹改名或移动后不用改路径。

不要用 python src/pipeline.py。那样当前目录是 src，找不到 src 这个包。
要跑 pipeline.py 末尾的分块、建库和批量问答，在项目根目录执行：

    python -m src.pipeline

这一步不会调用 MinerU。已有的 .faiss 会直接跳过，不会因为改了切分参数而重建。


环境变量
--------

写在项目根目录的 .env 里，不要写进代码：

    DASHSCOPE_API_KEY    向量化、重排、作答
    MINERU_API_KEY       云端 MinerU（不超过 200 页的 PDF）
    MINERU_API_BASE      本机 MinerU 地址，默认 http://127.0.0.1:8000

依赖见 requirements.txt。运行界面还需要 streamlit。


问答时实际做了什么
------------------

1. 从问题里匹配 subset.csv 的公司名。多家公司则按比较题处理。
2. 用 text-embedding-v1 把问题向量化，在该公司的 FAISS 库（IndexFlatIP，内积）里召回候选切片。向量按切片建，索引里没有整页。
3. 用切片上的页码把同一页的切片拼回整页，并去掉重复页。
4. 用 qwen-flash 对这些页面打 0 到 1 的相关分，再和向量相似度合成综合分，取得分最高的若干页。
5. 把页面和问题交给 qwen-plus。默认按开放叙述作答，输出分步推理、摘要、相关页码和最终答案。
6. 相关页码会回查这些页面，避免引用不存在的页。

界面上的综合分大约在 0 到 1，越大越相关。页码来自 MinerU content_list 的 page_idx 加 1。


新财报怎么入库
--------------

把 PDF 放到 data/stock_data/pdf_reports，文件名保持：

    【财报】公司名：公司名20xx年年度报告.pdf

然后在项目根目录执行：

    python -c "from pathlib import Path; from src.pipeline import Pipeline, max_config; Pipeline(Path('data/stock_data'), run_config=max_config).ingest_pdf_reports()"

这一步会补 subset.csv、解析还没有 markdown 的 PDF、按页分块、为还没有向量库的报告生成 faiss。
已有同名 markdown 的 PDF 不会重复解析，已有同名 faiss 的报告不会重复向量化。
解压目录不存在时，分块会保留原来的 JSON。要按新解析重做，需要先有解压目录，并删掉对应的 .faiss。

页数不超过 200 走云端 mineru.net。超过 200 页走本机 MinerU，调用前要先启动本机服务。
本机结果会转成 content_list.json，后面的按页分块才能读到真实页码。中芯国际 2024 年报共 222 页，走的是这条本机路径。

分块按页处理：一页不长就整页一块；页太长则按行切窗口，表格保持完整。默认窗口 20 行、重叠 5 行。
向量化时超过 2048 字的文本只截断进向量，库里仍保存全文。

Windows 上 faiss 自带的读写走 fopen，打不开中文路径。读取和写入都改由 Python 读写字节完成。


目录
----

app_streamlit.py                         问答界面
main.py                                  命令行：process-reports / process-questions
src/pipeline.py                          解析、分块、建库、单问/批量问答
src/pdf_mineru.py                        云端 MinerU，以及超过 200 页时的本机 MinerU
src/text_splitter.py                     按页分块，页码来自 content_list
src/ingestion.py                         DashScope 向量化，每份报告一个 faiss
src/retrieval.py                         按公司检索，HybridRetriever 负责向量召回加重排
src/reranking.py                         LLM 相关分与向量分融合
src/questions_processing.py              抽公司、检索、作答、校验页码
src/api_requests.py                      按题型选择提示词并调用模型
src/prompts.py                           提示词与答案 JSON 结构
data/stock_data/pdf_reports              原始 PDF
data/stock_data/debug_data/03_reports_markdown
                                         MinerU 产出的 markdown
data/stock_data/debug_data/mineru_zips   解析压缩包和解压目录
data/stock_data/databases/chunked_reports
                                         分块 JSON
data/stock_data/databases/vector_dbs     每家公司一个 .faiss
data/stock_data/subset.csv               sha1、文件名、公司名
data/stock_data/questions.json           批量问题


配置
----

界面使用 pipeline.py 里的 max_config：开启向量库、按页扩回、LLM 重排，作答模型 qwen-plus。
答案文件名带 config_suffix，所以是 answers_qwen_plus.json。
