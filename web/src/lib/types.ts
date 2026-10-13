// Keep in step with agent/models.py, plus agent/rank.py and agent/tradeoffs.py for stream payloads.
// Dates are ISO strings (YYYY-MM-DD). Product fields use the aliases FastAPI serialises by default.

export type Rule = "is" | "is_not" | "present" | "absent" | "at_most" | "at_least";
export type Status = "pass" | "fail" | "unclear";
export type EvidenceType = "image" | "details" | "reviews" | "attribute";
export type Gender = "men" | "women" | "boys" | "girls" | "unisex";
export type ProductGroup = "tops" | "bottoms" | "shoes" | "watches" | "bags" | "sunglasses";
export type DetailValue = string | boolean | number;
/** Where a search looks: live Shopify stores through UCP, or our demo catalogue. */
export type SearchSource = "live" | "catalog";
/** Where a product came from: our demo catalogue, or a live store found through UCP. */
export type ProductSource = "catalog" | "shopify";

export interface MustHave {
  id: string;
  attribute: string;
  rule: Rule;
  value: DetailValue;
  from_rule: boolean;
}

export interface NiceToHave {
  attribute: string;
  value: DetailValue;
}

export interface ShoppingBrief {
  category: string;
  gender: Gender | null;
  must_haves: MustHave[];
  nice_to_haves: NiceToHave[];
  rules_applied: string[];
  budget_max_inr: number | null;
  deadline_date: string | null;
  pincode: string | null;
  size: string | null;
  reference_image_url: string | null;
  language: string;
  missing: string[];
  /** Wishes the agent can't check yet, such as a brand. Shown to the shopper, never used to filter. */
  not_checkable: string[];
  question: string | null;
}

/** [ymin, xmin, ymax, xmax] on a 0-1000 scale. */
export type BoundingBox = [number, number, number, number];

export interface Evidence {
  type: EvidenceType;
  note: string;
  quote: string | null;
  box_2d: BoundingBox | null;
  agree: number | null;
  disagree: number | null;
}

export interface Check {
  must_have_id: string;
  status: Status;
  confidence: number;
  evidence: Evidence | null;
}

export interface Verification {
  product_id: string;
  checks: Check[];
  reason: string;
  all_pass: boolean;
  overall: Status;
}

/** One line of a product's tick-list, as built by agent/rank.py `ticks_for`. */
export interface Tick {
  must_have_id: string;
  label: string;
  status: Status;
  evidence: Evidence | null;
}

export interface RankedResult {
  product_id: string;
  title: string;
  /** Rupees, including delivery and the COD fee when the checklist asks for cash on delivery. */
  total_price: number;
  delivery_date: string | null;
  ticks: Tick[];
  reason: string;
  score: number;
}

/**
 * agent/cards.py `ResultCard`: a ranked result plus what the card shows. Live cards have no demo price,
 * delivery date, COD, returns or reviews, and their total is the item price because shipping is unknown.
 */
export interface ResultCard extends RankedResult {
  source: ProductSource;
  /** The live store's name. */
  store: string | null;
  /** The product's page on the live store. */
  product_url: string | null;
  shipping_note: string | null;
  brand: string | null;
  thumb: string | null;
  /** The gemini/ photo (about 1,000 px), the one the careful check looked at. */
  photo: string | null;
  original: string | null;
  price: Price | null;
  delivery_fee: number;
  cod: Cod | null;
  returns: Returns | null;
  rating_avg: number | null;
  rating_count: number;
  demo_data: boolean;
  sizes_in_stock: string[];
}

/** relax is "deadline", "budget" or "must_have:<attribute>"; to is an ISO date or rupees. */
export interface Tradeoff {
  relax: string;
  to: string | number | null;
  matches: number;
}

export interface Attribute {
  value: DetailValue;
  confidence: number;
  note: string;
  source: "gemini" | "catalogue";
  /** What Gemini saw in the photo, when the catalogue value was used instead. */
  gemini_value?: DetailValue | null;
}

export interface Price {
  mrp: number;
  selling: number;
  demo: boolean;
}

export interface Warehouse {
  city: string;
  pincode: string;
}

export interface Cod {
  available: boolean;
  fee: number;
}

export interface Returns {
  returnable: boolean;
  window_days: number;
  exchange: boolean;
}

export interface Review {
  rating: number;
  text: string;
  demo: boolean;
  flagged: boolean;
}

export interface Images {
  original: string;
  gemini: string;
  thumb: string;
}

export interface Product {
  id: string;
  title: string;
  brand: string | null;
  gender: Gender;
  masterCategory: string;
  subCategory: string;
  articleType: string;
  baseColour: string | null;
  usage: string | null;
  group: ProductGroup;
  description: string;
  attributes: Record<string, Attribute>;
  catalogue_details: Record<string, string>;
  needs_review: string[];
  price: Price | null;
  sizes: Record<string, number>;
  warehouse: Warehouse | null;
  delivery_fee: number;
  cod: Cod | null;
  returns: Returns | null;
  reviews: Review[];
  rating_avg: number | null;
  rating_count: number;
  images: Images | null;
  demo_data: boolean;
  source: ProductSource;
  /** Only on live products: the store's name and its own pages. */
  store: string | null;
  product_url: string | null;
  checkout_url: string | null;
}

