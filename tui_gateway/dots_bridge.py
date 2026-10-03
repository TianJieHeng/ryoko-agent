"""Exact native executor requests; intentionally absent from reconnect replay."""
from . import server_requests


def send_native_request(session_id, params, *, timeout, transport, inspect):
    if inspect:
        return server_requests.send("dots.effect.inspect", session_id, params,
            timeout=timeout, expected_transport=transport)
    return server_requests.send("dots.effect.dispatch", session_id, params,
        timeout=timeout, expected_transport=transport)


def read_native_page(session_id, params, *, timeout, transport):
    return server_requests.send("dots.page.read", session_id, params,
        timeout=timeout, expected_transport=transport)


def observe_native_computer(session_id, params, *, timeout, transport):
    return server_requests.send("dots.computer.observe", session_id, params,
        timeout=timeout, expected_transport=transport)


def request_native_approval(session_id, params, *, timeout, transport):
    return server_requests.send("dots.approval", session_id, params,
        timeout=timeout, expected_transport=transport)
