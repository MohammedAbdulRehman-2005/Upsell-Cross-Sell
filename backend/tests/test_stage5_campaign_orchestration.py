"""Stage 5: Comprehensive tests for order-delivery event processing and campaign orchestration.

Covers all 33 mandatory Stage 5 verification scenarios:
- Event validation (1-8)
- Idempotency & PostgreSQL concurrency (9-13)
- Customer consent & destination eligibility (14-19)
- Recommendation engine integration & metadata provenance (20-26)
- Campaign lifecycle, persistence, privacy sanitization, and Stage 5 state boundaries (27-33)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import event as sa_event, func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import create_app
from app.models.customer import Customer
from app.models.enums import (
    CampaignSuppressionReason,
    CatalogueMetadataSource,
    DeliveryEventOutcome,
    MessageDeliveryStatus,
    OrderStatus,
    WebhookEventType,
    WebhookProcessingStatus,
)
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.models.product import Product, ProductMapping
from app.models.webhook import WebhookEvent
from app.schemas.campaign import OrderDeliveredWebhookRequest, OrderDeliveryWebhookResponse
from app.services.campaign_orchestrator import (
    CampaignOrchestrationService,
    PermanentCampaignError,
    RetryableCampaignError,
    mask_phone_number,
    sanitize_event_payload_for_audit,
)
from app.services.catalogue_loader import CatalogueLoaderService


# ---------------------------------------------------------------------------
# Helpers for building realistic test fixtures
# ---------------------------------------------------------------------------


def _seed_mini_catalogue(
    session: Session,
    *,
    metadata_source: str = CatalogueMetadataSource.VERIFIED_CATALOGUE.value,
    is_synthetic: bool = False,
    num_targets: int = 6,
    is_rx: bool | None = False,
    is_active: bool | None = True,
    available_quantity: int | None = 25,
) -> None:
    """Create source SKU SRC-100 and 6 ranked target SKUs TGT-101..TGT-106."""
    src_product = Product(
        product_code="SRC-100",
        product_name="Hydrating Ceramide Cleanser",
        is_rx=False,
        is_active=True,
        available_quantity=50,
        unit_price=Decimal("499.00"),
        currency="INR",
        metadata_source=metadata_source,
        is_synthetic_metadata=is_synthetic,
    )
    session.add(src_product)

    for rank in range(1, 7):
        eligible_this_rank = rank <= num_targets
        tgt_code = f"TGT-10{rank}"
        tgt_product = Product(
            product_code=tgt_code,
            product_name=f"Barrier Repair Product {rank}",
            is_rx=is_rx if eligible_this_rank else True,
            is_active=is_active,
            available_quantity=available_quantity,
            unit_price=Decimal(f"{299 + rank * 50}.00"),
            currency="INR",
            metadata_source=metadata_source,
            is_synthetic_metadata=is_synthetic,
        )
        session.add(tgt_product)
        session.add(
            ProductMapping(
                source_product_code="SRC-100",
                recommended_product_code=tgt_code,
                recommendation_rank=rank,
                source_excel_row=rank + 1,
            )
        )
    session.commit()


def _create_customer_and_order(
    session: Session,
    *,
    external_customer_id: str = "CUST-5001",
    phone_number: str | None = "+919876543210",
    whatsapp_opt_in: bool | None = True,
    opted_out: bool | None = False,
    consent_source: str | None = "checkout_opt_in",
    consent_recorded_at: datetime | None = datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc),
    external_order_id: str = "ORD-5001",
    order_status: OrderStatus = OrderStatus.DELIVERED,
    line_items: list[tuple[str, int]] | None = None,
) -> tuple[Customer, Order]:
    customer = Customer(
        external_customer_id=external_customer_id,
        full_name="Priya Nair",
        phone_number=phone_number,
        whatsapp_opt_in=whatsapp_opt_in,
        opted_out=opted_out,
        consent_source=consent_source,
        consent_recorded_at=consent_recorded_at,
    )
    session.add(customer)
    session.flush()

    order = Order(
        external_order_id=external_order_id,
        customer_id=customer.id,
        order_status=order_status,
        delivered_at=(
            datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
            if order_status == OrderStatus.DELIVERED
            else None
        ),
    )
    items = [("SRC-100", 1)] if line_items is None else line_items
    for idx, (sku, qty) in enumerate(items):
        order.line_items.append(
            OrderLineItem(
                line_index=idx,
                product_code=sku,
                product_name=f"Name for {sku}",
                quantity=qty,
            )
        )
    session.add(order)
    session.commit()
    session.refresh(customer)
    session.refresh(order)
    return customer, order


def _event_payload(
    *,
    event_id: str = "evt_stage5_001",
    event_type: str = "order.delivered",
    order_id: str = "ORD-5001",
    occurred_at: str = "2026-11-11T12:05:00Z",
    source: str = "order_service",
    metadata: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "event_id": event_id,
        "event_type": event_type,
        "order_id": order_id,
        "occurred_at": occurred_at,
        "source": source,
    }
    if metadata is not None:
        body["metadata"] = metadata
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# 1-8: Event Validation & Webhook Security Tests
# ---------------------------------------------------------------------------


def test_01_valid_order_delivered_event_creates_ready_campaign(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 1: Valid order.delivered event for eligible delivered order."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH.value
    assert data["idempotent_replay"] is False
    assert data["campaign_status"] == MessageDeliveryStatus.READY_FOR_DISPATCH.value
    assert data["recommendation_status"] == "SUCCESS"
    assert data["recommended_skus"] == [f"TGT-10{i}" for i in range(1, 7)]
    assert data["suppression_reason"] is None


def test_02_invalid_event_type_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 2: Invalid event type is rejected without creating any campaign."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session)

    resp = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(event_type="order.created"),
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.REJECTED_UNSUPPORTED_EVENT_TYPE.value
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 0


def test_03_missing_required_event_fields_rejected(
    client: TestClient,
) -> None:
    """Scenario 3: Missing required event fields fail Pydantic validation with HTTP 422."""
    for missing_key in ("event_id", "event_type", "order_id", "occurred_at", "source"):
        bad_payload = _event_payload()
        bad_payload.pop(missing_key)
        resp = client.post("/api/v1/webhooks/order-delivered", json=bad_payload)
        assert resp.status_code == 422, f"Expected 422 when missing {missing_key}"


def test_04_unknown_order_id_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 4: Unknown order_id returns 404 REJECTED_ORDER_NOT_FOUND and creates no campaign."""
    resp = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(order_id="ORD-DOES-NOT-EXIST"),
    )
    assert resp.status_code == 404
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.REJECTED_ORDER_NOT_FOUND.value
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 0
    wh = db_session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == "evt_stage5_001"))
    assert wh is not None
    assert wh.processing_status == WebhookProcessingStatus.FAILED


