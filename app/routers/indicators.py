from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BladeRecord
from app.schemas import IndicatorOut, ManualRecordCreate

router = APIRouter(prefix="/api/indicators", tags=["indicators"])


def _apply_filters(
    stmt,
    blade_type: Optional[str],
    eval_status: Optional[str],
    inspect_date: Optional[date],
    inspector: Optional[str],
):
    if blade_type:
        stmt = stmt.where(BladeRecord.blade_type == blade_type)
    if eval_status:
        stmt = stmt.where(BladeRecord.eval_status == eval_status)
    if inspect_date:
        stmt = stmt.where(BladeRecord.inspect_date == inspect_date)
    if inspector:
        stmt = stmt.where(BladeRecord.inspector.contains(inspector))
    return stmt


@router.get("", response_model=list[IndicatorOut])
def list_indicators(
    blade_type: Optional[str] = Query(None, alias="blade_type"),
    eval_status: Optional[str] = Query(None, alias="eval"),
    inspect_date: Optional[date] = Query(None, alias="inspect_date"),
    inspector: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(BladeRecord).order_by(BladeRecord.updated_at.desc())
    stmt = _apply_filters(stmt, blade_type, eval_status, inspect_date, inspector)
    rows = db.scalars(stmt).all()
    return [IndicatorOut.model_validate(r) for r in rows]


@router.post("", response_model=IndicatorOut, status_code=201)
def create_manual_indicator(body: ManualRecordCreate, db: Session = Depends(get_db)):
    if db.get(BladeRecord, body.id):
        raise HTTPException(status_code=409, detail="记录ID已存在")
    row = BladeRecord(
        id=body.id,
        blade_type=body.blade_type,
        blade_no=body.blade_no,
        inspect_date=body.inspect_date,
        inspector=body.inspector,
        batch_no=body.batch_no,
        eval_status="待评估",
        icp_status="pending",
        ps_status="pending",
        nb_status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return IndicatorOut.model_validate(row)


@router.get("/{record_id}", response_model=IndicatorOut)
def get_indicator(record_id: str, db: Session = Depends(get_db)):
    row = db.get(BladeRecord, record_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    return IndicatorOut.model_validate(row)


@router.delete("/{record_id}", status_code=204)
def delete_indicator(record_id: str, db: Session = Depends(get_db)):
    row = db.get(BladeRecord, record_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    db.delete(row)
    db.commit()
    return None
