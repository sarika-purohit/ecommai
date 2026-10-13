// Live products shaped like the agent's Shopify results (found through UCP), and an "On other sites"
// list shaped like a Grounding with Google Search answer, for the mock API.
// Everything here stands in for server data, so it is not in the strings file.

import type { Attribute, BoundingBox, Gender, Price, ProductGroup, Status, Tick, WebResult } from "@/lib/types";

export interface LiveProduct {
  /** A Shopify GID, as UCP gives it. */
  id: string;
  title: string;
  /** The Shopify vendor. */
  brand: string | null;
  store: string;
  product_url: string;
  /** The store's cart link; a size's variant id is added to it. */
  cart_url: string;
  variant_base: number;
  gender: Gender;
  masterCategory: string;
  subCategory: string;
  articleType: string;
  baseColour: string | null;
  usage: string | null;
  group: ProductGroup;
  description: string;
  /** What the careful check read from the store's photo and description. */
  attributes: Record<string, Attribute>;
  price: Price;
  sizes: Record<string, number>;
  /** The store's own photo on cdn.shopify.com. */
  photo: string;
  /** Where the careful check marked a detail on the store's photo. */
  boxes?: Partial<Record<string, BoundingBox>>;
  /** Words from the store's description that prove a detail. */
  listing?: Partial<Record<string, string>>;
}

const PLACEHOLDER_PHOTO = "https://cdn.shopify.com/s/files/1/0533/2089/files/placeholder-images-image_large.png";

/** agent/sources/shopify.py SHIPPING_NOTE, on every live card and product. */
export const SHIPPING_NOTE = "Shipping and delivery shown at the store's checkout";

function seen(value: string, note: string, confidence = 0.9): Attribute {
  return { value, confidence, note, source: "gemini" };
}

