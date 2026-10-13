import httpx
import pytest

from agent.models import MustHave, ShoppingBrief
from agent.sources import shopify
from agent.sources.shopify import Catalog, CatalogError
from tests.fixtures.app.fakes import ShopifyServer, search_products, shopify_fixture

PUNE = "411038"
LILIUM = "gid://shopify/p/3V4Xryr32ubFelobSCmrGa?shop=84346503471"
JANASYA = "gid://shopify/p/6YCYATAVKvMyCEP24NCeIF?shop=60712091818"
G230 = "gid://shopify/p/DmptlUPpvXkzRbz0yzmrv?shop=67284107316"
LILIUM_PHOTO = "https://cdn.shopify.com/s/files/1/0843/4650/3471/files/13_3a80946e-a95f-41a5-b49f-b48729c360ec.webp"
COTTON = MustHave(id="m1", attribute="fabric", rule="is", value="cotton")
POCKETS = MustHave(id="m2", attribute="pockets", rule="present", value=True)
NOT_SHEER = MustHave(id="m3", attribute="sheerness", rule="is_not", value="see-through")
COD = MustHave(id="r1", attribute="cod", rule="present", value=True, from_rule=True)


def kurta_brief(**fields) -> ShoppingBrief:
    base = {"category": "Kurtas", "gender": "women", "must_haves": [COTTON, POCKETS], "budget_max_inr": 1500,
            "pincode": PUNE}
    return ShoppingBrief(**(base | fields))


def sneaker_brief() -> ShoppingBrief:
    return ShoppingBrief(
        category="Casual Shoes", gender="men", budget_max_inr=4000, pincode="560001", size="UK 9",
        must_haves=[MustHave(id="m1", attribute="type", rule="is", value="sneakers"),
                    MustHave(id="m2", attribute="colour", rule="is", value="white"),
                    MustHave(id="m3", attribute="logo_visible", rule="is", value="none")],
    )


def watch_brief() -> ShoppingBrief:
    return ShoppingBrief(
        category="Watches", gender="men", budget_max_inr=6000, pincode="110001",
        must_haves=[MustHave(id="m1", attribute="display", rule="is", value="analog"),
                    MustHave(id="m2", attribute="dial_colour", rule="is", value="black"),
                    MustHave(id="m3", attribute="strap_material", rule="is", value="steel")],
    )


# ---------- Building requests ----------


def test_the_search_asks_for_indian_stores_that_ship_to_the_pincode_within_the_raised_budget_in_paise():
    server = ShopifyServer()
    server.catalog().search(kurta_brief(size="M"), "Office ke liye ladies cotton kurta chahiye, pockets zaroor")
    [body] = server.bodies
    assert body["jsonrpc"] == "2.0" and body["method"] == "tools/call" and body["params"]["name"] == "search_catalog"
    arguments = body["params"]["arguments"]
    assert arguments["meta"] == {"ucp-agent": {"profile": shopify.EXAMPLE_PROFILE}}
    assert arguments["catalog"] == {
        "query": "women's cotton kurta with pockets",
        "context": {"address_country": "IN", "currency": "INR", "postal_code": PUNE,
                    "intent": "Office ke liye ladies cotton kurta chahiye, pockets zaroor"},
        "filters": {
            "ships_to": {"country": "IN", "postal_code": PUNE},
            "ships_from": [{"country": "IN"}],
            "price": {"min": 0, "max": 180000},
            "attributes": [{"name": "Size", "values": ["M"]}],
        },
        "pagination": {"limit": 40},
    }


def test_the_query_uses_plain_words_and_leaves_out_negatives_and_shop_details():
    assert shopify.query_words(sneaker_brief()) == "men's white sneakers"
    assert shopify.query_words(watch_brief()) == "men's analog display black dial steel strap watch"
    assert shopify.query_words(kurta_brief(must_haves=[COTTON, NOT_SHEER, COD])) == "women's cotton kurta"
    assert shopify.query_words(kurta_brief(gender=None, must_haves=[])) == "kurta"


def test_sizes_are_written_the_way_shopifys_size_filter_takes_them():
    assert shopify.size_values(sneaker_brief()) == ["9", "UK 9"]
    assert shopify.size_values(kurta_brief(size="XXL")) == ["XXL", "2XL"]
    assert shopify.size_values(kurta_brief(size="M")) == ["M"]
    assert shopify.size_values(watch_brief().model_copy(update={"size": "M"})) == []
    request = shopify.search_catalog(watch_brief(), "", limit=80)
    assert "attributes" not in request["filters"] and request["pagination"] == {"limit": 50}
    assert "intent" not in request["context"]


