"""Stage 8: Storefront checkout and demo delivery progression service."""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.customer import Customer
from app.models.demo_delivery import DemoDeliveryEvent
from app.models.enums import DeliveryEventOutcome, MessageDeliveryStatus, OrderStatus, WebhookEventType
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.repositories.customer_repository import CustomerRepository
from app.repositories.message_repository import MessageLogRepository
from app.repositories.order_repository import OrderRepository
from app.repositories.product_repository import ProductRepository
from app.schemas.campaign import OrderDeliveredWebhookRequest
from app.schemas.storefront import (
    StorefrontCartItem,
    StorefrontCheckoutRequest,
    StorefrontOrderResponse,
    StorefrontTrackingResponse,
    StorefrontTrackingStep,
)
from app.services.campaign_orchestrator import CampaignOrchestrationService
from app.services.whatsapp.dispatcher import DispatchCoordinator, build_recommendation_cards

logger = logging.getLogger(__name__)

MILESTONE_ORDER = [
    (OrderStatus.PLACED.value, "Order Placed"),
    (OrderStatus.CONFIRMED.value, "Order Confirmed"),
    (OrderStatus.PACKED.value, "Packed & Quality Checked"),
    (OrderStatus.OUT_FOR_DELIVERY.value, "Out for Delivery"),
    (OrderStatus.DELIVERED.value, "Delivered to Customer"),
]


