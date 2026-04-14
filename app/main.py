from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import indicators, jobs

_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_HTML = _ROOT / "三合一(gemini).HTML"

app = FastAPI(
    title="航发叶片数字检测 API",
    description="ICP 配准、泊松重建、网格指标与 MySQL 指标管理",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(indicators.router)
app.include_router(jobs.router)


@app.get("/")
def index():
    if FRONTEND_HTML.is_file():
        return FileResponse(FRONTEND_HTML, media_type="text/html; charset=utf-8")
    return {
        "message": "后端已启动。请将 三合一(gemini).HTML 放在项目根目录，或自行配置静态页面。",
        "docs": "/docs",
    }
