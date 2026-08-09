from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

# --- PYTORCH 2.3.1 POLYFILL: nn.RMSNorm (added in PyTorch 2.4) ---
# Required by VieNeu TTS model on legacy GPU (GTX 1050 / sm_61)
import torch
import torch.nn as nn
if not hasattr(nn, 'RMSNorm'):
    class _RMSNorm(nn.Module):
        def __init__(self, normalized_shape, eps=1e-6):
            super().__init__()
            if isinstance(normalized_shape, int):
                normalized_shape = (normalized_shape,)
            self.weight = nn.Parameter(torch.ones(normalized_shape))
            self.eps = eps

        def forward(self, x):
            variance = x.float().pow(2).mean(-1, keepdim=True)
            return (self.weight * x * torch.rsqrt(variance + self.eps)).to(x.dtype)

    nn.RMSNorm = _RMSNorm
# -----------------------------------------------------------------


from app.core.config import DATA_DIR
from app.db.session import engine
from app.models.history import Base
from app.models.social_account import SocialAccount
from app.models.upload_schedule import UploadSchedule
from app.models.edit_profile import EditProfile
from app.models.proxy import Proxy
from app.models.live_job import LiveStreamJob
from app.models.followed_account import FollowedAccount
from app.models.twitter_nurture_config import TwitterNurtureConfig
from app.ai_studio.models import AIGeneration
from app.api.social_accounts import router as social_accounts_router
from app.api.upload_schedule import router as upload_schedule_router

import time
from sqlalchemy import text, inspect
from sqlalchemy.exc import OperationalError

