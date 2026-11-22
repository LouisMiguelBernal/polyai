import os, logging, time
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import select, delete, func, and_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from db import (AsyncSessionLocal, Wallet, WalletPosition, ClusterEvent,
    AlertLog, WhaleEvent, ScoreTrend)
from polymarket_client import (fetch_top_wallets_by_volume, fetch_wallet_profiles_batch,
    fetch_positions_batch, fetch_market_info, compute_historical_accuracy_stub)
from telegram_alerts import send_alert, send_whale_alert

logger = logging.getLogger(__name__)
TOP_WALLET_COUNT     = int(os.getenv("TOP_WALLET_COUNT",500))
MIN_TRADES           = int(os.getenv("MIN_TRADES",100))
MIN_WIN_RATE         = float(os.getenv("MIN_WIN_RATE",60))
CONVICTION_THRESHOLD = float(os.getenv("CONVICTION_THRESHOLD",75))
CLUSTER_MIN_WALLETS  = int(os.getenv("CLUSTER_MIN_WALLETS",10))
WHALE_THRESHOLD_USDC = float(os.getenv("WHALE_THRESHOLD_USDC",10000))
ALERT_COOLDOWN_HOURS = float(os.getenv("ALERT_COOLDOWN_HOURS",6))

# ── Alert cooldown tracking ─────────────────────────────────
_alert_cooldowns: dict[str, datetime] = {}  # "market_id:outcome" -> last alert time

def _is_on_cooldown(market_id: str, outcome: str) -> bool:
    key = f"{market_id}:{outcome}"
    last = _alert_cooldowns.get(key)
    if not last: return False
    return (datetime.utcnow() - last).total_seconds() < ALERT_COOLDOWN_HOURS * 3600

def _mark_alerted(market_id: str, outcome: str):
    _alert_cooldowns[f"{market_id}:{outcome}"] = datetime.utcnow()


async def fetch_and_filter_wallets():
    logger.info("Fetching leaderboard...")
    leaderboard = await fetch_top_wallets_by_volume(limit=TOP_WALLET_COUNT)
    if not leaderboard: return []
    profiles = await fetch_wallet_profiles_batch(leaderboard, concurrency=8)
    qualified = []
    for p in profiles:
        addr        = p.get("address","")
        total_trades= int(p.get("total_trades") or 0)
        win_rate    = float(p.get("win_rate") or 0.0)
        roi_30d     = float(p.get("roi_30d") or 0.0)
        pnl         = float(p.get("total_profit") or 0.0)
        roi_pct     = float(p.get("roi_pct") or 0.0)
        volume      = float(p.get("volume_usdc") or 0.0)
        recent_24h  = int(p.get("recent_trades_24h") or 0)
        if not addr: continue
        if total_trades < MIN_TRADES: continue
        if win_rate < MIN_WIN_RATE: continue
        if roi_30d <= 0: continue
        qualified.append({"address":addr,"total_profit":pnl,"roi_pct":roi_pct,
            "win_rate":win_rate,"total_trades":total_trades,"roi_30d":roi_30d,
            "volume_usdc":volume,"recent_trades_24h":recent_24h})
    logger.info(f"{len(qualified)} wallets qualified")
    return qualified

async def persist_wallets(wallets):
    async with AsyncSessionLocal() as session:
        for rank, w in enumerate(sorted(wallets,key=lambda x:x["total_profit"],reverse=True),1):
            stmt = sqlite_insert(Wallet).values(address=w["address"],total_profit=w["total_profit"],
                roi_pct=w["roi_pct"],win_rate=w["win_rate"],total_trades=w["total_trades"],
                roi_30d=w["roi_30d"],volume_usdc=w["volume_usdc"],rank=rank,
                last_updated=datetime.utcnow()).on_conflict_do_update(
                index_elements=["address"],
                set_={"total_profit":w["total_profit"],"roi_pct":w["roi_pct"],
                    "win_rate":w["win_rate"],"total_trades":w["total_trades"],
                    "roi_30d":w["roi_30d"],"volume_usdc":w["volume_usdc"],
                    "rank":rank,"last_updated":datetime.utcnow()})
            await session.execute(stmt)
        await session.commit()

