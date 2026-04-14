"""
ICP 配准、泊松重建、基于网格的几何指标估计。
指标为工程近似（PCA / 切片），便于与真实 NURBS 管线对接替换。
需安装 Open3D（建议 Anaconda Python 3.11：conda install -c open3d open3d）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Tuple

import numpy as np

from app.services.ply_io import (
    o3d_write_point_cloud,
    o3d_write_triangle_mesh,
    open3d_safe_local_path,
    read_ply_vertex_xyz_numpy,
)


def _require_o3d():
    try:
        import open3d as o3d
    except ImportError as e:
        raise ImportError(
            "未安装 Open3D。请使用 Anaconda 创建 Python 3.11 环境并执行：conda install -c open3d open3d"
        ) from e
    return o3d


def _load_target_cloud(ply_path: Path):
    """
    兼容多种 PLY：纯点云、带三角面的网格、仅有顶点无 face 的「伪网格」点云。

    Windows 上 Open3D 常无法用 C++ 窄路径打开「含中文目录」下的文件，会读成空；
    故先复制到 %TEMP%（纯 ASCII 路径）再交给 Open3D；仍失败则用 Python 解析顶点。
    """
    o3d = _require_o3d()
    try:
        head = ply_path.read_bytes()[:512]
        if head.startswith(b"\xef\xbb\xbf"):
            head = head[3:]
        head = head.lstrip()
        if head and not head.lower().startswith(b"ply"):
            raise ValueError(
                "上传的文件头不是 PLY（应以 ply 开头）。请确认扩展名与内容一致，勿把 STL/OBJ 改名成 .ply。"
            )
    except OSError as e:
        raise ValueError(f"无法读取文件：{e}") from e

    io_path, cleanup = open3d_safe_local_path(ply_path)
    try:
        mesh = o3d.io.read_triangle_mesh(str(io_path))
        n_verts = len(mesh.vertices)
        n_tri = len(mesh.triangles)

        if n_verts > 0:
            if n_tri > 0:
                mesh.compute_vertex_normals()
                n_samples = min(100_000, max(10_000, int(n_verts) * 20))
                return mesh.sample_points_uniformly(number_of_points=n_samples)
            pcd = o3d.geometry.PointCloud()
            pcd.points = mesh.vertices
            return pcd

        pc = o3d.io.read_point_cloud(str(io_path))
        if len(pc.points) > 0:
            return pc
    finally:
        cleanup()

    try:
        pts = read_ply_vertex_xyz_numpy(ply_path)
    except Exception as e:
        raise ValueError(
            "Open3D 与备用解析器均无法读取该 PLY。请确认文件完整、为标准 PLY，"
            "或尝试另存为 ASCII PLY / binary_little_endian。"
        ) from e
    if pts.shape[0] < 1:
        raise ValueError("PLY 备用解析未得到任何顶点")
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    return pcd


def _read_point_cloud_o3d(path: Path, o3d):
    io_path, cleanup = open3d_safe_local_path(path)
    try:
        return o3d.io.read_point_cloud(str(io_path))
    finally:
        cleanup()


def run_icp(
    target_ply: Path,
    source_pcd: Path,
    out_registered_pcd: Path,
    max_iterations: int = 50,
) -> Tuple[float, int, list[list[float]]]:
    o3d = _require_o3d()
    target = _load_target_cloud(target_ply)
    source = _read_point_cloud_o3d(source_pcd, o3d)
    if len(source.points) < 20:
        raise ValueError("源点云有效点数过少，请检查 PCD 文件")

    bbox_t = target.get_axis_aligned_bounding_box()
    bbox_s = source.get_axis_aligned_bounding_box()
    merged = bbox_t.get_extent() + bbox_s.get_extent()
    extent = float(np.linalg.norm(merged))
    voxel = max(float(extent) * 0.003, 1e-6)

    target_d = target.voxel_down_sample(voxel)
    source_d = source.voxel_down_sample(voxel)
    if len(target_d.points) < 10 or len(source_d.points) < 10:
        raise ValueError("下采样后点数不足，请调整模型尺度或采样密度")

    target_d.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 3, max_nn=40)
    )
    source_d.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 3, max_nn=40)
    )

    threshold = voxel * 5.0
    tc = target_d.get_center()
    sc = source_d.get_center()
    trans_init = np.eye(4)
    trans_init[:3, 3] = tc - sc

    criteria = o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=max_iterations)
    reg = o3d.pipelines.registration.registration_icp(
        source_d,
        target_d,
        threshold,
        trans_init,
        o3d.pipelines.registration.TransformationEstimationPointToPlane(),
        criteria,
    )

    T = reg.transformation
    source_full = _read_point_cloud_o3d(source_pcd, o3d)
    source_full.transform(T)
    out_registered_pcd.parent.mkdir(parents=True, exist_ok=True)
    o3d_write_point_cloud(source_full, out_registered_pcd, o3d)

    mat = T.tolist()
    rmse = float(reg.inlier_rmse) if reg.inlier_rmse == reg.inlier_rmse else 0.0
    return rmse, int(max_iterations), mat


def _mesh_cleanup(mesh):
    """不调用 remove_non_manifold_edges，避免删三角后产生开放边界、破坏水密性。"""
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_unreferenced_vertices()


def _try_fill_holes_tensor(o3d, mesh, extent: float):
    """
    在仍非水密时尝试 Tensor 版 fill_holes。
    大网格上 fill_holes / from_legacy 极慢，阈值略放宽以覆盖常见泊松面数，仍保响应。
    """
    if mesh.is_watertight():
        return mesh
    if os.getenv("POISSON_TRY_FILL_HOLES", "1").strip() in ("0", "false", "False"):
        return mesh
    n_tri = int(len(mesh.triangles))
    n_v = int(len(mesh.vertices))
    if n_tri > 160_000 or n_v > 100_000:
        return mesh
    try:
        t_mesh = o3d.t.geometry.TriangleMesh.from_legacy(mesh)
        hole_size = float(max(extent * 0.08, 1e-6))
        try:
            filled = t_mesh.fill_holes(hole_size=hole_size)
        except TypeError:
            filled = t_mesh.fill_holes()
        out = filled.to_legacy()
        out.compute_vertex_normals()
        _mesh_cleanup(out)
        if out.is_watertight():
            return out
    except Exception:
        pass
    return mesh


def _poisson_downsample_pcd(o3d, pcd, max_points: int):
    """体素下采样，使点数不超过 max_points，避免泊松在百万点上耗时过长。"""
    n = len(pcd.points)
    if n <= max_points:
        return pcd
    bbox = pcd.get_axis_aligned_bounding_box()
    ext = np.asarray(bbox.get_extent())
    diag = float(np.linalg.norm(ext))
    if diag < 1e-12:
        return pcd
    ratio = n / float(max_points)
    voxel = diag * 0.0015 * (ratio ** (1.0 / 3.0))
    voxel = max(voxel, diag * 1e-6)
    down = pcd.voxel_down_sample(voxel)
    guard = 0
    while len(down.points) > max_points * 1.1 and guard < 12:
        voxel *= 1.35
        down = pcd.voxel_down_sample(voxel)
        guard += 1
    return down if len(down.points) >= 50 else pcd


def _orient_normals_proxy_then_propagate(o3d, pcd, scale: float):
    """
    在 ~4 万点代理云上做法线一致化（秒级），再用 KDTree 将法线赋回泊松点云，
    比全点云 tangent_plane 快得多，且比单纯相机朝向更利于泊松闭合与贴点。
    """
    n = len(pcd.points)
    max_proxy = int(os.getenv("POISSON_ORIENT_PROXY_POINTS", "38000"))
    max_proxy = max(4000, min(50000, max_proxy))

    proxy = pcd
    pv = max(scale * 0.0018, 1e-9)
    guard = 0
    while len(proxy.points) > max_proxy and guard < 18:
        proxy = proxy.voxel_down_sample(pv)
        pv *= 1.22
        guard += 1
    if len(proxy.points) < 400:
        try:
            center = np.asarray(pcd.get_center())
            pcd.orient_normals_towards_camera_location(center + np.array([0.0, 0.0, scale * 2.5]))
        except Exception:
            pass
        return pcd

    proxy.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=scale * 0.042, max_nn=60)
    )
    try:
        k_or = int(min(22, max(9, len(proxy.points) // 900)))
        proxy.orient_normals_consistent_tangent_plane(k_or)
    except Exception:
        try:
            c = np.asarray(proxy.get_center())
            proxy.orient_normals_towards_camera_location(c + np.array([0.0, 0.0, scale * 2.5]))
        except Exception:
            pass

    try:
        from scipy.spatial import cKDTree

        qp = np.asarray(proxy.points)
        qn = np.asarray(proxy.normals)
        if qp.shape[0] != qn.shape[0] or qn.shape[0] == 0:
            raise ValueError("proxy normals missing")
        tree = cKDTree(qp)
        pl = np.asarray(pcd.points)
        try:
            _, idx = tree.query(pl, k=1, workers=-1)
        except TypeError:
            _, idx = tree.query(pl, k=1)
        idx = np.asarray(idx).reshape(-1)
        nn = qn[idx]
        pcd.normals = o3d.utility.Vector3dVector(nn.astype(np.float64))
    except Exception:
        try:
            center = np.asarray(pcd.get_center())
            pcd.orient_normals_towards_camera_location(center + np.array([0.0, 0.0, scale * 2.5]))
        except Exception:
            pass
    return pcd


def run_poisson(
    registered_pcd: Path,
    out_stl: Path,
    depth: int = 9,
) -> Tuple[int, int, bool]:
    o3d = _require_o3d()
    pcd = _read_point_cloud_o3d(registered_pcd, o3d)
    if len(pcd.points) < 50:
        raise ValueError("配准点云点数过少，无法泊松重建")

    bbox = pcd.get_axis_aligned_bounding_box()
    extent = float(np.linalg.norm(bbox.get_extent()))
    scale = max(extent, 1e-9)

    max_pts = int(os.getenv("POISSON_MAX_POINTS", "200000"))
    pcd = _poisson_downsample_pcd(o3d, pcd, max_pts)

    pcd.estimate_normals(
        o3d.geometry.KDTreeSearchParamHybrid(radius=scale * 0.042, max_nn=60)
    )
    pcd = _orient_normals_proxy_then_propagate(o3d, pcd, scale)

    extra_d = int(os.getenv("POISSON_EXTRA_DEPTH", "1"))
    eff_depth = min(12, max(4, int(depth)) + max(0, min(2, extra_d)))
    poisson_scale = float(os.getenv("POISSON_SCALE", "1.09"))

    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        pcd,
        depth=eff_depth,
        width=0,
        scale=poisson_scale,
        linear_fit=True,
    )

    # 重要：原先按密度分位数裁顶点会在等值面上「挖洞」，几乎必然导致 is_watertight=False。
    # 泊松理论输出为闭曲面；若需裁掉飞片，可改用极保守阈值（默认关闭）。
    densities = np.asarray(densities)
    trim_quantile = float(os.getenv("POISSON_DENSITY_TRIM_Q", "0"))
    if trim_quantile > 0 and densities.size and len(mesh.vertices) > 0:
        q = float(np.quantile(densities, min(0.05, max(0.001, trim_quantile))))
        mask = densities < q
        if bool(np.any(mask)) and float(np.mean(mask)) < 0.15:
            mesh.remove_vertices_by_mask(mask)

    _mesh_cleanup(mesh)
    mesh.compute_vertex_normals()

    mesh = _try_fill_holes_tensor(o3d, mesh, extent)

    out_stl.parent.mkdir(parents=True, exist_ok=True)
    o3d_write_triangle_mesh(mesh, out_stl, o3d)

    watertight = bool(mesh.is_watertight())
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.triangles)
    return int(len(vertices)), int(len(faces)), watertight


def _slice_indices(t: np.ndarray, low: float, high: float) -> np.ndarray:
    tmin, tmax = float(t.min()), float(t.max())
    span = tmax - tmin or 1.0
    lo = tmin + span * low
    hi = tmin + span * high
    return (t >= lo) & (t <= hi)


def compute_mesh_blade_metrics(
    mesh,
    icp_rmse: float | None,
) -> Tuple[float, float, float, float, str]:
    v = np.asarray(mesh.vertices)
    if v.shape[0] < 30:
        return 0.0, 0.0, 0.0, 0.0, "待评估"

    c = v.mean(axis=0)
    X = v - c
    _, _, vh = np.linalg.svd(X, full_matrices=False)
    u = vh[0]
    v2 = vh[1]
    v3 = vh[2]
    t = X @ u

    length = float(t.max() - t.min())

    i_mid = _slice_indices(t, 0.45, 0.55)
    if i_mid.sum() < 10:
        i_mid = np.ones_like(t, dtype=bool)
    plane_pts = X[i_mid]
    y = plane_pts @ v2
    z = plane_pts @ v3
    chord = float(np.ptp(y)) if y.size else 0.0
    thick = float(np.ptp(z)) if z.size else 0.0

    i_lo = _slice_indices(t, 0.2, 0.3)
    i_hi = _slice_indices(t, 0.7, 0.8)
    twist = 0.0
    if i_lo.sum() >= 5 and i_hi.sum() >= 5:
        xy0 = np.stack([X[i_lo] @ v2, X[i_lo] @ v3], axis=1)
        xy1 = np.stack([X[i_hi] @ v2, X[i_hi] @ v3], axis=1)
        _, _, w0 = np.linalg.svd(xy0, full_matrices=False)
        _, _, w1 = np.linalg.svd(xy1, full_matrices=False)
        a0 = np.arctan2(w0[0, 1], w0[0, 0])
        a1 = np.arctan2(w1[0, 1], w1[0, 0])
        twist = float(np.degrees(((a1 - a0 + np.pi) % (2 * np.pi)) - np.pi))

    if chord <= 0:
        chord = float(np.ptp(X @ v2))
    if thick <= 0:
        thick = float(np.ptp(X @ v3)) * 0.35

    eval_s = "待评估"
    if icp_rmse is not None and icp_rmse > 0.08 * (length * 0.01 + 1.0):
        eval_s = "需优化"
    elif length > 1e-6 and chord > 1e-6:
        eval_s = "合格"

    return length, chord, twist, thick, eval_s


def run_nurbs_metrics(
    mesh_stl: Path,
    icp_rmse: float | None,
) -> Tuple[float, float, float, float, str]:
    o3d = _require_o3d()
    io_path, cleanup = open3d_safe_local_path(mesh_stl)
    try:
        mesh = o3d.io.read_triangle_mesh(str(io_path))
    finally:
        cleanup()
    if not mesh.has_vertices():
        raise ValueError("STL 网格无效")
    mesh.remove_duplicated_vertices()
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_non_manifold_edges()
    return compute_mesh_blade_metrics(mesh, icp_rmse)


def transform_to_json_rows(T: list[list[float]]) -> dict[str, Any]:
    return {"matrix": T}
