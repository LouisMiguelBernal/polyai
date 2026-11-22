"""
main.py — FastAPI server + APScheduler + static dashboard
Run: python main.py  then open http://localhost:8000
"""
import os, asyncio, logging, csv, io
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, func, desc
import httpx

from db import (init_db, AsyncSessionLocal, Wallet, WalletPosition,
    ClusterEvent, AlertLog, WhaleEvent, ScoreTrend)
from pipeline import run_pipeline
from telegram_alerts import send_startup_message, send_test_alert
from polymarket_client import (add_to_blacklist, remove_from_blacklist, get_blacklist,
    add_to_watchlist, remove_from_watchlist, get_watchlist)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", 60))
DATA_API      = "https://data-api.polymarket.com"
scheduler     = AsyncIOScheduler()
_last_run: dict = {}


@asynccontextmanager
async def lifespan(app):
    await init_db()
    await send_startup_message()
    asyncio.create_task(_initial_run())
    scheduler.add_job(_scheduled_run, "interval", minutes=POLL_INTERVAL,
        id="pipeline", max_instances=1)
    scheduler.start()
    logger.info(f"✅ Pipeline scheduled every {POLL_INTERVAL}m — Dashboard: http://localhost:8000")
    yield
    scheduler.shutdown()


async def _initial_run():
    await asyncio.sleep(3)
    global _last_run; _last_run = await run_pipeline()


async def _scheduled_run():
    global _last_run; _last_run = await run_pipeline()


app = FastAPI(title="PolyAI", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


# ── Dashboard ────────────────────────────────────────────────
@app.get("/", response_class=FileResponse)
async def serve_dashboard():
    dash = Path(__file__).parent / "dashboard.html"
    return FileResponse(str(dash))


# ── Status ───────────────────────────────────────────────────
@app.get("/api/status")
async def get_status():
    job = scheduler.get_job("pipeline")
    return {"status": "online", "last_run": _last_run,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "poll_interval_minutes": POLL_INTERVAL}


@app.post("/api/pipeline/run")
async def trigger_pipeline():
    global _last_run; _last_run = await run_pipeline()
    return {"success": True, "summary": _last_run}


@app.post("/api/alerts/test")
async def trigger_test_alert():
    msg, ok = await send_test_alert()
    return {"success": ok, "message": msg}


# ── Wallets ──────────────────────────────────────────────────
@app.get("/api/wallets")
async def get_wallets(limit: int = 100, offset: int = 0,
        sort_by: str = "rank", order: str = "asc"):
    col_map = {"rank": Wallet.rank, "profit": Wallet.total_profit,
        "win_rate": Wallet.win_rate, "roi": Wallet.roi_30d,
        "volume": Wallet.volume_usdc, "trades": Wallet.total_trades}
    col = col_map.get(sort_by, Wallet.rank)
    order_fn = desc(col) if order == "desc" else col.asc()
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Wallet).order_by(order_fn).limit(limit).offset(offset))
        wallets = result.scalars().all()
        total   = await s.scalar(select(func.count(Wallet.address)))
    return {"total": total, "wallets": [{
        "address":      w.address,
        "short":        w.address[:6]+"…"+w.address[-4:],
        "rank":         w.rank,
        "total_profit": round(w.total_profit, 2),
        "roi_pct":      round(w.roi_pct, 2),
        "win_rate":     round(w.win_rate, 2),
        "total_trades": w.total_trades,
        "roi_30d":      round(w.roi_30d, 2),
        "volume_usdc":  round(w.volume_usdc, 2),
        "last_updated": w.last_updated.isoformat() if w.last_updated else None,
    } for w in wallets]}


