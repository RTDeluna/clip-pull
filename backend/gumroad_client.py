import httpx

from license_config import GUMROAD_PRODUCT_ID, GUMROAD_PRODUCT_PERMALINK, GUMROAD_VERIFY_URL

REQUEST_TIMEOUT_SECONDS = 15.0


class GumroadClientError(Exception):
    """Raised when Gumroad can't be reached at all (a network-level failure),
    as distinct from Gumroad responding that a key is invalid. Lets callers
    tell 'Gumroad said no' apart from 'couldn't reach Gumroad.'"""


def verify_license(license_key: str, *, increment_uses_count: bool = True) -> dict:
    """POST the key to Gumroad's public license-verify endpoint (no auth
    header required -- it's meant to be called from the buyer's own app) and
    return the parsed JSON body."""
    data = {
        "product_permalink": GUMROAD_PRODUCT_PERMALINK,
        "license_key": license_key,
        "increment_uses_count": str(increment_uses_count).lower(),
    }
    # Sent alongside product_permalink, not instead of it -- see
    # license_config.GUMROAD_PRODUCT_ID for why both are included.
    if GUMROAD_PRODUCT_ID:
        data["product_id"] = GUMROAD_PRODUCT_ID
    try:
        response = httpx.post(
            GUMROAD_VERIFY_URL,
            data=data,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise GumroadClientError("Couldn't reach Gumroad to verify the license.") from exc

    # Gumroad returns 404 (with {"success": false, ...}) for an invalid key,
    # not just 200, so the status code isn't a reliable signal the way it is
    # for other APIs -- parse the body regardless and let the caller inspect
    # `success`. Fall back to a synthetic failure if the body isn't JSON.
    try:
        return response.json()
    except ValueError:
        return {"success": False, "message": "Unexpected response from Gumroad."}
