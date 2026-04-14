"""
PLY 读取辅助：解决 Windows 下 Open3D 无法打开「含中文等非 ASCII 路径」的问题；
并在 Open3D 仍失败时用纯 Python 解析顶点（ASCII / binary_little_endian / binary_big_endian）。
"""
from __future__ import annotations

import os
import struct
import tempfile
import shutil
from pathlib import Path
from typing import Any, Callable

import numpy as np


def _is_ascii_only_path(p: Path) -> bool:
    try:
        str(p.resolve()).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def open3d_safe_local_path(src: Path) -> tuple[Path, Callable[[], None]]:
    """
    若路径含中文等字符，复制到 %TEMP% 下临时文件，供 Open3D 使用。
    返回 (供 Open3D 使用的路径, 清理函数)。
    """
    src = src.resolve()
    if _is_ascii_only_path(src):
        return src, lambda: None
    suffix = src.suffix or ".ply"
    fd, tmp_name = tempfile.mkstemp(prefix="o3d_", suffix=suffix)
    os.close(fd)
    tmp_path = Path(tmp_name)
    tmp_path.write_bytes(src.read_bytes())

    def cleanup() -> None:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    return tmp_path, cleanup


def o3d_write_point_cloud(pcd: Any, dest: Path, o3d_mod: Any) -> None:
    """将点云写入 dest；路径含中文时先写入 TEMP 再复制。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _is_ascii_only_path(dest):
        ok = o3d_mod.io.write_point_cloud(str(dest), pcd)
        if ok is False:
            raise OSError(f"Open3D 写入点云失败: {dest}")
        return
    fd, tmp_name = tempfile.mkstemp(prefix="o3d_", suffix=".pcd")
    os.close(fd)
    try:
        ok = o3d_mod.io.write_point_cloud(tmp_name, pcd)
        if ok is False:
            raise OSError("Open3D 写入临时点云失败")
        shutil.copyfile(tmp_name, dest)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def o3d_write_triangle_mesh(mesh: Any, dest: Path, o3d_mod: Any) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _is_ascii_only_path(dest):
        ok = o3d_mod.io.write_triangle_mesh(str(dest), mesh)
        if ok is False:
            raise OSError(f"Open3D 写入网格失败: {dest}")
        return
    fd, tmp_name = tempfile.mkstemp(prefix="o3d_", suffix=".stl")
    os.close(fd)
    try:
        ok = o3d_mod.io.write_triangle_mesh(tmp_name, mesh)
        if ok is False:
            raise OSError("Open3D 写入临时网格失败")
        shutil.copyfile(tmp_name, dest)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def read_ply_vertex_xyz_numpy(ply_path: Path) -> np.ndarray:
    """
    从 PLY 解析顶点坐标 (N,3) float64。仅依赖 pathlib 读文件，路径可为 Unicode。
    支持常见 ASCII 与 binary PLY（vertex 元素中不含 list 属性）。
    """
    raw = ply_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    low = raw.lower()
    eh = low.find(b"end_header")
    if eh < 0:
        raise ValueError("PLY 缺少 end_header")
    rest = raw[eh:]
    nl = rest.find(b"\n")
    if nl < 0:
        raise ValueError("PLY 头格式错误")
    header_end = eh + nl + 1
    header_text = raw[:header_end].decode("latin-1", errors="replace")
    body = raw[header_end:]

    lines = [ln.strip() for ln in header_text.splitlines() if ln.strip()]
    fmt = "ascii"
    n_vertex = 0
    vertex_props: list[tuple[str, str]] = []
    section: str | None = None

    for ln in lines:
        parts = ln.split()
        if not parts:
            continue
        if parts[0] == "format" and len(parts) >= 2:
            fmt = parts[1]
        elif parts[0] == "element" and len(parts) >= 3:
            name = parts[1]
            count = int(parts[2])
            if name == "vertex":
                section = "vertex"
                n_vertex = count
                vertex_props = []
            else:
                section = None
        elif parts[0] == "property" and section == "vertex":
            if parts[1] == "list":
                raise ValueError("vertex 元素含 list 属性，当前解析器不支持")
            typ, pname = parts[1], parts[2]
            vertex_props.append((typ, pname))

    if n_vertex <= 0 or not vertex_props:
        raise ValueError("PLY 头中未找到有效的 element vertex")

    try:
        ix = next(i for i, (_, n) in enumerate(vertex_props) if n.lower() == "x")
        iy = next(i for i, (_, n) in enumerate(vertex_props) if n.lower() == "y")
        iz = next(i for i, (_, n) in enumerate(vertex_props) if n.lower() == "z")
    except StopIteration:
        ix, iy, iz = 0, 1, 2
        if len(vertex_props) < 3:
            raise ValueError("PLY 顶点属性中找不到 x/y/z，且列数不足 3")

    type_map = {
        "char": "b",
        "int8": "b",
        "uchar": "B",
        "uint8": "B",
        "short": "h",
        "int16": "h",
        "ushort": "H",
        "uint16": "H",
        "int": "i",
        "int32": "i",
        "uint": "I",
        "uint32": "I",
        "float": "f",
        "float32": "f",
        "double": "d",
        "float64": "d",
    }

    if fmt == "ascii":
        text = body.decode("utf-8", errors="replace")
        all_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        v_lines = all_lines[:n_vertex]
        if len(v_lines) < n_vertex:
            raise ValueError(f"ASCII PLY 顶点行不足：需要 {n_vertex}，实际 {len(v_lines)}")
        out = np.empty((n_vertex, 3), dtype=np.float64)
        for i, line in enumerate(v_lines):
            vals = line.split()
            if len(vals) < len(vertex_props):
                raise ValueError(f"顶点行 {i} 数值个数不足（需至少 {len(vertex_props)} 列）")
            out[i, 0] = float(vals[ix])
            out[i, 1] = float(vals[iy])
            out[i, 2] = float(vals[iz])
        return out

    if fmt not in ("binary_little_endian", "binary_big_endian"):
        raise ValueError(f"不支持的 PLY format: {fmt}")

    endian = "<" if fmt == "binary_little_endian" else ">"
    struct_codes: list[str] = []
    for typ, _name in vertex_props:
        c = type_map.get(typ)
        if c is None:
            raise ValueError(f"不支持的顶点属性类型: {typ}")
        struct_codes.append(c)
    row_fmt = endian + "".join(struct_codes)
    row_bytes = struct.calcsize(row_fmt)
    need = n_vertex * row_bytes
    if len(body) < need:
        raise ValueError(
            f"二进制 PLY 主体过短：声明 {n_vertex} 顶点 × {row_bytes} 字节，需要 {need}，实际 {len(body)}"
        )

    out = np.empty((n_vertex, 3), dtype=np.float64)
    for i in range(n_vertex):
        off = i * row_bytes
        row = body[off : off + row_bytes]
        vals = struct.unpack(row_fmt, row)
        out[i, 0] = float(vals[ix])
        out[i, 1] = float(vals[iy])
        out[i, 2] = float(vals[iz])
    return out