class DemoDeliveryService:
    """Manages storefront customer orders and automated demo delivery progression."""

    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.order_repo = OrderRepository(session)
        self.customer_repo = CustomerRepository(session)
        self.product_repo = ProductRepository(session)
        self.msg_repo = MessageLogRepository(session)

    def create_order(self, request: StorefrontCheckoutRequest) -> StorefrontOrderResponse:
        """Validate shopping cart, create or update Customer, persist Order, and schedule DemoDelivery."""
        # 1. Validate line items
        if not request.items:
            raise ValueError("Cart must contain at least one item.")

        sku_codes = [item.product_code.strip() for item in request.items]
        catalogue_products = {p.product_code: p for p in self.product_repo.get_by_codes(sku_codes)}

        for sku in sku_codes:
            if sku not in catalogue_products:
                raise ValueError(f"Product '{sku}' does not exist in the catalogue.")

        # 2. Resolve or create Customer
        now_utc = datetime.now(timezone.utc)
        customer = self.customer_repo.get_by_phone(request.customer_phone)

        if customer is None:
            cust_suffix = secrets.token_hex(4).upper()
            external_cust_id = f"CUST-STORE-{cust_suffix}"
            customer = Customer(
                external_customer_id=external_cust_id,
                full_name=request.customer_name,
                phone_number=request.customer_phone,
                whatsapp_opt_in=request.whatsapp_opt_in,
                opted_out=False,
                consent_source="STOREFRONT_CHECKOUT" if request.whatsapp_opt_in else None,
                consent_recorded_at=now_utc if request.whatsapp_opt_in else None,
            )
            self.session.add(customer)
            self.session.flush()
        else:
            customer.full_name = request.customer_name
            # If customer explicitly checked WhatsApp opt-in at checkout, record consent
            if request.whatsapp_opt_in:
                customer.whatsapp_opt_in = True
                customer.opted_out = False
                customer.consent_source = "STOREFRONT_CHECKOUT"
                customer.consent_recorded_at = now_utc
            self.session.flush()

        # 3. Create Order
        ord_suffix = f"{int(time.time())}-{secrets.token_hex(2).upper()}"
        external_order_id = f"ORD-STORE-{ord_suffix}"

        order = Order(
            external_order_id=external_order_id,
            customer_id=customer.id,
            order_status=OrderStatus.PLACED,
            order_source="STOREFRONT",
            is_demo_order=True,
            delivery_address=request.delivery_address,
        )
        self.session.add(order)
        self.session.flush()

        # 4. Create OrderLineItems and calculate total
        total_amount = 0.0
        item_count = 0
        for idx, item in enumerate(request.items):
            prod = catalogue_products[item.product_code.strip()]
            unit_price = float(prod.unit_price) if prod.unit_price is not None else 100.0
            line_item = OrderLineItem(
                order_id=order.id,
                line_index=idx,
                product_code=prod.product_code,
                product_name=prod.product_name,
                quantity=item.quantity,
            )
            self.session.add(line_item)
            total_amount += unit_price * item.quantity
            item_count += item.quantity

        # 5. Schedule DemoDeliveryEvent
        delay = float(self.settings.demo_delivery_delay_seconds)
        target_at = now_utc + timedelta(seconds=delay)
        step_timestamps = {OrderStatus.PLACED.value: now_utc.isoformat()}

        demo_event = DemoDeliveryEvent(
            order_id=order.id,
            status="SCHEDULED",
            scheduled_at=now_utc,
            target_delivery_at=target_at,
            step_timestamps=step_timestamps,
        )
        self.session.add(demo_event)
        self.session.commit()

        tracking_url = f"/store/order/{external_order_id}"

        return StorefrontOrderResponse(
            order_id=order.id,
            external_order_id=order.external_order_id,
            customer_id=customer.id,
            customer_name=customer.full_name or "Valued Customer",
            customer_phone=customer.phone_number or "",
            order_status=order.order_status,
            total_amount=round(total_amount, 2),
            item_count=item_count,
            whatsapp_opt_in=bool(customer.whatsapp_opt_in and not customer.opted_out),
            is_demo_order=order.is_demo_order,
            tracking_url=tracking_url,
            estimated_delivery_seconds=delay,
            created_at=order.created_at,
        )

    def advance_demo_delivery_if_due(self, order_id: int) -> bool:
        """Inspect and advance order delivery milestone if time threshold reached."""
        demo_event = (
            self.session.query(DemoDeliveryEvent)
            .filter(DemoDeliveryEvent.order_id == order_id)
            .first()
        )
        if not demo_event or demo_event.status == "DELIVERED":
            return False

        order = self.order_repo.get_by_id(order_id)
        if not order:
            return False

        now_utc = datetime.now(timezone.utc)
        total_span = (demo_event.target_delivery_at - demo_event.scheduled_at).total_seconds()
        if total_span <= 0:
            total_span = 12.0
        elapsed = (now_utc - demo_event.scheduled_at).total_seconds()
        fraction = max(0.0, elapsed / total_span)

        step_ts = dict(demo_event.step_timestamps or {})
        changed = False

        if fraction >= 1.0 or now_utc >= demo_event.target_delivery_at:
            # Reached DELIVERED milestone
            order.order_status = OrderStatus.DELIVERED
            order.delivered_at = now_utc
            step_ts[OrderStatus.CONFIRMED.value] = step_ts.get(OrderStatus.CONFIRMED.value, now_utc.isoformat())
            step_ts[OrderStatus.PACKED.value] = step_ts.get(OrderStatus.PACKED.value, now_utc.isoformat())
            step_ts[OrderStatus.OUT_FOR_DELIVERY.value] = step_ts.get(OrderStatus.OUT_FOR_DELIVERY.value, now_utc.isoformat())
            step_ts[OrderStatus.DELIVERED.value] = now_utc.isoformat()

            demo_event.status = "DELIVERED"
            demo_event.completed_at = now_utc
            demo_event.step_timestamps = step_ts
            self.session.flush()

            # Trigger Stage 5 Campaign Orchestrator
            self._trigger_order_delivered_campaign(order)
            changed = True
        elif fraction >= 0.75:
            if order.order_status != OrderStatus.OUT_FOR_DELIVERY:
                order.order_status = OrderStatus.OUT_FOR_DELIVERY
                step_ts[OrderStatus.OUT_FOR_DELIVERY.value] = now_utc.isoformat()
                demo_event.status = "PROCESSING"
                demo_event.step_timestamps = step_ts
                changed = True
        elif fraction >= 0.50:
            if order.order_status in (OrderStatus.PLACED, OrderStatus.CONFIRMED, OrderStatus.PENDING):
                order.order_status = OrderStatus.PACKED
                step_ts[OrderStatus.PACKED.value] = now_utc.isoformat()
                demo_event.status = "PROCESSING"
                demo_event.step_timestamps = step_ts
                changed = True
        elif fraction >= 0.25:
            if order.order_status in (OrderStatus.PLACED, OrderStatus.PENDING):
                order.order_status = OrderStatus.CONFIRMED
                step_ts[OrderStatus.CONFIRMED.value] = now_utc.isoformat()
                demo_event.status = "PROCESSING"
                demo_event.step_timestamps = step_ts
                changed = True

        if changed:
            self.session.commit()
        return changed

    def _trigger_order_delivered_campaign(self, order: Order) -> None:
        """Run Stage 5 Campaign Orchestration and Stage 6 WhatsApp Dispatch on delivery."""
        try:
            orchestrator = CampaignOrchestrationService(self.session, settings=self.settings)
            customer = self.customer_repo.get_by_id(order.customer_id)
            event_id = f"demo_deliv_{order.id}_{order.external_order_id}"

            webhook_req = OrderDeliveredWebhookRequest(
                event_id=event_id,
                event_type="order.delivered",
                order_id=order.external_order_id,
                customer_id=customer.external_customer_id if customer else f"cust-{order.customer_id}",
                occurred_at=datetime.now(timezone.utc),
                source="order_service",
            )

            result = orchestrator.process_order_delivered_event(
                webhook_req,
                raw_payload=webhook_req.model_dump(mode="json"),
            )
            logger.info(
                "Demo delivery campaign orchestration result for order %s: %s (campaign_id=%s)",
                order.external_order_id,
                result.outcome,
                result.campaign_id,
            )

            # If campaign is ready for dispatch, run DispatchCoordinator
            if result.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH and result.campaign_id:
                dispatcher = DispatchCoordinator(self.session, settings=self.settings)
                dispatch_res = dispatcher.dispatch_campaign(result.campaign_id)
                logger.info(
                    "Demo delivery WhatsApp dispatch for campaign %s: %s (provider=%s)",
                    result.campaign_id,
                    dispatch_res.delivery_status,
                    dispatch_res.provider,
                )
        except Exception as exc:
            logger.exception("Error executing delivery campaign orchestration: %s", exc)

    def get_tracking(self, order_identifier: str | int) -> StorefrontTrackingResponse:
        """Retrieve tracking stepper and campaign outcomes for an order."""
        if isinstance(order_identifier, int) or (isinstance(order_identifier, str) and order_identifier.isdigit()):
            order = self.order_repo.get_by_id(int(order_identifier))
        else:
            order = self.order_repo.get_by_external_id(str(order_identifier))

        if not order:
            raise ValueError(f"Order '{order_identifier}' not found.")

        # Advance delivery progression if time is due
        self.advance_demo_delivery_if_due(order.id)
        # Reload order
        self.session.refresh(order)

        customer = self.customer_repo.get_by_id(order.customer_id)
        demo_event = (
            self.session.query(DemoDeliveryEvent)
            .filter(DemoDeliveryEvent.order_id == order.id)
            .first()
        )

        now_utc = datetime.now(timezone.utc)
        step_timestamps = dict(demo_event.step_timestamps or {}) if demo_event else {}

        # Build steps
        current_status = str(order.order_status)
        steps: list[StorefrontTrackingStep] = []
        milestone_keys = [k for k, _ in MILESTONE_ORDER]
        current_idx = milestone_keys.index(current_status) if current_status in milestone_keys else 0

        for idx, (status_key, label) in enumerate(MILESTONE_ORDER):
            ts = step_timestamps.get(status_key)
            is_completed = idx <= current_idx
            is_current = idx == current_idx
            steps.append(
                StorefrontTrackingStep(
                    status=status_key,
                    label=label,
                    timestamp=ts,
                    is_completed=is_completed,
                    is_current=is_current,
                )
            )

        seconds_remaining = None
        delivery_completed = current_status == OrderStatus.DELIVERED.value
        if demo_event and not delivery_completed:
            rem = (demo_event.target_delivery_at - now_utc).total_seconds()
            seconds_remaining = max(0.0, round(rem, 1))

        # Check for campaign & recommendations
        campaign = (
            self.session.query(MessageLog)
            .filter(MessageLog.order_id == order.id)
            .order_by(MessageLog.id.desc())
            .first()
        )

        campaign_status = str(campaign.delivery_status) if campaign else None
        campaign_id = campaign.id if campaign else None
        recommended_skus = list(campaign.recommended_skus or []) if campaign else []
        recommendations: list[dict[str, Any]] = []

        if campaign and recommended_skus:
            cards = build_recommendation_cards(self.session, campaign)
            recommendations = [
                {
                    "slot_number": c.slot_number,
                    "product_code": c.product_code,
                    "product_name": c.product_name,
                    "category": c.category,
                    "unit_price": c.unit_price,
                    "source_product_code": c.source_product_code,
                    "source_rank": c.source_rank,
                }
                for c in cards
            ]

        demo_banner = (
            "DEMO MODE: Automated delivery simulation is active. Recommendations will be dispatched via WhatsApp once delivered."
            if not delivery_completed
            else "DELIVERED: Demo order completed. Cross-sell recommendations evaluated and dispatched."
        )

        return StorefrontTrackingResponse(
            order_id=order.id,
            external_order_id=order.external_order_id,
            order_status=current_status,
            customer_name=customer.full_name if customer else "Valued Customer",
            delivery_address=order.delivery_address,
            is_demo_order=order.is_demo_order,
            steps=steps,
            seconds_remaining=seconds_remaining,
            delivery_completed=delivery_completed,
            demo_banner=demo_banner,
            whatsapp_opt_in=bool(customer and customer.whatsapp_opt_in and not customer.opted_out),
            whatsapp_campaign_status=campaign_status,
            whatsapp_campaign_id=campaign_id,
            whatsapp_message_status=str(campaign.delivery_status) if campaign else None,
            recommended_skus=recommended_skus,
            recommendations=recommendations,
        )
