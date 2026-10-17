import os
import json
import shutil
import requests
import time
import zipfile
from pathlib import Path
from PyPDF2 import PdfReader

# 云端精准解析单文件最多 200 页，超出后改走本机 MinerU
CLOUD_PAGE_LIMIT = 200
LOCAL_MINERU_API_BASE = os.getenv("MINERU_API_BASE", "http://127.0.0.1:8000")
LOCAL_MINERU_TIER = "standard"
LOCAL_MINERU_POLL_INTERVAL = 2
LOCAL_MINERU_POLL_TIMEOUT = 7200
LOCAL_MINERU_PENDING_STUCK = 180
LOCAL_MINERU_IN_PROGRESS = {"queued", "running", "waiting-file", "pending", "converting"}


class MinerUQueueStuck(Exception):
    """本机 MinerU 任务长时间停在排队，未进入 running。"""

# 旧版写死的 Token 已失效；本地上传解析使用环境变量 MINERU_API_KEY
api_key = os.getenv(
    "MINERU_API_KEY",
    "eyJ0eXBleIjoiSldUIiwiYWxnIjoiSFM1MTIifQ",
)

def get_task_id(file_name):
    url='https://mineru.net/api/v4/extract/task'
    header = {
        'Content-Type':'application/json',
        "Authorization":f"Bearer {api_key}".format(api_key)
    }
    pdf_url = 'https://vl-image.oss-cn-shanghai.aliyuncs.com/pdf/' + file_name
    data = {
        'url':pdf_url,
        'is_ocr':True,
        'enable_formula': False,
    }

    res = requests.post(url,headers=header,json=data)
    print(res.status_code)
    print(res.json())
    print(res.json()["data"])
    task_id = res.json()["data"]['task_id']
    return task_id

def get_result(task_id):
    url = f'https://mineru.net/api/v4/extract/task/{task_id}'
    header = {
        'Content-Type':'application/json',
        "Authorization":f"Bearer {api_key}".format(api_key)
    }

    while True:
        res = requests.get(url, headers=header)
        result = res.json()["data"]
        print(result)
        state = result.get('state')
        err_msg = result.get('err_msg', '')
        # 如果任务还在进行中，等待后重试
        if state in ['pending', 'running']:
            print("任务未完成，等待5秒后重试...")
            time.sleep(5)
            continue
        # 如果有错误，输出错误信息
        if err_msg:
            print(f"任务出错: {err_msg}")
            return
        # 如果任务完成，下载文件
        if state == 'done':
            full_zip_url = result.get('full_zip_url')
            if full_zip_url:
                local_filename = f"{task_id}.zip"
                print(f"开始下载: {full_zip_url}")
                r = requests.get(full_zip_url, stream=True)
                with open(local_filename, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                print(f"下载完成，已保存到: {local_filename}")
                # 下载完成后自动解压
                unzip_file(local_filename)
            else:
                print("未找到 full_zip_url，无法下载。")
            return
        # 其他未知状态
        print(f"未知状态: {state}")
        return

# 解压zip文件的函数
def unzip_file(zip_path, extract_dir=None):
    """
    解压指定的zip文件到目标文件夹。
    :param zip_path: zip文件路径
    :param extract_dir: 解压目标文件夹，默认为zip同名目录
    """
    if extract_dir is None:
        extract_dir = zip_path.rstrip('.zip')
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)
    print(f"已解压到: {extract_dir}")

def _auth_header():
    token = os.getenv("MINERU_API_KEY") or api_key
    if not token or not token.startswith("sk-"):
        raise RuntimeError("未配置可用的 MINERU_API_KEY，无法调用 MinerU")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def apply_local_upload(file_names, model_version="pipeline"):
    """为一批本地 PDF 申请 MinerU 上传地址。单次不超过 50 个。"""
    url = "https://mineru.net/api/v4/file-urls/batch"
    data = {
        "files": [
            {"name": name, "is_ocr": True, "data_id": f"pdf_{i}"}
            for i, name in enumerate(file_names)
        ],
        "model_version": model_version,
        "enable_formula": False,
        "enable_table": True,
        "language": "ch",
    }
    res = requests.post(url, headers=_auth_header(), json=data, timeout=60)
    body = res.json()
    if res.status_code != 200 or body.get("code") != 0:
        raise RuntimeError(f"申请 MinerU 上传地址失败: {res.status_code} {body}")
    return body["data"]["batch_id"], body["data"]["file_urls"]


