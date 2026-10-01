import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from trader_db.models.base import PRICE, Base, CreatedAtMixin


class TradingCalendar(Base):
    """Торговый календарь root. Правила и праздники добавляет отдельная миграция."""

    __tablename__ = "trading_calendars"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Europe/Moscow")


class Root(CreatedAtMixin, Base):
    """Базовый актив фьючерса (NG, BR, GOLD...). Заводится пользователем."""

    __tablename__ = "roots"
    __table_args__ = (
        CheckConstraint("tick_size > 0", name="tick_size_positive"),
        CheckConstraint("roll_trading_days >= 0", name="roll_days_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    exchange: Mapped[str] = mapped_column(String(32))
    quote_currency: Mapped[str] = mapped_column(String(3))
    tick_size: Mapped[Decimal] = mapped_column(PRICE)
    calendar_id: Mapped[int] = mapped_column(
        ForeignKey("trading_calendars.id", ondelete="RESTRICT")
    )
    # Ролл за N торговых дней до экспирации, в начале торговой недели (ADR-0007).
    roll_trading_days: Mapped[int] = mapped_column(Integer)
    include_weekend_sessions: Mapped[bool] = mapped_column(
        Boolean, server_default=false()
    )

    calendar: Mapped[TradingCalendar] = relationship()
    contracts: Mapped[list["Contract"]] = relationship(back_populates="root")


class Contract(CreatedAtMixin, Base):
    """Серия фьючерса.

    Ключ — ``(root_id, expiration_date)``: SECID неуникален во времени.
    """

    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint(
            "root_id", "expiration_date", name="uq_contracts_root_expiration"
        ),
        CheckConstraint(
            "last_trade_date IS NULL OR last_trade_date <= expiration_date",
            name="last_trade_before_expiration",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("roots.id", ondelete="RESTRICT"))
    expiration_date: Mapped[dt.date] = mapped_column(Date)
    last_trade_date: Mapped[dt.date | None] = mapped_column(Date)
    secid: Mapped[str | None] = mapped_column(String(32))

    root: Mapped[Root] = relationship(back_populates="contracts")
    provider_ids: Mapped[list["ContractProviderId"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan"
    )


class DataProvider(Base):
    __tablename__ = "data_providers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))


class ContractProviderId(Base):
    """Код контракта у провайдера (ISS SECID)."""

    __tablename__ = "contract_provider_ids"
    __table_args__ = (
        Index(
            "ix_contract_provider_ids_lookup", "provider_id", "id_type", "external_id"
        ),
    )

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), primary_key=True
    )
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("data_providers.id", ondelete="RESTRICT"), primary_key=True
    )
    id_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128))

    contract: Mapped[Contract] = relationship(back_populates="provider_ids")


class Timeframe(Base):
    """Таймфрейм. ``1m`` — служебный (архивный raw, в UI не показывается)."""

    __tablename__ = "timeframes"
    __table_args__ = (
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
    )

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    # Номинальная длительность; для 1d/1w реальные границы задаёт календарь.
    duration_seconds: Mapped[int] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, unique=True)
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default=false())


class ContractStepPrice(Base):
    """Дневная стоимость шага цены в ₽ (меняется вместе с курсом)."""

    __tablename__ = "contract_step_prices"
    __table_args__ = (CheckConstraint("step_price > 0", name="step_price_positive"),)

    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    step_price: Mapped[Decimal] = mapped_column(PRICE)
