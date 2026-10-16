import click
from pathlib import Path
from src.pipeline import Pipeline, configs, preprocess_configs

# 相对本文件定位数据目录，不依赖启动时的工作目录
DATA_ROOT = Path(__file__).resolve().parent / "data" / "stock_data"

@click.group()
def cli():
    """Pipeline CLI：MinerU 主线建库与批量问答。"""
    pass

@cli.command()
@click.option('--config', type=click.Choice(['ser_tab', 'no_ser_tab']), default='no_ser_tab', help='Configuration preset to use')
def process_reports(config):
    """对 markdown 报告分块并创建向量库。"""
    root_path = DATA_ROOT
    run_config = preprocess_configs[config]
    pipeline = Pipeline(root_path, run_config=run_config)
    
    click.echo(f"Processing reports (config={config})...")
    pipeline.process_parsed_reports()

@cli.command()
@click.option('--config', type=click.Choice(['base', 'pdr', 'max']), default='max', help='Configuration preset to use')
def process_questions(config):
    """批量处理 questions.json 并写出 answers。"""
    root_path = DATA_ROOT
    run_config = configs[config]
    pipeline = Pipeline(root_path, run_config=run_config)
    
    click.echo(f"Processing questions (config={config})...")
    pipeline.process_questions()

if __name__ == '__main__':
    cli()
