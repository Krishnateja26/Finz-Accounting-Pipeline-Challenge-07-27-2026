from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProcessingStatus


class RawTransaction(BaseModel):
    """Exactly what was uploaded, one document per source row.

    This document is NEVER edited after creation. Corrections, normalization,
    and classification all live on the `Transaction` document instead, which
    references this record via `raw_record_ids`. This is what lets us prove
    every source row is traceable and that nothing silently disappears.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = Field(default=None, alias="_id")
    import_batch_id: str
    source_file: str
    source_row_number: int
    raw_record: dict[str, Any]  # the row exactly as uploaded, original column names
    ingested_at: datetime = Field(default_factory=datetime.utcnow)
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    processing_error: str | None = None
    normalized_transaction_id: str | None = None  # set once linked to a Transaction
