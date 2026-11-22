import os
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, Text, Index
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polyai.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

class Wallet(Base):
    __tablename__ = "wallets"
    address      = Column(String, primary_key=True)
    total_profit = Column(Float, default=0.0)
    roi_pct      = Column(Float, default=0.0)
    win_rate     = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    roi_30d      = Column(Float, default=0.0)
    volume_usdc  = Column(Float, default=0.0)
    rank         = Column(Integer, default=0)
    last_updated = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_wallets_rank","rank"),)

class WalletPosition(Base):
    __tablename__ = "wallet_positions"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address  = Column(String, nullable=False)
    market_id       = Column(String, nullable=False)
    market_question = Column(Text, default="")
    outcome         = Column(String, default="")
    size_usdc       = Column(Float, default=0.0)
    entry_price     = Column(Float, default=0.0)
    current_price   = Column(Float, default=0.0)
    unrealised_pnl  = Column(Float, default=0.0)
    recorded_at     = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_positions_market","market_id"),)

class ClusterEvent(Base):
    __tablename__ = "cluster_events"
    id                  = Column(Integer, primary_key=True, autoincrement=True)
    market_id           = Column(String, nullable=False)
    market_question     = Column(Text, default="")
    outcome             = Column(String, default="")
    wallet_count        = Column(Integer, default=0)
    avg_roi             = Column(Float, default=0.0)
    avg_win_rate        = Column(Float, default=0.0)
    total_size_usdc     = Column(Float, default=0.0)
    conviction_score    = Column(Float, default=0.0)
    historical_accuracy = Column(Float, default=0.0)
    alert_sent          = Column(Boolean, default=False)
    detected_at         = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_cluster_conviction","conviction_score"),)

class AlertLog(Base):
    __tablename__ = "alert_logs"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id       = Column(Integer, nullable=False)
    market_id        = Column(String, nullable=False)
    market_question  = Column(Text, default="")
    conviction_score = Column(Float, default=0.0)
    message          = Column(Text, default="")
    sent_at          = Column(DateTime, default=datetime.utcnow)
    success          = Column(Boolean, default=True)

class WhaleEvent(Base):
    __tablename__ = "whale_events"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address  = Column(String, nullable=False)
    market_id       = Column(String, nullable=False)
    market_question = Column(Text, default="")
    outcome         = Column(String, default="")
    size_usdc       = Column(Float, default=0.0)
    wallet_win_rate = Column(Float, default=0.0)
    wallet_roi      = Column(Float, default=0.0)
    alert_sent      = Column(Boolean, default=False)
    detected_at     = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_whale_detected","detected_at"),)

class ScoreTrend(Base):
    __tablename__ = "score_trends"
    id               = Column(Integer, primary_key=True, autoincrement=True)
    market_id        = Column(String, nullable=False)
    outcome          = Column(String, default="")
    conviction_score = Column(Float, default=0.0)
    wallet_count     = Column(Integer, default=0)
    total_size_usdc  = Column(Float, default=0.0)
    recorded_at      = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_trend_market","market_id","recorded_at"),)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
