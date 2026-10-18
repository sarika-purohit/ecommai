// Sample agent responses for building the screens before the real agent API runs.
// Everything here stands in for server data, so it is not in the strings file.
// Checks come from each product's saved details, and ranking and trade-offs follow agent/rank.py and
// agent/tradeoffs.py, so the counts on "what to give up" match what one tap gives.

import { addDays, todayInIndia } from "@/lib/format";
import { KURTAS, WATCHES, type CatalogProduct } from "@/lib/mock-catalog";
import { LIVE_PRODUCTS, SHIPPING_NOTE, searchEntryPointFor, shopifyPhoto, webResultsFor, type LiveProduct } from "@/lib/mock-live";
import type {
  BriefChange,
  BriefInput,
  BriefResponse,
  BriefUpdateResponse,
  CatalogItem,
  CatalogPage,
  CatalogQuery,
  CategoryCount,
  ChatMessagesBody,
  ChatProduct,
  ChatSummary,
  Check,
  CheckoutRequest,
  CheckoutResponse,
  DemoOrder,
  DetailValue,
  Evidence,
  FeedbackRequest,
  GenderFilter,
  Look,
  LookPart,
  Looks,
  LookSlot,
  LooksRequest,
  LookSuggestion,
  Me,
  MeUpdate,
  MustHave,
  NiceToHave,
  OkResponse,
  OpenChatResponse,
  OrderList,
  OrderRequest,
  OrderResponse,
  OrderSuggestions,
  ProductDetails,
  ResultCard,
  RulesBody,
  SavedChat,
  SearchEvent,
  SearchSource,
  ShoppingBrief,
  Status,
  Tick,
  Tradeoff,
  WebEvent,
} from "@/lib/types";

type Detail = Omit<MustHave, "id" | "from_rule">;
type MockProduct = CatalogProduct | LiveProduct;

interface MockSession {
  brief: ShoppingBrief;
  /** Missing on sessions saved before the choice existed, which searched the catalogue. */
  source?: SearchSource;
  questionTarget: "size" | "dial_markings" | null;
  /** ruleKey() of personal rules switched off for this search. */
  rulesOff: string[];
  nextId: number;
  /** The best match from the last search, for "cheaper" and "different colour". */
  lastTop: { total: number; colour: string | null } | null;
  /** Set when the request mentions "test error": the next search stops with an error event. */
  failNext: boolean;
}

interface MockProfile {
  rules: MustHave[];
  pincode: string | null;
  language: string;
  has_taste: boolean;
}

export class MockNotFound extends Error {
  constructor() {
    super("Not found");
    this.name = "MockNotFound";
  }
}

const SESSIONS_KEY = "ecommai.mock.sessions";
const PROFILE_KEY = "ecommai.mock.profile";
const BUCKET = "https://storage.googleapis.com/ecommai-511419-catalog";
/** Products the quick check rules out before the careful check, when there's anything to rule out on. */
const QUICK_DROPS = 28;
const LIVE_QUICK_DROPS = 6;
const STARTING_RULES: MustHave[] = [{ id: "r1", attribute: "cod", rule: "present", value: true, from_rule: true }];
const TOP_SIZES = ["S", "M", "L", "XL", "XXL"];

// ---------- Storage ----------