def upload_local_pdf(upload_url, pdf_path):
    """把本地 PDF 传到 MinerU 签名地址。不要设置 Content-Type。"""
    with open(pdf_path, "rb") as f:
        res = requests.put(upload_url, data=f, timeout=300)
    if res.status_code not in (200, 201):
        raise RuntimeError(f"上传失败 {pdf_path}: {res.status_code} {res.text[:300]}")


def poll_batch_results(batch_id, timeout_sec=7200, interval_sec=8):
    """轮询批量解析结果，直到全部结束或超时。"""
    url = f"https://mineru.net/api/v4/extract-results/batch/{batch_id}"
    header = _auth_header()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = requests.get(url, headers=header, timeout=60)
        body = res.json()
        if res.status_code != 200 or body.get("code") != 0:
            raise RuntimeError(f"查询 MinerU 批次失败: {res.status_code} {body}")
        results = body.get("data", {}).get("extract_result") or []
        states = [item.get("state") for item in results]
        print(f"批次 {batch_id} 状态: {states}")
        if results and all(s in ("done", "failed") for s in states):
            return results
        time.sleep(interval_sec)
    raise TimeoutError(f"MinerU 批次 {batch_id} 超时未完成")


def pdf_page_count(pdf_path):
    """读取 PDF 页数，用来判断是否超过云端页数上限。"""
    reader = PdfReader(str(pdf_path))
    return len(reader.pages)


def _local_mineru_headers():
    """本机 MinerU 的 JSON 请求头。"""
    return {"Content-Type": "application/json"}


def _local_mineru_raise_http(action, resp):
    """把本机 MinerU 的 HTTP 错误转成可读异常。"""
    text = resp.text
    try:
        payload = resp.json()
        err = payload.get("error") or payload
        text = json.dumps(err, ensure_ascii=False)
    except Exception:
        pass
    raise RuntimeError(f"{action}失败 HTTP {resp.status_code}: {text}")


def _local_mineru_abs_url(url):
    """把本机返回的相对路径拼成可请求地址。"""
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return LOCAL_MINERU_API_BASE.rstrip("/") + "/" + url.lstrip("/")


def _local_mineru_download_file(file_id):
    """下载本机解析产物。"""
    url = f"{LOCAL_MINERU_API_BASE}/v1/files/{file_id}/content"
    resp = requests.get(url, timeout=300, allow_redirects=True)
    if resp.status_code != 200:
        _local_mineru_raise_http("MinerU下载产物", resp)
    return resp.content


