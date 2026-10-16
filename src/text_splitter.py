import json
import re
import tiktoken
from pathlib import Path
from typing import List, Dict, Optional, Tuple
# LangChain 1.x 起分块器独立为 langchain_text_splitters
from langchain_text_splitters import RecursiveCharacterTextSplitter
import pandas as pd
import os

# 文本分块工具类，支持按页分块、表格插入、token统计等
class TextSplitter():
    def _get_serialized_tables_by_page(self, tables: List[Dict]) -> Dict[int, List[Dict]]:
        """按页分组已序列化表格，便于后续插入到对应页面分块中"""
        tables_by_page = {}
        for table in tables:
            if 'serialized' not in table:
                continue
                
            page = table['page']
            if page not in tables_by_page:
                tables_by_page[page] = []
            
            table_text = "\n".join(
                block["information_block"] 
                for block in table["serialized"]["information_blocks"]
            )
            
            tables_by_page[page].append({
                "page": page,
                "text": table_text,
                "table_id": table["table_id"],
                "length_tokens": self.count_tokens(table_text)
            })
            
        return tables_by_page

    def _split_report(self, file_content: Dict[str, any], serialized_tables_report_path: Optional[Path] = None) -> Dict[str, any]:
        """将报告按页分块，保留markdown表格内容，可选插入序列化表格块。"""
        chunks = []
        chunk_id = 0
        
        tables_by_page = {}
        if serialized_tables_report_path is not None:
            # 加载序列化表格，按页分组
            with open(serialized_tables_report_path, 'r', encoding='utf-8') as f:
                parsed_report = json.load(f)
            tables_by_page = self._get_serialized_tables_by_page(parsed_report.get('tables', []))
        
        for page in file_content['content']['pages']:
            # 普通文本分块
            page_chunks = self._split_page(page)
            for chunk in page_chunks:
                chunk['id'] = chunk_id
                chunk['type'] = 'content'
                chunk_id += 1
                chunks.append(chunk)
            
            # 插入序列化表格分块
            if tables_by_page and page['page'] in tables_by_page:
                for table in tables_by_page[page['page']]:
                    table['id'] = chunk_id
                    table['type'] = 'serialized_table'
                    chunk_id += 1
                    chunks.append(table)
        
        file_content['content']['chunks'] = chunks
        return file_content

    def count_tokens(self, string: str, encoding_name="o200k_base"):
        # 统计字符串的token数，支持自定义编码
        encoding = tiktoken.get_encoding(encoding_name)
        tokens = encoding.encode(string)
        token_count = len(tokens)
        return token_count

    def _split_page(self, page: Dict[str, any], chunk_size: int = 300, chunk_overlap: int = 50) -> List[Dict[str, any]]:
        """将单页文本分块，保留原始markdown表格。"""
        text_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name="gpt-4o",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        chunks = text_splitter.split_text(page['text'])
        chunks_with_meta = []
        for chunk in chunks:
            chunks_with_meta.append({
                "page": page['page'],
                "length_tokens": self.count_tokens(chunk),
                "text": chunk
            })
        return chunks_with_meta

    #对 json 文件分块，输出还是 json
    def split_all_reports(self, all_report_dir: Path, output_dir: Path, serialized_tables_dir: Optional[Path] = None):
        """
        批量处理目录下所有报告（json文件），对每个报告进行文本分块，并输出到目标目录。
        如果提供了序列化表格目录，会尝试将表格内容插入到对应页面的分块中。
        主要用于后续向量化和检索的预处理。
        参数：
            all_report_dir: 存放待处理报告json的目录
            output_dir: 分块后输出的目标目录
            serialized_tables_dir: （可选）存放序列化表格的目录
        """
        # 获取所有报告文件路径
        all_report_paths = list(all_report_dir.glob("*.json"))
        
        # 遍历每个报告文件
        for report_path in all_report_paths:
            serialized_tables_path = None
            # 如果提供了表格序列化目录，查找对应表格文件
            if serialized_tables_dir is not None:
                serialized_tables_path = serialized_tables_dir / report_path.name
                if not serialized_tables_path.exists():
                    print(f"警告：未找到 {report_path.name} 的序列化表格报告")
                
            # 读取报告内容
            with open(report_path, 'r', encoding='utf-8') as file:
                report_data = json.load(file)
                
            # 分块处理，插入表格分块（如有）
            updated_report = self._split_report(report_data, serialized_tables_path)
            # 确保输出目录存在
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # 写入分块后的报告到目标目录
            with open(output_dir / report_path.name, 'w', encoding='utf-8') as file:
                json.dump(updated_report, file, indent=2, ensure_ascii=False)
                
        # 输出处理文件数统计
        print(f"已分块处理 {len(all_report_paths)} 个文件")

    def _build_line_page_anchors(self, md_lines: List[str]) -> List[Tuple[int, int]]:
        """
        从 markdown 目录与正文标题建立 (行号, PDF页码) 锚点，用于给分块补页码。
        MinerU 导出的 md 通常没有逐行页码，但目录里有「第X节 ... N」。
        """
        toc_pages = {}  # 节名 -> 页码
        in_toc = False
        toc_pat = re.compile(
            r'^(第[一二三四五六七八九十百零〇\d]+节)\s*.+?[\.．…\s>]*?(\d+)\s*$'
        )
        for line in md_lines:
            s = line.strip()
            if s.startswith('#') and '目录' in s:
                in_toc = True
                continue
            if not in_toc:
                continue
            # 目录结束：正文一级标题出现
            if s.startswith('#') and '目录' not in s:
                break
            m = toc_pat.match(s)
            if m:
                toc_pages[m.group(1)] = int(m.group(2))

        anchors: List[Tuple[int, int]] = []
        header_pat = re.compile(r'^#\s*(第[一二三四五六七八九十百零〇\d]+节)\b')
        for idx, line in enumerate(md_lines, start=1):
            m = header_pat.match(line.strip())
            if not m:
                continue
            sec = m.group(1)
            if sec in toc_pages:
                anchors.append((idx, toc_pages[sec]))

        # 去重：同一节只保留第一次出现
        seen = set()
        uniq = []
        for line_no, page in anchors:
            if page in seen:
                continue
            seen.add(page)
            uniq.append((line_no, page))
        uniq.sort(key=lambda x: x[0])
        return uniq

    def _estimate_page_from_line(self, line_no: int, anchors: List[Tuple[int, int]]) -> int:
        """按目录锚点线性插值，估计某行对应的 PDF 页码（1-based）。"""
        if line_no <= 0:
            return 0
        if not anchors:
            # 无目录时按经验密度粗估（约每 28 行一页）
            return max(1, (line_no - 1) // 28 + 1)

        if line_no <= anchors[0][0]:
            # 目录前的封面/重要提示，通常从第 1 页起
            first_line, first_page = anchors[0]
            if first_line <= 1:
                return first_page
            ratio = (line_no - 1) / max(first_line - 1, 1)
            return max(1, int(round(1 + ratio * max(first_page - 1, 0))))

        for i in range(len(anchors) - 1):
            l0, p0 = anchors[i]
            l1, p1 = anchors[i + 1]
            if line_no < l1:
                ratio = (line_no - l0) / max(l1 - l0, 1)
                return max(1, int(round(p0 + ratio * (p1 - p0))))

        # 最后一节之后：按末两锚点的行/页密度外推
        l0, p0 = anchors[-1]
        if len(anchors) >= 2:
            l_prev, p_prev = anchors[-2]
            dens = max((l0 - l_prev) / max(p0 - p_prev, 1), 1.0)
        else:
            dens = 28.0
        return max(p0, int(round(p0 + (line_no - l0) / dens)))

    def split_markdown_file(self, md_path: Path, chunk_size: int = 30, chunk_overlap: int = 5):
        """
        按行分割 markdown 文件，每个分块记录起止行号、估计页码和内容。
        :param md_path: markdown 文件路径
        :param chunk_size: 每个分块的最大行数
        :param chunk_overlap: 分块重叠行数
        :return: 分块列表
        """
        with open(md_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        anchors = self._build_line_page_anchors(lines)
        chunks = []
        i = 0
        total_lines = len(lines)
        while i < total_lines:
            start = i
            end = min(i + chunk_size, total_lines)
            chunk_text = ''.join(lines[start:end])
            start_line = start + 1  # 行号从1开始
            chunks.append({
                'lines': [start_line, end],
                'page': self._estimate_page_from_line(start_line, anchors),
                'text': chunk_text
            })
            i += chunk_size - chunk_overlap
        return chunks

    def split_markdown_reports(self, all_md_dir: Path, output_dir: Path, chunk_size: int = 20, chunk_overlap: int = 5, subset_csv: Path = None):
        """
        批量处理目录下所有 markdown 文件，分块并输出为 json 文件到目标目录。
        :param all_md_dir: 存放 .md 文件的目录
        :param output_dir: 输出 .json 文件的目录
        :param chunk_size: 每个分块的最大行数
        :param chunk_overlap: 分块重叠行数
        :param subset_csv: subset.csv 路径，用于建立 file_name 到 company_name 的映射
        """
        # 建立 file_name（去扩展名）到 company_name 的映射
        file2company = {}
        file2sha1 = {}
        if subset_csv is not None and os.path.exists(subset_csv):
            # 优先尝试 utf-8，失败则尝试 gbk
            try:
                df = pd.read_csv(subset_csv, encoding='utf-8')
            except UnicodeDecodeError:
                print('警告：subset.csv 不是 utf-8 编码，自动尝试 gbk 编码...')
                df = pd.read_csv(subset_csv, encoding='gbk')
            # 自动识别主键列
            if 'file_name' in df.columns:
                for _, row in df.iterrows():
                    file_no_ext = os.path.splitext(str(row['file_name']))[0]
                    file2company[file_no_ext] = row['company_name']
                    if 'sha1' in row:
                        file2sha1[file_no_ext] = row['sha1']
            elif 'sha1' in df.columns:
                for _, row in df.iterrows():
                    file_no_ext = str(row['sha1'])
                    file2company[file_no_ext] = row['company_name']
                    file2sha1[file_no_ext] = row['sha1']
            else:
                raise ValueError('subset.csv 缺少 file_name 或 sha1 列，无法建立文件名到公司名的映射')
        
        all_md_paths = list(all_md_dir.glob("*.md"))
        output_dir.mkdir(parents=True, exist_ok=True)
        mineru_root = all_md_dir.parent / "mineru_zips"
        for md_path in all_md_paths:
            # 用 MinerU content_list 的 page_idx 标真实页码，不再按目录插值
            try:
                chunks = self.split_mineru_content_list(
                    md_path.stem, mineru_root, chunk_size, chunk_overlap
                )
            except FileNotFoundError as exc:
                print(f"跳过 {md_path.name}，保留已有分块: {exc}")
                continue
            output_json_path = output_dir / (md_path.stem + ".json")
            # 查找 company_name 和 sha1
            file_no_ext = md_path.stem
            company_name = file2company.get(file_no_ext, "")
            sha1 = file2sha1.get(file_no_ext, "")
            # metainfo 只保留 sha1、company_name、file_name 字段
            metainfo = {"sha1": sha1, "company_name": company_name, "file_name": md_path.name}
            with open(output_json_path, 'w', encoding='utf-8') as f:
                json.dump({"metainfo": metainfo, "content": {"chunks": chunks}}, f, ensure_ascii=False, indent=2)
            print(f"已处理: {md_path.name} -> {output_json_path.name}")
        print(f"共分割 {len(all_md_paths)} 个 markdown 文件")

    def _find_content_list(self, stem: str, mineru_root: Path) -> Path:
        """定位某份报告的 MinerU content_list（不含 v2）。"""
        folder = mineru_root / f"{stem}.zip_unzip"
        if not folder.exists():
            raise FileNotFoundError(f"未找到 MinerU 解压目录: {folder}")
        matches = [
            p for p in folder.glob("*content_list.json")
            if not p.name.endswith("content_list_v2.json")
        ]
        if len(matches) != 1:
            raise FileNotFoundError(f"{stem} 的 content_list 数量异常: {matches}")
        return matches[0]

    def _table_fields(self, item: Dict) -> Tuple[str, str, str]:
        """拆出表题、表体、表注。"""
        caption = "\n".join(str(x) for x in (item.get("table_caption") or []) if x)
        body = str(item.get("table_body") or "")
        footnote = "\n".join(str(x) for x in (item.get("table_footnote") or []) if x)
        return caption, body, footnote

    def _join_table(self, caption: str, body: str, footnote: str) -> str:
        parts = [part for part in (caption, body, footnote) if part]
        return "\n".join(parts).strip()

    def _split_table_pieces(self, caption: str, body: str, footnote: str, char_limit: int) -> List[str]:
        """
        表格保持完整。超过向量长度上限时按行切开，每一块都带表题。
        """
        whole = self._join_table(caption, body, footnote)
        if len(whole) <= char_limit:
            return [whole] if whole else []
        rows = re.findall(r"<tr\b[^>]*>.*?</tr>", body, flags=re.IGNORECASE | re.DOTALL)
        if not rows:
            return [whole]
        pieces = []
        bucket: List[str] = []

        def render(row_list: List[str], with_footnote: bool) -> str:
            table_html = "<table>" + "".join(row_list) + "</table>"
            note = footnote if with_footnote else ""
            return self._join_table(caption, table_html, note)

        for row in rows:
            if bucket and len(render(bucket + [row], False)) > char_limit:
                pieces.append(render(bucket, False))
                bucket = [row]
            else:
                bucket.append(row)
        if bucket:
            pieces.append(render(bucket, True))
        return [piece for piece in pieces if piece]

    def _split_long_text(self, text: str, chunk_size: int, chunk_overlap: int, char_limit: int) -> List[str]:
        """长正文按行切开；不超过行数和字数上限时保持原样。"""
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return []
        pieces = []
        step = chunk_size - chunk_overlap
        i = 0
        while i < len(lines):
            window = lines[i:i + chunk_size]
            buf = ""
            for line in window:
                candidate = line if not buf else buf + "\n" + line
                if buf and len(candidate) > char_limit:
                    pieces.append(buf)
                    buf = line
                else:
                    buf = candidate
            if buf:
                pieces.append(buf)
            if i + chunk_size >= len(lines):
                break
            i += step
        return pieces

    def _page_blocks(self, content_list: List[Dict]) -> Dict[int, List[Dict]]:
        """按 PDF 页收集正文和表格，页眉页脚不进入切片。"""
        pages: Dict[int, List[Dict]] = {}
        for item in content_list:
            if "page_idx" not in item:
                continue
            page = int(item["page_idx"]) + 1
            if item.get("type") == "text":
                text = (item.get("text") or "").strip()
                if text:
                    pages.setdefault(page, []).append({"kind": "text", "text": text})
            elif item.get("type") == "table":
                caption, body, footnote = self._table_fields(item)
                if caption or body or footnote:
                    pages.setdefault(page, []).append({
                        "kind": "table",
                        "caption": caption,
                        "body": body,
                        "footnote": footnote,
                    })
        return pages

    def split_mineru_content_list(self, stem: str, mineru_root: Path, chunk_size: int = 20, chunk_overlap: int = 5, char_limit: int = 2048):
        """
        按页分情况切块：
        短页且不超过向量长度时，整页作为一个切片。
        页内行数或字数超限时，正文按行切，表格整表保留；超长表按行切开并重复表题。
        """
        if chunk_size <= chunk_overlap:
            raise ValueError("chunk_size 必须大于 chunk_overlap")
        content_path = self._find_content_list(stem, mineru_root)
        with open(content_path, "r", encoding="utf-8") as f:
            content_list = json.load(f)
        chunks = []

        def add_chunk(page: int, text: str):
            text = text.strip()
            if not text:
                return
            chunks.append({
                "id": len(chunks),
                "page": page,
                "lines": [1, len(text.splitlines())],
                "text": text,
            })

        page_blocks = self._page_blocks(content_list)
        for page in sorted(page_blocks):
            blocks = page_blocks[page]
            page_parts = []
            for block in blocks:
                if block["kind"] == "text":
                    page_parts.append(block["text"])
                else:
                    page_parts.append(self._join_table(block["caption"], block["body"], block["footnote"]))
            page_text = "\n".join(part for part in page_parts if part).strip()
            # 内容不多的页直接整页成块，表格自然完整
            if page_text and len(page_text.splitlines()) <= chunk_size and len(page_text) <= char_limit:
                add_chunk(page, page_text)
                continue
            text_buf: List[str] = []

            def flush_text():
                if not text_buf:
                    return
                blob = "\n".join(text_buf).strip()
                text_buf.clear()
                for piece in self._split_long_text(blob, chunk_size, chunk_overlap, char_limit):
                    add_chunk(page, piece)

            for block in blocks:
                if block["kind"] == "text":
                    text_buf.append(block["text"])
                    continue
                flush_text()
                for piece in self._split_table_pieces(
                    block["caption"], block["body"], block["footnote"], char_limit
                ):
                    add_chunk(page, piece)
            flush_text()
        return chunks