async def fetch_and_cluster(wallets):
    addresses  = [w["address"] for w in wallets]
    wallet_map = {w["address"]:w for w in wallets}
    all_positions = await fetch_positions_batch(addresses, concurrency=8)
    async with AsyncSessionLocal() as session:
        await session.execute(delete(WalletPosition)); await session.commit()
    market_clusters = defaultdict(list)
    now, rows = datetime.utcnow(), []
    whale_candidates = []
    for addr, positions in all_positions.items():
        w = wallet_map.get(addr,{})
        for pos in positions:
            market_id = pos.get("conditionId") or pos.get("market") or pos.get("marketId","")
            outcome   = pos.get("outcome","YES").upper()
            size      = float(pos.get("size") or pos.get("currentValue") or 0.0)
            price     = float(pos.get("curPrice") or pos.get("price") or 0.0)
            entry     = float(pos.get("avgPrice") or price)
            question  = pos.get("title") or pos.get("question") or ""
            if not market_id or size < 0.01: continue
            rows.append(WalletPosition(wallet_address=addr,market_id=market_id,
                market_question=question,outcome=outcome,size_usdc=size,
                entry_price=entry,current_price=price,
                unrealised_pnl=(price-entry)*size if price and entry else 0.0,
                recorded_at=now))
            market_clusters[(market_id,outcome)].append(
                {"wallet":addr,"size":size,"win_rate":w.get("win_rate",0),
                 "roi_30d":w.get("roi_30d",0),"question":question,
                 "recent_24h":w.get("recent_trades_24h",0)})
            # Whale detection
            if size >= WHALE_THRESHOLD_USDC:
                whale_candidates.append({
                    "wallet":addr,"market_id":market_id,"question":question,
                    "outcome":outcome,"size":size,
                    "win_rate":w.get("win_rate",0),"roi":w.get("roi_30d",0)})
    async with AsyncSessionLocal() as session:
        session.add_all(rows); await session.commit()
    clusters = []
    for (market_id,outcome), entries in market_clusters.items():
        if len(entries) < CLUSTER_MIN_WALLETS: continue
        # Momentum: average recent trade activity across aligned wallets
        avg_momentum = sum(e["recent_24h"] for e in entries)/len(entries) if entries else 0
        clusters.append({"market_id":market_id,"outcome":outcome,"wallet_count":len(entries),
            "avg_roi":sum(e["roi_30d"] for e in entries)/len(entries),
            "avg_win_rate":sum(e["win_rate"] for e in entries)/len(entries),
            "total_size":sum(e["size"] for e in entries),"question":entries[0]["question"],
            "avg_momentum":avg_momentum})
    logger.info(f"{len(clusters)} clusters, {len(whale_candidates)} whale positions found")
    return clusters, whale_candidates

def compute_conviction_score(cluster, historical_accuracy):
    wallet_score   = min(100,((cluster["wallet_count"]-CLUSTER_MIN_WALLETS)/40)*100)
    roi_score      = min(100,(cluster["avg_roi"]/200)*100)
    hist_score     = min(100,max(0,(historical_accuracy-0.5)/0.5*100))
    size_score     = min(100,(cluster["total_size"]/50_000)*100)
    momentum_score = min(100, cluster.get("avg_momentum",0) * 10)  # 10 recent trades = max
    # Weighted: wallets 25%, ROI 20%, history 20%, size 15%, momentum 20%
    return round(wallet_score*0.25 + roi_score*0.20 + hist_score*0.20 +
                 size_score*0.15 + momentum_score*0.20, 2)