# Retry logic for database connection (handles Postgres slow startup on fresh clone)
def init_db():
    max_retries = 5
    for i in range(max_retries):
        try:
            Base.metadata.create_all(bind=engine)
            
            inspector = inspect(engine)
            
            # Check and add proxy_id, user_agent, followers_count, videos_count, total_views, total_likes, health_metrics to social_accounts
            try:
                if 'social_accounts' in inspector.get_table_names():
                    social_accounts_cols = [col['name'] for col in inspector.get_columns('social_accounts')]
                    with engine.connect() as conn:
                        if 'proxy_id' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN proxy_id INTEGER REFERENCES proxies(id) ON DELETE SET NULL;"))
                        if 'user_agent' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN user_agent VARCHAR(500);"))
                        if 'followers_count' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN followers_count INTEGER DEFAULT 0;"))
                        if 'videos_count' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN videos_count INTEGER DEFAULT 0;"))
                        if 'total_views' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN total_views INTEGER DEFAULT 0;"))
                        if 'total_likes' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN total_likes INTEGER DEFAULT 0;"))
                        if 'health_metrics' not in social_accounts_cols:
                            conn.execute(text("ALTER TABLE social_accounts ADD COLUMN health_metrics TEXT;"))
                        conn.commit()
            except Exception as e:
                print(f"Error updating social_accounts schema: {e}")

            # Check and add views_count, health_status, engine_type to upload_schedules
            try:
                if 'upload_schedules' in inspector.get_table_names():
                    upload_schedules_cols = [col['name'] for col in inspector.get_columns('upload_schedules')]
                    with engine.connect() as conn:
                        if 'views_count' not in upload_schedules_cols:
                            conn.execute(text("ALTER TABLE upload_schedules ADD COLUMN views_count INTEGER;"))
                        if 'health_status' not in upload_schedules_cols:
                            conn.execute(text("ALTER TABLE upload_schedules ADD COLUMN health_status VARCHAR(50) DEFAULT 'unknown';"))
                        if 'engine_type' not in upload_schedules_cols:
                            conn.execute(text("ALTER TABLE upload_schedules ADD COLUMN engine_type VARCHAR(50) DEFAULT 'playwright';"))
                        conn.commit()
            except Exception as e:
                print(f"Error updating upload_schedules schema: {e}")
                
            # Check and add target_account_name to live_stream_jobs
            try:
                if 'live_stream_jobs' in inspector.get_table_names():
                    live_jobs_cols = [col['name'] for col in inspector.get_columns('live_stream_jobs')]
                    with engine.connect() as conn:
                        if 'target_account_name' not in live_jobs_cols:
                            conn.execute(text("ALTER TABLE live_stream_jobs ADD COLUMN target_account_name VARCHAR(255);"))
                        conn.commit()
            except Exception as e:
                print(f"Error updating live_stream_jobs schema: {e}")
                
            # Check and add original_caption, original_hashtags, and author_sec_uid to video_history
            try:
                if 'video_history' in inspector.get_table_names():
                    video_history_cols = [col['name'] for col in inspector.get_columns('video_history')]
                    with engine.connect() as conn:
                        if 'original_caption' not in video_history_cols:
                            conn.execute(text("ALTER TABLE video_history ADD COLUMN original_caption TEXT;"))
                        if 'original_hashtags' not in video_history_cols:
                            conn.execute(text("ALTER TABLE video_history ADD COLUMN original_hashtags TEXT;"))
                        if 'author_sec_uid' not in video_history_cols:
                            conn.execute(text("ALTER TABLE video_history ADD COLUMN author_sec_uid VARCHAR(255);"))
                        conn.commit()
            except Exception as e:
                print(f"Error updating video_history schema: {e}")

            # Migrate old video histories to have author_sec_uid if matching followed_accounts
            try:
                if 'followed_accounts' in inspector.get_table_names() and 'video_history' in inspector.get_table_names():
                    with engine.connect() as conn:
                        res = conn.execute(text("SELECT sec_uid, nickname FROM followed_accounts;")).fetchall()
                        for sec_uid, nickname in res:
                            if nickname:
                                conn.execute(
                                    text("UPDATE video_history SET author_sec_uid = :sec_uid WHERE (source = :source_nick OR source = :source_full) AND author_sec_uid IS NULL;"),
                                    {"sec_uid": sec_uid, "source_nick": nickname, "source_full": f"Douyin - {nickname}"}
                                )
                        conn.commit()
            except Exception as e:
                print(f"Error migrating video_history author_sec_uid: {e}")
                
            # Reset stuck upload schedules and video history processes on restart
            try:
                if 'upload_schedules' in inspector.get_table_names():
                    with engine.connect() as conn:
                        conn.execute(text("UPDATE upload_schedules SET status = 'failed', error_message = 'Tiến trình bị hủy do hệ thống (Celery/Backend) khởi động lại đột ngột.' WHERE status = 'uploading';"))
                        conn.commit()
                        print("Reset stuck uploading schedules to failed.")
            except Exception as e:
                print(f"Error resetting uploading schedules: {e}")

            try:
                if 'video_history' in inspector.get_table_names():
                    with engine.connect() as conn:
                        conn.execute(text("""
                            UPDATE video_history 
                            SET status = 'failed', error_message = 'Tiến trình bị gián đoạn do khởi động lại hệ thống.' 
                            WHERE status IN ('downloading', 'transcribing', 'translating', 'generating_tts', 'rendering');
                        """))
                        conn.commit()
                        print("Reset stuck video history processes to failed.")
            except Exception as e:
                print(f"Error resetting video history: {e}")

            # Auto-purge pending Celery queue tasks to prevent execution of outdated backlog commands
            try:
                from app.core.celery_app import celery_app
                purged_count = celery_app.control.purge()
                if purged_count:
                    print(f"Purged {purged_count} pending Celery tasks on startup.")
            except Exception as e:
                print(f"Error purging Celery tasks: {e}")

            print("Database connected and initialized successfully.")
            break
        except OperationalError as e:
            if i == max_retries - 1:
                print("Failed to connect to the database after multiple retries. Exiting.")
                raise e
            print(f"Database connection failed, retrying in 5 seconds... ({i+1}/{max_retries})")
            time.sleep(5)

init_db()

app = FastAPI(title="Video Reup System API")

# Cho phép React Web gọi tới API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Phục vụ file tĩnh (Video, Audio, SRT) cho phần Preview
app.mount("/api/files", StaticFiles(directory=DATA_DIR), name="files")

from app.api import crawler, processor, settings, history, discovery, social_accounts, analytics, edit_profiles, proxies, faceless, live, system
from app.ai_studio.router import router as ai_studio_router

app.include_router(system.router, prefix="/api/system", tags=["System"])
app.include_router(crawler.router, prefix="/api/crawler", tags=["Crawler"])
app.include_router(processor.router, prefix="/api/processor", tags=["Processor"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])
app.include_router(history.router, prefix="/api/history", tags=["History"])
app.include_router(ai_studio_router, prefix="/api/ai-studio", tags=["AI Studio"])
app.include_router(discovery.router, prefix="/api", tags=["Discovery"])
app.include_router(social_accounts_router, prefix="/api/social-accounts", tags=["Social Accounts"])
app.include_router(upload_schedule_router, prefix="/api/upload-schedules", tags=["Upload Schedules"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(edit_profiles.router, prefix="/api/edit-profiles", tags=["Edit Profiles"])
app.include_router(proxies.router, prefix="/api/proxies", tags=["Proxies"])
app.include_router(faceless.router, prefix="/api/faceless", tags=["Faceless AI"])
app.include_router(live.router, prefix="/api/live", tags=["Live Restream"])

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API Server is running"}
