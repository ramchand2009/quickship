"""Small, fixed-host client for India Post's UAT endpoints."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://test.cept.gov.in/beextcustomer"
MAX_RESPONSE_BYTES = 128 * 1024


class PostOfficeError(Exception):
    def __init__(self, message, http_status=None, payload=None):
        super().__init__(message)
        self.http_status = http_status
        self.payload = payload


def _decode_response(response):
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise PostOfficeError("India Post response exceeded the 128 KB sandbox limit.")
    content_type = response.headers.get("Content-Type", "")
    if "json" in content_type.lower():
        try:
            return json.loads(body), content_type
        except ValueError as exc:
            raise PostOfficeError("India Post returned invalid JSON.", response.status) from exc
    return body.decode("utf-8", errors="replace"), content_type


def _request(method, path, *, token=None, payload=None, query=None):
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        raise PostOfficeError("Invalid India Post sandbox path.")
    url = BASE_URL + path
    if query:
        url += "?" + urlencode(query)
    headers = {"Accept": "application/json, application/xml, text/xml"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            data, content_type = _decode_response(response)
            return response.status, data, content_type
    except HTTPError as exc:
        data, _ = _decode_response(exc)
        raise PostOfficeError("India Post rejected the request.", exc.code, data) from exc
    except (URLError, TimeoutError) as exc:
        raise PostOfficeError("Could not reach the India Post sandbox.") from exc


def access_token():
    username = os.environ.get("INDIA_POST_SANDBOX_USERNAME", "").strip()
    password = os.environ.get("INDIA_POST_SANDBOX_PASSWORD", "")
    if not username or not password:
        raise PostOfficeError("Configure INDIA_POST_SANDBOX_USERNAME and INDIA_POST_SANDBOX_PASSWORD.")
    status, payload, _ = _request("POST", "/v1/access/login", payload={"username": username, "password": password})
    data = payload.get("data") if isinstance(payload, dict) else None
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise PostOfficeError("Token missing from India Post login response.", status, payload)
    return token, status, payload


def tariff(token, values):
    service = values["service"]
    path = "/v1/speed-post/tariffs" if service == "SP" else "/v1/business-parcel-tariff/calculate"
    query = {
        "product-code": service,
        "weight": values["weight"],
        "source-pincode": values["source_pincode"],
        "destination-pincode": values["destination_pincode"],
        "length": values.get("length", 0),
        "width": values.get("width", 0),
        "height": values.get("height", 0),
    }
    return _request("GET", path, token=token, query=query)


def booking(token, customer_id, payload):
    return _request("POST", f"/process-articles/{customer_id}", token=token, payload=payload)


def tracking(token, article_numbers):
    return _request("POST", "/v1/tracking/bulk", token=token, payload={"bulk": article_numbers})


def event_download(token, method="GET", payload=None):
    path = os.environ.get("INDIA_POST_SANDBOX_EVENT_PATH", "/event/download").strip()
    if method not in ("GET", "POST"):
        raise PostOfficeError("Event method must be GET or POST.")
    return _request(method, path, token=token, payload=payload if method == "POST" else None)
