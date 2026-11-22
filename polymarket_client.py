import os, asyncio, logging, time
from collections import defaultdict
from datetime import datetime, timedelta
import httpx

logger   = logging.getLogger(__name__)
DATA_API = "https://data-api.polymarket.com"
GAMMA_API= "https://gamma-api.polymarket.com"
TIMEOUT  = httpx.Timeout(30.0)

# ── Blacklist / Watchlist ───────────────────────────────────
_blacklist: set[str] = set()
_watchlist: set[str] = set()

def add_to_blacklist(address: str):
    _blacklist.add(address.lower())
def remove_from_blacklist(address: str):
    _blacklist.discard(address.lower())
def get_blacklist() -> list[str]:
    return sorted(_blacklist)

def add_to_watchlist(address: str):
    _watchlist.add(address.lower())
def remove_from_watchlist(address: str):
    _watchlist.discard(address.lower())
def get_watchlist() -> list[str]:
    return sorted(_watchlist)

def is_blacklisted(address: str) -> bool:
    return address.lower() in _blacklist

async def fetch_top_wallets_by_volume(limit=500):
    logger.info("Fetching top wallets via /trades...")
    wallet_stats = defaultdict(lambda: {"total_cash":0.0,"trade_count":0,"address":"","first_seen":None,"last_seen":None})
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for offset in range(0, 2000, 500):
            try:
                r = await client.get(f"{DATA_API}/trades", params={
                    "limit":500,"offset":offset,"filterType":"CASH",
                    "filterAmount":10,"takerOnly":"false"})
                r.raise_for_status()
                trades = r.json()
                if not trades: break
                for t in trades:
                    addr = t.get("proxyWallet","")
                    if not addr or is_blacklisted(addr): continue
                    cash = float(t.get("usdcSize") or t.get("size") or 0)
                    ts   = t.get("timestamp") or t.get("matchedAt","")
                    wallet_stats[addr]["total_cash"]  += cash
                    wallet_stats[addr]["trade_count"] += 1
                    wallet_stats[addr]["address"]      = addr
                    if ts:
                        if wallet_stats[addr]["first_seen"] is None or ts < wallet_stats[addr]["first_seen"]:
                            wallet_stats[addr]["first_seen"] = ts
                        if wallet_stats[addr]["last_seen"] is None or ts > wallet_stats[addr]["last_seen"]:
                            wallet_stats[addr]["last_seen"] = ts
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.warning(f"Trades fetch error: {e}"); break
    sorted_w = sorted(wallet_stats.values(), key=lambda x:x["total_cash"], reverse=True)[:limit]
    # Always include watchlisted wallets even if they didn't make the volume cut
    included = {w["address"] for w in sorted_w}
    for addr in _watchlist:
        if addr not in included:
            sorted_w.append({"total_cash":0.0,"trade_count":0,"address":addr,"first_seen":None,"last_seen":None})
    logger.info(f"Discovered {len(sorted_w)} wallets")
    return sorted_w

async def fetch_wallet_trades(address, limit=200):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(f"{DATA_API}/trades",
                params={"user":address,"limit":limit,"takerOnly":"false"})
            r.raise_for_status()
            d = r.json(); return d if isinstance(d,list) else []
        except Exception as e:
            logger.warning(f"Trades error {address[:8]}: {e}"); return []

async def fetch_wallet_positions(address):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(f"{DATA_API}/positions",
                params={"user":address,"sizeThreshold":"1","limit":500,"sortBy":"CURRENT"})
            r.raise_for_status()
            d = r.json(); return d if isinstance(d,list) else []
        except Exception as e:
            logger.warning(f"Positions error {address[:8]}: {e}"); return []

