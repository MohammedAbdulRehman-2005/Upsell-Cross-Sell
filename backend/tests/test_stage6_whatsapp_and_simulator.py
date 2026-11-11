"""Stage 6 tests: dispatch coordinator, providers, Meta webhooks, simulator."""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models.customer import Customer
from app.models.enums import MessageDeliveryStatus as S
from app.models.enums import MessagingProvider
from app.models.message import MessageLog
from app.models.order import Order, OrderLineItem
from app.models.product import Product
from app.services.whatsapp import (
    DispatchCoordinator,
    MetaCloudWhatsAppProvider,
    SimulatorWhatsAppProvider,
    get_whatsapp_provider,
    verify_meta_webhook_signature,
)

PHONE = "+919876543210"


def _settings(**kw) -> Settings:
    base = dict(environment="testing", database_url="sqlite+pysqlite:///:memory:")
    base.update(kw)
    return Settings(**base)


def _campaign(session: Session, *, status: S = S.READY_FOR_DISPATCH, phone: str | None = PHONE,
              suffix: str = "1", skus=("TGT-1", "TGT-2")) -> MessageLog:
    for i, code in enumerate(("TGT-1", "TGT-2", "SRC-1")):
        if session.query(Product).filter_by(product_code=code).first() is None:
            session.add(Product(product_code=code, product_name=f"Product {code}",
                                unit_price=Decimal("100.00")))
    cust = Customer(external_customer_id=f"C-{suffix}", phone_number=phone,
                    whatsapp_opt_in=True, opted_out=False)
    session.add(cust)
    session.flush()
    order = Order(external_order_id=f"O-{suffix}", customer_id=cust.id, order_status="DELIVERED",
                  delivered_at=datetime.now(timezone.utc))
    order.line_items = [OrderLineItem(line_index=0, product_code="SRC-1", quantity=1)]
    session.add(order)
    session.flush()
    msg = MessageLog(idempotency_key=f"campaign:test:{suffix}", order_id=order.id,
                     customer_id=cust.id, campaign_type="POST_DELIVERY_UPSELL",
                     recipient_phone=phone, delivery_status=status,
                     recommended_skus=list(skus), recommendation_status="SUCCESS")
    session.add(msg)
    session.commit()
    return msg


def _meta_settings() -> Settings:
    return _settings(whatsapp_provider="meta_cloud_api", whatsapp_access_token=SecretStr("tok_SECRET123"),
                     whatsapp_phone_number_id="12345")