export const LIVE_PRODUCTS: LiveProduct[] = [
  {
    id: "gid://shopify/p/3V4Xryr32ubFelobSCmrGa",
    title: "Olive Leaf Print Cotton A-Line Kurta",
    brand: "Lilium",
    store: "Lilium",
    product_url: "https://lilium-in.myshopify.com/products/olive-leaf-print-cotton-a-line-kurta",
    cart_url: "https://lilium-in.myshopify.com/cart",
    variant_base: 47318265020571,
    gender: "women",
    masterCategory: "Apparel",
    subCategory: "Topwear",
    articleType: "Kurtas",
    baseColour: "Olive",
    usage: "Ethnic",
    group: "tops",
    description:
      "An everyday A-line kurta in pure cotton cambric with an all-over leaf print in soft olive. V-neck, three-quarter sleeves and two side pockets. Calf length. Model is 5'5\" and wears size S.",
    attributes: {
      sleeve_length: seen("three-quarter", "Sleeves end a little below the elbow.", 0.9),
      neckline: seen("v-neck", "The neckline dips into a clear V.", 0.95),
      pockets: seen("side", "The store’s description says it has two side pockets; they’re in the seams, so the photo can’t show them.", 0.85),
      pattern: seen("floral", "An all-over leaf print covers the kurta.", 0.9),
      print_intensity: seen("medium", "The leaf print is tone-on-tone but covers the whole kurta.", 0.8),
      fabric: seen("cotton", "The store’s description says pure cotton cambric.", 0.95),
      sheerness: seen("unknown", "Neither the photo nor the description says whether it’s see-through.", 0),
      fit: seen("a-line", "It flares out gently from the waist.", 0.9),
      length: seen("calf", "The hem falls to mid-calf on the model.", 0.9),
      closure: seen("none", "A pull-on kurta with no buttons or zip.", 0.85),
      usage: seen("ethnic", "An everyday Indian kurta.", 0.9),
    },
    price: { mrp: 1499, selling: 999, demo: false },
    sizes: { S: 4, M: 7, L: 0, XL: 3, XXL: 2 },
    photo: "https://cdn.shopify.com/s/files/1/0843/4650/3471/files/13_3a80946e-a95f-41a5-b49f-b48729c360ec.webp",
    boxes: {
      neckline: [228, 392, 312, 566],
      sleeve_length: [232, 262, 452, 372],
      pattern: [330, 318, 905, 702],
      print_intensity: [330, 318, 905, 702],
    },
    listing: {
      fabric: "An everyday A-line kurta in pure cotton cambric",
      pockets: "V-neck, three-quarter sleeves and two side pockets.",
    },
  },
  {
    id: "gid://shopify/p/7kQm2Lw9XcVb4TnRe8YdPz",
    title: "Men’s Indigo Hand Block Print Cotton Kurta",
    brand: "Jaipur Threads",
    store: "Jaipur Threads",
    product_url: "https://jaipur-threads.myshopify.com/products/indigo-hand-block-print-cotton-kurta",
    cart_url: "https://jaipur-threads.myshopify.com/cart",
    variant_base: 44102937583812,
    gender: "men",
    masterCategory: "Apparel",
    subCategory: "Topwear",
    articleType: "Kurtas",
    baseColour: "Navy Blue",
    usage: "Ethnic",
    group: "tops",
    description:
      "Hand block printed in Jaipur on 100% cotton. Mandarin collar, full sleeves, one chest pocket and side slits. Regular fit, knee length. Colour: Indigo blue. Natural dyes, so wash it separately.",
    attributes: {
      sleeve_length: seen("full", "The store’s description says full sleeves.", 0.9),
      neckline: seen("mandarin", "The store’s description says mandarin collar.", 0.9),
      pockets: seen("chest", "The store’s description says one chest pocket.", 0.9),
      pattern: seen("geometric", "The description says hand block printed; the motif isn’t named.", 0.6),
      fabric: seen("cotton", "The store’s description says 100% cotton.", 0.95),
      sheerness: seen("unknown", "The description doesn’t say whether it’s see-through.", 0),
      fit: seen("regular", "The store’s description says regular fit.", 0.9),
      length: seen("knee", "The store’s description says knee length.", 0.9),
      usage: seen("ethnic", "A traditional block-printed kurta.", 0.9),
    },
    price: { mrp: 1099, selling: 1099, demo: false },
    sizes: { S: 2, M: 5, L: 6, XL: 0, XXL: 1 },
    photo: PLACEHOLDER_PHOTO,
    listing: {
      sleeve_length: "Mandarin collar, full sleeves, one chest pocket and side slits.",
      neckline: "Mandarin collar, full sleeves, one chest pocket and side slits.",
      pockets: "Mandarin collar, full sleeves, one chest pocket and side slits.",
      fabric: "Hand block printed in Jaipur on 100% cotton.",
      fit: "Regular fit, knee length.",
      length: "Regular fit, knee length.",
      colour: "Colour: Indigo blue.",
    },
  },
  {
    id: "gid://shopify/p/1HsN6pFa3UjK0ZqWy5GtMc",
    title: "Classic White Canvas Sneakers",
    brand: "Stride Lab",
    store: "Stride Lab",
    product_url: "https://stride-lab.myshopify.com/products/classic-white-canvas-sneakers",
    cart_url: "https://stride-lab.myshopify.com/cart",
    variant_base: 45671230984127,
    gender: "unisex",
    masterCategory: "Footwear",
    subCategory: "Shoes",
    articleType: "Casual Shoes",
    baseColour: "White",
    usage: "Casual",
    group: "shoes",
    description:
      "Low-top sneakers with a white cotton canvas upper, cushioned insole and a vulcanised rubber sole. Runs true to size.",
    attributes: {
      type: seen("sneakers", "Low-top lace-up sneakers.", 0.9),
      upper_material: seen("canvas", "The store’s description says cotton canvas upper.", 0.95),
      sole_type: seen("rubber", "The store’s description says vulcanised rubber sole.", 0.9),
    },
    price: { mrp: 2499, selling: 1799, demo: false },
    sizes: { "UK 6": 3, "UK 7": 5, "UK 8": 0, "UK 9": 2, "UK 10": 1 },
    photo: PLACEHOLDER_PHOTO,
    listing: {
      upper_material: "Low-top sneakers with a white cotton canvas upper",
      sole_type: "cushioned insole and a vulcanised rubber sole.",
      colour: "a white cotton canvas upper",
    },
  },
];

/** Shopify's CDN resizes a photo when given a width. */
export function shopifyPhoto(photo: string, width?: number): string {
  if (!width) return photo;
  return `${photo}${photo.includes("?") ? "&" : "?"}width=${width}`;
}

// ---------- "On other sites" ----------

const WEB_PRICES: Record<string, [number, number, string | null]> = {
  Kurtas: [899, 749, "₹499 onwards"],
  Watches: [2499, 1899, "₹899 onwards"],
  "Casual Shoes": [1299, 1099, "₹699 onwards"],
};
const WEB_SIZES: Record<string, string[]> = { Kurtas: ["S", "M", "L", "XL"], "Casual Shoes": ["UK 7", "UK 8", "UK 9"] };

/** agent/web_search.py PAGE_NOTE, at the end of every note read from another site's page. */
const PAGE_NOTE = "Read from the store's page by Gemini (via Google)";

/** A must-have as the agent sends it to the page check: its id and the agent's label. */
export interface WebMustHave {
  id: string;
  label: string;
}

