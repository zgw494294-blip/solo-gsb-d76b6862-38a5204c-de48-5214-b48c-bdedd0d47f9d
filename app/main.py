"""乐谱反复展开台 —— FastAPI 后端。

路由：
- GET  /            单页应用
- GET  /api/health  健康检查
- POST /api/expand  校验并展开小节序列
- POST /api/import-musicxml MusicXML 结构导入（转换后走现有校验与展开）
- POST /api/export-musicxml MusicXML 结构导出（校验展开后往返自检，再生成文件）
- POST /api/compare 改谱影响对照（基准版 vs 当前版）
- POST /api/solve      反复次数反求（待求位置取 2~4 展开成目标序列）
- POST /api/fix-right  右反复终点纠错（只切换各行右终点，预算 4 处）
- POST /api/locate     排练记录定位（记录与演奏带任意区间做编辑距离匹配）
- POST /api/cues       遍次排练提示单（提示按遍次条件命中访问的指定拍）
- POST /api/excerpt    演奏片段摘谱（选中访问区间 → 可回读的单 part MusicXML）
- POST /api/audit-musicxml MusicXML 小节时值审计（缺拍 / 非和弦重叠 / 越过小节边界）
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .cues import build_cue_sheet
from .diff import compare
from .excerpt import build_excerpt
from .expander import MAX_VISITS, expand
from .locate import locate_records
from .musicxml_audit import audit_musicxml
from .musicxml_export import export_musicxml
from .musicxml_import import import_musicxml
from .rightfix import fix_right_repeats
from .solve import solve

app = FastAPI(title="乐谱反复展开台", version="1.7.0")

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_INVALID_JSON_BODY = {
    "ok": False,
    "errors": [{
        "code": "INVALID_JSON",
        "message": "请求体不是合法的 JSON。",
        "index": None,
        "measure": None,
    }],
}


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"status": "ok", "max_visits": MAX_VISITS}


@app.post("/api/expand")
async def api_expand(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    result, errors = expand(raw)
    if errors:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": errors},
        )
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/import-musicxml")
async def api_import_musicxml(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or "musicxml" not in raw:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 musicxml（文件文本）的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = import_musicxml(raw["musicxml"])
    if errors:
        # 不支持的结构 / 未配对记号 / 现有校验展开失败：按源小节回报
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": errors},
        )
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/audit-musicxml")
async def api_audit_musicxml(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or "musicxml" not in raw:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 musicxml（文件文本）的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = audit_musicxml(raw["musicxml"])
    if errors:
        # 文件范围不符 / 硬错误（缺 divisions、非法 duration、负拍位、
        # 孤立 chord 等）：按源小节回报，不显示通过
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": errors},
        )
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/export-musicxml")
async def api_export_musicxml(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    # 请求体即小节数组（同 /api/expand）；导出先复用完整校验与展开。
    result, errors = export_musicxml(raw)
    if errors:
        # 非法谱面按原始行显示错误，结构不可编码时同样不产生下载
        return JSONResponse(
            status_code=422,
            content={"ok": False, "errors": errors},
        )
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/compare")
async def api_compare(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or "baseline" not in raw or "current" not in raw:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 baseline 与 current 两个小节数组的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    # 两版分别走现有的校验与展开规则
    base_result, base_errors = expand(raw["baseline"])
    curr_result, curr_errors = expand(raw["current"])
    if base_errors or curr_errors:
        # 任一版本无效：只回报该版错误，不产生对照差异
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "baseline_errors": base_errors,
                "current_errors": curr_errors,
            },
        )
    assert base_result is not None and curr_result is not None
    return JSONResponse(content={
        "ok": True,
        "result": compare(base_result, curr_result),
    })


@app.post("/api/solve")
async def api_solve(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or any(
            k not in raw for k in ("measures", "unknowns", "target")):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 measures、unknowns 与 target 的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = solve(raw)
    if errors:
        # 任一组校验失败：回报全部错误分组，不产生方案
        return JSONResponse(status_code=422, content={"ok": False, **errors})
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/fix-right")
async def api_fix_right(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or "measures" not in raw:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 measures 小节数组的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = fix_right_repeats(raw)
    if errors:
        # 行级校验失败：回报错误，不进入搜索
        return JSONResponse(status_code=422,
                            content={"ok": False, **errors})
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/locate")
async def api_locate(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or any(
            k not in raw for k in ("measures", "record", "tolerance")):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 measures、record 与 tolerance 的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = locate_records(raw)
    if errors:
        # 任一组校验失败：回报全部错误分组，不产生定位结果
        return JSONResponse(status_code=422,
                            content={"ok": False, **errors})
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/cues")
async def api_cues(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or any(
            k not in raw for k in ("measures", "cues")):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 measures 与 cues 的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = build_cue_sheet(raw)
    if errors:
        # 任一组校验失败：回报全部错误分组，不产生提示单
        return JSONResponse(status_code=422,
                            content={"ok": False, **errors})
    return JSONResponse(content={"ok": True, "result": result})


@app.post("/api/excerpt")
async def api_excerpt(request: Request) -> JSONResponse:
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_INVALID_JSON_BODY)

    if not isinstance(raw, dict) or any(
            k not in raw for k in ("measures", "segments")):
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "errors": [{
                    "code": "INVALID_REQUEST",
                    "message": "请求体必须是包含 measures 与 segments 的对象。",
                    "index": None,
                    "measure": None,
                }],
            },
        )

    result, errors = build_excerpt(raw)
    if errors:
        # 任一组校验失败：回报全部错误分组，不产生摘谱与下载内容
        return JSONResponse(status_code=422,
                            content={"ok": False, **errors})
    return JSONResponse(content={"ok": True, "result": result})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


# 静态资源必须最后挂载，否则会抢先匹配 /api 路由。
app.mount("/", StaticFiles(directory=_STATIC_DIR), name="static")
