import os

# The real Gumroad product doesn't exist yet, so this default is an
# intentional placeholder. The app owner will set the
# CLIP_PULL_GUMROAD_PERMALINK env var to the real product slug once the
# product is created -- do not treat the placeholder value as valid.
GUMROAD_PRODUCT_PERMALINK = os.environ.get(
    "CLIP_PULL_GUMROAD_PERMALINK", "clippull-pro-placeholder"
)
# Gumroad's license-verify API accepts product_permalink for older products,
# but products created from ~2023 onward reportedly require product_id
# instead (shown on the product's page once license keys are enabled).
# Optional and unset by default -- gumroad_client sends it alongside
# product_permalink only when this is configured, so verification keeps
# working either way without needing to know for certain which one Gumroad
# actually requires for this product.
GUMROAD_PRODUCT_ID = os.environ.get("CLIP_PULL_GUMROAD_PRODUCT_ID")
GUMROAD_VERIFY_URL = "https://api.gumroad.com/v2/licenses/verify"

# Local-only escape hatch for testing the Pro activation flow before a real
# Gumroad product exists. None unless a developer explicitly sets this env
# var -- never given a default value here, so a packaged/production build
# that doesn't set it (which it never should) gets no bypass at all.
DEV_LICENSE_KEY = os.environ.get("CLIP_PULL_DEV_LICENSE_KEY")