@app.get("/api/wallets/{address}/positions")
async def get_wallet_positions(address: str):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(WalletPosition)
            .where(WalletPosition.wallet_address == address)
            .order_by(desc(WalletPosition.size_usdc)))
        positions = result.scalars().all()
    return [{"market_id": p.market_id, "market_question": p.market_question,
        "outcome": p.outcome, "size_usdc": round(p.size_usdc, 2),
        "entry_price": round(p.entry_price, 4), "current_price": round(p.current_price, 4),
        "unrealised_pnl": round(p.unrealised_pnl, 2)} for p in positions]


# ── Trades — proxied from Polymarket Data API ─────────────────
@app.get("/api/trades")
async def get_trades(limit: int = 100, side: str = ""):
    params = {"limit": limit, "takerOnly": "false", "filterType": "CASH", "filterAmount": 10}
    if side:
        params["side"] = side
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{DATA_API}/trades", params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning(f"Trades proxy error: {e}")
        return []


# ── Clusters ─────────────────────────────────────────────────
@app.get("/api/clusters")
async def get_clusters(limit: int = 30, min_score: float = 0):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(ClusterEvent)
            .where(ClusterEvent.conviction_score >= min_score)
            .order_by(desc(ClusterEvent.conviction_score)).limit(limit))
        clusters = result.scalars().all()
    return [{"id": c.id, "market_id": c.market_id, "market_question": c.market_question,
        "outcome": c.outcome, "wallet_count": c.wallet_count,
        "avg_roi": round(c.avg_roi, 2), "avg_win_rate": round(c.avg_win_rate, 2),
        "total_size_usdc": round(c.total_size_usdc, 2),
        "conviction_score": round(c.conviction_score, 2),
        "historical_accuracy": round(c.historical_accuracy * 100, 1),
        "alert_sent": c.alert_sent,
        "detected_at": c.detected_at.isoformat() if c.detected_at else None,
    } for c in clusters]


# ── Whale Events ─────────────────────────────────────────────
@app.get("/api/whales")
async def get_whales(limit: int = 30):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(WhaleEvent)
            .order_by(desc(WhaleEvent.detected_at)).limit(limit))
        whales = result.scalars().all()
    return [{"id": w.id, "wallet_address": w.wallet_address,
        "short": w.wallet_address[:6]+"…"+w.wallet_address[-4:],
        "market_id": w.market_id, "market_question": w.market_question,
        "outcome": w.outcome, "size_usdc": round(w.size_usdc, 2),
        "wallet_win_rate": round(w.wallet_win_rate, 2),
        "wallet_roi": round(w.wallet_roi, 2),
        "detected_at": w.detected_at.isoformat() if w.detected_at else None,
    } for w in whales]


# ── Score Trends ─────────────────────────────────────────────
@app.get("/api/trends/{market_id}")
async def get_score_trends(market_id: str, limit: int = 50):
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(ScoreTrend)
            .where(ScoreTrend.market_id == market_id)
            .order_by(desc(ScoreTrend.recorded_at)).limit(limit))
        trends = result.scalars().all()
    return [{"conviction_score": round(t.conviction_score, 2),
        "wallet_count": t.wallet_count,
        "total_size_usdc": round(t.total_size_usdc, 2),
        "outcome": t.outcome,
        "recorded_at": t.recorded_at.isoformat() if t.recorded_at else None,
    } for t in reversed(trends)]


# ── Alerts ───────────────────────────────────────────────────
@app.get("/api/alerts")
async def get_alerts(limit: int = 30):
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(AlertLog).order_by(desc(AlertLog.sent_at)).limit(limit))
        logs = result.scalars().all()
    return [{"id": l.id, "market_id": l.market_id, "market_question": l.market_question,
        "conviction_score": round(l.conviction_score, 2),
        "sent_at": l.sent_at.isoformat() if l.sent_at else None,
        "success": l.success} for l in logs]


