"""Repository for :class:`~models.user.User`."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.user import User
from repositories.base_repository import CompanyScopedRepository


class UserRepository(CompanyScopedRepository[User]):
    """Data access for :class:`~models.user.User`, scoped to one company."""

    def __init__(self, session: Session, *, company_id: int) -> None:
        """Create a repository bound to ``session`` and ``company_id``."""
        super().__init__(session, model=User, company_id=company_id)

    def get_by_username(self, username: str) -> User | None:
        """Fetch a user by their unique-per-company login username.

        Args:
            username: The user's :attr:`~models.user.User.username`.

        Returns:
            The matching user, or ``None``.
        """
        statement = select(User).where(
            User.company_id == self.company_id,
            User.username == username,
            User.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def get_by_email(self, email: str) -> User | None:
        """Fetch a user by their unique-per-company email address.

        Args:
            email: The user's :attr:`~models.user.User.email`.

        Returns:
            The matching user, or ``None``.
        """
        statement = select(User).where(
            User.company_id == self.company_id,
            User.email == email,
            User.is_deleted.is_(False),
        )
        return self.session.execute(statement).scalar_one_or_none()

    def list_active(self) -> list[User]:
        """List every currently-active user in this company.

        Returns:
            Users with :attr:`~models.user.User.is_active` set,
            ordered by username.
        """
        statement = (
            select(User)
            .where(
                User.company_id == self.company_id,
                User.is_active.is_(True),
                User.is_deleted.is_(False),
            )
            .order_by(User.username)
        )
        return list(self.session.execute(statement).scalars().all())