def test_get_product_asks_for_the_one_store_we_checked_with_the_shoppers_pincode_and_selection():
    assert shopify.get_product_catalog(G230, PUNE, [{"name": "Size", "label": "M"}]) == {
        "id": "gid://shopify/p/DmptlUPpvXkzRbz0yzmrv",
        "context": {"address_country": "IN", "currency": "INR", "postal_code": PUNE},
        "filters": {"ships_to": {"country": "IN", "postal_code": PUNE}, "shops": ["gid://shopify/Shop/67284107316"]},
        "selected": [{"name": "Size", "label": "M"}],
    }
    assert "shops" not in shopify.get_product_catalog("gid://shopify/p/X", None)["filters"]


def test_our_id_for_a_live_product_names_the_store_offering_it():
    assert shopify.live_id("gid://shopify/p/X", "gid://shopify/Shop/123") == "gid://shopify/p/X?shop=123"
    assert shopify.live_id("gid://shopify/p/X", None) == "gid://shopify/p/X"
    assert shopify.split_id("gid://shopify/p/X?shop=123") == ("gid://shopify/p/X", "gid://shopify/Shop/123")
    assert shopify.split_id("gid://shopify/p/X?shop=../x") == ("gid://shopify/p/X", None)
    server = ShopifyServer()
    product = server.catalog().get_product(G230, kurta_brief())
    assert product.id == G230
    assert server.calls("get_product")[0]["catalog"]["filters"]["shops"] == ["gid://shopify/Shop/67284107316"]


def test_the_profile_url_is_ours_when_deployed_and_shopifys_example_locally():
    assert shopify.profile_url({"UCP_AGENT_PROFILE_URL": "https://api.example/.well-known/ucp"}) == (
        "https://api.example/.well-known/ucp"
    )
    assert shopify.profile_url({"K_SERVICE": "ecommai-agent"}) == shopify.DEPLOYED_API_URL + "/.well-known/ucp"
    assert shopify.profile_url({}) == shopify.EXAMPLE_PROFILE


def test_our_agent_profile_declares_catalog_search_lookup_and_shopifys_global_extension():
    profile = shopify.agent_profile()["ucp"]
    assert profile["version"] == "2026-08-25"
    assert profile["services"]["dev.ucp.shopping"][0]["transport"] == "mcp"
    capabilities = profile["capabilities"]
    assert set(capabilities) == {"dev.ucp.shopping.catalog.search", "dev.ucp.shopping.catalog.lookup",
                                 "dev.shopify.catalog.global"}
    [extension] = capabilities["dev.shopify.catalog.global"]
    assert extension["version"] == "2026-08-25"
    assert set(extension["extends"]) == {"dev.ucp.shopping.catalog.search", "dev.ucp.shopping.catalog.lookup"}
    assert all(entry["version"] == "2026-08-25" for entries in capabilities.values() for entry in entries)


# ---------- From Shopify's products to ours ----------


def test_search_results_become_products_and_bad_ones_are_left_out():
    products = ShopifyServer().catalog().search(kurta_brief(), "")
    assert [p.id for p in products] == [G230, LILIUM, JANASYA]

    lilium = products[1]
    assert lilium.source == "shopify" and lilium.demo_data is False and lilium.reviews == []
    assert lilium.title == "Olive Moss Cotton A-Line Kurti with Pockets | LS1101"
    assert lilium.description == (
        "Crafted from breathable cotton fabric, this kurti features 3/4 sleeves and functional side pockets & a soft "
        "lining. SKU: LS1101"
    )
    assert lilium.price.model_dump() == {"mrp": 1299, "selling": 1299, "demo": False}
    assert lilium.sizes == {"XS-32": 1}
    assert lilium.images.original == LILIUM_PHOTO
    assert lilium.images.gemini == LILIUM_PHOTO + "?width=1000" and lilium.images.thumb == LILIUM_PHOTO + "?width=300"
    assert lilium.store == "LiliumByShrivha"
    assert lilium.product_url.startswith("https://liliumbyshrivha.com/products/olive-moss-cotton-a-line-kurti")
    assert "utm_source=shopify" in lilium.product_url
    assert lilium.checkout_url == (
        "https://liliumbyshrivha.com/cart/58252983173423:1?_gsid=YVyJ6Z1hFcza&utm_source=shopify&utm_medium=catalog"
    )
    assert (lilium.article_type, lilium.group, lilium.gender) == ("Kurtas", "tops", "women")
    assert (lilium.master_category, lilium.sub_category) == ("Apparel", "Topwear")
    assert lilium.rating_avg is None and lilium.rating_count == 0 and lilium.brand is None

    janasya = products[2]
    assert janasya.price.model_dump() == {"mrp": 2059, "selling": 799, "demo": False}
    assert products[0].catalogue_details["Specs"].startswith("Material: 100% Cotton")


