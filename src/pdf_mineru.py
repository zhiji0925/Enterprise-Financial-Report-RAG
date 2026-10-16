import os
import shutil
import requests
import time
import zipfile
from pathlib import Path

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


def parse_local_pdfs(pdf_paths, output_md_dir, work_dir, model_version="pipeline"):
    """
    用 MinerU 解析本地 PDF，每个 PDF 产出一份同名 markdown。
    :return: {pdf文件名: markdown路径}
    """
    output_md_dir = Path(output_md_dir)
    work_dir = Path(work_dir)
    output_md_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    saved = {}
    pdf_paths = [Path(p) for p in pdf_paths]
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