@pytest.mark.parametrize(
    "non_delivered_status",
    [OrderStatus.PENDING, OrderStatus.PROCESSING, OrderStatus.SHIPPED],
)
def test_05_non_delivered_order_states_do_not_trigger_campaign(
    client: TestClient,
    db_session: Session,
    non_delivered_status: OrderStatus,
) -> None:
    """Scenario 5: Orders in PENDING, PROCESSING, or SHIPPED state do not trigger a campaign."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, order_status=non_delivered_status)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.REJECTED_ORDER_NOT_DELIVERED.value
    assert data["campaign_id"] is None
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 0


def test_06_cancelled_order_does_not_trigger_campaign(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 6: Order in CANCELLED state is ignored and creates no campaign."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, order_status=OrderStatus.CANCELLED)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.REJECTED_ORDER_CANCELLED.value
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 0


def test_07_event_claiming_delivered_when_persisted_order_is_shipped_is_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 7: Event payload claiming order_status='DELIVERED' when persisted DB state is SHIPPED."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, order_status=OrderStatus.SHIPPED)

    resp = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(order_status="DELIVERED"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.REJECTED_ORDER_NOT_DELIVERED.value
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 0


def test_08_unauthorized_event_source_and_webhook_token_security(
    db_session: Session,
    test_database_url: str,
) -> None:
    """Scenario 8: Unauthorized event source (403) and webhook authentication modes (401 / 503)."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session)

    # Part A: Unauthorized event source
    dev_settings = Settings(
        environment="testing",
        database_url=SecretStr(test_database_url),
        order_webhook_auth_mode="dev_insecure_open",
    )
    app_dev = create_app(dev_settings)
    app_dev.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app_dev) as dev_client:
        resp_unauth_src = dev_client.post(
            "/api/v1/webhooks/order-delivered",
            json=_event_payload(source="untrusted_external_caller"),
        )
        assert resp_unauth_src.status_code == 403
        assert resp_unauth_src.json()["outcome"] == DeliveryEventOutcome.REJECTED_UNAUTHORIZED_SOURCE.value

    # Part B: Production environment rejects dev_insecure_open (fails closed with 503)
    prod_insecure_settings = Settings(
        environment="production",
        database_url=SecretStr(test_database_url),
        order_webhook_auth_mode="dev_insecure_open",
    )
    app_prod_insecure = create_app(prod_insecure_settings)
    app_prod_insecure.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app_prod_insecure) as prod_insecure_client:
        resp_prod_open = prod_insecure_client.post(
            "/api/v1/webhooks/order-delivered",
            json=_event_payload(),
        )
        assert resp_prod_open.status_code == 503

    # Part C: shared_token_header mode requires valid X-Webhook-Token
    token_settings = Settings(
        environment="production",
        database_url=SecretStr(test_database_url),
        order_webhook_auth_mode="shared_token_header",
        order_webhook_shared_token=SecretStr("super-secret-stage5-token"),
    )
    app_token = create_app(token_settings)
    app_token.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app_token) as token_client:
        # Missing header -> 401
        resp_missing = token_client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
        assert resp_missing.status_code == 401

        # Wrong header -> 401
        resp_wrong = token_client.post(
            "/api/v1/webhooks/order-delivered",
            json=_event_payload(),
            headers={"X-Webhook-Token": "wrong-token"},
        )
        assert resp_wrong.status_code == 401

        # Valid header -> 200
        resp_valid = token_client.post(
            "/api/v1/webhooks/order-delivered",
            json=_event_payload(),
            headers={"X-Webhook-Token": "super-secret-stage5-token"},
        )
        assert resp_valid.status_code == 200
        assert resp_valid.json()["outcome"] == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH.value


# ---------------------------------------------------------------------------
# 9-13: Idempotency, Concurrency & Transaction Recovery Tests
# ---------------------------------------------------------------------------