function pageTick(mustHave: WebMustHave, status: Status, quote: string | null, note: string): Tick {
  return {
    must_have_id: mustHave.id,
    label: mustHave.label,
    status,
    evidence: { type: "details", note: `${note} ${PAGE_NOTE}.`, quote, box_2d: null, agree: null, disagree: null },
  };
}

function overallOf(ticks: Tick[]): Status {
  if (ticks.some((tick) => tick.status === "fail")) return "fail";
  return ticks.every((tick) => tick.status === "pass") ? "pass" : "unclear";
}

/**
 * Three big Indian shops for the query, as the page check leaves them: a Myntra page read with every must-have
 * on it, a Flipkart page read that doesn't mention the last one, and an Amazon page Google couldn't read.
 */
export function webResultsFor(query: string, category: string, mustHaves: WebMustHave[], photo: string | null): WebResult[] {
  const [myntraPrice, flipkartPrice, amazonPrice] = WEB_PRICES[category] ?? [999, 899, null];
  const words = query.replace(/[₹,]/g, "").replace(/\s+/g, " ").trim();
  const title = words.replace(/\b\w/g, (letter) => letter.toUpperCase());
  const passing = mustHaves.map((m) => pageTick(m, "pass", `Product Details: ${m.label}`, "The product details say so."));
  const unsure = mustHaves.map((m, index) =>
    index === mustHaves.length - 1
      ? pageTick(m, "unclear", null, "The page doesn’t say.")
      : pageTick(m, "pass", `Specifications: ${m.label}`, "The specifications list it."),
  );
  const unchecked = { read: false, store: null, price_inr: null, image_url: null, sizes_in_stock: [], ticks: [], overall: null };
  return [
    {
      title: `Sangria ${title}`,
      url: `https://www.myntra.com/${category.toLowerCase().replace(/\s+/g, "-")}?rawQuery=${encodeURIComponent(words)}`,
      site: "myntra.com",
      price_text: null,
      snippet: null,
      read: true,
      unread_reason: null,
      store: "Myntra",
      price_inr: myntraPrice,
      image_url: photo,
      sizes_in_stock: WEB_SIZES[category] ?? [],
      ticks: passing,
      overall: overallOf(passing),
    },
    {
      title: `Anouk ${title}`,
      url: `https://www.flipkart.com/search?q=${encodeURIComponent(words)}`,
      site: "flipkart.com",
      price_text: null,
      snippet: `Buy ${words} online at Flipkart.`,
      read: true,
      unread_reason: null,
      store: "Flipkart",
      price_inr: flipkartPrice,
      image_url: null,
      sizes_in_stock: (WEB_SIZES[category] ?? []).slice(1),
      ticks: unsure,
      overall: overallOf(unsure),
    },
    {
      title: `Amazon.in: ${title}`,
      url: `https://www.amazon.in/s?k=${encodeURIComponent(words).replace(/%20/g, "+")}`,
      site: "amazon.in",
      price_text: amazonPrice,
      snippet: `Shop ${words} at Amazon.in. Free delivery on eligible orders.`,
      ...unchecked,
      unread_reason: "blocked",
    },
  ];
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

/** A small stand-in for Google's Search Suggestions HTML: one row of chips, each a Google search. */
export function searchEntryPointFor(queries: string[]): string {
  const chips = queries
    .map((query) => `<a class="chip" href="https://www.google.com/search?q=${encodeURIComponent(query)}">${escapeHtml(query)}</a>`)
    .join("");
  return [
    "<style>",
    ".container{align-items:center;border-radius:8px;display:flex;font-family:Google Sans,Roboto,sans-serif;font-size:14px;line-height:20px;padding:8px 12px}",
    ".headline{display:flex;margin-right:4px;font-weight:500}",
    ".carousel{overflow:auto;scrollbar-width:none;white-space:nowrap;margin-right:-12px}",
    ".chip{display:inline-block;border:solid 1px;border-radius:16px;min-width:14px;padding:5px 16px;text-align:center;user-select:none;margin:0 8px;text-decoration:none}",
    "@media (prefers-color-scheme:light){.container{background-color:#fafafa;box-shadow:0 0 0 1px #0000000f}.headline{color:#1f1f1f}.chip{background-color:#fff;border-color:#d2d2d2;color:#5e5e5e}}",
    "@media (prefers-color-scheme:dark){.container{background-color:#1f1f1f;box-shadow:0 0 0 1px #ffffff26}.headline{color:#fff}.chip{background-color:#2c2c2c;border-color:#3c4043;color:#fff}}",
    "</style>",
    `<div class="container"><div class="headline">Google</div><div class="carousel">${chips}</div></div>`,
  ].join("");
}