def parse_with_local_mineru(pdf_path, output_md_dir, work_dir):
    """
    调用本机 MinerU：创建上传、写入内容、完成上传、提交解析、轮询并保存 zip。
    产物目录与云端一致，供后续按 content_list 分块。
    """
    pdf_path = Path(pdf_path)
    output_md_dir = Path(output_md_dir)
    work_dir = Path(work_dir)
    output_md_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    health = requests.get(f"{LOCAL_MINERU_API_BASE}/v1/health", timeout=10)
    if health.status_code != 200:
        _local_mineru_raise_http("MinerU健康检查", health)

    file_size = pdf_path.stat().st_size
    apply_resp = requests.post(
        f"{LOCAL_MINERU_API_BASE}/v1/uploads",
        headers=_local_mineru_headers(),
        json={
            "filename": pdf_path.name,
            "bytes": file_size,
            "mime_type": "application/pdf",
            "purpose": "parse",
        },
        timeout=60,
    )
    if apply_resp.status_code != 200:
        _local_mineru_raise_http("MinerU创建上传", apply_resp)
    upload_info = apply_resp.json()
    upload_id = upload_info["id"]
    print(f"本机 MinerU upload_id={upload_id}，开始上传 {pdf_path.name}")

    file_bytes = pdf_path.read_bytes()
    upload_url = _local_mineru_abs_url(upload_info.get("upload_url"))
    if upload_url:
        put_headers = dict(upload_info.get("upload_headers") or {})
        if "Content-Type" not in put_headers:
            put_headers["Content-Type"] = "application/octet-stream"
        upload_resp = requests.put(upload_url, data=file_bytes, headers=put_headers, timeout=600)
    else:
        upload_resp = requests.put(
            f"{LOCAL_MINERU_API_BASE}/v1/uploads/{upload_id}/content",
            data=file_bytes,
            headers={"Content-Type": "application/octet-stream"},
            timeout=600,
        )
    if upload_resp.status_code not in (200, 201, 204):
        _local_mineru_raise_http("MinerU文件上传", upload_resp)

    complete_resp = requests.post(
        f"{LOCAL_MINERU_API_BASE}/v1/uploads/{upload_id}/complete",
        headers=_local_mineru_headers(),
        json={},
        timeout=60,
    )
    if complete_resp.status_code != 200:
        _local_mineru_raise_http("MinerU完成上传", complete_resp)
    complete_info = complete_resp.json()
    file_obj = complete_info.get("file") or {}
    file_id = file_obj.get("id")
    if not file_id:
        raise RuntimeError(f"MinerU完成上传后没有 file_id: {complete_info}")
    print("上传完成，提交本机解析任务")

    job_resp = requests.post(
        f"{LOCAL_MINERU_API_BASE}/v1/parse/jobs",
        headers=_local_mineru_headers(),
        json={
            "files": [{
                "source": {"type": "file_id", "file_id": file_id},
                "page_range": "all",
            }],
            "tier": LOCAL_MINERU_TIER,
            "ocr_mode": "ocr",
            "output_formats": ["markdown", "zip"],
        },
        timeout=60,
    )
    if job_resp.status_code not in (200, 202):
        _local_mineru_raise_http("MinerU创建解析任务", job_resp)
    job_info = job_resp.json()
    job_id = job_info["job_id"]
    print(f"本机 MinerU job_id={job_id}，等待解析")

    started = time.time()
    pending_since = None
    result_item = None
    while True:
        if time.time() - started > LOCAL_MINERU_POLL_TIMEOUT:
            raise TimeoutError(f"本机 MinerU 解析超时: {pdf_path.name}")
        poll_resp = requests.get(f"{LOCAL_MINERU_API_BASE}/v1/parse/jobs/{job_id}", timeout=30)
        if poll_resp.status_code != 200:
            _local_mineru_raise_http("MinerU查询任务", poll_resp)
        poll_result = poll_resp.json()
        state = poll_result.get("status", "")
        files = poll_result.get("files") or []
        item = files[0] if files else {}
        file_state = item.get("status") or state
        if state in {"completed", "partial"} and file_state == "completed":
            result_item = item
            break
        if state in {"failed", "canceled"} or file_state == "failed":
            err = item.get("error") or poll_result
            raise RuntimeError(f"本机 MinerU 解析失败: {err}")
        if state not in LOCAL_MINERU_IN_PROGRESS and file_state not in LOCAL_MINERU_IN_PROGRESS:
            raise RuntimeError(f"本机 MinerU 未知任务状态: {state}/{file_state}")
        if state == "queued":
            if pending_since is None:
                pending_since = time.time()
            elif time.time() - pending_since > LOCAL_MINERU_PENDING_STUCK:
                raise MinerUQueueStuck(f"本机 MinerU 队列无进展(state={state}): {pdf_path.name}")
        else:
            pending_since = None
        progress = poll_result.get("progress") or {}
        completed = progress.get("completed")
        total = progress.get("total")
        if completed is not None and total is not None:
            print(f"解析中: {state} {completed}/{total}")
        else:
            print(f"解析中: {state}")
        time.sleep(LOCAL_MINERU_POLL_INTERVAL)

    output_files = result_item.get("output_files") or {}
    zip_ref = output_files.get("zip") or {}
    zip_id = zip_ref.get("file_id")
    if not zip_id:
        raise RuntimeError(f"本机 MinerU 结果中没有 zip: {pdf_path.name}")
    print(f"下载本机解析结果: {pdf_path.name}")
    zip_path = work_dir / f"{pdf_path.stem}.zip"
    zip_path.write_bytes(_local_mineru_download_file(zip_id))
    md_path = output_md_dir / f"{pdf_path.stem}.md"
    _save_markdown_from_zip(zip_path, md_path)
    content_path = _export_content_list(Path(str(zip_path) + "_unzip"))
    print(f"已保存 markdown: {md_path}")
    print(f"已写出 content_list: {content_path}")
    return md_path


def _save_markdown_from_zip(zip_bytes_path, target_md_path):
    """从 MinerU 结果 zip 中取出 full.md（或唯一 md）写到目标路径。"""
    extract_dir = Path(str(zip_bytes_path) + "_unzip")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    with zipfile.ZipFile(zip_bytes_path, "r") as zip_ref:
        zip_ref.extractall(extract_dir)
    md_files = list(extract_dir.rglob("*.md"))
    full_md = next((p for p in md_files if p.name == "full.md"), None)
    chosen = full_md or (md_files[0] if len(md_files) == 1 else None)
    if chosen is None:
        raise RuntimeError(f"zip 中未找到 markdown: {zip_bytes_path} -> {[p.name for p in md_files]}")
    target_md_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(chosen, target_md_path)
    return target_md_path