def test_09_duplicate_event_same_event_id_is_idempotent_noop(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 9: Duplicate delivery event with identical event_id returns DUPLICATE_EVENT_NOOP."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session)

    first = client.post("/api/v1/webhooks/order-delivered", json=_event_payload(event_id="evt_dup_09"))
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["outcome"] == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH.value
    assert first_data["idempotent_replay"] is False

    second = client.post("/api/v1/webhooks/order-delivered", json=_event_payload(event_id="evt_dup_09"))
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["outcome"] == DeliveryEventOutcome.DUPLICATE_EVENT_NOOP.value
    assert second_data["idempotent_replay"] is True
    assert second_data["campaign_id"] == first_data["campaign_id"]
    assert second_data["recommended_skus"] == first_data["recommended_skus"]

    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 1
    assert db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 1


def test_10_duplicate_event_different_event_id_same_order_is_idempotent_noop(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 10: Different event_ids for the same delivered order do not create duplicate campaigns."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session)

    first = client.post("/api/v1/webhooks/order-delivered", json=_event_payload(event_id="evt_order_first"))
    assert first.status_code == 200
    first_data = first.json()
    assert first_data["outcome"] == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH.value

    second = client.post("/api/v1/webhooks/order-delivered", json=_event_payload(event_id="evt_order_second"))
    assert second.status_code == 200
    second_data = second.json()
    assert second_data["outcome"] == DeliveryEventOutcome.DUPLICATE_ORDER_CAMPAIGN_NOOP.value
    assert second_data["idempotent_replay"] is True
    assert second_data["campaign_id"] == first_data["campaign_id"]

    # Exactly 1 campaign in message_logs, 2 audit records in webhook_events (PROCESSED + IGNORED)
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 1
    assert db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 2


def test_11_concurrent_duplicate_delivery_events_against_postgresql(
    test_engine: Any,
    db_session: Session,
) -> None:
    """Scenario 11: Concurrent delivery events against PostgreSQL create exactly one campaign record."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, external_order_id="ORD-CONCURRENT-1")
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-5002",
        external_order_id="ORD-CONCURRENT-2",
    )

    SessionFactory = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    # Sub-case A: 6 concurrent workers sending DIFFERENT event_ids for the SAME order (ORD-CONCURRENT-1)
    def _worker_distinct_event(idx: int) -> OrderDeliveryWebhookResponse:
        with SessionFactory() as worker_session:
            svc = CampaignOrchestrationService(worker_session)
            req = OrderDeliveredWebhookRequest(
                event_id=f"evt_concurrent_distinct_{idx}",
                event_type="order.delivered",
                order_id="ORD-CONCURRENT-1",
                occurred_at=datetime(2026, 11, 11, 12, 30, tzinfo=timezone.utc),
                source="order_service",
            )
            return svc.process_order_delivered_event(req)

    with ThreadPoolExecutor(max_workers=6) as pool:
        distinct_results = list(pool.map(_worker_distinct_event, range(6)))

    created_outcomes = [
        r for r in distinct_results if r.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH
    ]
    noop_outcomes = [
        r for r in distinct_results if r.outcome == DeliveryEventOutcome.DUPLICATE_ORDER_CAMPAIGN_NOOP
    ]
    assert len(created_outcomes) == 1
    assert len(noop_outcomes) == 5

    # Sub-case B: 6 concurrent workers sending the IDENTICAL event_id for ORD-CONCURRENT-2
    def _worker_same_event(_idx: int) -> OrderDeliveryWebhookResponse:
        with SessionFactory() as worker_session:
            svc = CampaignOrchestrationService(worker_session)
            req = OrderDeliveredWebhookRequest(
                event_id="evt_concurrent_identical",
                event_type="order.delivered",
                order_id="ORD-CONCURRENT-2",
                occurred_at=datetime(2026, 11, 11, 12, 35, tzinfo=timezone.utc),
                source="order_service",
            )
            return svc.process_order_delivered_event(req)

    with ThreadPoolExecutor(max_workers=6) as pool:
        same_event_results = list(pool.map(_worker_same_event, range(6)))

    created_same = [
        r for r in same_event_results if r.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH
    ]
    duplicate_same = [
        r
        for r in same_event_results
        if r.outcome
        in (
            DeliveryEventOutcome.DUPLICATE_EVENT_NOOP,
            DeliveryEventOutcome.DUPLICATE_ORDER_CAMPAIGN_NOOP,
        )
    ]
    assert len(created_same) == 1
    assert len(duplicate_same) == 5

    db_session.expire_all()
    campaigns = list(db_session.scalars(select(MessageLog)).all())
    assert len(campaigns) == 2


def test_12_retry_after_transient_failure_recovers_without_duplicate_campaign(
    db_session: Session,
) -> None:
    """Scenario 12: Retry after transient failure transitions FAILED_RETRYABLE -> READY_FOR_DISPATCH."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session)

    class FailingOnceRecommendationService:
        def __init__(self) -> None:
            self.calls = 0

        def generate_for_order_input(self, order_input: Any) -> Any:
            self.calls += 1
            raise RetryableCampaignError("Simulated transient catalogue read timeout")

    failing_svc = CampaignOrchestrationService(
        db_session,
        recommendation_service=FailingOnceRecommendationService(),  # type: ignore[arg-type]
    )
    req = OrderDeliveredWebhookRequest(**_event_payload(event_id="evt_retry_12"))
    first_res = failing_svc.process_order_delivered_event(req)
    assert first_res.outcome == DeliveryEventOutcome.FAILED_RETRYABLE
    assert first_res.campaign_status == MessageDeliveryStatus.FAILED_RETRYABLE

    campaign_after_fail = db_session.scalar(select(MessageLog))
    assert campaign_after_fail is not None
    assert campaign_after_fail.delivery_status == MessageDeliveryStatus.FAILED_RETRYABLE
    assert campaign_after_fail.retry_count == 0

    # Now retry with the healthy recommendation service
    recovery_svc = CampaignOrchestrationService(db_session)
    second_res = recovery_svc.process_order_delivered_event(req)
    assert second_res.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH
    assert second_res.campaign_status == MessageDeliveryStatus.READY_FOR_DISPATCH
    assert second_res.campaign_id == campaign_after_fail.id
    assert second_res.recommended_skus == [f"TGT-10{i}" for i in range(1, 7)]

    # Still only 1 campaign record in PostgreSQL, updated in-place with retry_count=1
    assert db_session.scalar(select(func.count()).select_from(MessageLog)) == 1
    db_session.refresh(campaign_after_fail)
    assert campaign_after_fail.delivery_status == MessageDeliveryStatus.READY_FOR_DISPATCH
    assert campaign_after_fail.retry_count == 1


def test_13_transaction_rollback_leaves_no_partial_campaign_record(
    test_engine: Any,
    db_session: Session,
) -> None:
    """Scenario 13: Real PostgreSQL transaction rollback, recovery, replay, and concurrent retries.

    Verifies all 7 mandatory rollback & recovery guarantees:
    1. Event insertion followed by a deliberately injected campaign persistence failure.
    2. Transaction rollback removes all partial writes from that transaction.
    3. No event is incorrectly treated as successfully processed.
    4. Retrying the same event ID after the failure processes successfully.
    5. Exactly one campaign record exists after successful recovery.
    6. Replaying the successful event creates no additional campaign.
    7. Concurrent retries do not create duplicate campaigns.
    """
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, external_order_id="ORD-ROLLBACK-13")
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-5013B",
        external_order_id="ORD-CONCURRENT-RETRY-13B",
    )

    SessionFactory = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    # 1. Inject a real PostgreSQL persistence failure when MessageLog is flushed after WebhookEvent insertion
    fail_on_message_flush = {"active": True, "webhook_was_flushed_before_failure": False}

    def _inject_campaign_persistence_failure(
        session: Session,
        _flush_context: Any,
        _instances: Any,
    ) -> None:
        if not fail_on_message_flush["active"]:
            return
        has_message_log = any(isinstance(obj, MessageLog) for obj in session.new)
        if has_message_log:
            # Confirm WebhookEvent was already inserted in this transaction before MessageLog flush fails
            wh_in_tx = session.scalar(
                select(WebhookEvent).where(WebhookEvent.event_id == "evt_rollback_13")
            )
            fail_on_message_flush["webhook_was_flushed_before_failure"] = wh_in_tx is not None
            raise SQLAlchemyError("Simulated PostgreSQL failure during MessageLog campaign persistence")

    sa_event.listen(db_session, "before_flush", _inject_campaign_persistence_failure)
    try:
        svc = CampaignOrchestrationService(db_session)
        req = OrderDeliveredWebhookRequest(
            **_event_payload(event_id="evt_rollback_13", order_id="ORD-ROLLBACK-13")
        )
        with pytest.raises(SQLAlchemyError, match="Simulated PostgreSQL failure"):
            svc.process_order_delivered_event(req)
    finally:
        sa_event.remove(db_session, "before_flush", _inject_campaign_persistence_failure)

    assert fail_on_message_flush["webhook_was_flushed_before_failure"] is True

    # 2 & 3. Verify via an independent PostgreSQL session that rollback removed ALL partial writes
    # and no event is marked processed
    with SessionFactory() as verify_session:
        assert (
            verify_session.scalar(
                select(func.count())
                .select_from(WebhookEvent)
                .where(WebhookEvent.event_id == "evt_rollback_13")
            )
            == 0
        )
        assert (
            verify_session.scalar(
                select(func.count())
                .select_from(WebhookEvent)
                .where(WebhookEvent.processing_status == WebhookProcessingStatus.PROCESSED)
            )
            == 0
        )
        assert verify_session.scalar(select(func.count()).select_from(MessageLog)) == 0

    # 4 & 5. Retry the exact same event_id after failure -> succeeds and persists 1 campaign
    recovery_svc = CampaignOrchestrationService(db_session)
    recovered_res = recovery_svc.process_order_delivered_event(req)
    assert recovered_res.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH
    assert recovered_res.idempotent_replay is False
    assert recovered_res.campaign_status == MessageDeliveryStatus.READY_FOR_DISPATCH
    assert len(recovered_res.recommended_skus) == 6

    with SessionFactory() as verify_session:
        wh_rows = list(
            verify_session.scalars(
                select(WebhookEvent).where(WebhookEvent.event_id == "evt_rollback_13")
            ).all()
        )
        assert len(wh_rows) == 1
        assert wh_rows[0].processing_status == WebhookProcessingStatus.PROCESSED

        msg_rows = list(verify_session.scalars(select(MessageLog)).all())
        assert len(msg_rows) == 1
        assert msg_rows[0].delivery_status == MessageDeliveryStatus.READY_FOR_DISPATCH
        assert msg_rows[0].trigger_event_id == "evt_rollback_13"

    # 6. Replay the successful event -> idempotent no-op, no additional campaign created
    replay_res = recovery_svc.process_order_delivered_event(req)
    assert replay_res.outcome == DeliveryEventOutcome.DUPLICATE_EVENT_NOOP
    assert replay_res.idempotent_replay is True
    assert replay_res.campaign_id == recovered_res.campaign_id

    with SessionFactory() as verify_session:
        assert verify_session.scalar(select(func.count()).select_from(MessageLog)) == 1

    # 7. Concurrent retries of the same event_id after a FAILED_RETRYABLE state create no duplicate campaigns
    class TransientFailService:
        def generate_for_order_input(self, _order_input: Any) -> Any:
            raise RetryableCampaignError("Transient DB read timeout")

    with SessionFactory() as initial_fail_session:
        fail_svc = CampaignOrchestrationService(
            initial_fail_session,
            recommendation_service=TransientFailService(),  # type: ignore[arg-type]
        )
        fail_req = OrderDeliveredWebhookRequest(
            **_event_payload(
                event_id="evt_concurrent_retry_13b",
                order_id="ORD-CONCURRENT-RETRY-13B",
            )
        )
        fail_res = fail_svc.process_order_delivered_event(fail_req)
        assert fail_res.outcome == DeliveryEventOutcome.FAILED_RETRYABLE

    def _concurrent_retry_worker(_idx: int) -> OrderDeliveryWebhookResponse:
        with SessionFactory() as worker_session:
            worker_svc = CampaignOrchestrationService(worker_session)
            worker_req = OrderDeliveredWebhookRequest(
                **_event_payload(
                    event_id="evt_concurrent_retry_13b",
                    order_id="ORD-CONCURRENT-RETRY-13B",
                )
            )
            return worker_svc.process_order_delivered_event(worker_req)

    with ThreadPoolExecutor(max_workers=6) as pool:
        concurrent_retry_results = list(pool.map(_concurrent_retry_worker, range(6)))

    ready_outcomes = [
        r
        for r in concurrent_retry_results
        if r.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH
    ]
    noop_outcomes = [
        r
        for r in concurrent_retry_results
        if r.outcome
        in (
            DeliveryEventOutcome.DUPLICATE_EVENT_NOOP,
            DeliveryEventOutcome.DUPLICATE_ORDER_CAMPAIGN_NOOP,
        )
    ]
    assert len(ready_outcomes) == 1
    assert len(noop_outcomes) == 5

    with SessionFactory() as verify_session:
        ord_13b = verify_session.scalar(
            select(Order).where(Order.external_order_id == "ORD-CONCURRENT-RETRY-13B")
        )
        assert ord_13b is not None
        campaigns_13b = list(
            verify_session.scalars(
                select(MessageLog).where(MessageLog.order_id == ord_13b.id)
            ).all()
        )
        assert len(campaigns_13b) == 1
        assert campaigns_13b[0].delivery_status == MessageDeliveryStatus.READY_FOR_DISPATCH
        assert campaigns_13b[0].retry_count == 1
        wh_13b = verify_session.scalar(
            select(WebhookEvent).where(WebhookEvent.event_id == "evt_concurrent_retry_13b")
        )
        assert wh_13b is not None
        assert wh_13b.processing_status == WebhookProcessingStatus.PROCESSED