def _meta(handler) -> MetaCloudWhatsAppProvider:
    s = _meta_settings()
    return MetaCloudWhatsAppProvider(settings=s, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _dispatch(session, cid, provider, settings=None, **kw):
    return DispatchCoordinator(session, settings=settings or _settings(), provider=provider).dispatch_campaign(cid, **kw)


# ---- dispatch / simulator -------------------------------------------------

def test_simulator_dispatch_sets_sent_simulated_and_persists_message(db_session: Session):
    c = _campaign(db_session)
    r = _dispatch(db_session, c.id, SimulatorWhatsAppProvider(settings=_settings()))
    assert r.delivery_status == "SENT_SIMULATED" and r.is_simulated
    assert r.simulated_message is not None and "SIMULATED" in r.simulated_message.body_text
    assert "Product TGT-1" in r.simulated_message.body_text and "TGT-2" in r.simulated_message.body_text
    db_session.refresh(c)
    assert c.delivery_status == "SENT_SIMULATED" and c.delivered_at is None


def test_duplicate_dispatch_is_noop(db_session: Session):
    c = _campaign(db_session)
    p = SimulatorWhatsAppProvider(settings=_settings())
    _dispatch(db_session, c.id, p)
    r2 = _dispatch(db_session, c.id, p)
    assert r2.dispatch_action == "NO_OP_ALREADY_DISPATCHED"
    from app.repositories.simulator_repository import SimulatorMessageRepository
    assert len(SimulatorMessageRepository(db_session).list_by_message_log_id(c.id)) == 1


@pytest.mark.parametrize("status", [S.SUPPRESSED, S.FAILED_PERMANENT, S.DELIVERY_UNKNOWN])
def test_non_dispatchable_statuses_rejected(db_session: Session, status):
    c = _campaign(db_session, status=status)
    called = []

    def handler(req):
        called.append(1)
        return httpx.Response(200, json={})

    r = _dispatch(db_session, c.id, _meta(handler), settings=_meta_settings())
    assert r.dispatch_action == "REJECTED_NON_DISPATCHABLE_STATUS" and not called


def test_invalid_phone_fails_permanent_without_provider_call(db_session: Session):
    c = _campaign(db_session, phone="9876543210")
    r = _dispatch(db_session, c.id, SimulatorWhatsAppProvider(settings=_settings()))
    assert r.delivery_status == "FAILED_PERMANENT"


def test_dispatching_is_committed_before_provider_call(db_session: Session, test_engine):
    c = _campaign(db_session)
    seen = {}

    def hook(cid):
        other = sessionmaker(bind=test_engine)()
        try:
            seen["status"] = other.get(MessageLog, cid).delivery_status
        finally:
            other.close()

    DispatchCoordinator(db_session, settings=_settings(), provider=SimulatorWhatsAppProvider(settings=_settings()),
                        before_provider_send_hook=hook).dispatch_campaign(c.id)
    assert str(seen["status"]) == "DISPATCHING"


# ---- Meta provider ----------------------------------------------------------

def test_meta_success_is_provider_accepted_not_delivered(db_session: Session):
    c = _campaign(db_session)

    def handler(req):
        assert req.headers["authorization"] == "Bearer tok_SECRET123"
        body = json.loads(req.content)
        assert body["to"] == PHONE and body["type"] == "template"
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    r = _dispatch(db_session, c.id, _meta(handler), settings=_meta_settings())
    assert r.delivery_status == "PROVIDER_ACCEPTED" and r.provider_message_id == "wamid.ABC"
    db_session.refresh(c)
    assert c.delivered_at is None and c.provider_accepted_at is not None


@pytest.mark.parametrize("code,body,expected", [
    (401, {"error": {"message": "bad tok_SECRET123", "code": 190}}, "FAILED_PERMANENT"),
    (429, {"error": {"message": "slow", "code": 4}}, "FAILED_RETRYABLE"),
    (500, {"error": {"message": "oops"}}, "FAILED_RETRYABLE"),
    (400, {"error": {"message": "Template does not exist", "code": 132001}}, "FAILED_PERMANENT"),
    (400, {"error": {"message": "bad recipient", "code": 131026}}, "FAILED_PERMANENT"),
])
def test_meta_error_classification_and_no_simulator_fallback(db_session: Session, code, body, expected):
    c = _campaign(db_session, suffix=f"e{code}")
    r = _dispatch(db_session, c.id, _meta(lambda req: httpx.Response(code, json=body)), settings=_meta_settings())
    assert r.delivery_status == expected
    assert r.is_simulated is False
    assert "tok_SECRET123" not in json.dumps(r.model_dump(mode="json"))
    from app.repositories.simulator_repository import SimulatorMessageRepository
    assert SimulatorMessageRepository(db_session).list_by_message_log_id(c.id) == []


def test_meta_read_timeout_is_delivery_unknown_and_blocks_blind_resend(db_session: Session):
    c = _campaign(db_session)

    def handler(req):
        raise httpx.ReadTimeout("timed out", request=req)

    r = _dispatch(db_session, c.id, _meta(handler), settings=_meta_settings())
    assert r.delivery_status == "DELIVERY_UNKNOWN"
    r2 = _dispatch(db_session, c.id, _meta(lambda q: httpx.Response(200, json={"messages": [{"id": "x"}]})),
                   settings=_meta_settings())
    assert r2.dispatch_action == "REJECTED_NON_DISPATCHABLE_STATUS"


def test_meta_connect_error_retryable_then_retry_requires_flag(db_session: Session):
    c = _campaign(db_session)

    def bad(req):
        raise httpx.ConnectError("refused", request=req)

    assert _dispatch(db_session, c.id, _meta(bad), settings=_meta_settings()).delivery_status == "FAILED_RETRYABLE"
    ok = lambda q: httpx.Response(200, json={"messages": [{"id": "wamid.R"}]})
    assert _dispatch(db_session, c.id, _meta(ok), settings=_meta_settings()).dispatch_action == "REJECTED_RETRY_REQUIRES_EXPLICIT_FLAG"
    r = _dispatch(db_session, c.id, _meta(ok), settings=_meta_settings(), force_retry=True)
    assert r.delivery_status == "PROVIDER_ACCEPTED" and r.retry_count == 1


def test_missing_meta_credentials_not_configured_no_fallback(db_session: Session):
    s = _settings(whatsapp_provider="meta_cloud_api")
    provider = get_whatsapp_provider(settings=s)
    assert isinstance(provider, MetaCloudWhatsAppProvider)
    assert provider.validate_configuration().is_valid is False
    c = _campaign(db_session)
    r = _dispatch(db_session, c.id, provider, settings=s)
    assert r.provider_error_code == "PROVIDER_NOT_CONFIGURED" and not r.is_simulated


# ---- webhooks ---------------------------------------------------------------

def _sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _status_payload(wamid: str, status: str, ts: int = 1_700_000_000) -> dict:
    return {"entry": [{"changes": [{"value": {"statuses": [
        {"id": wamid, "status": status, "timestamp": str(ts), "recipient_id": "919876543210"}]}}]}]}


@pytest.fixture()
def wa_client(db_session, test_settings):
    from app.api.routes.webhooks import get_webhook_settings
    from app.db.session import get_db
    from app.main import create_app
    s = test_settings.model_copy(update={
        "whatsapp_app_secret": SecretStr("appsecret"),
        "whatsapp_webhook_verify_token": SecretStr("verify-me")})
    app = create_app(s)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_webhook_settings] = lambda: s
    with TestClient(app) as c:
        yield c


