"""Abandoning the browser must not confirm an unpaid order or hide a paid one."""
import pytest

from app.services.webpay import WebpayError
from test_pagos import PAY, Pagos, PLAN_USUARIO


def test_cancel_unfinished_attempt_is_idempotent_and_new_payment_has_a_new_token():
    p = Pagos()
    _, headers = p.athlete()
    first = p.started(headers, PLAN_USUARIO.id)
    checkout = first['checkout_url'].removeprefix('http://svc.test')
    assert p.client.get(checkout).status_code == 200
    for _ in range(2):
        response = p.client.post(PAY + '/webpay/cancel', headers=headers, json={'token_ws': first['token_ws']})
        assert response.status_code == 200
        assert response.json()['status'] == 'CANCELLED'
    assert p.subscription(headers).status_code == 404
    assert p.client.get(checkout).status_code == 409
    second = p.started(headers, PLAN_USUARIO.id)
    assert second['token_ws'] != first['token_ws']
    assert second['buy_order'] != first['buy_order']
    assert p.client.get(second['checkout_url'].removeprefix('http://svc.test')).status_code == 200


def test_cancel_recovers_an_already_authorized_payment_without_losing_or_extending_it():
    p = Pagos()
    _, headers = p.athlete()
    started = p.started(headers, PLAN_USUARIO.id)
    p.webpay.decide(started['token_ws'], 'aprobar')
    # Transbank completed it, but the app has not received the callback yet.
    p.webpay.commit_transaction(started['token_ws'])
    response = p.client.post(PAY + '/webpay/cancel', headers=headers, json={'token_ws': started['token_ws']})
    assert response.status_code == 200
    assert response.json()['status'] == 'APPROVED'
    end = response.json()['suscripcion']['fecha_fin']
    again = p.client.post(PAY + '/webpay/cancel', headers=headers, json={'token_ws': started['token_ws']})
    assert again.json()['suscripcion']['fecha_fin'] == end
    assert p.subscription(headers).json()['fecha_fin'] == end


def test_cancellation_requires_the_owner_session():
    p = Pagos()
    _, owner = p.athlete()
    _, other = p.athlete()
    started = p.started(owner, PLAN_USUARIO.id)
    for headers, expected in [(None, 401), (other, 404)]:
        response = p.client.post(PAY + '/webpay/cancel', headers=headers, json={'token_ws': started['token_ws']})
        assert response.status_code == expected
    assert p.repository.get_transaccion(token_ws=started['token_ws']).status == 'PENDING'


def test_provider_failure_keeps_pending_and_never_commits(monkeypatch):
    p = Pagos()
    _, headers = p.athlete()
    started = p.started(headers, PLAN_USUARIO.id)
    def unavailable(_):
        raise WebpayError('Unavailable')
    def never_commit(_):
        pytest.fail('Cancelling an unfinished form must not commit a charge')
    monkeypatch.setattr(p.webpay, 'transaction_status', unavailable)
    monkeypatch.setattr(p.webpay, 'commit_transaction', never_commit)
    response = p.client.post(PAY + '/webpay/cancel', headers=headers, json={'token_ws': started['token_ws']})
    assert response.status_code == 502
    assert p.repository.get_transaccion(token_ws=started['token_ws']).status == 'PENDING'
    assert p.subscription(headers).status_code == 404


def test_an_approved_checkout_is_not_reopened():
    p = Pagos()
    _, headers = p.athlete()
    started, response = p.pay(headers, PLAN_USUARIO.id)
    assert response.status_code == 200
    assert p.client.get(started['checkout_url'].removeprefix('http://svc.test')).status_code == 409


def test_authorization_racing_cancellation_is_recovered_exactly_once(monkeypatch):
    p = Pagos()
    _, headers = p.athlete()
    started = p.started(headers, PLAN_USUARIO.id)
    p.webpay.decide(started['token_ws'], 'aprobar')
    original_commit = p.webpay.commit_transaction
    def concurrent_cancel(token):
        transaction = p.repository.get_transaccion(token_ws=token)
        p.repository.settle_transaccion(transaction.id, 'CANCELLED', None)
        return original_commit(token)
    monkeypatch.setattr(p.webpay, 'commit_transaction', concurrent_cancel)
    response = p.commit(headers, token_ws=started['token_ws'])
    assert response.status_code == 200
    end = response.json()['suscripcion']['fecha_fin']
    assert p.commit(headers, token_ws=started['token_ws']).json()['suscripcion']['fecha_fin'] == end


def test_late_callback_checks_provider_before_recovering_cancelled_attempt():
    p = Pagos()
    _, headers = p.athlete()
    started = p.started(headers, PLAN_USUARIO.id)
    p.client.post(PAY + '/webpay/cancel', headers=headers, json={'token_ws': started['token_ws']})
    p.webpay.decide(started['token_ws'], 'aprobar')
    p.webpay.commit_transaction(started['token_ws'])
    response = p.commit(headers, token_ws=started['token_ws'])
    assert response.status_code == 200
    assert response.json()['status'] == 'APPROVED'