# ---------------------------------------------------------------------------
# 14-19: Customer Consent & Destination Eligibility Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_phone",
    [
        "+919876543210",      # Standard +91 E.164 (12 digits)
        "+12345678",          # Minimum valid E.164 length (8 digits after '+')
        "+123456789012345",   # Maximum valid E.164 length (15 digits after '+')
    ],
)
def test_14_opted_in_customer_with_valid_phone_is_eligible(
    client: TestClient,
    db_session: Session,
    valid_phone: str,
) -> None:
    """Scenario 14: Opted-in customer with strict E.164 '+' phone number produces READY_FOR_DISPATCH."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(
        db_session,
        phone_number=valid_phone,
        whatsapp_opt_in=True,
        opted_out=False,
    )

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    assert resp.json()["campaign_status"] == MessageDeliveryStatus.READY_FOR_DISPATCH.value


def test_15_customer_without_marketing_consent_is_suppressed(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 15: whatsapp_opt_in=False -> SUPPRESSED with WHATSAPP_MARKETING_CONSENT_MISSING."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(
        db_session,
        whatsapp_opt_in=False,
        opted_out=False,
    )

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["suppression_reason"] == CampaignSuppressionReason.WHATSAPP_MARKETING_CONSENT_MISSING.value
    assert data["recommended_skus"] == []


def test_16_customer_explicitly_opted_out_overrides_opt_in(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 16: opted_out=True overrides whatsapp_opt_in=True -> SUPPRESSED with CUSTOMER_OPTED_OUT."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(
        db_session,
        whatsapp_opt_in=True,
        opted_out=True,
    )

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["suppression_reason"] == CampaignSuppressionReason.CUSTOMER_OPTED_OUT.value
    assert data["recommended_skus"] == []


@pytest.mark.parametrize(
    ("opt_in", "opted_out"),
    [(None, False), (True, None), (None, None)],
)
def test_17_customer_with_unknown_null_consent_fails_closed(
    client: TestClient,
    db_session: Session,
    opt_in: bool | None,
    opted_out: bool | None,
) -> None:
    """Scenario 17: Unknown/NULL consent flags fail closed with CONSENT_STATUS_UNKNOWN."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(
        db_session,
        whatsapp_opt_in=opt_in,
        opted_out=opted_out,
    )

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["suppression_reason"] == CampaignSuppressionReason.CONSENT_STATUS_UNKNOWN.value