def test_webhook_challenge(wa_client):
    ok = wa_client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = wa_client.get("/api/v1/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def _post(client, payload, secret="appsecret", sig=True):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if sig:
        headers["X-Hub-Signature-256"] = _sig(secret, body)
    return client.post("/api/v1/webhooks/whatsapp", content=body, headers=headers)


def _accepted(db_session, wamid="wamid.W1", suffix="w"):
    c = _campaign(db_session, suffix=suffix)
    c.delivery_status = S.PROVIDER_ACCEPTED
    c.provider = MessagingProvider.META_CLOUD_API
    c.provider_message_id = wamid
    db_session.commit()
    return c


def test_webhook_rejects_bad_or_missing_signature(wa_client, db_session):
    c = _accepted(db_session)
    assert _post(wa_client, _status_payload("wamid.W1", "delivered"), secret="wrong").status_code == 401
    assert _post(wa_client, _status_payload("wamid.W1", "delivered"), sig=False).status_code == 401
    db_session.refresh(c)
    assert c.delivery_status == "PROVIDER_ACCEPTED"


def test_webhook_progression_dedup_and_no_regression(wa_client, db_session):
    c = _accepted(db_session)
    r = _post(wa_client, _status_payload("wamid.W1", "delivered", 1_700_000_100))
    assert r.status_code == 200 and r.json()["campaigns_updated"] == 1
    db_session.refresh(c)
    assert c.delivery_status == "DELIVERED" and c.delivered_at is not None
    dup = _post(wa_client, _status_payload("wamid.W1", "delivered", 1_700_000_100))
    assert dup.json()["duplicates_ignored"] == 1
    late = _post(wa_client, _status_payload("wamid.W1", "sent", 1_700_000_050))
    assert late.json()["regressed_statuses_ignored"] == 1
    db_session.refresh(c)
    assert c.delivery_status == "DELIVERED"
    assert _post(wa_client, _status_payload("wamid.W1", "read", 1_700_000_200)).json()["campaigns_updated"] == 1
    db_session.refresh(c)
    assert c.delivery_status == "READ" and c.read_at is not None
    _post(wa_client, _status_payload("wamid.W1", "delivered", 1_700_000_300))
    db_session.refresh(c)
    assert c.delivery_status == "READ"


def test_webhook_unknown_id_ignored_and_customer_messages_not_trusted(wa_client, db_session):
    c = _accepted(db_session)
    assert _post(wa_client, _status_payload("wamid.UNKNOWN", "delivered")).json()["unknown_messages_ignored"] == 1
    inbound = {"entry": [{"changes": [{"value": {"messages": [{"id": "wamid.W1", "status": "delivered", "text": {"body": "delivered"}}]}}]}]}
    assert _post(wa_client, inbound).json()["status"] == "IGNORED_NO_STATUS_EVENTS"
    db_session.refresh(c)
    assert c.delivery_status == "PROVIDER_ACCEPTED"


def test_simulated_campaign_never_marked_delivered_by_webhook(wa_client, db_session):
    c = _campaign(db_session)
    _dispatch(db_session, c.id, SimulatorWhatsAppProvider(settings=_settings()))
    db_session.refresh(c)
    r = _post(wa_client, _status_payload(c.provider_message_id, "delivered"))
    assert r.json()["regressed_statuses_ignored"] == 1
    db_session.refresh(c)
    assert c.delivery_status == "SENT_SIMULATED"


def test_signature_fails_closed_in_production_without_secret():
    s = _settings(environment="staging")
    assert verify_meta_webhook_signature(raw_body=b"{}", signature_header=None, settings=s) is False


# ---- simulator interaction + API ------------------------------------------------

def test_simulator_interactions_and_api(client: TestClient, db_session: Session):
    c = _campaign(db_session)
    r = client.post(f"/api/v1/campaigns/{c.id}/dispatch")
    assert r.status_code == 200 and r.json()["delivery_status"] == "SENT_SIMULATED"
    for action, code in [("VIEW_RECOMMENDATION", "TGT-1"), ("INTERESTED", "TGT-1"),
                         ("NOT_INTERESTED", "TGT-2"), ("VIEW_ALL_RECOMMENDATIONS", None)]:
        resp = client.post(f"/api/v1/simulator/campaigns/{c.id}/interact",
                           json={"action_type": action, "product_code": code})
        assert resp.status_code == 200, resp.text
    msgs = client.get(f"/api/v1/simulator/campaigns/{c.id}/messages").json()
    assert len(msgs) == 1 + 4 * 2
    assert [m["id"] for m in msgs] == sorted(m["id"] for m in msgs)
    assert all(m["is_simulated"] for m in msgs)
    assert client.post(f"/api/v1/simulator/campaigns/{c.id}/interact",
                       json={"action_type": "INTERESTED", "product_code": "NOPE"}).status_code == 409
    detail = client.get(f"/api/v1/campaigns/{c.id}").json()
    assert detail["delivery_status"] == "SENT_SIMULATED" and len(detail["recommendation_cards"]) == 2
    assert client.get("/simulator").status_code == 200
    assert client.get("/api/v1/campaigns/provider-status").json()["active_provider"] == "WHATSAPP_SIMULATOR"


def test_suppressed_campaign_cannot_interact(client: TestClient, db_session: Session):
    c = _campaign(db_session, status=S.SUPPRESSED, skus=())
    assert client.post(f"/api/v1/simulator/campaigns/{c.id}/interact",
                       json={"action_type": "VIEW_ALL_RECOMMENDATIONS"}).status_code == 409


def test_demo_seed_is_idempotent_and_produces_simulated_conversation(client: TestClient):
    r1 = client.post("/api/v1/simulator/demo-seed")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["campaigns_dispatched_simulated"] and body["campaigns_suppressed"]
    r2 = client.post("/api/v1/simulator/demo-seed")
    assert r2.status_code == 200
    campaigns = client.get("/api/v1/campaigns").json()
    assert len(campaigns) == 3


def test_concurrent_dispatch_sends_exactly_once(db_session: Session, test_engine):
    if test_engine.dialect.name != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL.")
    c = _campaign(db_session, suffix="conc")
    cid = c.id
    calls: list[int] = []
    lock = threading.Lock()

    def handler(req):
        with lock:
            calls.append(1)
        return httpx.Response(200, json={"messages": [{"id": "wamid.CONC"}]})

    results: list[str] = []

    def worker():
        sess = sessionmaker(bind=test_engine)()
        try:
            r = _dispatch(sess, cid, _meta(handler), settings=_meta_settings())
            results.append(r.dispatch_action)
        finally:
            sess.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1
    assert results.count("DISPATCHED_PROVIDER_ACCEPTED") == 1
