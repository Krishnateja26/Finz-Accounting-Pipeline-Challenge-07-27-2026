import io
import json
from hashlib import sha256

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.database import get_db
from app.services.gemini_classifier import GeminiClassificationError, classify_with_gemini
from app.services.ingestion import ingest_rows
from app.services.normalization import ColumnMapping

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


RAW_BANK_SHEET_NAME = "Raw Bank Transactions"
HEADER_MARKERS = {"Bank Transaction ID", "Transaction Date", "Amount (USD)"}


def _safe_batch_summary(batch: dict | None) -> dict | None:
    if not batch:
        return None
    return {
        "_id": str(batch.get("_id")) if batch.get("_id") is not None else None,
        "source_file": batch.get("source_file"),
        "created_at": batch.get("created_at").isoformat() if batch.get("created_at") else None,
    }


def _normalize_excel_table(df: pd.DataFrame) -> pd.DataFrame:
    for idx, row in df.iterrows():
        values = {str(value).strip() for value in row.tolist() if value is not None}
        if HEADER_MARKERS.issubset(values):
            normalized = df.iloc[idx + 1:].copy()
            normalized.columns = [str(value).strip() for value in row.tolist()]
            normalized = normalized.dropna(how="all")
            return normalized.reset_index(drop=True)
    return df


def _validate_bank_rows(rows: list[dict], sheet_name: str | None, sheet_names: list[str]) -> None:
    if not rows:
        raise HTTPException(400, "This sheet does not contain any rows to import.")

    columns = {str(column).strip() for column in rows[0].keys()}
    if HEADER_MARKERS.issubset(columns):
        return

    guidance = (
        "This worksheet does not look like the bank transaction export. "
        "Choose the sheet that contains Bank Transaction ID, Transaction Date, Description, Amount (USD), Currency, and Bank Account."
    )
    if RAW_BANK_SHEET_NAME in sheet_names and sheet_name != RAW_BANK_SHEET_NAME:
        guidance += f" For this workbook, try the worksheet named '{RAW_BANK_SHEET_NAME}'."
    raise HTTPException(400, guidance)


def _read_workbook(filename: str, content: bytes, requested_sheet_name: str | None = None) -> tuple[list[dict], str | None, list[str]]:
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(content))
        sheet_name = None
        sheet_names = []
    elif filename.lower().endswith((".xlsx", ".xls")):
        workbook = pd.ExcelFile(io.BytesIO(content))
        sheet_names = workbook.sheet_names
        if requested_sheet_name:
            if requested_sheet_name not in sheet_names:
                raise HTTPException(400, f"Sheet not found: {requested_sheet_name}")
            sheet_name = requested_sheet_name
        else:
            sheet_name = RAW_BANK_SHEET_NAME if RAW_BANK_SHEET_NAME in sheet_names else sheet_names[0]
        df = pd.read_excel(workbook, sheet_name=sheet_name, header=None)
        df = _normalize_excel_table(df)
    else:
        raise HTTPException(400, "Unsupported file type -- upload a .csv or .xlsx file")
    df = df.where(pd.notnull(df), None)
    rows = df.to_dict(orient="records")
    _validate_bank_rows(rows, sheet_name, sheet_names)
    return rows, sheet_name, sheet_names


@router.post("/preview")
async def preview_upload(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Step 1 of the upload flow: parse the file and return the detected
    columns, a suggested mapping, and a 5-row preview so the person can
    correct the mapping before anything is imported."""
    content = await file.read()
    file_hash = sha256(content).hexdigest()
    rows, sheet_name, sheet_names = _read_workbook(file.filename, content, sheet_name)
    if not rows:
        raise HTTPException(400, "File contains no rows")

    columns = list(rows[0].keys())
    suggested = ColumnMapping.suggest(columns)

    existing_batch = await db.import_batches.find_one(
        {"$or": [
            {"file_hash": file_hash},
            {"source_file": file.filename, "row_count": len(rows)},
        ]},
        {"_id": 1, "source_file": 1, "created_at": 1},
    )

    return {
        "filename": file.filename,
        "file_hash": file_hash,
        "already_uploaded": bool(existing_batch),
        "existing_import_batch": _safe_batch_summary(existing_batch),
        "sheet_name": sheet_name,
        "sheet_names": sheet_names,
        "columns": columns,
        "row_count": len(rows),
        "preview_rows": rows[:5],
        "suggested_mapping": suggested.__dict__,
    }


@router.get("/gemini/status")
async def gemini_status():
    settings = get_settings()
    return {"configured": bool(settings.gemini_api_key), "model": settings.gemini_model}


@router.post("/gemini/test")
async def gemini_test():
    try:
        result = await classify_with_gemini("ACH CREDIT TEST CUSTOMER REPAIR JOB", 12500)
    except GeminiClassificationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@router.post("/import")
async def import_upload(
    file: UploadFile = File(...),
    mapping_json: str = Form(...),
    sheet_name: str | None = Form(None),
    use_gemini: bool = Form(True),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Step 2: actually ingest the file using a (possibly hand-corrected)
    column mapping submitted from the mapping screen."""
    content = await file.read()
    file_hash = sha256(content).hexdigest()
    rows, _sheet_name, _sheet_names = _read_workbook(file.filename, content, sheet_name)
    existing_batch = await db.import_batches.find_one(
        {"$or": [
            {"file_hash": file_hash},
            {"source_file": file.filename, "row_count": len(rows)},
        ]},
        {"_id": 1, "source_file": 1, "created_at": 1},
    )
    if existing_batch:
        raise HTTPException(
            409,
            {
                "message": "This file appears to have already been uploaded. Clear data first if you want to rerun the same workbook.",
                "existing_import_batch": _safe_batch_summary(existing_batch),
            },
        )

    mapping_dict = json.loads(mapping_json)
    mapping = ColumnMapping(**mapping_dict)

    summary = await ingest_rows(
        db,
        source_file=file.filename,
        rows=rows,
        mapping=mapping,
        file_hash=file_hash,
        sheet_name=_sheet_name,
        use_gemini=use_gemini,
    )
    return summary