def test_17b_production_and_staging_enforce_consent_audit_metadata_fail_closed(
    db_session: Session,
    test_database_url: str,
) -> None:
    """Scenario 17B: Production & staging enforce consent_source and consent_recorded_at and cannot bypass."""
    # 1. Verify environment defaults
    dev_cfg = Settings(environment="development", database_url=SecretStr(test_database_url))
    test_cfg = Settings(environment="testing", database_url=SecretStr(test_database_url))
    staging_cfg = Settings(environment="staging", database_url=SecretStr(test_database_url))
    prod_cfg = Settings(environment="production", database_url=SecretStr(test_database_url))

    assert dev_cfg.require_consent_audit_metadata is False
    assert test_cfg.require_consent_audit_metadata is False
    assert staging_cfg.require_consent_audit_metadata is True
    assert prod_cfg.require_consent_audit_metadata is True

    # 2. Verify production and staging cannot silently disable require_consent_audit_metadata
    with pytest.raises(ValidationError, match="require_consent_audit_metadata cannot be set to False"):
        Settings(
            environment="production",
            require_consent_audit_metadata=False,
            database_url=SecretStr(test_database_url),
        )

    with pytest.raises(ValidationError, match="require_consent_audit_metadata cannot be set to False"):
        Settings(
            environment="staging",
            require_consent_audit_metadata=False,
            database_url=SecretStr(test_database_url),
        )

    # 3. Verify production enforcement fails closed when consent_source or consent_recorded_at is missing
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-PROD-NO-SOURCE",
        external_order_id="ORD-PROD-NO-SOURCE",
        consent_source=None,
        consent_recorded_at=datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc),
    )
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-PROD-NO-TS",
        external_order_id="ORD-PROD-NO-TS",
        consent_source="checkout_opt_in",
        consent_recorded_at=None,
    )
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-PROD-FULL-AUDIT",
        external_order_id="ORD-PROD-FULL-AUDIT",
        consent_source="checkout_opt_in",
        consent_recorded_at=datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc),
    )

    prod_svc = CampaignOrchestrationService(db_session, settings=prod_cfg)

    res_no_source = prod_svc.process_order_delivered_event(
        OrderDeliveredWebhookRequest(
            **_event_payload(event_id="evt_prod_no_src", order_id="ORD-PROD-NO-SOURCE")
        )
    )
    assert res_no_source.outcome == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED
    assert res_no_source.campaign_status == MessageDeliveryStatus.SUPPRESSED
    assert res_no_source.suppression_reason == CampaignSuppressionReason.CONSENT_STATUS_UNKNOWN

    res_no_ts = prod_svc.process_order_delivered_event(
        OrderDeliveredWebhookRequest(
            **_event_payload(event_id="evt_prod_no_ts", order_id="ORD-PROD-NO-TS")
        )
    )
    assert res_no_ts.outcome == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED
    assert res_no_ts.campaign_status == MessageDeliveryStatus.SUPPRESSED
    assert res_no_ts.suppression_reason == CampaignSuppressionReason.CONSENT_STATUS_UNKNOWN

    res_full = prod_svc.process_order_delivered_event(
        OrderDeliveredWebhookRequest(
            **_event_payload(event_id="evt_prod_full_ok", order_id="ORD-PROD-FULL-AUDIT")
        )
    )
    assert res_full.outcome == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH
    assert res_full.campaign_status == MessageDeliveryStatus.READY_FOR_DISPATCH


