"""Repositories for the payroll models in :mod:`models.payroll`.

Grouped in one file, mirroring how the payroll models are grouped in
``models/payroll.py``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.enums import PayrollAutoRuleType
from models.payroll import (
    PayrollAdjustment,
    PayrollAutomaticRule,
    PayrollRun,
    PayrollRunLine,
    PayrollRunSnapshot,
)
from repositories.base_repository import CompanyScopedRepository


class PayrollAutomaticRuleRepository(CompanyScopedRepository[PayrollAutomaticRule]):
    """Data access for :class:`~models.payroll.PayrollAutomaticRule`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=PayrollAutomaticRule, company_id=company_id)

    def get_by_rule_type(
        self, rule_type: PayrollAutoRuleType
    ) -> PayrollAutomaticRule | None:
        """Fetch this company's configuration for one rule type.

        Args:
            rule_type: The rule type to look up.

        Returns:
            The matching rule row, or ``None`` if this company has
            never configured it (treat as disabled with no
            calculation method chosen yet).
        """
        statement = select(PayrollAutomaticRule).where(
            PayrollAutomaticRule.company_id == self.company_id,
            PayrollAutomaticRule.rule_type == rule_type,
            PayrollAutomaticRule.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_all_rules(self) -> list[PayrollAutomaticRule]:
        """List every rule type this company has configured, ordered by type."""
        statement = (
            select(PayrollAutomaticRule)
            .where(
                PayrollAutomaticRule.company_id == self.company_id,
                PayrollAutomaticRule.is_deleted.is_(False),
            )
            .order_by(PayrollAutomaticRule.rule_type)
        )
        return list(self.session.execute(statement).scalars().all())


class PayrollAdjustmentRepository(CompanyScopedRepository[PayrollAdjustment]):
    """Data access for :class:`~models.payroll.PayrollAdjustment`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=PayrollAdjustment, company_id=company_id)

    def list_for_employee_period(
        self, employee_id: int, *, year: int, month: int, include_cancelled: bool = False
    ) -> list[PayrollAdjustment]:
        """List one employee's adjustments for one pay period.

        Args:
            employee_id: The employee's id.
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).
            include_cancelled: Whether to include cancelled (soft-
                deleted) adjustments.

        Returns:
            Matching adjustments, most recently created first.
        """
        statement = (
            select(PayrollAdjustment)
            .where(
                PayrollAdjustment.company_id == self.company_id,
                PayrollAdjustment.employee_id == employee_id,
                PayrollAdjustment.year == year,
                PayrollAdjustment.month == month,
            )
            .order_by(PayrollAdjustment.created_at.desc())
        )
        if not include_cancelled:
            statement = statement.where(PayrollAdjustment.is_deleted.is_(False))
        return list(self.session.execute(statement).scalars().all())

    def list_for_company_period(
        self, *, year: int, month: int, include_cancelled: bool = False
    ) -> list[PayrollAdjustment]:
        """List every employee's adjustments for one pay period.

        Used by :meth:`~services.payroll_service.PayrollService.compute_payroll_run`
        to aggregate every employee's deductions/bonuses for a month in
        one query.

        Args:
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).
            include_cancelled: Whether to include cancelled (soft-
                deleted) adjustments.

        Returns:
            Every matching adjustment across all employees.
        """
        statement = select(PayrollAdjustment).where(
            PayrollAdjustment.company_id == self.company_id,
            PayrollAdjustment.year == year,
            PayrollAdjustment.month == month,
        )
        if not include_cancelled:
            statement = statement.where(PayrollAdjustment.is_deleted.is_(False))
        return list(self.session.execute(statement).scalars().all())


class PayrollRunRepository(CompanyScopedRepository[PayrollRun]):
    """Data access for :class:`~models.payroll.PayrollRun`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=PayrollRun, company_id=company_id)

    def get_for_period(self, *, year: int, month: int) -> PayrollRun | None:
        """Fetch this company's payroll run for one calendar month, if any.

        Args:
            year: The pay period's calendar year.
            month: The pay period's calendar month (1-12).

        Returns:
            The matching run, or ``None`` if it has not been created
            yet (no computation has ever been run for this period).
        """
        statement = select(PayrollRun).where(
            PayrollRun.company_id == self.company_id,
            PayrollRun.year == year,
            PayrollRun.month == month,
            PayrollRun.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_recent(self, *, limit: int = 24) -> list[PayrollRun]:
        """List this company's most recent payroll runs, newest first.

        Args:
            limit: Maximum number of runs to return.

        Returns:
            Matching runs ordered by year/month descending.
        """
        statement = (
            select(PayrollRun)
            .where(PayrollRun.company_id == self.company_id, PayrollRun.is_deleted.is_(False))
            .order_by(PayrollRun.year.desc(), PayrollRun.month.desc())
            .limit(limit)
        )
        return list(self.session.execute(statement).scalars().all())


class PayrollRunLineRepository(CompanyScopedRepository[PayrollRunLine]):
    """Data access for :class:`~models.payroll.PayrollRunLine`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=PayrollRunLine, company_id=company_id)

    def list_for_run(self, payroll_run_id: int) -> list[PayrollRunLine]:
        """List every employee's computed line for one run.

        Args:
            payroll_run_id: The owning run's id.

        Returns:
            Matching lines, ordered by employee id.
        """
        statement = (
            select(PayrollRunLine)
            .where(
                PayrollRunLine.company_id == self.company_id,
                PayrollRunLine.payroll_run_id == payroll_run_id,
                PayrollRunLine.is_deleted.is_(False),
            )
            .order_by(PayrollRunLine.employee_id)
        )
        return list(self.session.execute(statement).scalars().all())

    def get_for_run_and_employee(
        self, payroll_run_id: int, employee_id: int
    ) -> PayrollRunLine | None:
        """Fetch one employee's computed line within one run, if it exists.

        Args:
            payroll_run_id: The owning run's id.
            employee_id: The employee's id.

        Returns:
            The matching line, or ``None``.
        """
        statement = select(PayrollRunLine).where(
            PayrollRunLine.company_id == self.company_id,
            PayrollRunLine.payroll_run_id == payroll_run_id,
            PayrollRunLine.employee_id == employee_id,
            PayrollRunLine.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()


class PayrollRunSnapshotRepository(CompanyScopedRepository[PayrollRunSnapshot]):
    """Data access for :class:`~models.payroll.PayrollRunSnapshot`."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=PayrollRunSnapshot, company_id=company_id)

    def list_for_run(self, payroll_run_id: int) -> list[PayrollRunSnapshot]:
        """List every historical snapshot of one run, oldest first.

        Args:
            payroll_run_id: The owning run's id.

        Returns:
            Matching snapshots ordered by ``run_version`` ascending.
        """
        statement = (
            select(PayrollRunSnapshot)
            .where(
                PayrollRunSnapshot.company_id == self.company_id,
                PayrollRunSnapshot.payroll_run_id == payroll_run_id,
            )
            .order_by(PayrollRunSnapshot.run_version)
        )
        return list(self.session.execute(statement).scalars().all())
