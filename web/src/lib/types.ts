// Keep in step with agent/models.py, plus agent/rank.py and agent/tradeoffs.py for stream payloads.
// Dates are ISO strings (YYYY-MM-DD). Product fields use the aliases FastAPI serialises by default.

export type Rule = "is" | "is_not" | "present" | "absent" | "at_most" | "at_least";
export type Status = "pass" | "fail" | "unclear";
export type EvidenceType = "image" | "details" | "reviews" | "attribute";
export type Gender = "men" | "women" | "boys" | "girls" | "unisex";
export type ProductGroup = "tops" | "bottoms" | "shoes" | "watches" | "bags" | "sunglasses";
export type DetailValue = string | boolean | number;

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
  total_price: number;
  delivery_date: string | null;
  ticks: Tick[];
  reason: string;
  score: number;
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
}

/** GET /api/products/{id}: the product, plus its tick-list for the session's must-haves when a session is given. */
export interface ProductDetails extends Product {
  ticks?: Tick[];
}

// ---------- Requests and responses ----------

export interface BriefInput {
  text?: string;
  /** A photo or screenshot the shopper added, or a link to one. */
  image?: Blob | string;
  pincode: string;
  language: string;
}

export interface BriefRequestBody {
  text?: string;
  image_url?: string;
  image_base64?: string;
  image_mime_type?: string;
  pincode: string;
  language: string;
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
  | { type: "set_size"; size: string | null };

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
}

export interface CheckingEvent {
  type: "checking";
  product_id: string;
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
  tradeoffs: Tradeoff[];
  matches?: RankedResult[];
  almost?: RankedResult[];
  duplicates?: number;
}

export interface ErrorEvent {
  type: "error";
  message: string;
  retryable: boolean;
}

export type SearchEvent = CandidatesEvent | CheckingEvent | VerifiedEvent | DoneEvent | ErrorEvent;

export const SEARCH_EVENT_TYPES: ReadonlySet<SearchEvent["type"]> = new Set([
  "candidates",
  "checking",
  "verified",
  "done",
  "error",
]);