def _export_content_list(extract_dir):
    """把本机 MinerU 的 structured_content 写成 content_list.json，供按页分块。"""
    extract_dir = Path(extract_dir)
    src = extract_dir / "structured_content.json"
    if not src.exists():
        raise FileNotFoundError(f"未找到 structured_content.json: {src}")
    data = json.loads(src.read_text(encoding="utf-8"))
    text_types = {"text", "doc_title", "paragraph_title", "index"}
    items = []
    for page in data.get("pages") or []:
        if page.get("page_idx") is None:
            continue
        page_idx = int(page["page_idx"])
        for block in page.get("blocks") or []:
            btype = block.get("type")
            if btype in text_types:
                text = str(block.get("content") or "").strip()
                if not text:
                    continue
                item = {"type": "text", "text": text, "page_idx": page_idx}
                if block.get("level") is not None:
                    item["text_level"] = block["level"]
                items.append(item)
            elif btype == "table":
                body = str(block.get("content") or "")
                captions = block.get("captions") or []
                footnotes = block.get("footnotes") or []
                if isinstance(captions, str):
                    captions = [captions] if captions else []
                if isinstance(footnotes, str):
                    footnotes = [footnotes] if footnotes else []
                captions = [str(x) for x in captions if x]
                footnotes = [str(x) for x in footnotes if x]
                if not (body.strip() or captions or footnotes):
                    continue
                items.append({
                    "type": "table",
                    "table_caption": captions,
                    "table_footnote": footnotes,
                    "table_body": body,
                    "page_idx": page_idx,
                })
    if not items:
        raise RuntimeError(f"structured_content 没有可分块内容: {src}")
    out = extract_dir / "content_list.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def parse_local_pdfs(pdf_paths, output_md_dir, work_dir, model_version="pipeline"):
    """
    用 MinerU 解析本地 PDF，每个 PDF 产出一份同名 markdown。
    不超过 200 页走云端；超过 200 页走本机 MinerU。
    :return: {pdf文件名: markdown路径}
    """
    output_md_dir = Path(output_md_dir)
    work_dir = Path(work_dir)
    output_md_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    saved = {}
    pdf_paths = [Path(p) for p in pdf_paths]
    cloud_paths = []
    for pdf_path in pdf_paths:
        page_count = pdf_page_count(pdf_path)
        if page_count > CLOUD_PAGE_LIMIT:
            print(f"{pdf_path.name} 共 {page_count} 页，超过 {CLOUD_PAGE_LIMIT} 页，改用本机 MinerU")
            saved[pdf_path.name] = parse_with_local_mineru(pdf_path, output_md_dir, work_dir)
        else:
            cloud_paths.append(pdf_path)
    pdf_paths = cloud_paths
    batch_size = 20
    for start in range(0, len(pdf_paths), batch_size):
        batch = pdf_paths[start:start + batch_size]
        names = [p.name for p in batch]
        print(f"申请上传 {len(batch)} 个 PDF: {names}")
        batch_id, file_urls = apply_local_upload(names, model_version=model_version)
        if len(file_urls) != len(batch):
            raise RuntimeError(f"上传地址数量不匹配: {len(file_urls)} != {len(batch)}")
        for pdf_path, upload_url in zip(batch, file_urls):
            print(f"上传: {pdf_path.name}")
            upload_local_pdf(upload_url, pdf_path)
        results = poll_batch_results(batch_id)
        by_name = {item.get("file_name"): item for item in results}
        for pdf_path in batch:
            item = by_name.get(pdf_path.name)
            if item is None:
                raise RuntimeError(f"批次结果缺少文件: {pdf_path.name}")
            if item.get("state") != "done":
                raise RuntimeError(f"解析失败 {pdf_path.name}: {item.get('err_msg') or item}")
            zip_url = item.get("full_zip_url")
            if not zip_url:
                raise RuntimeError(f"缺少结果压缩包: {pdf_path.name}")
            zip_path = work_dir / f"{pdf_path.stem}.zip"
            print(f"下载解析结果: {pdf_path.name}")
            resp = requests.get(zip_url, stream=True, timeout=300)
            resp.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            md_path = output_md_dir / f"{pdf_path.stem}.md"
            _save_markdown_from_zip(zip_path, md_path)
            saved[pdf_path.name] = md_path
            print(f"已保存 markdown: {md_path}")
    return saved


if __name__ == "__main__":
    file_name = '【财报】中芯国际：中芯国际2024年年度报告.pdf'
    task_id = get_task_id(file_name)
    print('task_id:', task_id)
    get_result(task_id)