/**
 * GET /api/products/{id}: the product without its embedding (agent/cards.py `product_view`), plus its
 * tick-list for the session's must-haves when a session is given. Delivery date and total price are
 * optional extras; the product page falls back to the result card when they're missing.
 */
export interface ProductDetails extends Product {
  sizes_in_stock?: string[];
  ticks?: Tick[];
  delivery_date?: string | null;
  total_price?: number | null;
  /** Live products only, as on their result card. */
  shipping_note?: string | null;
}

// ---------- Requests and responses ----------

export interface BriefInput {
  text?: string;
  /** A photo or screenshot the shopper added, or a link to one. */
  image?: Blob | string;
  pincode: string;
  language: string;
  source: SearchSource;
}

export interface BriefRequestBody {
  text?: string;
  image_url?: string;
  image_base64?: string;
  image_mime_type?: string;
  pincode: string;
  language: string;
  source: SearchSource;
}

export interface BriefResponse {
  session_id: string;
  brief: ShoppingBrief;
  question?: string | null;
}

/** One edit to the checklist. Field names match ShoppingBrief so the agent can apply them directly. */
export type BriefChange =
  | { type: "text"; text: string }
  | { type: "answer"; question: string; answer: string }
  | { type: "add_must_have"; text: string }
  | { type: "edit_must_have"; id: string; rule: Rule; value: DetailValue }
  | { type: "remove_must_have"; id: string }
  | { type: "set_rule"; must_have: MustHave; enabled: boolean }
  | { type: "remove_nice_to_have"; attribute: string; value: DetailValue }
  | { type: "set_budget"; budget_max_inr: number | null }
  | { type: "set_deadline"; deadline_date: string | null }
  | { type: "set_pincode"; pincode: string }
  | { type: "set_size"; size: string | null }
  | { type: "set_source"; source: SearchSource };

export interface BriefUpdateRequest {
  session_id: string;
  change: BriefChange;
}

export interface BriefUpdateResponse {
  brief: ShoppingBrief;
}

export interface SearchRequest {
  session_id: string;
}

export interface CandidatesEvent {
  type: "candidates";
  count: number;
  /** Products the quick check ruled out from their saved details, before the careful check. */
  dropped: number;
}

export interface CheckingEvent {
  type: "checking";
  product_id: string;
  title: string;
  thumb: string | null;
}

export interface VerifiedEvent {
  type: "verified";
  product_id: string;
  status: Status;
  ticks: Tick[];
}

export interface DoneEvent {
  type: "done";
  checked: number;
  passed: number;
  unclear: number;
  rejected: Record<string, number>;
  duplicates: number;
  tradeoffs: Tradeoff[];
  matches: ResultCard[];
  almost: ResultCard[];
  not_checkable: string[];
}

/** Why a page wasn't checked: Google couldn't read it, or it lists many products. Null when we didn't get to it. */
export type UnreadReason = "blocked" | "list_page";

/**
 * A page on another site that Grounding with Google Search found (agent/web_search.py `WebResult`). When `read`,
 * Gemini read it through Google's URL context tool and checked it against the checklist; otherwise it's a plain link.
 */
export interface WebResult {
  title: string;
  /** Google's grounding link to the page. */
  url: string;
  site: string;
  price_text: string | null;
  snippet: string | null;
  read: boolean;
  unread_reason: UnreadReason | null;
  store: string | null;
  price_inr: number | null;
  /** The store's own product photo, shown from the store's site. */
  image_url: string | null;
  sizes_in_stock: string[];
  /** One per must-have (shop details excepted); proof is a quote from the page. Empty when not read. */
  ticks: Tick[];
  overall: Status | null;
}

/**
 * The "On other sites" list, sent after `done` on live searches when Google Search found anything; the stream
 * ends after it. Read and passing pages come first, then read and unclear, then plain links.
 */
export interface WebEvent {
  type: "web";
  results: WebResult[];
  /** Google's Search Suggestions (renderedContent). Must be shown exactly as given, next to the results. */
  search_entry_point_html: string | null;
  queries: string[];
}

export interface ErrorEvent {
  type: "error";
  message: string;
  retryable: boolean;
}

export type SearchEvent = CandidatesEvent | CheckingEvent | VerifiedEvent | WebEvent | DoneEvent | ErrorEvent;

export const SEARCH_EVENT_TYPES: ReadonlySet<SearchEvent["type"]> = new Set([
  "candidates",
  "checking",
  "verified",
  "web",
  "done",
  "error",
]);

export type FeedbackAction = "like" | "save" | "dislike" | "buy";

export interface FeedbackRequest {
  session_id: string;
  product_id: string;
  action: FeedbackAction;
}

export interface OkResponse {
  ok: boolean;
}

/** GET and PUT /api/rules. Saved rules are must-haves with from_rule: true. */
export interface RulesBody {
  rules: MustHave[];
}

/** GET /api/me */
export interface Me {
  name: string | null;
  email: string | null;
  pincode: string | null;
  rules: MustHave[];
  has_taste: boolean;
}

/** PUT /api/me */
export interface MeUpdate {
  pincode?: string;
  language?: string;
}

export interface CheckoutRequest {
  product_id: string;
  size: string | null;
}

export interface CheckoutResponse {
  /**
   * The store's checkout page for this product and size. Catalogue products go to our demo store,
   * /checkout?product=&size=; live products go to the store's own site.
   */
  redirect_url: string;
}
