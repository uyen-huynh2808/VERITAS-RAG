import os
from typing import Optional

import yaml
from pydantic import BaseModel


class DatabaseConfig(BaseModel):
    incoming_dir: str
    duckdb_path: str
    bronze_dir: str
    silver_dir: str
    gold_dir: str
    quarantine_dir: str


class ChunkingConfig(BaseModel):
    article_level: bool
    max_tokens: int
    overlap: int


class ParserConfig(BaseModel):
    prefer_docling: bool
    header_ocr_engine: str


class ContractConfig(BaseModel):
    rules_path: str


class ModelConfig(BaseModel):
    embedding_model: str
    embedding_batch_size: int
    embedding_max_length: int
    llm_model: str
    generation_temperature: float
    generation_max_tokens: int
    generation_reasoning_effort: str = "low"
    generation_include_reasoning: bool = False


class RetrievalConfig(BaseModel):
    top_k: int
    bm25_k1: float
    bm25_b: float
    supersession_rules_path: Optional[str] = None


class BenchmarkConfig(BaseModel):
    questions_path: str
    output_path: str
    gold_path: str
    results_dir: str
    cache_dir: str
    contract_metrics_path: str


class SystemConfig(BaseModel):
    seed: int
    db: DatabaseConfig
    chunking: ChunkingConfig
    parser: ParserConfig
    contracts: ContractConfig
    models: ModelConfig
    retrieval: RetrievalConfig
    benchmark: BenchmarkConfig


def load_system_config(
    config_path: str = "config/config.yaml",
) -> SystemConfig:

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return SystemConfig(
        seed=data["system"]["seed"],
        db=DatabaseConfig(
            **data.get("db", {})
        ),
        chunking=ChunkingConfig(
            **data.get("chunking", {})
        ),
        parser=ParserConfig(
            **data.get("parser", {})
        ),
        contracts=ContractConfig(
            **data.get("contracts", {})
        ),
        models=ModelConfig(
            **data.get("models", {})
        ),
        retrieval=RetrievalConfig(
            **data.get("retrieval", {})
        ),
        benchmark=BenchmarkConfig(
            **data.get("benchmark", {})
        ),
    )
