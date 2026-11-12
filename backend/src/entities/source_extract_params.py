import os

from pydantic import BaseModel, Field
from fastapi import Form
from typing import Optional

class SourceScanExtractParams(BaseModel):
    source_url: Optional[str] = Field(None, description="Source URL")
    aws_access_key_id: Optional[str] = Field(None, description="AWS Access Key ID")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS Secret Access Key")
    wiki_query: Optional[str] = Field(None, description="Wikipedia query")
    model: str = Field(..., description="Model name")
    gcs_bucket_name: Optional[str] = Field(None, description="GCS bucket name")
    gcs_bucket_folder: Optional[str] = Field(None, description="GCS bucket folder")
    source_type: Optional[str] = Field(None, description="Source type")
    gcs_project_id: Optional[str] = Field(None, description="GCS project ID")
    access_token: Optional[str] = Field(None, description="Access token")
    gcs_blob_filename: Optional[str] = Field(None, description="GCS blob filename")
    file_name: Optional[str] = Field(None, description="File name")
    allowedNodes: Optional[str] = Field(None, description="Allowed nodes")
    allowedRelationship: Optional[str] = Field(None, description="Allowed relationships")
    token_chunk_size: Optional[int] = Field(None, description="Token chunk size")
    chunk_overlap: Optional[int] = Field(None, description="Chunk overlap")
    chunks_to_combine: Optional[int] = Field(None, description="Chunks to combine")
    language: Optional[str] = Field(None, description="Language")
    retry_condition: Optional[str] = Field(None, description="Retry condition")
    additional_instructions: Optional[str] = Field(None, description="Additional instructions")
    embedding_provider: Optional[str] = Field(None, description="Embedding provider")
    embedding_model: Optional[str] = Field(None, description="Embedding model")
    extraction_method: Optional[str] = Field(None, description="Extraction method: standard | edc (model=edc is also treated as edc)")
    # EDC の初期スキーマの指定（取り込みごと。省略時は backend の設定 LLM_MODEL_CONFIG_EDC・EDC_ENRICH_SCHEMA に従う）
    edc_doctype: Optional[str] = Field(None, description="EDC doctype: auto | none | <registered doctype name>")
    edc_schema_mode: Optional[str] = Field(None, description="EDC schema mode: fixed | fixed_extended")

def _or_default(value: Optional[int], env_name: str, fallback: int) -> int:
    """取り込みの引数が省かれたときの既定値。画面を通さず API を直接呼んでも同じ大きさになるようにする"""
    if value is not None:
        return value
    try:
        return int(os.environ.get(env_name) or fallback)
    except ValueError:
        return fallback


def get_source_scan_extract_params(
    source_url: Optional[str] = Form(None),
    aws_access_key_id: Optional[str] = Form(None),
    aws_secret_access_key: Optional[str] = Form(None),
    wiki_query: Optional[str] = Form(None),
    model: str = Form(...),
    gcs_bucket_name: Optional[str] = Form(None),
    gcs_bucket_folder: Optional[str] = Form(None),
    source_type: Optional[str] = Form(None),
    gcs_project_id: Optional[str] = Form(None),
    access_token: Optional[str] = Form(None),
    gcs_blob_filename: Optional[str] = Form(None),
    file_name: Optional[str] = Form(None),
    allowedNodes: Optional[str] = Form(None),
    allowedRelationship: Optional[str] = Form(None),
    token_chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
    chunks_to_combine: Optional[int] = Form(None),
    language: Optional[str] = Form(None),
    retry_condition: Optional[str] = Form(None),
    additional_instructions: Optional[str] = Form(None),
    embedding_provider: Optional[str] = Form(None),
    embedding_model: Optional[str] = Form(None),
    extraction_method: Optional[str] = Form(None),
    edc_doctype: Optional[str] = Form(None),
    edc_schema_mode: Optional[str] = Form(None),
) -> SourceScanExtractParams:
    return SourceScanExtractParams(
        source_url=source_url,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        wiki_query=wiki_query,
        model=model,
        gcs_bucket_name=gcs_bucket_name,
        gcs_bucket_folder=gcs_bucket_folder,
        source_type=source_type,
        gcs_project_id=gcs_project_id,
        access_token=access_token,
        gcs_blob_filename=gcs_blob_filename,
        file_name=file_name,
        allowedNodes=allowedNodes,
        allowedRelationship=allowedRelationship,
        token_chunk_size=_or_default(token_chunk_size, "DEFAULT_TOKEN_CHUNK_SIZE", 512),
        chunk_overlap=_or_default(chunk_overlap, "DEFAULT_CHUNK_OVERLAP", 50),
        chunks_to_combine=_or_default(chunks_to_combine, "DEFAULT_CHUNKS_TO_COMBINE", 1),
        language=language,
        retry_condition=retry_condition,
        additional_instructions=additional_instructions,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        extraction_method=extraction_method,
        edc_doctype=edc_doctype,
        edc_schema_mode=edc_schema_mode
    )