function readJson<T>(storage: Storage | undefined, key: string): T | null {
  try {
    const raw = storage?.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeJson(storage: Storage | undefined, key: string, value: unknown) {
  try {
    storage?.setItem(key, JSON.stringify(value));
  } catch {}
}

function browserStorage(kind: "local" | "session"): Storage | undefined {
  if (typeof window === "undefined") return undefined;
  return kind === "local" ? window.localStorage : window.sessionStorage;
}

function loadSessions(): Record<string, MockSession> {
  return readJson<Record<string, MockSession>>(browserStorage("session"), SESSIONS_KEY) ?? {};
}

function saveSession(id: string, session: MockSession) {
  writeJson(browserStorage("session"), SESSIONS_KEY, { ...loadSessions(), [id]: session });
}

function getSession(id: string): MockSession {
  const session = loadSessions()[id];
  if (!session) throw new MockNotFound();
  return session;
}

function loadProfile(): MockProfile {
  return (
    readJson<MockProfile>(browserStorage("local"), PROFILE_KEY) ?? {
      rules: STARTING_RULES,
      pincode: null,
      language: "en",
      has_taste: false,
    }
  );
}

function saveProfile(profile: MockProfile) {
  writeJson(browserStorage("local"), PROFILE_KEY, profile);
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

// ---------- Words the agent understands ----------

const COLOUR_WORDS: Record<string, string> = {
  white: "white", safed: "white", cream: "white", "off-white": "white",
  black: "black", kala: "black", kaala: "black",
  blue: "blue", navy: "blue", neela: "blue", teal: "blue",
  green: "green", hara: "green", olive: "green",
  red: "red", maroon: "red", laal: "red", rust: "red",
  pink: "pink", yellow: "yellow", peela: "yellow", mustard: "yellow",
  grey: "grey", gray: "grey", beige: "beige", khaki: "beige",
  brown: "brown", purple: "purple", orange: "orange", gold: "gold", silver: "silver",
};

const COLOUR_FAMILY: Record<string, string> = {
  black: "black", charcoal: "black",
  white: "white", "off white": "white", cream: "white",
  grey: "grey", "grey melange": "grey",
  blue: "blue", "navy blue": "blue", "turquoise blue": "blue", teal: "blue",
  green: "green", olive: "green", "sea green": "green",
  red: "red", maroon: "red", rust: "red",
  pink: "pink", magenta: "pink", rose: "pink", peach: "pink",
  purple: "purple", lavender: "purple",
  yellow: "yellow", mustard: "yellow",
  orange: "orange",
  brown: "brown", "coffee brown": "brown", tan: "brown", bronze: "brown", copper: "brown",
  beige: "beige", khaki: "beige", nude: "beige", taupe: "beige",
  gold: "gold", silver: "silver", steel: "silver", metallic: "silver", multi: "multi",
};

const BRANDS = ["Puma", "Nike", "Adidas", "Fabindia", "Biba", "Manyavar", "Titan", "Fastrack", "Casio", "Fossil", "Levi's"];

const WEEKDAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

const SHOP_ATTRIBUTES = ["cod", "returnable", "exchange"];
const REVIEW_ATTRIBUTES = ["size_fit", "durability", "colour_fastness", "colour_match", "sheerness"];
const GROUP_ATTRIBUTES: Record<string, string[]> = {
  tops: ["sleeve_length", "neckline", "pockets", "pattern", "print_intensity", "fabric", "sheerness", "fit", "length", "closure", "usage", "colour"],
  watches: ["display", "dial_colour", "dial_markings", "case_shape", "case_material", "case_size", "strap_material", "strap_colour", "chronograph"],
  shoes: ["type", "sole_type", "sole_colour", "upper_material", "logo_visible", "heel_height", "colour"],
};
/** Only live stores sell shoes here; the demo catalogue has kurtas and watches. */
const SHOES = "Casual Shoes";

function colourFamily(baseColour: string | null): string | null {
  return baseColour ? (COLOUR_FAMILY[baseColour.trim().toLowerCase()] ?? null) : null;
}

function colourIn(text: string): { colour: string; negated: boolean } | null {
  const pattern = new RegExp(`\\b(not|no|except|without)?\\s*(${Object.keys(COLOUR_WORDS).join("|")})\\b`, "i");
  const match = pattern.exec(text);
  return match ? { colour: COLOUR_WORDS[match[2].toLowerCase()], negated: Boolean(match[1]) } : null;
}

function detailsFrom(text: string, category: string): Detail[] {
  const lower = text.toLowerCase();
  const found: Detail[] = [];
  const add = (detail: Detail) => {
    if (!found.some((d) => d.attribute === detail.attribute)) found.push(detail);
  };
  if (/\b(cash on delivery|cod)\b/.test(lower)) add({ attribute: "cod", rule: "present", value: true });
  if (/\b(returnable|easy returns?)\b/.test(lower)) add({ attribute: "returnable", rule: "present", value: true });

  if (category === "Watches") {
    const dial = /\b(\w+)\s+dial\b/.exec(lower);
    const colour = dial && COLOUR_WORDS[dial[1]] ? COLOUR_WORDS[dial[1]] : colourIn(lower)?.colour;
    if (colour) add({ attribute: "dial_colour", rule: "is", value: colour });
    if (/\b(steel|metal|chain)\b/.test(lower)) add({ attribute: "strap_material", rule: "is", value: "steel" });
    else if (/\bleather\b/.test(lower)) add({ attribute: "strap_material", rule: "is", value: "leather" });
    else if (/\b(silicone|rubber)\b/.test(lower)) add({ attribute: "strap_material", rule: "is", value: "silicone" });
    if (/\b(small|chhota|chhoti|petite)\b/.test(lower)) add({ attribute: "case_size", rule: "is", value: "small" });
    else if (/\b(big|large)\b/.test(lower)) add({ attribute: "case_size", rule: "is", value: "large" });
    if (/\broman\b/.test(lower)) add({ attribute: "dial_markings", rule: "is", value: "roman numerals" });
    else if (/\bnumbers?\b/.test(lower)) add({ attribute: "dial_markings", rule: "is", value: "numbers" });
    else if (/\b(lines|sticks)\b/.test(lower)) add({ attribute: "dial_markings", rule: "is", value: "lines" });
    return found;
  }

  if (/\b(not|no|never)\s+polyester\b/.test(lower)) add({ attribute: "fabric", rule: "is_not", value: "polyester" });
  const fabric = /\b(cotton|linen|rayon|silk|khadi)\b/.exec(lower);
  if (fabric) add({ attribute: "fabric", rule: "is", value: fabric[1] === "khadi" ? "cotton" : fabric[1] });
  if (/\bno pockets?\b/.test(lower)) add({ attribute: "pockets", rule: "absent", value: true });
  else if (/\b(pockets?|jeb)\b/.test(lower)) add({ attribute: "pockets", rule: "present", value: true });
  if (/three[- ]quarter|3\/4|teen chauthai/.test(lower)) add({ attribute: "sleeve_length", rule: "is", value: "three-quarter" });
  else if (/\b(full|long)[- ]sleeves?\b/.test(lower)) add({ attribute: "sleeve_length", rule: "is", value: "full" });
  else if (/\b(short|half)[- ]sleeves?\b/.test(lower)) add({ attribute: "sleeve_length", rule: "is", value: "short" });
  else if (/\bsleeveless\b/.test(lower)) add({ attribute: "sleeve_length", rule: "is", value: "sleeveless" });
  if (/see[- ]?through|transparent|\bsheer\b/.test(lower)) add({ attribute: "sheerness", rule: "is_not", value: "see-through" });
  if (/\b(loud|bold|busy)\s+prints?\b|not (too )?loud|\b(plain|simple|subtle)\b/.test(lower)) {
    add({ attribute: "print_intensity", rule: "is_not", value: "loud" });
  }
  if (/\bv[- ]?neck\b/.test(lower)) add({ attribute: "neckline", rule: "is", value: "v-neck" });
  const colour = colourIn(lower);
  if (colour) add({ attribute: "colour", rule: colour.negated ? "is_not" : "is", value: colour.colour });
  return found;
}

function budgetFrom(text: string): number | null {
  const match = /(?:under|below|upto|up to|within|max|less than|tak)\s*(?:₹|rs\.?|inr)?\s*([\d,]{3,})/i.exec(text);
  if (!match) return null;
  const amount = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

function weekdayOf(iso: string): number {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).getUTCDay();
}

function deadlineFrom(text: string): string | null {
  const lower = text.toLowerCase();
  const today = todayInIndia();
  if (/\b(tomorrow|kal)\b/.test(lower)) return addDays(today, 1);
  const day = WEEKDAYS.findIndex((name) => new RegExp(`\\b(${name}|${name.slice(0, 3)})\\b`).test(lower));
  if (day === -1) return null;
  return addDays(today, (day - weekdayOf(today) + 7) % 7 || 7);
}

function genderFrom(text: string): ShoppingBrief["gender"] {
  if (/\b(women|woman|ladies|female|girls?)\b/i.test(text)) return "women";
  if (/\b(men|man|gents|male)\b/i.test(text)) return "men";
  return null;
}

function brandsIn(text: string): string[] {
  return BRANDS.filter((brand) => new RegExp(`\\b${brand.replace("'", "'?")}\\b`, "i").test(text));
}

// ---------- Personal rules ----------

function ruleKey(mustHave: Pick<MustHave, "attribute" | "rule" | "value">): string {
  return `${mustHave.attribute}__${mustHave.rule}__${String(mustHave.value).toLowerCase().replace(/ /g, "-")}`;
}

function appliesTo(category: string, attribute: string): boolean {
  const group = category === "Watches" ? "watches" : category === SHOES ? "shoes" : "tops";
  return [...GROUP_ATTRIBUTES[group], ...SHOP_ATTRIBUTES, ...REVIEW_ATTRIBUTES].includes(attribute);
}

/** Same idea as agent/rules.py `apply_rules`: saved rules join as locked must-haves unless switched off or overridden. */
function withRules(brief: ShoppingBrief, rulesOff: string[]): ShoppingBrief {
  const own = brief.must_haves.filter((m) => !m.from_rule);
  const used = new Set(own.map((m) => m.id));
  const added: MustHave[] = [];
  for (const rule of loadProfile().rules) {
    if (rulesOff.includes(ruleKey(rule)) || !appliesTo(brief.category, rule.attribute)) continue;
    if (own.some((m) => m.attribute === rule.attribute) || added.some((m) => ruleKey(m) === ruleKey(rule))) continue;
    let n = 1;
    while (used.has(`r${n}`)) n += 1;
    used.add(`r${n}`);
    added.push({ ...rule, id: `r${n}`, from_rule: true });
  }
  return { ...brief, must_haves: [...own, ...added], rules_applied: added.map(agentLabel) };
}

// ---------- Making a brief ----------

function newBrief(input: BriefInput, category: string): ShoppingBrief {
  const text = input.text ?? "";
  return {
    category,
    gender: category === "Watches" ? null : genderFrom(text),
    must_haves: [],
    nice_to_haves:
      /\b(office|work|formal)\b/i.test(text) && category !== "Watches" && category !== SHOES ? [{ attribute: "usage", value: "office" }] : [],
    rules_applied: [],
    budget_max_inr: budgetFrom(text),
    min_rating: null,
    deadline_date: deadlineFrom(text),
    pincode: input.pincode ?? loadProfile().pincode,
    size: null,
    reference_image_url: typeof input.image === "string" ? input.image : null,
    language: input.language,
    missing: [],
    remembered: [],
    not_checkable: brandsIn(text),
    question: null,
  };
}

function categoryFor(text: string, source: SearchSource): string {
  if (/\bwatch/i.test(text)) return "Watches";
  if (source === "live" && /\b(sneakers?|shoes?|trainers?)\b/i.test(text)) return SHOES;
  return "Kurtas";
}

export async function createBrief(input: BriefInput): Promise<BriefResponse> {
  await sleep(900);
  const text = input.text ?? "";
  const source = input.source;
  const category = categoryFor(text, source);
  const brief = newBrief(input, category);
  const details = detailsFrom(text, category);
  brief.must_haves = details.map((detail, index) => ({ ...detail, id: `m${index + 1}`, from_rule: false }));

  let questionTarget: MockSession["questionTarget"] = null;
  if (category === "Watches" && !details.some((d) => d.attribute === "dial_markings")) {
    brief.question = "Numbers or lines on the dial?";
    questionTarget = "dial_markings";
  } else if (category === "Kurtas") {
    brief.missing = ["size"];
    brief.question = "Which size do you usually wear?";
    questionTarget = "size";
  }

  const session: MockSession = {
    brief: withRules(brief, []),
    source,
    questionTarget,
    rulesOff: [],
    nextId: details.length + 1,
    lastTop: null,
    failNext: /\btest error\b/i.test(text),
  };
  const id = `mock-${Date.now().toString(36)}`;
  saveSession(id, session);
  if (input.chat_id) keepInChat(input.chat_id, id, session, text);
  return { session_id: id, brief: session.brief, question: session.brief.question, product: productName(session.brief.category) };
}

// ---------- Editing a brief ----------

function withMustHave(session: MockSession, detail: Detail): MockSession {
  const brief = session.brief;
  const replaced = brief.must_haves.find((m) => !m.from_rule && m.attribute === detail.attribute);
  if (replaced) {
    const must_haves = brief.must_haves.map((m) => (m.id === replaced.id ? { ...m, ...detail } : m));
    return { ...session, brief: withRules({ ...brief, must_haves }, session.rulesOff) };
  }
  const added: MustHave = { ...detail, id: `m${session.nextId}`, from_rule: false };
  return {
    ...session,
    nextId: session.nextId + 1,
    brief: withRules({ ...brief, must_haves: [...brief.must_haves, added] }, session.rulesOff),
  };
}

function withNiceToHave(session: MockSession, nice: NiceToHave): MockSession {
  const has = session.brief.nice_to_haves.some((n) => n.attribute === nice.attribute && n.value === nice.value);
  return has ? session : { ...session, brief: { ...session.brief, nice_to_haves: [...session.brief.nice_to_haves, nice] } };
}

function applyText(session: MockSession, text: string): MockSession {
  const brief = session.brief;
  const watches = brief.category === "Watches";
  if (/\bcheaper\b/i.test(text)) {
    const from = brief.budget_max_inr ?? session.lastTop?.total ?? (watches ? 3000 : 1000);
    return { ...session, brief: { ...brief, budget_max_inr: Math.max(50, Math.floor((from * 0.8) / 50) * 50) } };
  }
  if (/\bformal\b/i.test(text)) {
    const next = withNiceToHave(session, { attribute: watches ? "display" : "usage", value: watches ? "analog" : "office" });
    return watches ? next : withMustHave(next, { attribute: "print_intensity", rule: "is_not", value: "loud" });
  }
  if (/\bdifferent colou?r\b/i.test(text)) {
    const attribute = watches ? "dial_colour" : "colour";
    const current = brief.must_haves.find((m) => m.attribute === attribute && !m.from_rule);
    const colour = current?.rule === "is" ? String(current.value) : session.lastTop?.colour;
    if (!colour) return session;
    return withMustHave(session, { attribute, rule: "is_not", value: colour });
  }
  let next = session;
  const budget = budgetFrom(text);
  if (budget) next = { ...next, brief: { ...next.brief, budget_max_inr: budget } };
  const deadline = deadlineFrom(text);
  if (deadline) next = { ...next, brief: { ...next.brief, deadline_date: deadline } };
  const details = detailsFrom(text, brief.category);
  if (details.length === 0 && !budget && !deadline) {
    return withMustHave(next, { attribute: "other", rule: "is", value: text.trim() });
  }
  return details.reduce(withMustHave, next);
}

function answerQuestion(session: MockSession, answer: string): MockSession {
  const brief = { ...session.brief, question: null };
  const text = answer.trim();
  if (session.questionTarget === "size") {
    return {
      ...session,
      questionTarget: null,
      brief: { ...brief, size: text.length <= 4 ? text.toUpperCase() : text, missing: brief.missing.filter((m) => m !== "size") },
    };
  }
  if (session.questionTarget === "dial_markings") {
    const value = /line/i.test(text) ? "lines" : /number/i.test(text) ? "numbers" : /roman/i.test(text) ? "roman numerals" : text.toLowerCase();
    return withMustHave({ ...session, questionTarget: null, brief }, { attribute: "dial_markings", rule: "is", value });
  }
  return { ...session, questionTarget: null, brief };
}

function applyChange(session: MockSession, change: BriefChange): MockSession {
  const brief = session.brief;
  switch (change.type) {
    case "remove_must_have":
      return { ...session, brief: withRules({ ...brief, must_haves: brief.must_haves.filter((m) => m.id !== change.id) }, session.rulesOff) };
    case "edit_must_have":
      return {
        ...session,
        brief: {
          ...brief,
          must_haves: brief.must_haves.map((m) => (m.id === change.id ? { ...m, rule: change.rule, value: change.value } : m)),
        },
      };
    case "add_must_have": {
      const parsed = detailsFrom(change.text, brief.category)[0];
      return withMustHave(session, parsed ?? { attribute: "other", rule: "is", value: change.text.trim() });
    }
    case "set_rule": {
      const key = ruleKey(change.must_have);
      const rulesOff = change.enabled ? session.rulesOff.filter((k) => k !== key) : [...session.rulesOff.filter((k) => k !== key), key];
      return { ...session, rulesOff, brief: withRules(brief, rulesOff) };
    }
    case "remove_nice_to_have":
      return {
        ...session,
        brief: { ...brief, nice_to_haves: brief.nice_to_haves.filter((n) => !(n.attribute === change.attribute && n.value === change.value)) },
      };
    case "set_budget":
      return { ...session, brief: { ...brief, budget_max_inr: change.budget_max_inr } };
    case "set_deadline":
      return { ...session, brief: { ...brief, deadline_date: change.deadline_date } };
    case "set_pincode":
      return { ...session, brief: { ...brief, pincode: change.pincode } };
    case "set_size":
      return { ...session, brief: { ...brief, size: change.size, missing: brief.missing.filter((m) => m !== "size") } };
    case "set_source":
      return { ...session, source: change.source };
    case "set_memory":
      return session;
    case "set_min_rating":
      return { ...session, brief: { ...brief, min_rating: change.min_rating } };
    case "answer":
      return answerQuestion(session, change.answer);
    case "text":
      return applyText(session, change.text);
  }
}

/** The words a change adds to what the shopper has said, as the agent keeps them. */
function wordsOf(change: BriefChange): string {
  if (change.type === "text") return change.text;
  if (change.type === "answer") return change.answer;
  return change.type === "add_must_have" ? change.text : "";
}

/** The chat's other products aren't switched between here: every change edits the session it was sent for. */
export async function updateBrief(sessionId: string, change: BriefChange, chatId?: string): Promise<BriefUpdateResponse> {
  await sleep(350);
  const next = applyChange(getSession(sessionId), change);
  saveSession(sessionId, next);
  if (chatId) {
    const said = [chatRecord(chatId)?.queryTexts[sessionId] ?? "", wordsOf(change)].filter(Boolean).join(". ");
    keepInChat(chatId, sessionId, next, said);
  }
  return { session_id: sessionId, brief: next.brief, product: productName(next.brief.category) };
}

// ---------- Labels, as agent/rules.py writes them ----------

/** True when the must-have asks for the detail to be there, false when it asks for it to be missing. */
function wantedPresence(mustHave: Pick<MustHave, "rule" | "value">): boolean | null {
  if (mustHave.rule === "present" || mustHave.rule === "absent") return mustHave.value === (mustHave.rule === "present");
  const text = String(mustHave.value).toLowerCase();
  if ((mustHave.rule === "is" || mustHave.rule === "is_not") && (text === "yes" || text === "no")) {
    return (text === "yes") === (mustHave.rule === "is");
  }
  return null;
}

function attributeWords(attribute: string): string {
  return attribute === "cod" ? "cash on delivery" : attribute.replace(/_/g, " ");
}

function agentLabel(mustHave: MustHave): string {
  const wanted = wantedPresence(mustHave);
  if (wanted !== null) return wanted ? attributeWords(mustHave.attribute) : `no ${attributeWords(mustHave.attribute)}`;
  const value = String(mustHave.value);
  return { is: value, is_not: `not ${value}`, at_most: `≤ ${value}`, at_least: `≥ ${value}`, present: value, absent: value }[mustHave.rule];
}

function failureLabel(mustHave: MustHave): string {
  const wanted = wantedPresence(mustHave);
  if (wanted !== null) return wanted ? `no ${attributeWords(mustHave.attribute)}` : attributeWords(mustHave.attribute);
  const value = String(mustHave.value);
  return { is: `not ${value}`, is_not: value, at_most: `over ${value}`, at_least: `under ${value}`, present: value, absent: value }[mustHave.rule];
}

// ---------- Checking a product ----------

function evidence(type: Evidence["type"], note: string, extra: Partial<Evidence> = {}): Evidence {
  return { type, note, quote: null, box_2d: null, agree: null, disagree: null, ...extra };
}

function meets(value: DetailValue, mustHave: MustHave): boolean | null {
  const text = String(value).toLowerCase();
  const wanted = wantedPresence(mustHave);
  if (wanted !== null) return !["none", "no", "unknown"].includes(text) === wanted;
  const target = String(mustHave.value).toLowerCase();
  if (mustHave.rule === "is") return text === target;
  if (mustHave.rule === "is_not") return text !== target;
  return null;
}

function judged(satisfied: boolean | null, confidence: number, found: Evidence): Check {
  const status: Status = satisfied === null ? "unclear" : satisfied ? "pass" : "fail";
  return { must_have_id: "", status, confidence: status === "unclear" ? 0.4 : confidence, evidence: found };
}

function shopCheck(product: CatalogProduct, mustHave: MustHave): Check {
  const { cod, returns } = product;
  switch (mustHave.attribute) {
    case "cod":
      return judged(
        meets(cod.available ? "yes" : "no", mustHave),
        1,
        evidence("attribute", cod.available ? `Cash on delivery is available, with a ₹${cod.fee} fee.` : "This product can’t be paid for on delivery."),
      );
    case "returnable":
      return judged(
        meets(returns.returnable ? "yes" : "no", mustHave),
        1,
        evidence("attribute", returns.returnable ? `Returnable within ${returns.window_days} days.` : "This product can’t be returned."),
      );
    default:
      return judged(
        meets(returns.exchange ? "yes" : "no", mustHave),
        1,
        evidence("attribute", returns.exchange ? "Sizes can be exchanged." : "This product can’t be exchanged."),
      );
  }
}

function sheernessCheck(product: CatalogProduct, mustHave: MustHave): Check {
  const thin = product.reviews.filter((r) => !r.flagged && /see[- ]?through|transparent|very thin/i.test(r.text));
  const thick = product.reviews.filter((r) => !r.flagged && /not see[- ]?through|thick|opaque/i.test(r.text));
  if (thin.length === 0 && thick.length === 0) {
    return judged(null, 0.4, evidence("reviews", "No review says whether it’s see-through.", { agree: 0, disagree: 0 }));
  }
  const opaque = thick.length >= thin.length * 3;
  const quote = (opaque ? thick : thin)[0].text;
  return judged(
    meets(opaque ? "opaque" : "see-through", mustHave),
    0.8,
    evidence("reviews", opaque ? "Reviews say it isn’t see-through." : "Some reviews say it’s see-through.", {
      quote,
      agree: opaque ? thick.length : thin.length,
      disagree: opaque ? thin.length : thick.length,
    }),
  );
}

function checkOne(product: CatalogProduct, mustHave: MustHave): Check {
  const box = product.boxes?.[mustHave.attribute] ?? null;
  const override = product.checks?.[mustHave.attribute];
  let check: Check;
  if (override) {
    const quote = product.listing?.[mustHave.attribute];
    check = {
      must_have_id: "",
      status: override.status,
      confidence: override.status === "unclear" ? 0.45 : 0.85,
      evidence: quote ? evidence("details", override.note, { quote }) : evidence("image", override.note, { box_2d: box }),
    };
  } else if (SHOP_ATTRIBUTES.includes(mustHave.attribute)) {
    check = shopCheck(product, mustHave);
  } else if (mustHave.attribute === "sheerness") {
    check = sheernessCheck(product, mustHave);
  } else if (mustHave.attribute === "colour") {
    const family = colourFamily(product.baseColour);
    check = judged(
      family ? meets(family, mustHave) : null,
      0.95,
      evidence("details", `The listing gives the colour as ${product.baseColour ?? "unknown"}.`, {
        quote: product.baseColour ? `Colour: ${product.baseColour}` : null,
      }),
    );
  } else {
    const attribute = product.attributes[mustHave.attribute];
    if (!attribute || String(attribute.value) === "unknown" || attribute.confidence < 0.6) {
      check = judged(null, 0.4, evidence("image", attribute?.note || "The photo and the listing don’t show this.", { box_2d: box }));
    } else {
      const quote = product.listing?.[mustHave.attribute];
      const found = quote
        ? evidence("details", attribute.note, { quote })
        : evidence("image", attribute.note, { box_2d: box });
      check = judged(meets(attribute.value, mustHave), Math.min(0.97, attribute.confidence), found);
    }
  }
  return { ...check, must_have_id: mustHave.id };
}

function isLive(product: MockProduct): product is LiveProduct {
  return "store" in product;
}

/** As agent/verify.py says for live stores, which keep payment and returns to their own checkout. */
const LIVE_SHOP_NOTES: Record<string, string> = {
  cod: "Live stores show cash on delivery at their own checkout.",
  returnable: "Returns are in the store's own refund policy.",
  exchange: "Exchanges are in the store's own refund policy.",
};

/** A live product is checked against the store's own photo and description; there are no reviews or shop data. */
function checkLive(product: LiveProduct, mustHave: MustHave): Check {
  const quote = product.listing?.[mustHave.attribute] ?? null;
  const box = product.boxes?.[mustHave.attribute] ?? null;
  let check: Check;
  if (SHOP_ATTRIBUTES.includes(mustHave.attribute)) {
    check = { must_have_id: "", status: "unclear", confidence: 0, evidence: evidence("details", LIVE_SHOP_NOTES[mustHave.attribute]) };
  } else if (mustHave.attribute === "colour") {
    const family = colourFamily(product.baseColour);
    check = judged(
      family ? meets(family, mustHave) : null,
      0.9,
      evidence("details", `The store gives the colour as ${product.baseColour ?? "unknown"}.`, {
        quote: quote ?? (product.baseColour ? `Colour: ${product.baseColour}` : null),
      }),
    );
  } else {
    const attribute = product.attributes[mustHave.attribute];
    const note = attribute?.note || "The store’s photo and description don’t show this.";
    const found = quote ? evidence("details", note, { quote }) : evidence("image", note, { box_2d: box });
    if (!attribute || String(attribute.value) === "unknown" || attribute.confidence < 0.6) {
      check = judged(null, 0.4, found);
    } else {
      check = judged(meets(attribute.value, mustHave), Math.min(0.95, attribute.confidence), found);
    }
  }
  return { ...check, must_have_id: mustHave.id };
}

function checksFor(product: MockProduct, brief: ShoppingBrief): Check[] {
  return brief.must_haves.map((mustHave) => (isLive(product) ? checkLive(product, mustHave) : checkOne(product, mustHave)));
}

function overall(checks: Check[]): Status {
  if (checks.some((c) => c.status === "fail")) return "fail";
  return checks.every((c) => c.status === "pass") ? "pass" : "unclear";
}

function ticksFor(brief: ShoppingBrief, checks: Check[]): Tick[] {
  return brief.must_haves.map((mustHave) => {
    const check = checks.find((c) => c.must_have_id === mustHave.id);
    return {
      must_have_id: mustHave.id,
      label: agentLabel(mustHave),
      status: check?.status ?? "unclear",
      evidence: check?.evidence ?? null,
    };
  });
}

// ---------- Delivery and price, as agent/delivery.py works them out ----------

const BIG_CITIES: [string, string][] = [
  ["400", "Mumbai"], ["401", "Mumbai"], ["110", "Delhi"], ["560", "Bengaluru"], ["700", "Kolkata"],
  ["600", "Chennai"], ["500", "Hyderabad"], ["411", "Pune"], ["380", "Ahmedabad"],
];
const REMOTE_PREFIXES = ["78", "79", "18", "19", "744"];

function indiaHour(): number {
  const hour = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", hourCycle: "h23" }).format(new Date());
  return Number(hour);
}

/** Live stores show delivery at their own checkout, so there's no date to work out. */
function deliveryDate(product: MockProduct, pincode: string | null): string | null {
  if (!pincode || isLive(product)) return null;
  const city = BIG_CITIES.find(([prefix]) => pincode.startsWith(prefix))?.[1];
  const days = city === product.warehouse.city ? 2 : city ? 3 : REMOTE_PREFIXES.some((p) => pincode.startsWith(p)) ? 8 : 5;
  let day = todayInIndia();
  if (indiaHour() >= 14) day = addDays(day, 1);
  if (weekdayOf(day) === 0) day = addDays(day, 1);
  for (let left = days; left > 0; ) {
    day = addDays(day, 1);
    if (weekdayOf(day) !== 0) left -= 1;
  }
  return day;
}

function wantsCod(brief: ShoppingBrief): boolean {
  return brief.must_haves.some((m) => m.attribute === "cod" && wantedPresence(m) === true);
}

/** A live product's total is its price: the store adds shipping at its checkout. */
function totalPrice(product: MockProduct, brief: ShoppingBrief | null): number {
  if (isLive(product)) return product.price.selling;
  const codFee = brief && wantsCod(brief) && product.cod.available ? product.cod.fee : 0;
  return product.price.selling + product.delivery_fee + codFee;
}

function sizeOrder(size: string): number {
  const index = TOP_SIZES.indexOf(size);
  return index === -1 ? TOP_SIZES.length : index;
}

function sizesInStock(product: MockProduct): string[] {
  return Object.entries(product.sizes)
    .filter(([, count]) => count > 0)
    .map(([size]) => size)
    .sort((a, b) => sizeOrder(a) - sizeOrder(b));
}

// ---------- Ranking, as agent/rank.py does it ----------

interface Candidate {
  product: MockProduct;
  checks: Check[];
  total: number;
  delivery: string | null;
}

interface Ranked {
  matches: ResultCard[];
  almost: ResultCard[];
  rejected: Record<string, number>;
  duplicates: number;
}

function limitProblem(brief: ShoppingBrief, candidate: Candidate): string | null {
  if (brief.budget_max_inr !== null && candidate.total > brief.budget_max_inr) return "over budget";
  if (brief.deadline_date && candidate.delivery && candidate.delivery > brief.deadline_date) return "late delivery";
  return null;
}

function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000);
}

function score(brief: ShoppingBrief, candidate: Candidate, cheapest: number, soonest: string | null): number {
  const { product } = candidate;
  const nice = brief.nice_to_haves.length
    ? brief.nice_to_haves.filter((n) => String(product.attributes[n.attribute]?.value ?? "").toLowerCase() === String(n.value).toLowerCase()).length /
      brief.nice_to_haves.length
    : 0;
  const speed = candidate.delivery && soonest ? 1 / (1 + daysBetween(soonest, candidate.delivery)) : 0;
  const rating =
    isLive(product) || product.rating_avg === null ? 0 : (product.rating_avg / 5) * (product.rating_count / (product.rating_count + 20));
  return 3 * nice + (1.5 * cheapest) / candidate.total + speed + rating;
}

function agentDay(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  const weekday = date.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
  const monthName = date.toLocaleDateString("en-GB", { month: "short", timeZone: "UTC" });
  return `${weekday} ${day} ${monthName}`;
}

function agentRupees(amount: number): string {
  return `₹${amount.toLocaleString("en-IN")}`;
}

function reasonFor(candidate: Candidate, ticks: Tick[], confirmed: boolean): string {
  if (!confirmed) {
    const unsure = ticks.filter((t) => t.status === "unclear").map((t) => t.label);
    return `${ticks.filter((t) => t.status === "pass").length} of ${ticks.length} confirmed · not sure about: ${unsure.join(", ")}`;
  }
  const parts = [ticks.length === 1 ? "1 must-have confirmed" : ticks.length ? `All ${ticks.length} must-haves confirmed` : null];
  const { product } = candidate;
  if (isLive(product)) {
    parts.push(`${agentRupees(candidate.total)} + shipping`);
    return parts.filter(Boolean).join(" · ");
  }
  parts.push(`${agentRupees(candidate.total)} delivered`);
  if (candidate.delivery) parts.push(`arrives ${agentDay(candidate.delivery)}`);
  const { rating_avg: rating, rating_count: count } = product;
  if (rating !== null) parts.push(`${rating.toFixed(1)}★ (${count} ${count === 1 ? "review" : "reviews"})`);
  return parts.filter(Boolean).join(" · ");
}

function thumbOf(product: MockProduct): string {
  return isLive(product) ? shopifyPhoto(product.photo, 300) : `${BUCKET}/thumb/${product.id}.jpg`;
}

function resultCard(candidate: Candidate, ticks: Tick[], confirmed: boolean, points: number): ResultCard {
  const { product } = candidate;
  const ranked = {
    product_id: product.id,
    title: product.title,
    total_price: candidate.total,
    delivery_date: candidate.delivery,
    ticks,
    reason: reasonFor(candidate, ticks, confirmed),
    score: Math.round(points * 10_000) / 10_000,
    brand: product.brand,
    sizes_in_stock: sizesInStock(product),
  };
  if (isLive(product)) {
    return {
      ...ranked,
      source: "shopify",
      store: product.store,
      product_url: product.product_url,
      shipping_note: SHIPPING_NOTE,
      thumb: thumbOf(product),
      photo: shopifyPhoto(product.photo, 1000),
      original: product.photo,
      price: product.price,
      delivery_fee: 0,
      cod: null,
      returns: null,
      rating_avg: null,
      rating_count: 0,
      demo_data: false,
    };
  }
  return {
    ...ranked,
    source: "catalog",
    store: null,
    product_url: null,
    shipping_note: null,
    thumb: thumbOf(product),
    photo: `${BUCKET}/gemini/${product.id}.jpg`,
    original: `${BUCKET}/original/${product.id}.jpg`,
    price: product.price,
    delivery_fee: product.delivery_fee,
    cod: product.cod,
    returns: product.returns,
    rating_avg: product.rating_avg,
    rating_count: product.rating_count,
    demo_data: true,
  };
}

function identity(product: MockProduct): string {
  return [product.brand, product.baseColour, product.title].map((part) => (part ?? "").trim().toLowerCase()).join("|");
}

function rank(brief: ShoppingBrief, candidates: Candidate[]): Ranked {
  const rejected = new Map<string, number>();
  const shown: { candidate: Candidate; ticks: Tick[] }[] = [];
  for (const candidate of candidates) {
    const ticks = ticksFor(brief, candidate.checks);
    const failed = brief.must_haves.find((m, index) => ticks[index]?.status === "fail");
    const problem = limitProblem(brief, candidate) ?? (failed ? failureLabel(failed) : null);
    if (problem) rejected.set(problem, (rejected.get(problem) ?? 0) + 1);
    else shown.push({ candidate, ticks });
  }
  const cheapest = Math.min(...shown.map((s) => s.candidate.total));
  const soonest = shown.map((s) => s.candidate.delivery).filter((d): d is string => Boolean(d)).sort()[0] ?? null;
  const entries = shown
    .map((s) => ({ ...s, confirmed: s.ticks.every((t) => t.status === "pass"), points: score(brief, s.candidate, cheapest, soonest) }))
    .sort((a, b) => Number(a.confirmed === false) - Number(b.confirmed === false) || b.points - a.points);
  const seen = new Set<string>();
  const unique = entries.filter((entry) => {
    const key = identity(entry.candidate.product);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const cards = unique.map((e) => ({ confirmed: e.confirmed, card: resultCard(e.candidate, e.ticks, e.confirmed, e.points) }));
  return {
    matches: cards.filter((c) => c.confirmed).map((c) => c.card),
    almost: cards.filter((c) => !c.confirmed).map((c) => c.card),
    rejected: Object.fromEntries(rejected),
    duplicates: entries.length - unique.length,
  };
}

// ---------- What to give up, as agent/tradeoffs.py does it ----------

function raisedBudget(budget: number): number {
  return Math.ceil((budget * 1.2) / 50) * 50;
}

function matchCount(brief: ShoppingBrief, candidates: Candidate[]): number {
  return rank(brief, candidates).matches.length;
}

function suggestTradeoffs(brief: ShoppingBrief, candidates: Candidate[]): Tradeoff[] {
  const now = matchCount(brief, candidates);
  if (now >= 3) return [];
  const limits: Tradeoff[] = [];
  if (brief.deadline_date) {
    for (const extra of [1, 2]) {
      const later = addDays(brief.deadline_date, extra);
      const matches = matchCount({ ...brief, deadline_date: later }, candidates);
      if (matches > now) {
        limits.push({ relax: "deadline", to: later, matches });
        break;
      }
    }
  }
  if (brief.budget_max_inr !== null) {
    const raised = raisedBudget(brief.budget_max_inr);
    const matches = matchCount({ ...brief, budget_max_inr: raised }, candidates);
    if (matches > now) limits.push({ relax: "budget", to: raised, matches });
  }
  const dropped: Tradeoff[] = [];
  for (const attribute of new Set(brief.must_haves.map((m) => m.attribute))) {
    const matches = matchCount({ ...brief, must_haves: brief.must_haves.filter((m) => m.attribute !== attribute) }, candidates);
    if (matches > now) dropped.push({ relax: `must_have:${attribute}`, to: null, matches });
  }
  dropped.sort((a, b) => b.matches - a.matches);
  return [...limits, ...dropped].slice(0, 4);
}

// ---------- The checking stream ----------

/** The order careful checks finish in, since they run in parallel. */
const FINISH_ORDER = [2, 0, 5, 8, 1, 11, 3, 9, 6, 10, 4, 7];

function poolFor(brief: ShoppingBrief, source: SearchSource): MockProduct[] {
  if (source === "live") return LIVE_PRODUCTS.filter((product) => product.articleType === brief.category);
  if (brief.category === SHOES) return [];
  return brief.category === "Watches" ? WATCHES : KURTAS;
}

const QUERY_NOUNS: Record<string, string> = { dial_colour: "dial", strap_material: "strap", sleeve_length: "sleeves", neckline: "neck" };

/** A shopper-style search for the checklist, like the ones Gemini sends to Google Search. */
function searchQuery(brief: ShoppingBrief): string {
  const own = brief.must_haves.filter((m) => !m.from_rule && !SHOP_ATTRIBUTES.includes(m.attribute));
  const values = own.filter((m) => m.rule === "is" && typeof m.value === "string" && m.attribute !== "other");
  const plain = values.filter((m) => !QUERY_NOUNS[m.attribute]).map((m) => String(m.value));
  const features = [
    ...values.filter((m) => QUERY_NOUNS[m.attribute]).map((m) => `${m.value} ${QUERY_NOUNS[m.attribute]}`),
    ...own.filter((m) => wantedPresence(m) === true).map((m) => attributeWords(m.attribute)),
  ];
  const parts = [...brief.not_checkable, ...plain, brief.category.toLowerCase()];
  if (features.length > 0) parts.push(`with ${features.join(" and ")}`);
  if (brief.gender && brief.gender !== "unisex") parts.push(`for ${brief.gender}`);
  if (brief.budget_max_inr !== null) parts.push(`under ₹${brief.budget_max_inr}`);
  return parts.join(" ");
}

/** What Grounding with Google Search would find for the checklist on other shops, after Google reads the pages. */
function webFor(brief: ShoppingBrief): WebEvent {
  const query = searchQuery(brief);
  const fallback = `${brief.category.toLowerCase()} online india`;
  const queries = query === brief.category.toLowerCase() ? [fallback] : [query, fallback];
  const mustHaves = brief.must_haves
    .filter((m) => !SHOP_ATTRIBUTES.includes(m.attribute))
    .map((m) => ({ id: m.id, label: agentLabel(m) }));
  const photo = LIVE_PRODUCTS.find((product) => product.articleType === brief.category)?.photo ?? null;
  return {
    type: "web",
    results: webResultsFor(query, brief.category, mustHaves, photo),
    search_entry_point_html: searchEntryPointFor(queries),
    queries,
  };
}

/** Quick-check drops, split across the reasons they'd be rejected for, so the counts add up. Never review-only details. */
function quickReasons(brief: ShoppingBrief, dropped: number): [string, number][] {
  const reasons = brief.must_haves.filter((m) => !m.from_rule && !REVIEW_ATTRIBUTES.includes(m.attribute)).map(failureLabel);
  if (brief.budget_max_inr !== null) reasons.push("over budget");
  if (reasons.length === 0) reasons.push(...brief.must_haves.filter((m) => !REVIEW_ATTRIBUTES.includes(m.attribute)).map(failureLabel));
  const split: [string, number][] = [];
  let left = dropped;
  reasons.forEach((reason, index) => {
    const take = index === reasons.length - 1 ? left : Math.ceil(left * 0.55);
    if (take > 0) split.push([reason, take]);
    left -= take;
  });
  return split;
}

export async function* streamSearch(sessionId: string, signal?: AbortSignal): AsyncGenerator<SearchEvent> {
  const session = getSession(sessionId);
  const { brief } = session;
  const source = session.source ?? "catalog";
  const pincode = brief.pincode ?? loadProfile().pincode;
  const candidates: Candidate[] = poolFor(brief, source).map((product) => ({
    product,
    checks: checksFor(product, brief),
    total: totalPrice(product, brief),
    delivery: deliveryDate(product, pincode),
  }));
  const quick = candidates.length === 0 ? [] : quickReasons(brief, source === "live" ? LIVE_QUICK_DROPS : QUICK_DROPS);
  const dropped = quick.reduce((sum, [, count]) => sum + count, 0);
  const order = FINISH_ORDER.filter((index) => index < candidates.length);

  await sleep(700, signal);
  yield { type: "candidates", count: candidates.length + dropped, dropped };

  for (const { product } of candidates) {
    yield { type: "checking", product_id: product.id, title: product.title, thumb: thumbOf(product) };
    await sleep(70, signal);
  }

  for (const [step, index] of order.entries()) {
    await sleep(240 + ((index * 137) % 5) * 80, signal);
    if (session.failNext && step === Math.min(5, order.length - 1)) {
      saveSession(sessionId, { ...session, failNext: false });
      yield { type: "error", message: "The checker lost its connection halfway. Try again.", retryable: true };
      return;
    }
    const candidate = candidates[index];
    yield {
      type: "verified",
      product_id: candidate.product.id,
      status: overall(candidate.checks),
      ticks: ticksFor(brief, candidate.checks),
    };
  }

  const ranked = rank(brief, candidates);
  const rejected = { ...ranked.rejected };
  for (const [reason, count] of quick) rejected[reason] = (rejected[reason] ?? 0) + count;
  const top = ranked.matches[0];
  const topProduct = top ? candidates.find((c) => c.product.id === top.product_id)?.product : undefined;
  saveSession(sessionId, {
    ...getSession(sessionId),
    lastTop: top && topProduct
      ? {
          total: top.total_price,
          colour: brief.category === "Watches" ? String(topProduct.attributes.dial_colour?.value ?? "") || null : colourFamily(topProduct.baseColour),
        }
      : null,
  });

  await sleep(300, signal);
  yield {
    type: "done",
    checked: candidates.length + dropped,
    passed: ranked.matches.length,
    unclear: ranked.almost.length,
    rejected: Object.fromEntries(Object.entries(rejected).sort((a, b) => b[1] - a[1])),
    duplicates: ranked.duplicates,
    tradeoffs: suggestTradeoffs(brief, candidates),
    matches: ranked.matches,
    almost: ranked.almost,
    not_checkable: brief.not_checkable,
  };

  if (source === "live") {
    await sleep(2500, signal);
    yield webFor(brief);
  }
}

// ---------- Product page and checkout ----------

function findProduct(productId: string): CatalogProduct | undefined {
  return [...KURTAS, ...WATCHES].find((product) => product.id === productId);
}

function findLive(productId: string): LiveProduct | undefined {
  return LIVE_PRODUCTS.find((product) => product.id === productId);
}

function liveView(product: LiveProduct, brief: ShoppingBrief | null): ProductDetails {
  return {
    id: product.id,
    title: product.title,
    brand: product.brand,
    gender: product.gender,
    masterCategory: product.masterCategory,
    subCategory: product.subCategory,
    articleType: product.articleType,
    baseColour: product.baseColour,
    usage: product.usage,
    group: product.group,
    description: product.description,
    attributes: product.attributes,
    catalogue_details: {},
    needs_review: [],
    price: product.price,
    sizes: product.sizes,
    warehouse: null,
    delivery_fee: 0,
    cod: null,
    returns: null,
    reviews: [],
    rating_avg: null,
    rating_count: 0,
    images: { original: product.photo, gemini: shopifyPhoto(product.photo, 1000), thumb: shopifyPhoto(product.photo, 300) },
    demo_data: false,
    source: "shopify",
    store: product.store,
    product_url: product.product_url,
    checkout_url: product.cart_url,
    shipping_note: SHIPPING_NOTE,
    sizes_in_stock: sizesInStock(product),
    ticks: brief ? ticksFor(brief, checksFor(product, brief)) : undefined,
    delivery_date: null,
    total_price: product.price.selling,
  };
}

export async function getProduct(productId: string, sessionId?: string): Promise<ProductDetails> {
  await sleep(300);
  const live = findLive(productId);
  if (live) return liveView(live, sessionId ? (loadSessions()[sessionId]?.brief ?? null) : null);
  const product = findProduct(productId);
  if (!product) throw new MockNotFound();
  const brief = sessionId ? (loadSessions()[sessionId]?.brief ?? null) : null;
  return {
    id: product.id,
    title: product.title,
    brand: product.brand,
    gender: product.gender,
    masterCategory: product.masterCategory,
    subCategory: product.subCategory,
    articleType: product.articleType,
    baseColour: product.baseColour,
    usage: product.usage,
    group: product.group,
    description: product.description,
    attributes: product.attributes,
    catalogue_details: {},
    needs_review: product.needs_review,
    price: product.price,
    sizes: product.sizes,
    warehouse: product.warehouse,
    delivery_fee: product.delivery_fee,
    cod: product.cod,
    returns: product.returns,
    reviews: product.reviews.map((review) => ({ ...review, demo: true })),
    rating_avg: product.rating_avg,
    rating_count: product.rating_count,
    images: {
      original: `${BUCKET}/original/${product.id}.jpg`,
      gemini: `${BUCKET}/gemini/${product.id}.jpg`,
      thumb: `${BUCKET}/thumb/${product.id}.jpg`,
    },
    demo_data: true,
    source: "catalog",
    store: null,
    product_url: null,
    checkout_url: null,
    sizes_in_stock: sizesInStock(product),
    ticks: brief ? ticksFor(brief, checksFor(product, brief)) : undefined,
    delivery_date: deliveryDate(product, brief?.pincode ?? loadProfile().pincode),
    total_price: totalPrice(product, brief),
  };
}

/** Live stores get a Shopify cart link for the size's variant, or the product page when no size is picked. */
export async function checkout(request: CheckoutRequest): Promise<CheckoutResponse> {
  await sleep(400);
  const live = findLive(request.product_id);
  if (live) {
    const variant = Object.keys(live.sizes).indexOf(request.size ?? "");
    return { redirect_url: variant === -1 ? live.product_url : `${live.cart_url}/${live.variant_base + variant}:1` };
  }
  if (!findProduct(request.product_id)) throw new MockNotFound();
  const params = new URLSearchParams({ product: request.product_id });
  if (request.size) params.set("size", request.size);
  return { redirect_url: `/checkout?${params.toString()}` };
}

export async function sendFeedback(request: FeedbackRequest): Promise<OkResponse> {
  await sleep(200);
  if (request.action !== "dislike") saveProfile({ ...loadProfile(), has_taste: true });
  return { ok: true };
}

// ---------- Rules and profile ----------

export async function getRules(): Promise<RulesBody> {
  await sleep(250);
  return { rules: loadProfile().rules };
}

export async function saveRules(body: RulesBody): Promise<RulesBody> {
  await sleep(350);
  const rules = body.rules.map((rule) => ({ ...rule, from_rule: true }));
  saveProfile({ ...loadProfile(), rules });
  return { rules };
}

export async function getMe(): Promise<Me> {
  await sleep(300);
  const profile = loadProfile();
  return { name: null, email: null, pincode: profile.pincode, rules: profile.rules, has_taste: profile.has_taste };
}

export async function updateMe(change: MeUpdate): Promise<OkResponse> {
  await sleep(300);
  saveProfile({ ...loadProfile(), ...change });
  return { ok: true };
}

export async function clearTaste(): Promise<OkResponse> {
  await sleep(300);
  saveProfile({ ...loadProfile(), has_taste: false });
  return { ok: true };
}

export async function deleteMe(): Promise<OkResponse> {
  await sleep(500);
  saveProfile({ rules: [], pincode: null, language: "en", has_taste: false });
  try {
    browserStorage("session")?.removeItem(SESSIONS_KEY);
  } catch {}
  return { ok: true };
}

// ---------- Store pages ----------

function catalogItem(product: CatalogProduct): CatalogItem {
  return {
    id: product.id,
    title: product.title,
    brand: product.brand,
    gender: product.gender,
    article_type: product.articleType,
    base_colour: product.baseColour,
    usage: product.usage,
    price: product.price,
    thumb: `${BUCKET}/thumb/${product.id}.jpg`,
    photo: `${BUCKET}/gemini/${product.id}.jpg`,
    rating_avg: product.rating_avg,
    rating_count: product.rating_count,
  };
}

const MOCK_GENDERS: Record<string, GenderFilter> = { men: "men", mens: "men", man: "men", women: "women", womens: "women", ladies: "women" };

function stemOf(word: string): string {
  return word.length > 3 && word.endsWith("s") ? word.slice(0, -1) : word;
}

/** A small stand-in for agent/browse.py: every word must appear in the product's words; gender words filter. */
export async function getCatalog(query: CatalogQuery): Promise<CatalogPage> {
  await sleep(250);
  const words = (query.q ?? "").toLowerCase().match(/[a-z0-9]+/g) ?? [];
  const gender = query.gender ?? words.map((word) => MOCK_GENDERS[word]).find(Boolean) ?? null;
  const wanted = words.filter((word) => !MOCK_GENDERS[word] && !["for", "with", "and", "a", "the"].includes(word)).map(stemOf);
  const matching = [...KURTAS, ...WATCHES].filter((product) => {
    if (gender && product.gender !== gender && product.gender !== "unisex") return false;
    if (query.min_rating && (product.rating_avg ?? 0) < query.min_rating) return false;
    const own = `${product.title} ${product.brand ?? ""} ${product.articleType} ${product.baseColour ?? ""}`.toLowerCase();
    const stems = new Set((own.match(/[a-z0-9]+/g) ?? []).map(stemOf));
    return wanted.every((word) => stems.has(word));
  });
  const categories = countCategories(matching);
  const filtered = query.category ? matching.filter((product) => product.articleType === query.category) : matching;
  const sorted = [...filtered].sort((a, b) => {
    if (query.sort === "price_low") return a.price.selling - b.price.selling;
    if (query.sort === "price_high") return b.price.selling - a.price.selling;
    if (query.sort === "rating") return (b.rating_avg ?? 0) - (a.rating_avg ?? 0);
    return 0;
  });
  const page = query.page ?? 1;
  const size = query.page_size ?? 24;
  return {
    items: sorted.slice((page - 1) * size, page * size).map(catalogItem),
    total: sorted.length,
    page,
    page_size: size,
    query: query.q ?? "",
    understood: gender && !query.gender ? { gender } : {},
    categories,
    partial: false,
  };
}

function countCategories(products: CatalogProduct[]): CategoryCount[] {
  const counts = new Map<string, number>();
  for (const product of products) counts.set(product.articleType, (counts.get(product.articleType) ?? 0) + 1);
  return [...counts].map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count);
}

export async function getCategories(gender: GenderFilter | null): Promise<CategoryCount[]> {
  await sleep(100);
  return countCategories([...KURTAS, ...WATCHES].filter((p) => !gender || p.gender === gender || p.gender === "unisex"));
}

// ---------- Complete the look ----------

const LOOK_WEIGHTS = { colour: 25, style: 25, occasion: 20, silhouette: 15, preferences: 10, budget: 5 };

/** A stand-in for agent/outfit.py on the small mock catalogue (kurtas and watches): fixed scores, no Gemini. */
export async function getLooks(productId: string, request: LooksRequest): Promise<Looks> {
  await sleep(700);
  const all = [...KURTAS, ...WATCHES];
  const anchor = all.find((product) => product.id === productId);
  if (!anchor) throw new MockNotFound();
  const slot: LookSlot = anchor.articleType === "Watches" ? "top" : "watch";
  const room = request.budget_max_inr ? request.budget_max_inr - anchor.price.selling : null;
  const fits = all.filter(
    (product) =>
      product.articleType !== anchor.articleType &&
      (anchor.gender === "unisex" || product.gender === anchor.gender || product.gender === "unisex") &&
      (room === null || product.price.selling <= room),
  );
  const suggestions = fits.slice(0, 4).map((product, index): LookSuggestion => {
    const parts: LookPart[] = [
      { name: "colour", weight: LOOK_WEIGHTS.colour, score: 0.9 - index * 0.1 },
      { name: "style", weight: LOOK_WEIGHTS.style, score: 0.8 - index * 0.05 },
      { name: "occasion", weight: LOOK_WEIGHTS.occasion, score: 0.8 },
      { name: "silhouette", weight: LOOK_WEIGHTS.silhouette, score: 0.7 },
      { name: "preferences", weight: LOOK_WEIGHTS.preferences, score: null },
      { name: "budget", weight: LOOK_WEIGHTS.budget, score: room === null ? null : 1 },
    ];
    const known = parts.filter((part) => part.score !== null);
    const match = Math.round((100 * known.reduce((sum, part) => sum + part.weight * (part.score ?? 0), 0)) / known.reduce((sum, part) => sum + part.weight, 0));
    return { slot, product: catalogItem(product), match, parts, reason: "Mock styling note: the real agent asks Gemini." };
  });
  const occasion = request.occasion ?? (anchor.usage === "Ethnic" ? "festive" : "casual");
  const looks: Look[] = suggestions.length
    ? [
        {
          occasion,
          title: "Sample look",
          summary: "Mock mode: a fixed example, not a styling opinion.",
          pieces: [{ slot, choices: suggestions }],
          ideas: anchor.articleType === "Watches" ? [] : ["a light dupatta"],
          total_price: anchor.price.selling + fits[0].price.selling,
          left_out: [],
        },
      ]
    : [];
  return {
    product_id: anchor.id,
    looks,
    rows: suggestions.length ? [{ kind: slot === "top" ? "pair" : "accessories", items: suggestions }] : [],
    weights: LOOK_WEIGHTS,
    budget_max_inr: request.budget_max_inr ?? null,
    sample_data: true,
    demo_prices: true,
    note: looks.length ? null : "We couldn't find products in our catalogue that go with this one.",
  };
}

// ---------- Saved chats ----------
// Stored in localStorage as the agent's chats/{id} documents are, with the same time rules (agent/chats.py): a
// product's updated_at moves only when the shopper chats about it. The searches the products point to are kept
// beside the chat, because the sessions themselves last only while the tab is open.

const CHATS_KEY = "ecommai.mock.chats";
const KEEP_DAYS = 90;
const KEEP_CHATS = 30;
const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_TITLE = 80;

interface MockChatRecord {
  chat: SavedChat;
  sessions: Record<string, MockSession>;
  queryTexts: Record<string, string>;
}

const PRODUCT_NAMES: Record<string, string> = {
  Kurtas: "kurta",
  Kurtis: "kurti",
  Tunics: "tunic",
  Shirts: "shirt",
  Tshirts: "t-shirt",
  Tops: "top",
  Jeans: "jeans",
  Trousers: "trousers",
  "Casual Shoes": "casual shoes",
  "Sports Shoes": "sports shoes",
  "Formal Shoes": "formal shoes",
  Watches: "watch",
  Handbags: "handbag",
  Backpacks: "backpack",
  Clutches: "clutch",
  Sunglasses: "sunglasses",
};

function productName(category: string): string | null {
  return PRODUCT_NAMES[category] ?? null;
}

function loadChats(): Record<string, MockChatRecord> {
  return readJson<Record<string, MockChatRecord>>(browserStorage("local"), CHATS_KEY) ?? {};
}

function chatRecord(chatId: string): MockChatRecord | null {
  return loadChats()[chatId] ?? null;
}

function saveChatRecord(record: MockChatRecord) {
  writeJson(browserStorage("local"), CHATS_KEY, { ...loadChats(), [record.chat.chat_id]: record });
}

function newChatRecord(chatId: string, moment: string): MockChatRecord {
  const chat: SavedChat = {
    chat_id: chatId,
    title: "",
    timestamps: { created_at: moment, updated_at: moment, last_opened_at: moment },
    messages: [],
    pending: null,
    products: [],
  };
  return { chat, sessions: {}, queryTexts: {} };
}

function mustHaveForm(mustHave: MustHave): unknown {
  switch (mustHave.rule) {
    case "is":
      return mustHave.value;
    case "is_not":
      return { not: mustHave.value };
    case "present":
    case "absent":
      return mustHave.rule === "present";
    case "at_most":
      return { at_most: Number(mustHave.value) };
    case "at_least":
      return { at_least: Number(mustHave.value) };
  }
}

function joined(forms: unknown[]): unknown {
  return forms.length === 1 ? forms[0] : forms;
}

/** The brief as a product's filters, as agent/chats.py writes them: colour, gender, size, the rest, then the limits. */
function chatFilters(brief: ShoppingBrief, source: SearchSource): Record<string, unknown> {
  const ownSize = ["Handbags", "Backpacks", "Clutches"].includes(brief.category);
  const forms = new Map<string, unknown[]>();
  for (const mustHave of brief.must_haves) {
    if (mustHave.from_rule) continue;
    forms.set(mustHave.attribute, [...(forms.get(mustHave.attribute) ?? []), mustHaveForm(mustHave)]);
  }
  const take = (attribute: string): unknown[] | undefined => {
    const found = forms.get(attribute);
    forms.delete(attribute);
    return found;
  };
  const filters: Record<string, unknown> = {};
  const colour = take("colour");
  if (colour) filters.colour = joined(colour);
  if (brief.gender) filters.gender = brief.gender;
  const ownSizes = ownSize ? take("size") : undefined;
  if (ownSizes) filters.size = joined(ownSizes);
  else if (!ownSize && brief.size) filters.size = brief.size;
  for (const [attribute, found] of forms) filters[attribute] = joined(found);
  if (brief.budget_max_inr !== null) filters.budget_max_inr = brief.budget_max_inr;
  if (brief.min_rating !== null) filters.min_rating = brief.min_rating;
  if (brief.deadline_date) filters.deadline_date = brief.deadline_date;
  if (brief.nice_to_haves.length > 0) {
    const prefer = new Map<string, unknown[]>();
    for (const nice of brief.nice_to_haves) prefer.set(nice.attribute, [...(prefer.get(nice.attribute) ?? []), nice.value]);
    filters.prefer = Object.fromEntries([...prefer].map(([attribute, values]) => [attribute, joined(values)]));
  }
  filters.source = source;
  return filters;
}

/** Saves this search as its product's entry in the chat. Only this product's updated_at moves, and the chat's. */
function keepInChat(chatId: string, sessionId: string, session: MockSession, queryText: string) {
  const name = productName(session.brief.category);
  const moment = new Date().toISOString();
  const record = chatRecord(chatId) ?? newChatRecord(chatId, moment);
  if (name && !session.brief.missing.includes("product")) {
    const old = record.chat.products.find((p) => p.product === name);
    const entry: ChatProduct = {
      product: name,
      timestamps: { created_at: old?.timestamps.created_at ?? moment, updated_at: moment },
      filters: chatFilters(session.brief, session.source ?? "catalog"),
      session_id: sessionId,
    };
    record.chat.products = old ? record.chat.products.map((p) => (p.product === name ? entry : p)) : [...record.chat.products, entry];
    record.chat.timestamps.updated_at = moment;
    record.sessions[sessionId] = session;
    record.queryTexts[sessionId] = queryText;
  }
  saveChatRecord(record);
}

function titleOf(text: string): string {
  const words = text.split(/\s+/).filter(Boolean).join(" ");
  if (words.length <= MAX_TITLE) return words;
  return `${words.slice(0, MAX_TITLE).replace(/\s+\S*$/, "").replace(/[ ,.;:]+$/, "")}…`;
}

export async function saveChatMessages(chatId: string, body: ChatMessagesBody): Promise<OkResponse> {
  const moment = new Date().toISOString();
  const record = chatRecord(chatId) ?? newChatRecord(chatId, moment);
  const chat = record.chat;
  const changed = JSON.stringify([chat.messages, chat.pending]) !== JSON.stringify([body.messages, body.pending]);
  chat.messages = body.messages;
  chat.pending = body.pending;
  if (!chat.title) chat.title = titleOf(body.messages.find((m) => m.role === "user")?.text ?? "");
  if (changed) chat.timestamps.updated_at = moment;
  saveChatRecord(record);
  return { ok: true };
}

export async function listChats(): Promise<{ chats: ChatSummary[] }> {
  const all = Object.values(loadChats()).sort((a, b) => b.chat.timestamps.updated_at.localeCompare(a.chat.timestamps.updated_at));
  const oldest = Date.now() - KEEP_DAYS * DAY_MS;
  const kept = all.slice(0, KEEP_CHATS).filter((r) => Date.parse(r.chat.timestamps.updated_at) >= oldest);
  const left = Object.fromEntries(kept.map((r) => [r.chat.chat_id, r]));
  if (kept.length < all.length) writeJson(browserStorage("local"), CHATS_KEY, left);
  return {
    chats: kept.map(({ chat }) => ({
      chat_id: chat.chat_id,
      title: chat.title,
      timestamps: chat.timestamps,
      products: chat.products.map((p) => p.product),
    })),
  };
}

/** Results aren't kept here, so a reopened chat always searches again. */
export async function openChat(chatId: string, product?: string): Promise<OpenChatResponse> {
  await sleep(150);
  const record = chatRecord(chatId);
  if (!record) throw new MockNotFound();
  const { chat } = record;
  const welcomeBack = Date.now() - Date.parse(chat.timestamps.last_opened_at) > DAY_MS;
  const entries = [...chat.products].sort((a, b) => b.timestamps.updated_at.localeCompare(a.timestamps.updated_at));
  const entry = entries.find((p) => p.product === product) ?? entries[0];
  const session = entry ? record.sessions[entry.session_id] : undefined;
  if (entry && session) saveSession(entry.session_id, session);
  chat.timestamps.last_opened_at = new Date().toISOString();
  saveChatRecord(record);
  return {
    chat,
    welcome_back: welcomeBack,
    active:
      entry && session
        ? {
            product: entry.product,
            session_id: entry.session_id,
            brief: session.brief,
            source: session.source ?? "catalog",
            query_text: record.queryTexts[entry.session_id] ?? "",
            results: null,
          }
        : null,
  };
}

// ---------- Demo orders ----------

let mockOrders: DemoOrder[] = [];

export async function placeOrder(request: OrderRequest): Promise<OrderResponse> {
  await sleep(300);
  const product = [...KURTAS, ...WATCHES].find((p) => p.id === request.product_id);
  if (!product) throw new MockNotFound();
  const order: DemoOrder = {
    id: `mock-${mockOrders.length + 1}`,
    product_id: product.id,
    title: product.title,
    article_type: product.articleType,
    gender: product.gender,
    base_colour: product.baseColour ?? null,
    usage: product.usage ?? null,
    size: request.size,
    price_inr: product.price.selling,
    ordered_at: new Date().toISOString(),
    source: "demo_catalog",
    seeded: false,
  };
  mockOrders = [order, ...mockOrders];
  return { order };
}

export async function getOrders(): Promise<OrderList> {
  await sleep(200);
  return { orders: mockOrders };
}

export async function clearOrders(): Promise<OkResponse> {
  await sleep(200);
  mockOrders = [];
  return { ok: true };
}

/** The mock catalogue is too small for suggestions that go with orders: always empty. */
export async function getOrderSuggestions(): Promise<OrderSuggestions> {
  await sleep(200);
  return { items: [], ideas: [], note: null, sample_data: true, demo_orders: true };
}
