"""What the app shows for a product: result cards for the search stream and the product page."""

from typing import Any, Literal

from pydantic import Field

from agent.models import Cod, Price, Product, Returns
from agent.rank import RankedResult, Tick, rupees
from agent.sources.shopify import SHIPPING_NOTE
from data_prep.simulate import size_names


class ResultCard(RankedResult):
    brand: str | None = None
    thumb: str | None = None
    photo: str | None = Field(default=None, description="The gemini-size photo, about 1,000 px")
    original: str | None = None
    price: Price | None = None
    delivery_fee: int = 0
    cod: Cod | None = None
    returns: Returns | None = None
    rating_avg: float | None = None
    rating_count: int = 0
    demo_data: bool = True
    sizes_in_stock: list[str] = Field(default_factory=list)
    source: Literal["catalog", "shopify"] = "catalog"
    store: str | None = Field(default=None, description="The live store's name")
    product_url: str | None = Field(default=None, description="The product's page on the live store")
    shipping_note: str | None = Field(default=None, description="For live stores, where shipping and delivery are shown")


def result_card(result: RankedResult, product: Product) -> ResultCard:
    images = product.images
    live = product.source == "shopify"
    return ResultCard(
        **(result.model_dump() | ({"reason": live_reason(result)} if live else {})),
        brand=product.brand,
        thumb=images.thumb if images else None,
        photo=images.gemini if images else None,
        original=images.original if images else None,
        price=product.price,
        delivery_fee=product.delivery_fee,
        cod=product.cod,
        returns=product.returns,
        rating_avg=product.rating_avg,
        rating_count=product.rating_count,
        demo_data=product.demo_data,
        sizes_in_stock=sizes_in_stock(product),
        source=product.source,
        store=product.store,
        product_url=product.product_url,
        shipping_note=SHIPPING_NOTE if live else None,
    )


def live_reason(result: RankedResult) -> str:
    """A live store's total has no shipping in it, so the reason line mustn't say "delivered"."""
    price = rupees(result.total_price)
    return result.reason.replace(f"{price} delivered", f"{price} + shipping")


def sizes_in_stock(product: Product) -> list[str]:
    """Sizes with stock, in size-chart order (Firestore maps come back in any order); a live store's as it lists them."""
    in_stock = [size for size, count in product.sizes.items() if count > 0]
    if product.source == "shopify":
        return in_stock
    chart = size_names(product.group, product.gender)
    return sorted(in_stock, key=lambda size: (chart.index(size) if size in chart else len(chart), size))


def product_view(product: Product, ticks: list[Tick] | None = None) -> dict[str, Any]:
    """The product with Firestore field names and every field present (null when missing), without the embedding."""
    view = product.model_dump(mode="json", by_alias=True, exclude={"embedding"})
    view["sizes_in_stock"] = sizes_in_stock(product)
    view["shipping_note"] = SHIPPING_NOTE if product.source == "shopify" else None
    if ticks is not None:
        view["ticks"] = [tick.model_dump(mode="json") for tick in ticks]
    return view
