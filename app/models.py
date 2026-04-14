from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import Date, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BladeRecord(Base):
    __tablename__ = "blade_record"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    blade_type: Mapped[str] = mapped_column(String(64))
    blade_no: Mapped[str] = mapped_column(String(64))
    inspect_date: Mapped[date] = mapped_column(Date)
    inspector: Mapped[str] = mapped_column(String(128))
    batch_no: Mapped[str] = mapped_column(String(64))

    length_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    chord_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    twist_deg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    thick_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    eval_status: Mapped[str] = mapped_column(String(32), default="待评估")

    icp_status: Mapped[str] = mapped_column(String(32), default="pending")
    ps_status: Mapped[str] = mapped_column(String(32), default="pending")
    nb_status: Mapped[str] = mapped_column(String(32), default="pending")

    icp_rmse: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    icp_iterations: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transform_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    registered_pcd_rel: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    mesh_stl_rel: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
