import os, logging
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
from telegram.constants import ParseMode
import httpx

logger    = logging.getLogger(__name__)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID","")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL","")

def _get_bot():
    if not BOT_TOKEN or not CHAT_ID: return None
    return Bot(token=BOT_TOKEN)

def _bar(score):
    return "█"*int(score/10) + "░"*(10-int(score/10))

def _momentum_label(momentum):
    if momentum >= 8: return "🚀 SURGING"
    if momentum >= 4: return "📈 ACTIVE"
    if momentum >= 1: return "〰️ STEADY"
    return "💤 QUIET"

def build_alert_message(market_question,outcome,wallet_count,conviction_score,
        avg_roi,avg_win_rate,total_size_usdc,historical_accuracy,market_id,
        momentum=0):
    out_emoji = "🟢" if outcome.upper()=="YES" else "🔴"
    severity  = "🔥 EXTREME" if conviction_score>=90 else ("⚡ HIGH" if conviction_score>=80 else "🎯 NOTABLE")
    ts        = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    mom_label = _momentum_label(momentum)
    return (f"<b>🤖 POLYAI SMART MONEY ALERT</b>\n<code>{'─'*32}</code>\n\n{severity}\n\n"
        f"📋 <b>Market</b>\n<i>{market_question}</i>\n\n"
        f"{out_emoji} <b>Position:</b> <code>{outcome}</code>\n\n"
        f"<b>📊 Conviction Score</b>\n<code>{_bar(conviction_score)}</code>  <b>{conviction_score:.0f}/100</b>\n\n"
        f"<b>👛 Aligned Wallets:</b>  <code>{wallet_count}</code>\n"
        f"<b>📈 Avg Wallet ROI:</b>   <code>{avg_roi:+.1f}%</code>\n"
        f"<b>🏆 Avg Win Rate:</b>     <code>{avg_win_rate:.1f}%</code>\n"
        f"<b>💰 Total Position:</b>   <code>${total_size_usdc:,.0f} USDC</code>\n"
        f"<b>🎯 Historical Acc:</b>   <code>{historical_accuracy*100:.1f}%</code>\n"
        f"<b>⚡ Momentum:</b>         <code>{mom_label}</code>\n\n"
        f"<code>{'─'*32}</code>\n"
        f"🔗 <a href='https://polymarket.com/event/{market_id}'>View on Polymarket</a>\n"
        f"⏱ <code>{ts}</code>")

def build_whale_message(wallet_address,market_question,outcome,size_usdc,
        win_rate,roi,market_id):
    out_emoji = "🟢" if outcome.upper()=="YES" else "🔴"
    short_addr = wallet_address[:6]+"…"+wallet_address[-4:]
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return (f"<b>🐋 POLYAI WHALE ALERT</b>\n<code>{'─'*32}</code>\n\n"
        f"📋 <b>Market</b>\n<i>{market_question}</i>\n\n"
        f"{out_emoji} <b>Position:</b> <code>{outcome}</code>\n"
        f"<b>💰 Size:</b>     <code>${size_usdc:,.0f} USDC</code>\n"
        f"<b>👛 Wallet:</b>   <code>{short_addr}</code>\n"
        f"<b>🏆 Win Rate:</b> <code>{win_rate:.1f}%</code>\n"
        f"<b>📈 ROI:</b>      <code>{roi:+.1f}%</code>\n\n"
        f"<code>{'─'*32}</code>\n"
        f"🔗 <a href='https://polymarket.com/event/{market_id}'>View on Polymarket</a>\n"
        f"⏱ <code>{ts}</code>")

def _build_discord_embed(title, message_html, color=0x00e5a0):
    """Convert HTML alert to Discord embed (strip HTML tags for plain text)."""
    import re
    plain = re.sub(r'<[^>]+>', '', message_html)
    plain = plain.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
    return {"embeds": [{"title": title, "description": plain[:4000], "color": color}]}

async def _send_discord(title, message_html, color=0x00e5a0):
    if not DISCORD_WEBHOOK_URL: return
    try:
        payload = _build_discord_embed(title, message_html, color)
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(DISCORD_WEBHOOK_URL, json=payload)
            r.raise_for_status()
            logger.info("Discord alert sent")
    except Exception as e:
        logger.warning(f"Discord failed: {e}")

async def send_alert(market_question,outcome,wallet_count,conviction_score,
        avg_roi,avg_win_rate,total_size_usdc,historical_accuracy,market_id,
        momentum=0):
    bot = _get_bot()
    message = build_alert_message(market_question,outcome,wallet_count,conviction_score,
        avg_roi,avg_win_rate,total_size_usdc,historical_accuracy,market_id,momentum)
    success = False
    if bot:
        try:
            await bot.send_message(chat_id=CHAT_ID,text=message,
                parse_mode=ParseMode.HTML,disable_web_page_preview=True)
            success = True
        except TelegramError as e:
            logger.error(f"TG failed: {e}")
    # Also send to Discord
    await _send_discord("🤖 Smart Money Alert", message)
    return (message, success or bool(DISCORD_WEBHOOK_URL))

async def send_whale_alert(wallet_address,market_question,outcome,size_usdc,
        win_rate,roi,market_id):
    bot = _get_bot()
    message = build_whale_message(wallet_address,market_question,outcome,
        size_usdc,win_rate,roi,market_id)
    success = False
    if bot:
        try:
            await bot.send_message(chat_id=CHAT_ID,text=message,
                parse_mode=ParseMode.HTML,disable_web_page_preview=True)
            success = True
        except TelegramError as e:
            logger.error(f"TG whale failed: {e}")
    await _send_discord("🐋 Whale Alert", message, color=0xff6b35)
    return (message, success or bool(DISCORD_WEBHOOK_URL))

async def send_startup_message():
    # Silent — no TG spam on startup, only real signals
    logger.info("PolyAI bot online (TG startup message disabled)")

async def send_test_alert():
    return await send_alert(
        market_question="Will BTC hit $150,000 before December 2026?",
        outcome="YES",wallet_count=18,conviction_score=83.5,
        avg_roi=162.4,avg_win_rate=72.1,total_size_usdc=44200.0,
        historical_accuracy=0.74,market_id="test-market-id",
        momentum=5)