def test_html_is_reduced_to_plain_text():
    markup = "<style>p{color:red}</style><p>Pure&nbsp;cotton &amp; lined</p><table><tr><td>M</td><td>38</td></tr></table>"
    assert shopify.plain_text(markup) == "Pure cotton & lined M 38"
    assert shopify.plain_text(None) == ""


def test_products_without_sizes_come_in_one_size():
    raw = next(p for p in search_products() if p["id"] == shopify.split_id(JANASYA)[0])
    assert shopify.to_product(raw, watch_brief()).sizes == {"One size": 1}


def test_get_product_gives_every_size_with_its_availability_in_chart_order():
    product = ShopifyServer().catalog().get_product(G230, kurta_brief())
    assert product.sizes == {"XS": 0, "S": 0, "M": 1, "L": 1, "XL": 1, "2XL": 1}
    assert product.store == "Swapna Weddings" and product.price.selling == 649


def test_one_bad_product_never_fails_the_search():
    products = search_products()
    products[0]["variants"] = [{**products[0]["variants"][0], "availability": {"available": False}}]
    products[1]["title"] = "  "
    products[2]["variants"][0]["price"] = {"amount": "free", "currency": "INR"}
    products[2]["price_range"] = None
    found = shopify.products_from([*products, "not a product", {"id": "gid://other/p/1"}], kurta_brief())
    assert [p.id for p in found] == []


@pytest.mark.parametrize(
    "size_label, wanted, same",
    [
        ("M", "M", True), ("Medium (M)", "M", True), ("M-36", "M", True), ("XS-32", "S", False),
        ("Extra small (XS)", "S", False), ("2XL-42", "XL", False), ("2XL", "XXL", True), ("XXXL", "3XL", True),
        ("9 UK", "UK 9", True), ("UK9", "UK 9", True), ("9", "UK 9", True), ("8.5 US", "UK 9", False),
        ("32", "32", True),
    ],
)
def test_store_size_labels_match_the_shoppers_size(size_label, wanted, same):
    assert shopify.same_size(size_label, wanted) is same


def test_store_sizes_sort_in_chart_order():
    labels = ["2XL-42", "3XL-44", "L-38", "M-36", "S-34", "XL-40", "XS-32"]
    assert sorted(labels, key=shopify.size_rank) == ["XS-32", "S-34", "M-36", "L-38", "XL-40", "2XL-42", "3XL-44"]
    assert sorted(["10", "8", "UK 9"], key=shopify.size_rank) == ["8", "UK 9", "10"]


def test_smaller_photos_come_from_shopifys_cdn_and_other_links_stay_as_they_are():
    assert shopify.with_width("https://cdn.shopify.com/a.jpg?v=2&width=50", 300) == "https://cdn.shopify.com/a.jpg?v=2&width=300"
    assert shopify.with_width("https://store.in/cdn/shop/files/a.jpg", 300) == "https://store.in/cdn/shop/files/a.jpg?width=300"
    assert shopify.with_width("https://images.example.com/a.jpg", 300) == "https://images.example.com/a.jpg"


# ---------- Errors and retries ----------


def test_rate_limits_and_server_errors_are_retried_then_reported():
    waits = []
    server = ShopifyServer(fail=[429, 503])
    catalog = Catalog(profile=shopify.EXAMPLE_PROFILE, transport=httpx.MockTransport(server.handle), sleep=waits.append)
    assert len(catalog.search(kurta_brief(), "")) == 3
    assert len(server.bodies) == 3 and len(waits) == 2

    failing = ShopifyServer(fail=[503, 502, 504])
    with pytest.raises(CatalogError) as raised:
        failing.catalog().search(kurta_brief(), "")
    assert raised.value.status == 504 and len(failing.bodies) == 3