async def process_whales(whale_candidates):
    """Detect and alert on whale-sized single positions."""
    alerts_sent = 0
    async with AsyncSessionLocal() as session:
        for wh in whale_candidates:
            # Check cooldown
            if _is_on_cooldown(wh["market_id"], f"whale:{wh['wallet']}"):
                continue
            event = WhaleEvent(
                wallet_address=wh["wallet"], market_id=wh["market_id"],
                market_question=wh["question"], outcome=wh["outcome"],
                size_usdc=wh["size"], wallet_win_rate=wh["win_rate"],
                wallet_roi=wh["roi"], alert_sent=True, detected_at=datetime.utcnow())
            session.add(event)
            msg, success = await send_whale_alert(
                wallet_address=wh["wallet"], market_question=wh["question"],
                outcome=wh["outcome"], size_usdc=wh["size"],
                win_rate=wh["win_rate"], roi=wh["roi"], market_id=wh["market_id"])
            if success:
                alerts_sent += 1
                _mark_alerted(wh["market_id"], f"whale:{wh['wallet']}")
        await session.commit()
    return alerts_sent

async def record_score_trends(clusters_with_scores):
    """Save conviction score snapshots for trend analysis."""
    async with AsyncSessionLocal() as session:
        now = datetime.utcnow()
        for c, score in clusters_with_scores:
            session.add(ScoreTrend(
                market_id=c["market_id"], outcome=c["outcome"],
                conviction_score=score, wallet_count=c["wallet_count"],
                total_size_usdc=c["total_size"], recorded_at=now))
        await session.commit()

async def process_clusters(clusters):
    alerts_sent = 0
    clusters_with_scores = []
    async with AsyncSessionLocal() as session:
        for c in clusters:
            hist     = await compute_historical_accuracy_stub(c["market_id"],c["outcome"])
            question = c.get("question","")
            if not question:
                info = await fetch_market_info(c["market_id"])
                question = info.get("question") or info.get("title") or c["market_id"]
            score = compute_conviction_score(c,hist)
            clusters_with_scores.append((c, score))
            event = ClusterEvent(market_id=c["market_id"],market_question=question,
                outcome=c["outcome"],wallet_count=c["wallet_count"],avg_roi=c["avg_roi"],
                avg_win_rate=c["avg_win_rate"],total_size_usdc=c["total_size"],
                conviction_score=score,historical_accuracy=hist,
                alert_sent=(score>=CONVICTION_THRESHOLD and not _is_on_cooldown(c["market_id"],c["outcome"])),
                detected_at=datetime.utcnow())
            session.add(event); await session.flush()
            if score >= CONVICTION_THRESHOLD and not _is_on_cooldown(c["market_id"],c["outcome"]):
                logger.info(f"ALERT: {question[:50]} score={score}")
                msg, success = await send_alert(market_question=question,outcome=c["outcome"],
                    wallet_count=c["wallet_count"],conviction_score=score,avg_roi=c["avg_roi"],
                    avg_win_rate=c["avg_win_rate"],total_size_usdc=c["total_size"],
                    historical_accuracy=hist,market_id=c["market_id"],
                    momentum=c.get("avg_momentum",0))
                session.add(AlertLog(cluster_id=event.id,market_id=c["market_id"],
                    market_question=question,conviction_score=score,message=msg,
                    sent_at=datetime.utcnow(),success=success))
                if success:
                    alerts_sent += 1
                    _mark_alerted(c["market_id"], c["outcome"])
            elif score >= CONVICTION_THRESHOLD:
                logger.info(f"COOLDOWN: {question[:50]} score={score} (suppressed)")
        await session.commit()
    # Record trends
    await record_score_trends(clusters_with_scores)
    return alerts_sent

async def run_pipeline():
    started = datetime.utcnow()
    logger.info("PIPELINE START")
    wallets  = await fetch_and_filter_wallets()
    await persist_wallets(wallets)
    clusters, whale_candidates = await fetch_and_cluster(wallets)
    alerts   = await process_clusters(clusters)
    whale_alerts = await process_whales(whale_candidates)
    elapsed  = (datetime.utcnow()-started).total_seconds()
    summary  = {"run_at":started.isoformat(),"elapsed_seconds":round(elapsed,1),
        "wallets_checked":TOP_WALLET_COUNT,"wallets_qualified":len(wallets),
        "clusters_found":len(clusters),"alerts_sent":alerts,
        "whale_positions":len(whale_candidates),"whale_alerts":whale_alerts}
    logger.info(f"PIPELINE DONE {summary}")
    return summary