# ── Stats ────────────────────────────────────────────────────
@app.get("/api/stats")
async def get_stats():
    async with AsyncSessionLocal() as s:
        wc  = await s.scalar(select(func.count(Wallet.address)))
        cc  = await s.scalar(select(func.count(ClusterEvent.id)))
        ac  = await s.scalar(select(func.count(AlertLog.id)))
        avg = await s.scalar(select(func.avg(ClusterEvent.conviction_score)))
        top = await s.scalar(select(func.max(ClusterEvent.conviction_score)))
        whc = await s.scalar(select(func.count(WhaleEvent.id)))
    return {"wallets_tracked": wc or 0, "clusters_detected": cc or 0,
        "alerts_sent": ac or 0, "avg_conviction": round(avg or 0, 1),
        "top_conviction": round(top or 0, 1), "whale_events": whc or 0}


# ── Watchlist / Blacklist Management ─────────────────────────
@app.get("/api/watchlist")
async def api_get_watchlist():
    return {"watchlist": get_watchlist()}

@app.post("/api/watchlist/{address}")
async def api_add_watchlist(address: str):
    add_to_watchlist(address)
    return {"success": True, "watchlist": get_watchlist()}

@app.delete("/api/watchlist/{address}")
async def api_remove_watchlist(address: str):
    remove_from_watchlist(address)
    return {"success": True, "watchlist": get_watchlist()}

@app.get("/api/blacklist")
async def api_get_blacklist():
    return {"blacklist": get_blacklist()}

@app.post("/api/blacklist/{address}")
async def api_add_blacklist(address: str):
    add_to_blacklist(address)
    return {"success": True, "blacklist": get_blacklist()}

@app.delete("/api/blacklist/{address}")
async def api_remove_blacklist(address: str):
    remove_from_blacklist(address)
    return {"success": True, "blacklist": get_blacklist()}


# ── CSV Export ───────────────────────────────────────────────
@app.get("/api/export/wallets")
async def export_wallets_csv():
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(Wallet).order_by(Wallet.rank))
        wallets = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["rank","address","total_profit","roi_pct","win_rate",
        "total_trades","roi_30d","volume_usdc","last_updated"])
    for w in wallets:
        writer.writerow([w.rank, w.address, round(w.total_profit,2), round(w.roi_pct,2),
            round(w.win_rate,2), w.total_trades, round(w.roi_30d,2),
            round(w.volume_usdc,2), w.last_updated.isoformat() if w.last_updated else ""])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition":"attachment; filename=polyai_wallets.csv"})

@app.get("/api/export/clusters")
async def export_clusters_csv():
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(ClusterEvent).order_by(desc(ClusterEvent.conviction_score)))
        clusters = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","market_id","market_question","outcome","wallet_count",
        "avg_roi","avg_win_rate","total_size_usdc","conviction_score",
        "historical_accuracy","alert_sent","detected_at"])
    for c in clusters:
        writer.writerow([c.id, c.market_id, c.market_question, c.outcome, c.wallet_count,
            round(c.avg_roi,2), round(c.avg_win_rate,2), round(c.total_size_usdc,2),
            round(c.conviction_score,2), round(c.historical_accuracy*100,1),
            c.alert_sent, c.detected_at.isoformat() if c.detected_at else ""])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition":"attachment; filename=polyai_clusters.csv"})

@app.get("/api/export/whales")
async def export_whales_csv():
    async with AsyncSessionLocal() as s:
        result = await s.execute(select(WhaleEvent).order_by(desc(WhaleEvent.detected_at)))
        whales = result.scalars().all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id","wallet_address","market_id","market_question","outcome",
        "size_usdc","wallet_win_rate","wallet_roi","detected_at"])
    for w in whales:
        writer.writerow([w.id, w.wallet_address, w.market_id, w.market_question,
            w.outcome, round(w.size_usdc,2), round(w.wallet_win_rate,2),
            round(w.wallet_roi,2), w.detected_at.isoformat() if w.detected_at else ""])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition":"attachment; filename=polyai_whales.csv"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=os.getenv("BACKEND_HOST","0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", 8000)), reload=False)