def test_shopifys_retry_after_sets_the_wait():
    waits = []
    limited = {"jsonrpc": "2.0", "id": 0, "error": {"code": -32000, "message": "Rate limit exceeded",
                                                     "data": {"retry_after": 1}}}
    answers = iter([httpx.Response(429, json=limited), httpx.Response(429, headers={"Retry-After": "99"}),
                    httpx.Response(429)])
    catalog = Catalog(transport=httpx.MockTransport(lambda request: next(answers)), sleep=waits.append)
    with pytest.raises(CatalogError) as raised:
        catalog.call("search_catalog", {})
    assert waits == [1.0, shopify.MAX_DELAY_S] and raised.value.status == 429


def test_timeouts_are_retried():
    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"products": []}}})

    catalog = Catalog(transport=httpx.MockTransport(handle), sleep=lambda seconds: None)
    assert catalog.search(kurta_brief(), "") == [] and len(calls) == 2


def test_client_errors_and_tool_errors_are_not_retried():
    server = ShopifyServer(fail=[400])
    with pytest.raises(CatalogError) as raised:
        server.catalog().search(kurta_brief(), "")
    assert raised.value.status == 400 and len(server.bodies) == 1

    for body in ({"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid profile"}},
                 {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [{"type": "text", "text": "bad"}]}},
                 {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}):
        calls = []
        catalog = Catalog(transport=httpx.MockTransport(lambda request, b=body: calls.append(1) or httpx.Response(200, json=b)))
        with pytest.raises(CatalogError):
            catalog.call("search_catalog", {})
        assert len(calls) == 1


def test_a_refused_size_filter_falls_back_to_searching_without_it():
    server = ShopifyServer(fail=[422])
    assert len(server.catalog().search(kurta_brief(size="M"), "")) == 3
    first, second = server.calls("search_catalog")
    assert "attributes" in first["catalog"]["filters"] and "attributes" not in second["catalog"]["filters"]


def test_an_unknown_product_is_a_404():
    with pytest.raises(CatalogError) as raised:
        ShopifyServer().catalog().get_product("gid://shopify/p/Gone", kurta_brief())
    assert raised.value.status == 404


# ---------- Checkout links ----------


def test_checkout_goes_to_the_stores_own_checkout_for_the_size_in_the_colour_shown():
    server = ShopifyServer()
    catalog = server.catalog()
    assert catalog.checkout_link(G230, PUNE, "L") == (
        "https://www.swapnawedding.com/cart/46385104748596:1?_gsid=M1VPnRbrLHUB&utm_source=shopify&utm_medium=catalog"
    )
    assert catalog.checkout_link(G230, PUNE, "M") == (
        "https://www.swapnawedding.com/cart/46385104748002:1?_gsid=M1VPnRbrLHUB&utm_source=shopify&utm_medium=catalog"
    )
    assert server.calls("get_product")[-1]["catalog"]["selected"] == [{"name": "Size", "label": "M"}]


def test_without_a_size_checkout_opens_the_stores_product_page_to_pick_one():
    link = ShopifyServer().catalog().checkout_link(G230, PUNE, None)
    assert link.startswith("https://www.swapnawedding.com/products/womens-kurti-g230?variant=46385104748596")


def test_a_one_size_product_goes_straight_to_checkout_and_a_size_not_available_is_refused():
    products = search_products()
    janasya = next(p for p in products if p["id"] == shopify.split_id(JANASYA)[0])
    janasya["options"] = [o for o in janasya["options"] if o["name"] != "Size"]
    janasya["variants"][0]["options"] = [{"name": "Color", "label": "Mustard"}]
    catalog = ShopifyServer(products).catalog()
    assert catalog.checkout_link(JANASYA, PUNE, "One size").startswith("https://janasya.com/cart/47683356721322:1")
    assert catalog.checkout_link(JANASYA, PUNE, None).startswith("https://janasya.com/cart/47683356721322:1")
    with pytest.raises(shopify.SizeUnavailable):
        catalog.checkout_link(G230, PUNE, "XS")


def test_a_product_without_links_is_bad():
    raw = shopify_fixture("get_product_g230.json")["result"]["structuredContent"]["product"]
    for variant in raw["variants"]:
        variant.pop("checkout_url")
        variant.pop("url")
    server = ShopifyServer()
    server.product = lambda catalog: {"product": raw}
    with pytest.raises(shopify.BadProduct):
        server.catalog().checkout_link(G230, PUNE, "L")
