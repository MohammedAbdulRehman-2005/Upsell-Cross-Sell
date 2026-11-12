"""Customer repository for clean database access."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate


class CustomerRepository:
    """Data access layer for Customer records."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, customer_id: int) -> Customer | None:
        return self.session.get(Customer, customer_id)

    def get_by_external_id(self, external_customer_id: str) -> Customer | None:
        stmt = select(Customer).where(Customer.external_customer_id == external_customer_id)
        return self.session.scalar(stmt)

    def get_by_phone(self, phone_number: str) -> Customer | None:
        stmt = select(Customer).where(Customer.phone_number == phone_number)
        return self.session.scalar(stmt)

    def list_all(self, limit: int = 100, offset: int = 0) -> list[Customer]:
        stmt = select(Customer).order_by(Customer.id).offset(offset).limit(limit)
        return list(self.session.scalars(stmt).all())

    def create(self, payload: CustomerCreate) -> Customer:
        customer = Customer(**payload.model_dump())
        self.session.add(customer)
        self.session.commit()
        self.session.refresh(customer)
        return customer

    def update(self, customer: Customer, payload: CustomerUpdate) -> Customer:
        updates = payload.model_dump(exclude_unset=True)
        for field_name, value in updates.items():
            setattr(customer, field_name, value)
        self.session.add(customer)
        self.session.commit()
        self.session.refresh(customer)
        return customer
