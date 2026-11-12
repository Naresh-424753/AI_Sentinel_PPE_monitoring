"""AI Sentinel API.   Run from the project root:  uvicorn backend.api:app --reload"""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import camera_watch, config, db, report_scheduler, sla
from backend.auth_routes import router as auth_router
from backend.detector import manager
from backend.events import bus
from backend.routes_admin import router as admin_router
from backend.routes_cameras import router as cameras_router
from backend.routes_extras import router as extras_router
from backend.routes_incidents import router as incidents_router
from backend.routes_notifications import router as notifications_router
from backend.routes_push import router as push_router
from backend.routes_reports import router as reports_router
from backend.routes_stats import router as stats_router


@asynccontextmanager
async def lifespan(app):
    print("==========================================")
    print("        AI SENTINEL STARTING")
    print("==========================================")
    db.init_db()
    db.seed_default_camera()
    bus.loop = asyncio.get_running_loop()
    sla.start()
    camera_watch.start()
    report_scheduler.start()
    if config.DISABLE_DETECTION:
        print("Detection disabled (DISABLE_DETECTION=true) - API only")
    else:
        print("Model:", config.MODEL_PATH)
        manager.start_all()
    print("API:  http://127.0.0.1:8000   Docs: http://127.0.0.1:8000/docs")
    yield
    manager.stop_all()


app = FastAPI(title="AI Sentinel API", description="Real-time PPE monitoring backend", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/images", StaticFiles(directory=config.INCIDENTS_DIR), name="images")
app.mount("/clips", StaticFiles(directory=config.CLIPS_DIR), name="clips")
app.mount("/proofs", StaticFiles(directory=config.PROOFS_DIR), name="proofs")

app.include_router(auth_router)
app.include_router(incidents_router)
app.include_router(stats_router)
app.include_router(cameras_router)
app.include_router(admin_router)
app.include_router(notifications_router)
app.include_router(reports_router)
app.include_router(push_router)
app.include_router(extras_router)


# ---- the built dashboard (frontend/dist), so one address serves the app AND the API ----
# Run `npm run build` in the frontend folder once. Without it nothing here is active (use `npm run dev` instead).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "frontend", "dist")
SPA_PAGES = ("/", "/incidents", "/cameras", "/admin", "/tv")

if os.path.isfile(os.path.join(DIST, "index.html")):
    if os.path.isdir(os.path.join(DIST, "assets")):
        app.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")
    if os.path.isdir(os.path.join(DIST, "icons")):
        app.mount("/icons", StaticFiles(directory=os.path.join(DIST, "icons")), name="icons")

    @app.middleware("http")
    async def dashboard_pages(request: Request, call_next):
        # A browser opening /incidents (or refreshing it) wants the page; the dashboard's own requests want JSON.
        if (request.method == "GET" and request.url.path in SPA_PAGES
                and "text/html" in request.headers.get("accept", "")):
            return FileResponse(os.path.join(DIST, "index.html"), headers={"Cache-Control": "no-cache"})
        return await call_next(request)

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(os.path.join(DIST, "sw.js"), media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(os.path.join(DIST, "manifest.webmanifest"), media_type="application/manifest+json")


@app.get("/health")
def health():
    cams = db.list_cameras()
    online = sum(1 for c in cams if manager.info(c)["status"] == "online")
    return {
        "success": True,
        "status": "online",
        "service": "AI Sentinel API",
        "model_loaded": os.path.exists(config.MODEL_PATH),
        "detection_enabled": not config.DISABLE_DETECTION,
        "cameras_total": len(cams),
        "cameras_online": online,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/")
def home():
    return {"system": "AI Sentinel", "status": "online", "docs": "/docs"}