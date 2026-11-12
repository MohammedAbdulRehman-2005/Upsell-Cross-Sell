"""Stage 7: REST API endpoints for Dashboard Overview, Orders & Customers, and Product Catalogue."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.models.customer import Customer
from app.models.enums import MessageDeliveryStatus, OrderStatus
from app.models.message import MessageLog
from app.models.order import Order
from app.models.product import Product, ProductMapping
from app.repositories.message_repository import MessageLogRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.schemas.dashboard import (
    CustomerListViewItem,
    DashboardOverviewMetrics,
    OrderListViewItem,
    ProductCatalogueItem,
    ProductDetailView,
    ProductMappingDetailItem,
)
from app.services.campaign_orchestrator import mask_phone_number
from app.services.whatsapp import get_whatsapp_provider, serialize_campaign_summary

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


def _settings(request: Request) -> Settings:
    s = getattr(request.app.state, "settings", None)
    return s if isinstance(s, Settings) else get_settings()


@router.get("/analytics/overview", response_model=DashboardOverviewMetrics)
def get_dashboard_overview(
    settings: Settings = Depends(_settings),
    session: Session = Depends(get_db),
) -> DashboardOverviewMetrics:
    """Return authoritative summary KPIs across campaigns, orders, customers, and catalogue."""
    provider = get_whatsapp_provider(settings=settings)
    provider_config = provider.validate_configuration()

    # Aggregate campaign counts by delivery status
    status_counts_raw = session.execute(
        select(MessageLog.delivery_status, func.count(MessageLog.id)).group_by(MessageLog.delivery_status)
    ).all()
    counts: dict[str, int] = {str(st): cnt for st, cnt in status_counts_raw}

    total_campaigns = sum(counts.values())
    total_orders = session.scalar(select(func.count(Order.id))) or 0
    delivered_orders = (
        session.scalar(select(func.count(Order.id)).where(Order.order_status == OrderStatus.DELIVERED))
        or 0
    )
    total_customers = session.scalar(select(func.count(Customer.id))) or 0
    opted_in_customers = (
        session.scalar(
            select(func.count(Customer.id)).where(
                Customer.whatsapp_opt_in == True,  # noqa: E712
                Customer.opted_out == False,  # noqa: E712
            )
        )
        or 0
    )
    opted_out_customers = (
        session.scalar(select(func.count(Customer.id)).where(Customer.opted_out == True)) or 0  # noqa: E712
    )
    total_products = session.scalar(select(func.count(Product.id))) or 0

    # Fetch top 5 recent campaigns
    recent_logs = MessageLogRepository(session).list_all_campaigns()[:5]
    recent_summaries = [serialize_campaign_summary(session, c) for c in recent_logs]

    return DashboardOverviewMetrics(
        total_campaigns=total_campaigns,
        ready_for_dispatch=counts.get(MessageDeliveryStatus.READY_FOR_DISPATCH.value, 0),
        sent_simulated=counts.get(MessageDeliveryStatus.SENT_SIMULATED.value, 0),
        provider_accepted=counts.get(MessageDeliveryStatus.PROVIDER_ACCEPTED.value, 0),
        delivered=counts.get(MessageDeliveryStatus.DELIVERED.value, 0),
        read=counts.get(MessageDeliveryStatus.READ.value, 0),
        suppressed=counts.get(MessageDeliveryStatus.SUPPRESSED.value, 0),
        failed_retryable=counts.get(MessageDeliveryStatus.FAILED_RETRYABLE.value, 0),
        failed_permanent=counts.get(MessageDeliveryStatus.FAILED_PERMANENT.value, 0),
        delivery_unknown=counts.get(MessageDeliveryStatus.DELIVERY_UNKNOWN.value, 0),
        total_orders=total_orders,
        delivered_orders=delivered_orders,
        total_customers=total_customers,
        opted_in_customers=opted_in_customers,
        opted_out_customers=opted_out_customers,
        total_products=total_products,
        active_provider=provider.provider_type.value,
        is_provider_configured=provider_config.is_valid,
        recent_campaigns=recent_summaries,
    )


@router.get("/customers", response_model=list[CustomerListViewItem])
def list_customers(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[CustomerListViewItem]:
    """List customer records with privacy-safe masked phone numbers and WhatsApp consent status."""
    stmt = (
        select(
            Customer,
            func.count(func.distinct(Order.id)).label("order_count"),
            func.count(func.distinct(MessageLog.id)).label("campaign_count"),
        )
        .outerjoin(Order, Order.customer_id == Customer.id)
        .outerjoin(MessageLog, MessageLog.customer_id == Customer.id)
        .group_by(Customer.id)
        .order_by(Customer.id.asc())
        .offset(offset)
        .limit(limit)
    )
    rows = session.execute(stmt).all()
    results: list[CustomerListViewItem] = []
    for customer, o_count, c_count in rows:
        results.append(
            CustomerListViewItem(
                id=customer.id,
                external_customer_id=customer.external_customer_id,
                full_name=customer.full_name,
                masked_phone=mask_phone_number(customer.phone_number),
                whatsapp_opt_in=customer.whatsapp_opt_in,
                opted_out=customer.opted_out,
                consent_source=customer.consent_source,
                consent_recorded_at=customer.consent_recorded_at,
                order_count=o_count or 0,
                campaign_count=c_count or 0,
                created_at=customer.created_at,
            )
        )
    return results


@router.get("/orders", response_model=list[OrderListViewItem])
def list_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[OrderListViewItem]:
    """List orders with customer reference, line items, and associated campaign delivery state."""
    order_repo = OrderRepository(session)
    orders = order_repo.list_all(limit=limit, offset=offset)

    # Correlate associated campaign delivery status
    order_ids = [o.id for o in orders]
    campaigns_by_order: dict[int, MessageLog] = {}
    if order_ids:
        c_stmt = (
            select(MessageLog)
            .where(MessageLog.order_id.in_(order_ids))
            .order_by(MessageLog.id.desc())
        )
        for c in session.scalars(c_stmt).all():
            if c.order_id not in campaigns_by_order:
                campaigns_by_order[c.order_id] = c

    results: list[OrderListViewItem] = []
    for order in orders:
        camp = campaigns_by_order.get(order.id)
        line_items_data = [
            {
                "line_index": item.line_index,
                "product_code": item.product_code,
                "product_name": item.product_name,
                "quantity": item.quantity,
            }
            for item in sorted(order.line_items, key=lambda x: x.line_index)
        ]
        results.append(
            OrderListViewItem(
                id=order.id,
                external_order_id=order.external_order_id,
                customer_id=order.customer_id,
                customer_external_id=order.customer.external_customer_id if order.customer else f"cust-{order.customer_id}",
                customer_name=order.customer.full_name if order.customer else None,
                order_status=str(order.order_status),
                delivered_at=order.delivered_at,
                line_item_count=len(line_items_data),
                items=line_items_data,
                recommended_skus=list(camp.recommended_skus or []) if camp else [],
                campaign_id=camp.id if camp else None,
                campaign_status=str(camp.delivery_status) if camp else None,
                created_at=order.created_at,
            )
        )
    return results


@router.get("/products", response_model=list[ProductCatalogueItem])
def list_products(
    q: str | None = Query(default=None, description="Search term for product code or name"),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> list[ProductCatalogueItem]:
    """List product catalogue with Rx eligibility, availability, and metadata provenance."""
    stmt = (
        select(
            Product,
            func.count(ProductMapping.id).label("mapping_count"),
        )
        .outerjoin(ProductMapping, ProductMapping.source_product_code == Product.product_code)
        .group_by(Product.id)
    )

    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Product.product_code.ilike(term), Product.product_name.ilike(term)))

    stmt = stmt.order_by(Product.product_code.asc()).offset(offset).limit(limit)
    rows = session.execute(stmt).all()

    results: list[ProductCatalogueItem] = []
    for prod, m_count in rows:
        results.append(
            ProductCatalogueItem(
                id=prod.id,
                product_code=prod.product_code,
                product_name=prod.product_name,
                unit_price=float(prod.unit_price) if prod.unit_price is not None else None,
                currency=prod.currency,
                is_rx=prod.is_rx,
                is_active=prod.is_active,
                available_quantity=prod.available_quantity,
                metadata_source=str(prod.metadata_source),
                is_synthetic_metadata=prod.is_synthetic_metadata,
                mapped_recommendations_count=m_count or 0,
            )
        )
    return results


@router.get("/products/{product_code}", response_model=ProductDetailView)
def get_product_detail(
    product_code: str,
    session: Session = Depends(get_db),
) -> ProductDetailView:
    """Get single product details with its 6 ranked mapped recommendation targets."""
    prod = session.scalar(
        select(Product)
        .where(Product.product_code == product_code)
        .options(selectinload(Product.source_mappings))
    )
    if prod is None:
        raise HTTPException(status_code=404, detail=f"Product '{product_code}' not found.")

    # Load targets
    mappings = session.scalars(
        select(ProductMapping)
        .where(ProductMapping.source_product_code == product_code)
        .order_by(ProductMapping.recommendation_rank.asc())
    ).all()

    target_codes = [m.recommended_product_code for m in mappings]
    targets_map: dict[str, Product] = {}
    if target_codes:
        targets = session.scalars(select(Product).where(Product.product_code.in_(target_codes))).all()
        targets_map = {t.product_code: t for t in targets}

    mapping_details: list[ProductMappingDetailItem] = []
    for m in mappings:
        tgt = targets_map.get(m.recommended_product_code)
        mapping_details.append(
            ProductMappingDetailItem(
                rank=m.recommendation_rank,
                recommended_product_code=m.recommended_product_code,
                recommended_product_name=tgt.product_name if tgt else m.recommended_product_code,
                unit_price=float(tgt.unit_price) if tgt and tgt.unit_price is not None else None,
                is_rx=tgt.is_rx if tgt else None,
                is_active=tgt.is_active if tgt else None,
                available_quantity=tgt.available_quantity if tgt else None,
                metadata_source=str(tgt.metadata_source) if tgt else None,
                is_synthetic_metadata=tgt.is_synthetic_metadata if tgt else False,
            )
        )

    return ProductDetailView(
        id=prod.id,
        product_code=prod.product_code,
        product_name=prod.product_name,
        unit_price=float(prod.unit_price) if prod.unit_price is not None else None,
        currency=prod.currency,
        is_rx=prod.is_rx,
        is_active=prod.is_active,
        available_quantity=prod.available_quantity,
        metadata_source=str(prod.metadata_source),
        is_synthetic_metadata=prod.is_synthetic_metadata,
        mapped_recommendations_count=len(mappings),
        mappings=mapping_details,
    )
