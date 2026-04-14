from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class CamelModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        from_attributes=True,
    )


class IndicatorOut(CamelModel):
    id: str
    blade_type: str = Field(alias="bladeType")
    blade_no: str = Field(alias="bladeNo")
    length_mm: Optional[float] = Field(default=None, alias="lengthMm")
    chord_mm: Optional[float] = Field(default=None, alias="chordMm")
    twist_deg: Optional[float] = Field(default=None, alias="twistDeg")
    thick_mm: Optional[float] = Field(default=None, alias="thickMm")
    eval_status: str = Field(alias="eval")
    inspect_date: date = Field(alias="inspectDate")
    inspector: str
    batch_no: str = Field(alias="batchNo")


class ManualRecordCreate(CamelModel):
    id: str = Field(min_length=1, max_length=64)
    blade_type: str = Field(alias="bladeType", min_length=1, max_length=64)
    blade_no: str = Field(alias="bladeNo", min_length=1, max_length=64)
    inspect_date: date = Field(alias="inspectDate")
    inspector: str = Field(min_length=1, max_length=128)
    batch_no: str = Field(alias="batchNo", min_length=1, max_length=64)


class JobTaskOut(CamelModel):
    id: str
    name: str
    blade_type: str = Field(alias="type")
    icp: str
    ps: str
    nb: str
    is_custom: bool = Field(default=False, alias="isCustom")


class IcpResultOut(CamelModel):
    rmse: float
    iterations: int
    matrix_rows: list[list[float]] = Field(alias="matrixRows")
    registered_pcd_rel: str = Field(alias="registeredPcdRel")


class PoissonResultOut(CamelModel):
    vertex_count: int = Field(alias="vertexCount")
    face_count: int = Field(alias="faceCount")
    watertight: bool
    mesh_stl_rel: str = Field(alias="meshStlRel")


class NurbsResultOut(CamelModel):
    length_mm: float = Field(alias="lengthMm")
    chord_mm: float = Field(alias="chordMm")
    twist_deg: float = Field(alias="twistDeg")
    thick_mm: float = Field(alias="thickMm")
    eval_status: str = Field(alias="eval")


class PipelineSummaryOut(CamelModel):
    job_id: str = Field(alias="jobId")
    icp: IcpResultOut
    poisson: PoissonResultOut
    nurbs: NurbsResultOut
