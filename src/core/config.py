import os

import yaml
from pydantic import BaseModel, Field


class DatabaseConfig(BaseModel):
    duckdb_path: str = "data/silver_parquet/lakehouse.duckdb"
    bronze_dir: str = "data/bronze_pdf"
    silver_dir: str = "data/silver_parquet"
    gold_dir: str = "data/gold_vectors"
    quarantine_dir: str = "data/quarantine"


class ContractConfig(BaseModel):
    rules_path: str = "config/contract_rules.json"


class ModelConfig(BaseModel):
    embedding_model: str = "BAAI/bge-m3"
    llm_provider: str = "gemini"
    llm_model: str = "gemini-1.5-flash"


class SystemConfig(BaseModel):
    seed: int = 42
    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    contracts: ContractConfig = Field(default_factory=ContractConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)


def load_system_config(
    config_path: str = "config/config.yaml",
) -> SystemConfig:

    if not os.path.exists(config_path):
        return SystemConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return SystemConfig(
        seed=data.get("system", {}).get("seed", 42),
        db=DatabaseConfig(**data.get("db", {})),
        contracts=ContractConfig(
            **data.get("contracts", {})
        ),
        models=ModelConfig(
            **data.get("models", {})
        ),
    )