async def enrich_wallet(address, base_volume):
    trades      = await fetch_wallet_trades(address, limit=200)
    total_trades= len(trades)
    buys        = [t for t in trades if t.get("side")=="BUY"]
    sells       = [t for t in trades if t.get("side")=="SELL"]
    # Better win rate: group by market, check if sell price > buy price
    market_buys = defaultdict(list)
    market_sells = defaultdict(list)
    for t in buys:
        mid = t.get("market","") or t.get("conditionId","")
        if mid: market_buys[mid].append(float(t.get("price",0)))
    for t in sells:
        mid = t.get("market","") or t.get("conditionId","")
        if mid: market_sells[mid].append(float(t.get("price",0)))
    wins, total_resolved = 0, 0
    for mid in market_buys:
        if mid in market_sells:
            avg_buy  = sum(market_buys[mid])/len(market_buys[mid])
            avg_sell = sum(market_sells[mid])/len(market_sells[mid])
            total_resolved += 1
            if avg_sell > avg_buy: wins += 1
    win_rate = (wins/total_resolved*100) if total_resolved > 0 else 0.0
    # Fallback: if no resolved markets, use high-confidence buy heuristic
    if total_resolved == 0 and buys:
        high_conf = [t for t in buys if float(t.get("price",0))>=0.5]
        win_rate  = (len(high_conf)/len(buys)*100)
    total_in    = sum(float(t.get("usdcSize",0)) for t in buys)
    total_out   = sum(float(t.get("usdcSize",0)) for t in sells)
    roi_pct     = ((total_out-total_in)/total_in*100) if total_in>0 else 0.0
    # Momentum: trades in last 24h vs overall rate
    recent_trades = 0
    try:
        cutoff_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()
        for t in trades:
            ts = t.get("timestamp") or t.get("matchedAt","")
            if ts and ts > cutoff_24h: recent_trades += 1
    except: pass
    return {"address":address,"total_profit":max(0,total_out-total_in),
        "roi_pct":roi_pct,"roi_30d":roi_pct,"win_rate":win_rate,
        "total_trades":total_trades,"volume_usdc":base_volume,
        "recent_trades_24h":recent_trades}

async def fetch_wallet_profiles_batch(wallet_summaries, concurrency=8):
    sem = asyncio.Semaphore(concurrency)
    async def _e(w):
        async with sem:
            r = await enrich_wallet(w["address"], w["total_cash"])
            await asyncio.sleep(0.15); return r
    results = await asyncio.gather(*[_e(w) for w in wallet_summaries], return_exceptions=True)
    return [r for r in results if isinstance(r,dict)]

async def fetch_positions_batch(addresses, concurrency=8):
    sem = asyncio.Semaphore(concurrency)
    result = {}
    async def _f(addr):
        async with sem:
            result[addr] = await fetch_wallet_positions(addr)
            await asyncio.sleep(0.2)
    await asyncio.gather(*[_f(a) for a in addresses])
    return result

async def fetch_market_info(market_id):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.get(f"{GAMMA_API}/markets", params={"conditionId":market_id})
            r.raise_for_status()
            d = r.json()
            return d[0] if isinstance(d,list) and d else {}
        except: return {}

# ── Real historical accuracy from resolved markets ──────────
_accuracy_cache: dict[str, tuple[float, float]] = {}  # market_id -> (accuracy, timestamp)
ACCURACY_CACHE_TTL = 3600  # 1 hour

async def compute_historical_accuracy(market_id, outcome):
    """Compute accuracy by checking the current market price as a proxy for crowd consensus.
    For resolved markets, the price IS the outcome (1.0 or 0.0).
    For active markets, the price reflects probability — we compare cluster direction to it."""
    cache_key = f"{market_id}:{outcome}"
    now = time.time()
    if cache_key in _accuracy_cache:
        cached_val, cached_at = _accuracy_cache[cache_key]
        if now - cached_at < ACCURACY_CACHE_TTL:
            return cached_val
    try:
        info = await fetch_market_info(market_id)
        if not info:
            return 0.65  # fallback
        # Check if market is resolved
        resolved = info.get("resolved", False) or info.get("closed", False)
        if resolved:
            winning = (info.get("outcome","") or "").upper()
            accuracy = 1.0 if winning == outcome.upper() else 0.0
        else:
            # Use current price as probability proxy
            price = float(info.get("outcomePrices","[0.5]").strip("[]").split(",")[0] or 0.5)
            if outcome.upper() == "NO":
                price = 1.0 - price
            # Higher price = higher market agreement with cluster direction
            accuracy = max(0.3, min(0.95, price))
        _accuracy_cache[cache_key] = (accuracy, now)
        return accuracy
    except Exception as e:
        logger.warning(f"Historical accuracy error {market_id[:8]}: {e}")
        return 0.65

# Keep stub name as alias for backward compat
async def compute_historical_accuracy_stub(market_id, outcome):
    return await compute_historical_accuracy(market_id, outcome)