@pytest.mark.parametrize("missing_phone", [None, "", "   ", "\t\n"])
def test_18_customer_with_missing_phone_number_is_suppressed(
    client: TestClient,
    db_session: Session,
    missing_phone: str | None,
) -> None:
    """Scenario 18: Missing, blank, or whitespace-only phone number -> SUPPRESSED with CUSTOMER_PHONE_MISSING."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, phone_number=missing_phone)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["suppression_reason"] == CampaignSuppressionReason.CUSTOMER_PHONE_MISSING.value


@pytest.mark.parametrize(
    "bad_phone",
    [
        "919876543210",          # Missing '+'
        "14155552671",           # Missing '+' (US format)
        "+0123456789",           # Invalid leading zero after '+'
        "09876543210",           # Missing '+' and leading zero
        "+1234567",              # Too few digits (7 digits after '+', minimum is 8)
        "+1234567890123456",     # Too many digits (16 digits after '+', maximum is 15)
        "invalid-phone",         # Non-numeric characters
        "+91-98765-43210",       # Hyphens not permitted in strict E.164
    ],
)
def test_19_customer_with_invalid_phone_number_is_suppressed(
    client: TestClient,
    db_session: Session,
    bad_phone: str,
) -> None:
    """Scenario 19: Strict E.164 validation rejects missing '+', leading zero, too few/many digits -> INVALID_WHATSAPP_DESTINATION."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, phone_number=bad_phone)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["suppression_reason"] == CampaignSuppressionReason.INVALID_WHATSAPP_DESTINATION.value
    # Verify phone number was never mutated/prepended with a country code
    persisted_customer = db_session.scalar(select(Customer))
    assert persisted_customer is not None
    assert persisted_customer.phone_number == bad_phone


# ---------------------------------------------------------------------------
# 20-26: Recommendation Engine Integration & Metadata Provenance Tests
# ---------------------------------------------------------------------------


