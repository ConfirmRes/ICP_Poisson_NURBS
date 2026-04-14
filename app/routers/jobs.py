from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DATA_DIR
from app.database import get_db
from app.models import BladeRecord
from app.schemas import (
    IcpResultOut,
    JobTaskOut,
    NurbsResultOut,
    PipelineSummaryOut,
    PoissonResultOut,
)
from app.services.geometry_pipeline import (
    run_icp,
    run_nurbs_metrics,
    run_poisson,
    transform_to_json_rows,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_FORBIDDEN_ID_CHARS = frozenset('/\\:*?"<>|')


def _job_dir(job_id: str) -> Path:
    if not job_id or len(job_id) > 64:
        raise HTTPException(status_code=400, detail="任务/记录 ID 长度须在 1–64 之间")
    if ".." in job_id or any(c in _FORBIDDEN_ID_CHARS for c in job_id):
        raise HTTPException(status_code=400, detail="记录 ID 含非法字符")
    d = DATA_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record_or_404(db: Session, job_id: str) -> BladeRecord:
    row = db.get(BladeRecord, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在，请先在数据采集模块手动录入")
    return row


def _resolved_artifact_path(row: BladeRecord, kind: str) -> Path | None:
    """kind: registered | mesh — 返回已存在文件路径，且必须位于 backend/data 下以防路径穿越。"""
    data_root = DATA_DIR.resolve()
    if kind == "registered":
        if not row.registered_pcd_rel:
            return None
        p = (DATA_DIR.parent / row.registered_pcd_rel).resolve()
    elif kind == "mesh":
        if not row.mesh_stl_rel:
            return None
        p = (DATA_DIR.parent / row.mesh_stl_rel).resolve()
    else:
        return None
    try:
        p.relative_to(data_root)
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p


@router.get("/{job_id}/artifact/registered.pcd")
def download_registered_pcd(job_id: str, db: Session = Depends(get_db)):
    row = _record_or_404(db, job_id)
    path = _resolved_artifact_path(row, "registered")
    if path is None:
        raise HTTPException(status_code=404, detail="尚无配准点云，请先完成 ICP")
    return FileResponse(path, media_type="application/octet-stream", filename="registered.pcd")


@router.get("/{job_id}/artifact/mesh.stl")
def download_mesh_stl(job_id: str, db: Session = Depends(get_db)):
    row = _record_or_404(db, job_id)
    path = _resolved_artifact_path(row, "mesh")
    if path is None:
        raise HTTPException(status_code=404, detail="尚无泊松网格，请先完成曲面重构")
    return FileResponse(path, media_type="model/stl", filename="mesh_poisson.stl")


@router.get("", response_model=list[JobTaskOut])
def list_jobs(db: Session = Depends(get_db)):
    rows = db.scalars(select(BladeRecord).order_by(BladeRecord.updated_at.desc())).all()
    out: list[JobTaskOut] = []
    for r in rows:
        name = f"{r.batch_no} · {r.blade_no}"
        out.append(
            JobTaskOut(
                id=r.id,
                name=name,
                blade_type=r.blade_type,
                icp=r.icp_status,
                ps=r.ps_status,
                nb=r.nb_status,
                is_custom=False,
            )
        )
    return out


async def _save_upload(up: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = await up.read()
    if not raw:
        raise HTTPException(status_code=400, detail=f"空文件: {up.filename}")
    dest.write_bytes(raw)


def _execute_icp(
    db: Session,
    row: BladeRecord,
    cad_path: Path,
    scan_path: Path,
    reg_path: Path,
    max_iterations: int,
) -> IcpResultOut:
    row.error_message = None
    row.icp_status = "processing"
    row.ps_status = "pending"
    row.nb_status = "pending"
    db.commit()

    try:
        rmse, iters, mat = run_icp(cad_path, scan_path, reg_path, max_iterations=max_iterations)
    except Exception as e:
        row.icp_status = "pending"
        row.error_message = str(e)[:1024]
        db.commit()
        raise HTTPException(status_code=422, detail=str(e)) from e

    rel = str(reg_path.relative_to(DATA_DIR.parent))
    row.icp_rmse = rmse
    row.icp_iterations = iters
    row.transform_json = transform_to_json_rows(mat)
    row.registered_pcd_rel = rel
    row.icp_status = "completed"
    db.commit()

    return IcpResultOut(
        rmse=rmse,
        iterations=iters,
        matrix_rows=mat,
        registered_pcd_rel=rel,
    )


def _execute_poisson(db: Session, row: BladeRecord, depth: int) -> PoissonResultOut:
    if row.icp_status != "completed" or not row.registered_pcd_rel:
        raise HTTPException(status_code=400, detail="请先完成 ICP 配准")
    reg_path = (DATA_DIR.parent / row.registered_pcd_rel).resolve()
    if not reg_path.is_file():
        raise HTTPException(status_code=400, detail="配准点云文件缺失，请重新执行 ICP")

    jd = _job_dir(row.id)
    stl_path = jd / "mesh_poisson.stl"
    row.ps_status = "processing"
    db.commit()
    try:
        vcount, fcount, watertight = run_poisson(reg_path, stl_path, depth=depth)
    except Exception as e:
        row.ps_status = "pending"
        row.error_message = str(e)[:1024]
        db.commit()
        raise HTTPException(status_code=422, detail=str(e)) from e

    rel = str(stl_path.relative_to(DATA_DIR.parent))
    row.mesh_stl_rel = rel
    row.ps_status = "completed"
    db.commit()
    return PoissonResultOut(
        vertex_count=vcount,
        face_count=fcount,
        watertight=watertight,
        mesh_stl_rel=rel,
    )


def _execute_nurbs(db: Session, row: BladeRecord) -> NurbsResultOut:
    if row.ps_status != "completed" or not row.mesh_stl_rel:
        raise HTTPException(status_code=400, detail="请先完成泊松曲面重构")
    mesh_path = (DATA_DIR.parent / row.mesh_stl_rel).resolve()
    if not mesh_path.is_file():
        raise HTTPException(status_code=400, detail="网格文件缺失，请重新执行泊松重构")

    row.nb_status = "processing"
    db.commit()
    try:
        L, C, Tw, Th, ev = run_nurbs_metrics(mesh_path, row.icp_rmse)
    except Exception as e:
        row.nb_status = "pending"
        row.error_message = str(e)[:1024]
        db.commit()
        raise HTTPException(status_code=422, detail=str(e)) from e

    row.length_mm = L
    row.chord_mm = C
    row.twist_deg = Tw
    row.thick_mm = Th
    row.eval_status = ev
    row.nb_status = "completed"
    db.commit()
    return NurbsResultOut(length_mm=L, chord_mm=C, twist_deg=Tw, thick_mm=Th, eval_status=ev)


@router.post("/{job_id}/icp", response_model=IcpResultOut)
async def run_job_icp(
    job_id: str,
    cad: UploadFile = File(..., description="目标 CAD，.ply"),
    scan: UploadFile = File(..., description="源点云，.pcd"),
    max_iterations: int = Form(50),
    db: Session = Depends(get_db),
):
    row = _record_or_404(db, job_id)
    jd = _job_dir(job_id)
    cad_path = jd / "target.ply"
    scan_path = jd / "source.pcd"
    reg_path = jd / "registered.pcd"

    await _save_upload(cad, cad_path)
    await _save_upload(scan, scan_path)

    return _execute_icp(db, row, cad_path, scan_path, reg_path, max_iterations)


@router.post("/{job_id}/poisson", response_model=PoissonResultOut)
def run_job_poisson(
    job_id: str,
    depth: int = 9,
    db: Session = Depends(get_db),
):
    row = _record_or_404(db, job_id)
    return _execute_poisson(db, row, depth)


@router.post("/{job_id}/nurbs", response_model=NurbsResultOut)
def run_job_nurbs(job_id: str, db: Session = Depends(get_db)):
    row = _record_or_404(db, job_id)
    return _execute_nurbs(db, row)


@router.post("/{job_id}/full-pipeline", response_model=PipelineSummaryOut)
async def run_full_pipeline(
    job_id: str,
    cad: UploadFile = File(...),
    scan: UploadFile = File(...),
    max_iterations: int = Form(50),
    poisson_depth: int = Form(9),
    db: Session = Depends(get_db),
):
    """上传 CAD+扫描，一键执行 ICP → 泊松 → 网格指标分析，并写回指标表。"""
    row = _record_or_404(db, job_id)
    jd = _job_dir(job_id)
    cad_path = jd / "target.ply"
    scan_path = jd / "source.pcd"
    reg_path = jd / "registered.pcd"

    await _save_upload(cad, cad_path)
    await _save_upload(scan, scan_path)

    icp_out = _execute_icp(db, row, cad_path, scan_path, reg_path, max_iterations)
    db.refresh(row)
    poisson_out = _execute_poisson(db, row, poisson_depth)
    db.refresh(row)
    nurbs_out = _execute_nurbs(db, row)
    return PipelineSummaryOut(job_id=job_id, icp=icp_out, poisson=poisson_out, nurbs=nurbs_out)

