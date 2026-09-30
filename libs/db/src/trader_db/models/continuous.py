"""События ролла continuous-серии (ADR-0007, ADR-0019)."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from trader_db.models.base import PRICE, Base, CreatedAtMixin

RATIO = Numeric(24, 12)


class RollEvent(CreatedAtMixin, Base):
    """Переключение continuous-серии root с одного контракта на следующий.

    ``rolled_at`` — первый момент торговой недели ролла; ``ratio`` — отношение
    цен нового контракта к старому в последний общий торговый день до ролла.
    ``available_at`` — когда событие стало известно (ratio считается по данным до
    ``rolled_at``).
    """

    __tablename__ = "roll_events"
    __table_args__ = (
        UniqueConstraint(
            "root_id", "from_contract_id", "to_contract_id", name="uq_roll_events_pair"
        ),
        UniqueConstraint("root_id", "rolled_at", name="uq_roll_events_moment"),
        CheckConstraint("ratio > 0", name="ratio_positive"),
        CheckConstraint("from_contract_id <> to_contract_id", name="contracts_differ"),
        CheckConstraint("available_at >= rolled_at", name="available_after_roll"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    root_id: Mapped[int] = mapped_column(ForeignKey("roots.id", ondelete="RESTRICT"))
    from_contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    to_contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    rolled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ratio: Mapped[Decimal] = mapped_column(RATIO)
    basis_trading_day: Mapped[dt.date] = mapped_column(Date)
    from_close: Mapped[Decimal] = mapped_column(PRICE)
    to_close: Mapped[Decimal] = mapped_column(PRICE)
    available_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
