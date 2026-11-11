"""Interactive customer simulator conversation and demo seeding service."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.enums import (
    MessageDeliveryStatus,
    SimulatorCustomerAction,
    SimulatorMessageDirection,
)
from app.repositories.message_repository import MessageLogRepository
from app.repositories.simulator_repository import SimulatorMessageRepository
from app.models.customer import Customer
from app.models.enums import OrderStatus
from app.models.order import Order, OrderLineItem
from app.schemas.campaign import OrderDeliveredWebhookRequest
from app.schemas.whatsapp import (
    SimulatorDemoSeedResponseSchema,
    SimulatorInteractionRequest,
    SimulatorInteractionResponse,
    SimulatorMessageSchema,
)
from app.services.campaign_orchestrator import CampaignOrchestrationService
from app.services.catalogue_loader import CatalogueLoaderService
from app.services.whatsapp.dispatcher import (
    DispatchCoordinator,
    build_recommendation_cards,
)
from app.services.whatsapp.providers import SimulatorWhatsAppProvider


class SimulatorInteractionService:
    """Manages interactive customer responses in the local WhatsApp simulator."""

    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    def record_customer_interaction(
        self,
        campaign_id: int,
        request: SimulatorInteractionRequest,
    ) -> SimulatorInteractionResponse:
        msg_repo = MessageLogRepository(self.session)
        sim_repo = SimulatorMessageRepository(self.session)

        campaign = msg_repo.get_by_id(campaign_id)
        if campaign is None:
            raise ValueError(f"Campaign {campaign_id} not found.")

        if str(campaign.delivery_status) == MessageDeliveryStatus.SUPPRESSED.value:
            raise ValueError(
                f"Campaign {campaign_id} is SUPPRESSED ({campaign.suppression_reason}) "
                "and cannot receive customer interactions."
            )

        cards = build_recommendation_cards(self.session, campaign)
        cards_by_sku = {c.product_code: c for c in cards}

        selected_sku = (request.product_code or "").strip() or None
        if selected_sku and selected_sku not in cards_by_sku:
            raise ValueError(
                f"Product '{selected_sku}' is not among campaign {campaign_id}'s recommended SKUs."
            )
        if not selected_sku and cards and request.action_type in (
            SimulatorCustomerAction.VIEW_RECOMMENDATION,
            SimulatorCustomerAction.INTERESTED,
        ):
            selected_sku = cards[0].product_code

        target_card = cards_by_sku.get(selected_sku) if selected_sku else None
        recipient_phone = campaign.recipient_phone or "+910000000000"
        action_val = request.action_type.value

        # Build inbound customer message text and outbound simulated assistant reply
        if request.action_type == SimulatorCustomerAction.VIEW_RECOMMENDATION and target_card:
            inbound_body = (
                f"[SIMULATED CUSTOMER ACTION: {action_val}] "
                f"Tell me more about {target_card.product_name} ({target_card.product_code})."
            )
            price_text = f"₹{target_card.unit_price:.2f}" if target_card.unit_price is not None else "Price on request"
            source_info = (
                f"Recommended because you ordered {target_card.source_product_code} (Rank #{target_card.source_rank})."
                if target_card.source_product_code
                else "Recommended based on your delivered order items."
            )
            outbound_body = (
                "[SIMULATED WHATSAPP REPLY — DEMO MODE]\n"
                f"Product Details: {target_card.product_name} ({target_card.product_code})\n"
                f"• Category: {target_card.category or 'OTC Healthcare'}\n"
                f"• Unit Price: {price_text}\n"
                f"• Why recommended: {source_info}\n\n"
                "Would you like to mark this item as Interested for your next order?"
            )
        elif request.action_type == SimulatorCustomerAction.INTERESTED:
            if target_card:
                inbound_body = (
                    f"[SIMULATED CUSTOMER ACTION: {action_val}] "
                    f"I'm interested in ordering {target_card.product_name} ({target_card.product_code})."
                )
                price_text = f" (₹{target_card.unit_price:.2f})" if target_card.unit_price is not None else ""
                outbound_body = (
                    "[SIMULATED WHATSAPP REPLY — DEMO MODE]\n"
                    f"Great choice! We have recorded your interest in {target_card.product_name} "
                    f"({target_card.product_code}){price_text}.\n"
                    "A Plural care specialist or quick-checkout link has been queued for your account."
                )
            else:
                inbound_body = f"[SIMULATED CUSTOMER ACTION: {action_val}] I'm interested in these recommendations."
                outbound_body = (
                    "[SIMULATED WHATSAPP REPLY — DEMO MODE]\n"
                    "Thank you! We have recorded your interest in the recommended products."
                )
        elif request.action_type == SimulatorCustomerAction.NOT_INTERESTED:
            if target_card:
                inbound_body = (
                    f"[SIMULATED CUSTOMER ACTION: {action_val}] "
                    f"Not interested in {target_card.product_name} ({target_card.product_code})."
                )
                outbound_body = (
                    "[SIMULATED WHATSAPP REPLY — DEMO MODE]\n"
                    f"Understood — we've noted that you're not interested in {target_card.product_name} "
                    f"({target_card.product_code}). You can still browse the other recommendations above anytime."
                )
            else:
                inbound_body = f"[SIMULATED CUSTOMER ACTION: {action_val}] No thanks, not interested right now."
                outbound_body = (
                    "[SIMULATED WHATSAPP REPLY — DEMO MODE]\n"
                    "No problem at all! Thank you for shopping with Plural. Have a healthy day!"
                )
        else:
            # VIEW_ALL_RECOMMENDATIONS
            inbound_body = (
                f"[SIMULATED CUSTOMER ACTION: {action_val}] "
                "Show me all recommended products for my order."
            )
            lines = [
                "[SIMULATED WHATSAPP REPLY — DEMO MODE]",
                f"Here are all {len(cards)} personalized recommendations for your order:",
            ]
            for card in cards:
                p_str = f" — ₹{card.unit_price:.2f}" if card.unit_price is not None else ""
                lines.append(f"{card.slot_number}. {card.product_name} ({card.product_code}){p_str}")
            outbound_body = "\n".join(lines)

        if request.customer_note:
            inbound_body = f"{inbound_body}\nNote: {request.customer_note.strip()}"

        inbound_record = sim_repo.create(
            simulated_message_id=f"sim_in.{campaign.id}.{uuid.uuid4().hex[:12]}",
            message_log_id=campaign.id,
            order_id=campaign.order_id,
            customer_id=campaign.customer_id,
            recipient_phone=recipient_phone,
            direction=SimulatorMessageDirection.INBOUND,
            message_type="CUSTOMER_INTERACTION",
            body_text=inbound_body,
            action_type=action_val,
            selected_product_code=selected_sku,
            metadata_json={
                "action_type": action_val,
                "product_code": selected_sku,
                "customer_note": request.customer_note,
            },
            is_simulated=True,
            commit=False,
        )
        outbound_record = sim_repo.create(
            simulated_message_id=f"sim_out.{campaign.id}.{uuid.uuid4().hex[:12]}",
            message_log_id=campaign.id,
            order_id=campaign.order_id,
            customer_id=campaign.customer_id,
            recipient_phone=recipient_phone,
            direction=SimulatorMessageDirection.OUTBOUND,
            message_type="SIMULATED_ASSISTANT_REPLY",
            body_text=outbound_body,
            recommendations_payload=[c.to_dict() for c in cards]
            if request.action_type == SimulatorCustomerAction.VIEW_ALL_RECOMMENDATIONS
            else ([target_card.to_dict()] if target_card else None),
            action_type=action_val,
            selected_product_code=selected_sku,
            is_simulated=True,
            commit=True,
        )

        all_msgs = [
            SimulatorMessageSchema.model_validate(m)
            for m in sim_repo.list_by_message_log_id(campaign.id)
        ]

        return SimulatorInteractionResponse(
            campaign_id=campaign.id,
            action_type=action_val,
            selected_product_code=selected_sku,
            inbound_message=SimulatorMessageSchema.model_validate(inbound_record),
            outbound_reply=SimulatorMessageSchema.model_validate(outbound_record),
            all_messages=all_msgs,
        )

    def _ensure_demo_order(
        self,
        *,
        customer_ext_id: str,
        full_name: str,
        phone: str,
        opt_in: bool,
        order_ext_id: str,
        line_items: list[tuple[str, int]],
    ) -> None:
        from sqlalchemy import select

        if self.session.scalar(select(Order).where(Order.external_order_id == order_ext_id)):
            return
        now = datetime.now(timezone.utc)
        customer = self.session.scalar(
            select(Customer).where(Customer.external_customer_id == customer_ext_id)
        )
        if customer is None:
            customer = Customer(
                external_customer_id=customer_ext_id,
                full_name=full_name,
                phone_number=phone,
                whatsapp_opt_in=opt_in,
                opted_out=False,
                consent_source="DEMO_CHECKOUT_OPT_IN" if opt_in else None,
                consent_recorded_at=now if opt_in else None,
            )
            self.session.add(customer)
            self.session.flush()
        order = Order(
            external_order_id=order_ext_id,
            customer_id=customer.id,
            order_status=OrderStatus.DELIVERED,
            delivered_at=now,
        )
        order.line_items = [
            OrderLineItem(line_index=idx, product_code=code, quantity=qty)
            for idx, (code, qty) in enumerate(line_items)
        ]
        self.session.add(order)
        self.session.commit()

    def seed_demo_campaigns(self) -> SimulatorDemoSeedResponseSchema:
        """Idempotently seed demo campaigns so the simulator UI has data for a live walkthrough.

        Demo data is explicitly synthetic: the catalogue is loaded with the synthetic eligibility
        fixture (provenance SYNTHETIC_FIXTURE) only if no products exist yet.
        """
        from sqlalchemy import func, select

        from app.models.product import Product

        if not self.session.scalar(select(func.count()).select_from(Product)):
            CatalogueLoaderService(self.session).load_from_files(
                workbook_path=self.settings.raw_workbook_path,
                normalized_json_path=self.settings.normalized_mappings_path,
                normalized_csv_path=self.settings.normalized_mappings_csv_path,
                validation_report_path=self.settings.validation_report_path,
                synthetic_fixture_path=self.settings.synthetic_catalogue_fixture_path,
                apply_synthetic_metadata=True,
            )

        demos = [
            dict(customer_ext_id="CUST-DEMO-01", full_name="Demo Customer One", phone="+919876543210",
                 opt_in=True, order_ext_id="DEMO-ORD-1001", line_items=[("SAMPLE-001", 2), ("SAMPLE-008", 1)]),
            dict(customer_ext_id="CUST-DEMO-02", full_name="Demo Customer Two", phone="+919811122233",
                 opt_in=True, order_ext_id="DEMO-ORD-1002", line_items=[("SAMPLE-002", 1)]),
            dict(customer_ext_id="CUST-DEMO-03", full_name="Demo Customer Three (no consent)",
                 phone="+919800011122", opt_in=False, order_ext_id="DEMO-ORD-1003",
                 line_items=[("SAMPLE-003", 1)]),
        ]
        orchestrator = CampaignOrchestrationService(self.session, settings=self.settings)
        ready: list[int] = []
        dispatched: list[int] = []
        suppressed: list[int] = []
        for idx, demo in enumerate(demos):
            self._ensure_demo_order(**demo)
            result = orchestrator.process_order_delivered_event(
                OrderDeliveredWebhookRequest(
                    event_id=f"evt-demo-{demo['order_ext_id']}",
                    event_type="order.delivered",
                    order_id=demo["order_ext_id"],
                    occurred_at=datetime.now(timezone.utc),
                    source=(self.settings.allowed_delivery_event_sources or ["order_service"])[0],
                )
            )
            cid = result.campaign_id
            if cid is None:
                continue
            if str(result.campaign_status) in (MessageDeliveryStatus.SUPPRESSED.value, "SUPPRESSED"):
                suppressed.append(cid)
            elif idx == 0:
                res = DispatchCoordinator(
                    self.session,
                    settings=self.settings,
                    provider=SimulatorWhatsAppProvider(settings=self.settings),
                ).dispatch_campaign(cid)
                if res.delivery_status == MessageDeliveryStatus.SENT_SIMULATED.value:
                    dispatched.append(cid)
                else:
                    ready.append(cid)
            else:
                ready.append(cid)

        return SimulatorDemoSeedResponseSchema(
            status="DEMO_CAMPAIGNS_READY",
            campaigns_ready=ready,
            campaigns_dispatched_simulated=dispatched,
            campaigns_suppressed=suppressed,
            detail="Demo campaigns ensured (synthetic catalogue metadata; simulator only).",
        )