def test_20_order_producing_6_recommendations_marks_ready_for_dispatch(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 20: Order producing 6 recommendations -> READY_FOR_DISPATCH (SUCCESS)."""
    _seed_mini_catalogue(db_session, num_targets=6)
    _create_customer_and_order(db_session)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    data = resp.json()
    assert data["campaign_status"] == MessageDeliveryStatus.READY_FOR_DISPATCH.value
    assert data["recommendation_status"] == "SUCCESS"
    assert len(data["recommended_skus"]) == 6


def test_21_order_producing_partial_recommendations_marks_ready_without_padding(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 21: Order producing 1-5 recommendations -> READY_FOR_DISPATCH (PARTIAL_SUCCESS)."""
    _seed_mini_catalogue(db_session, num_targets=3)
    _create_customer_and_order(db_session)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    data = resp.json()
    assert data["campaign_status"] == MessageDeliveryStatus.READY_FOR_DISPATCH.value
    assert data["recommendation_status"] == "PARTIAL_SUCCESS"
    assert data["recommended_skus"] == ["TGT-101", "TGT-102", "TGT-103"]


def test_22_order_producing_no_eligible_recommendations_is_suppressed(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 22: Order producing NO_ELIGIBLE_RECOMMENDATIONS -> SUPPRESSED."""
    _seed_mini_catalogue(db_session, num_targets=0)
    _create_customer_and_order(db_session)

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["recommendation_status"] == "NO_ELIGIBLE_RECOMMENDATIONS"
    assert data["suppression_reason"] == CampaignSuppressionReason.NO_ELIGIBLE_RECOMMENDATIONS.value
    assert data["recommended_skus"] == []


def test_23_order_producing_no_mapped_source_products_is_suppressed(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 23: Order with only unmapped SKUs -> SUPPRESSED (NO_MAPPED_SOURCE_PRODUCTS)."""
    _seed_mini_catalogue(db_session)
    _create_customer_and_order(db_session, line_items=[("UNMAPPED-SKU-999", 2)])

    resp = client.post("/api/v1/webhooks/order-delivered", json=_event_payload())
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["recommendation_status"] == "NO_MAPPED_SOURCE_PRODUCTS"
    assert data["suppression_reason"] == CampaignSuppressionReason.NO_MAPPED_SOURCE_PRODUCTS.value
    assert data["recommended_skus"] == []


def test_24_workbook_only_metadata_in_postgresql_suppresses_campaign(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 24: Seeding real workbook-only metadata (NULL eligibility) -> NO_ELIGIBLE_RECOMMENDATIONS -> SUPPRESSED."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=False,
    )

    _create_customer_and_order(
        db_session,
        external_order_id="ORD-WORKBOOK-ONLY",
        line_items=[("SAMPLE-001", 1), ("SAMPLE-008", 2)],
    )

    resp = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(event_id="evt_wb_24", order_id="ORD-WORKBOOK-ONLY"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_SUPPRESSED.value
    assert data["campaign_status"] == MessageDeliveryStatus.SUPPRESSED.value
    assert data["recommendation_status"] == "NO_ELIGIBLE_RECOMMENDATIONS"
    assert data["suppression_reason"] == CampaignSuppressionReason.NO_ELIGIBLE_RECOMMENDATIONS.value
    assert data["recommended_skus"] == []
    assert data["metadata_provenance"]["contains_workbook_only_metadata"] is True
    assert data["metadata_provenance"]["contains_synthetic_metadata"] is False


def test_25_synthetic_fixture_metadata_in_postgresql_prepares_campaign_with_synthetic_provenance(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 25: Seeding with synthetic fixture metadata -> READY_FOR_DISPATCH with SYNTHETIC_FIXTURE provenance."""
    settings = get_settings()
    loader = CatalogueLoaderService(db_session)
    loader.load_from_files(
        workbook_path=settings.raw_workbook_path,
        normalized_json_path=settings.normalized_mappings_path,
        normalized_csv_path=settings.normalized_mappings_csv_path,
        validation_report_path=settings.validation_report_path,
        synthetic_fixture_path=settings.synthetic_catalogue_fixture_path,
        apply_synthetic_metadata=True,
    )

    _create_customer_and_order(
        db_session,
        external_order_id="ORD-SYNTHETIC-25",
        line_items=[("SAMPLE-001", 1), ("SAMPLE-008", 10)],
    )

    resp = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(event_id="evt_synth_25", order_id="ORD-SYNTHETIC-25"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH.value
    assert data["campaign_status"] == MessageDeliveryStatus.READY_FOR_DISPATCH.value
    assert data["recommendation_status"] == "SUCCESS"
    assert len(data["recommended_skus"]) == 6
    assert data["recommended_skus"] == [
        "SAMPLE-002",
        "SAMPLE-003",
        "SAMPLE-004",
        "SAMPLE-010",
        "SAMPLE-011",
        "SAMPLE-012",
    ]
    assert data["metadata_provenance"]["contains_synthetic_metadata"] is True
    assert data["metadata_provenance"]["selected_source_counts"] == {"SYNTHETIC_FIXTURE": 6}


def test_26_verified_catalogue_metadata_prepares_campaign_with_verified_provenance(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenario 26: Verified catalogue metadata -> READY_FOR_DISPATCH with VERIFIED_CATALOGUE provenance."""
    _seed_mini_catalogue(
        db_session,
        metadata_source=CatalogueMetadataSource.VERIFIED_CATALOGUE.value,
        is_synthetic=False,
    )
    _create_customer_and_order(db_session, external_order_id="ORD-VERIFIED-26")

    resp = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(event_id="evt_verified_26", order_id="ORD-VERIFIED-26"),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["outcome"] == DeliveryEventOutcome.CAMPAIGN_READY_FOR_DISPATCH.value
    assert data["campaign_status"] == MessageDeliveryStatus.READY_FOR_DISPATCH.value
    assert data["metadata_provenance"]["contains_synthetic_metadata"] is False
    assert data["metadata_provenance"]["selected_source_counts"] == {"VERIFIED_CATALOGUE": 6}


# ---------------------------------------------------------------------------
# 27-33: Campaign Lifecycle, Persistence, Privacy & Stage 5 Boundary Tests
# ---------------------------------------------------------------------------


def test_27_to_33_campaign_persistence_trace_privacy_and_stage5_status_boundaries(
    client: TestClient,
    db_session: Session,
) -> None:
    """Scenarios 27-33:
    27. Full decision_trace persisted in campaign record.
    28. Ordered recommended_skus preserved in exact recommendation sequence.
    29. Suppression reason persisted accurately.
    30. Trigger event_id linked to campaign record and webhook payload sanitized for privacy.
    31. No campaign marked PROVIDER_ACCEPTED in Stage 5.
    32. No campaign marked SENT_SIMULATED in Stage 5.
    33. No campaign marked DELIVERED in Stage 5.
    """
    _seed_mini_catalogue(db_session, num_targets=5)
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-5027",
        phone_number="+919876543210",
        external_order_id="ORD-5027",
    )
    _create_customer_and_order(
        db_session,
        external_customer_id="CUST-5029",
        phone_number="+919876543211",
        whatsapp_opt_in=False,
        external_order_id="ORD-5029",
    )

    # Dispatch event 1 (eligible -> READY_FOR_DISPATCH with 5 items)
    resp1 = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(
            event_id="evt_stage5_trace_27",
            order_id="ORD-5027",
            customer_phone="+919876543210",
            metadata={"api_key": "secret-upstream-key", "courier": "BlueDart"},
        ),
    )
    assert resp1.status_code == 200

    # Dispatch event 2 (opt-in missing -> SUPPRESSED)
    resp2 = client.post(
        "/api/v1/webhooks/order-delivered",
        json=_event_payload(
            event_id="evt_stage5_suppress_29",
            order_id="ORD-5029",
        ),
    )
    assert resp2.status_code == 200

    campaigns = list(db_session.scalars(select(MessageLog).order_by(MessageLog.id.asc())).all())
    assert len(campaigns) == 2
    ready_campaign, suppressed_campaign = campaigns[0], campaigns[1]

    # Scenario 27: Full decision_trace persisted in campaign record
    assert ready_campaign.decision_trace is not None
    assert "order_summary" in ready_campaign.decision_trace
    assert "source_evaluations" in ready_campaign.decision_trace
    assert "candidate_evaluations" in ready_campaign.decision_trace
    assert "selection_provenance" in ready_campaign.decision_trace
    assert ready_campaign.decision_trace["selected_recommendation_count"] == 5

    # Scenario 28: Ordered recommended_skus preserved
    assert ready_campaign.recommended_skus == ["TGT-101", "TGT-102", "TGT-103", "TGT-104", "TGT-105"]

    # Scenario 29: Suppression reason persisted accurately
    assert suppressed_campaign.delivery_status == MessageDeliveryStatus.SUPPRESSED
    assert (
        suppressed_campaign.suppression_reason
        == CampaignSuppressionReason.WHATSAPP_MARKETING_CONSENT_MISSING.value
    )
    assert suppressed_campaign.recommended_skus == []

    # Scenario 30: Trigger event_id linked and webhook payload sanitized (phone masked, secret redacted)
    assert ready_campaign.trigger_event_id == "evt_stage5_trace_27"
    assert suppressed_campaign.trigger_event_id == "evt_stage5_suppress_29"

    wh1 = db_session.scalar(
        select(WebhookEvent).where(WebhookEvent.event_id == "evt_stage5_trace_27")
    )
    assert wh1 is not None
    assert wh1.payload["customer_phone"] == mask_phone_number("+919876543210")
    assert "+919876543210" not in str(wh1.payload)
    assert wh1.payload["metadata"]["api_key"] == "***REDACTED***"
    assert wh1.payload["metadata"]["courier"] == "BlueDart"

    # Scenarios 31, 32, 33: No campaign is marked PROVIDER_ACCEPTED, SENT_SIMULATED, or DELIVERED in Stage 5
    forbidden_stage5_statuses = {
        MessageDeliveryStatus.PROVIDER_ACCEPTED,
        MessageDeliveryStatus.SENT_SIMULATED,
        MessageDeliveryStatus.DELIVERED,
    }
    for campaign in campaigns:
        assert campaign.delivery_status not in forbidden_stage5_statuses
        assert campaign.provider_message_id is None
        assert campaign.provider_accepted_at is None
        assert campaign.delivered_at is None


def test_permanent_failure_on_corrupted_persisted_order_line_items(
    db_session: Session,
) -> None:
    """Additional verification: Persisted order with no line items is marked FAILED_PERMANENT."""
    _seed_mini_catalogue(db_session)
    customer = Customer(
        external_customer_id="CUST-EMPTY-ORD",
        full_name="Test User",
        phone_number="+919876543210",
        whatsapp_opt_in=True,
        opted_out=False,
    )
    db_session.add(customer)
    db_session.flush()
    empty_order = Order(
        external_order_id="ORD-EMPTY-LINES",
        customer_id=customer.id,
        order_status=OrderStatus.DELIVERED,
    )
    db_session.add(empty_order)
    db_session.commit()

    svc = CampaignOrchestrationService(db_session)
    res = svc.process_order_delivered_event(
        OrderDeliveredWebhookRequest(**_event_payload(event_id="evt_perm_fail", order_id="ORD-EMPTY-LINES"))
    )
    assert res.outcome == DeliveryEventOutcome.FAILED_PERMANENT
    assert res.campaign_status == MessageDeliveryStatus.FAILED_PERMANENT
    assert res.suppression_reason == CampaignSuppressionReason.INVALID_PERSISTED_ORDER_DATA.value
