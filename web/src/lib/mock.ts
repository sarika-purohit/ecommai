// Sample agent responses for building the screens before the real agent API runs.
// Everything here stands in for server data, so it is not in the strings file.
// Checks come from each product's saved details, and ranking and trade-offs follow agent/rank.py and
// agent/tradeoffs.py, so the counts on "what to give up" match what one tap gives.

import { addDays, todayInIndia } from "@/lib/format";
import { KURTAS, WATCHES, type CatalogProduct } from "@/lib/mock-catalog";
import type {
  BriefChange,
  BriefInput,
  BriefResponse,
  BriefUpdateResponse,
  Check,
  CheckoutRequest,
  CheckoutResponse,
  DetailValue,
  Evidence,
  FeedbackRequest,
  Me,
  MeUpdate,
  MustHave,
  NiceToHave,
  OkResponse,
  ProductDetails,
  ResultCard,
  RulesBody,
  SearchEvent,
  ShoppingBrief,
  Status,
  Tick,
  Tradeoff,
} from "@/lib/types";

type Detail = Omit<MustHave, "id" | "from_rule">;

interface MockSession {
  brief: ShoppingBrief;
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
};

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
  const group = category === "Watches" ? "watches" : "tops";
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
    nice_to_haves: /\b(office|work|formal)\b/i.test(text) && category !== "Watches" ? [{ attribute: "usage", value: "office" }] : [],
    rules_applied: [],
    budget_max_inr: budgetFrom(text),
    deadline_date: deadlineFrom(text),
    pincode: input.pincode,
    size: null,
    reference_image_url: typeof input.image === "string" ? input.image : null,
    language: input.language,
    missing: [],
    not_checkable: brandsIn(text),
    question: null,
  };
}

export async function createBrief(input: BriefInput): Promise<BriefResponse> {
  await sleep(900);
  const text = input.text ?? "";
  const category = /\bwatch/i.test(text) ? "Watches" : "Kurtas";
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
    questionTarget,
    rulesOff: [],
    nextId: details.length + 1,
    lastTop: null,
    failNext: /\btest error\b/i.test(text),
  };
  const id = `mock-${Date.now().toString(36)}`;
  saveSession(id, session);
  return { session_id: id, brief: session.brief, question: session.brief.question };
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
    case "answer":
      return answerQuestion(session, change.answer);
    case "text":
      return applyText(session, change.text);
  }
}

export async function updateBrief(sessionId: string, change: BriefChange): Promise<BriefUpdateResponse> {
  await sleep(350);
  const next = applyChange(getSession(sessionId), change);
  saveSession(sessionId, next);
  return { brief: next.brief };
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

function checksFor(product: CatalogProduct, brief: ShoppingBrief): Check[] {
  return brief.must_haves.map((mustHave) => checkOne(product, mustHave));
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

function deliveryDate(product: CatalogProduct, pincode: string | null): string | null {
  if (!pincode) return null;
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

function totalPrice(product: CatalogProduct, brief: ShoppingBrief | null): number {
  const codFee = brief && wantsCod(brief) && product.cod.available ? product.cod.fee : 0;
  return product.price.selling + product.delivery_fee + codFee;
}

function sizeOrder(size: string): number {
  const index = TOP_SIZES.indexOf(size);
  return index === -1 ? TOP_SIZES.length : index;
}

function sizesInStock(product: CatalogProduct): string[] {
  return Object.entries(product.sizes)
    .filter(([, count]) => count > 0)
    .map(([size]) => size)
    .sort((a, b) => sizeOrder(a) - sizeOrder(b));
}

// ---------- Ranking, as agent/rank.py does it ----------

interface Candidate {
  product: CatalogProduct;
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
  const rating = product.rating_avg === null ? 0 : (product.rating_avg / 5) * (product.rating_count / (product.rating_count + 20));
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
  parts.push(`${agentRupees(candidate.total)} delivered`);
  if (candidate.delivery) parts.push(`arrives ${agentDay(candidate.delivery)}`);
  const { rating_avg: rating, rating_count: count } = candidate.product;
  if (rating !== null) parts.push(`${rating.toFixed(1)}★ (${count} ${count === 1 ? "review" : "reviews"})`);
  return parts.filter(Boolean).join(" · ");
}

function resultCard(candidate: Candidate, ticks: Tick[], confirmed: boolean, points: number): ResultCard {
  const { product } = candidate;
  return {
    product_id: product.id,
    title: product.title,
    total_price: candidate.total,
    delivery_date: candidate.delivery,
    ticks,
    reason: reasonFor(candidate, ticks, confirmed),
    score: Math.round(points * 10_000) / 10_000,
    brand: product.brand,
    thumb: `${BUCKET}/thumb/${product.id}.jpg`,
    photo: `${BUCKET}/gemini/${product.id}.jpg`,
    original: `${BUCKET}/original/${product.id}.jpg`,
    price: product.price,
    delivery_fee: product.delivery_fee,
    cod: product.cod,
    returns: product.returns,
    rating_avg: product.rating_avg,
    rating_count: product.rating_count,
    demo_data: true,
    sizes_in_stock: sizesInStock(product),
  };
}

function identity(product: CatalogProduct): string {
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

function poolFor(brief: ShoppingBrief): CatalogProduct[] {
  return brief.category === "Watches" ? WATCHES : KURTAS;
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
  const pincode = brief.pincode ?? loadProfile().pincode;
  const candidates: Candidate[] = poolFor(brief).map((product) => ({
    product,
    checks: checksFor(product, brief),
    total: totalPrice(product, brief),
    delivery: deliveryDate(product, pincode),
  }));
  const quick = quickReasons(brief, QUICK_DROPS);
  const dropped = quick.reduce((sum, [, count]) => sum + count, 0);

  await sleep(700, signal);
  yield { type: "candidates", count: candidates.length + dropped, dropped };

  for (const { product } of candidates) {
    yield { type: "checking", product_id: product.id, title: product.title, thumb: `${BUCKET}/thumb/${product.id}.jpg` };
    await sleep(70, signal);
  }

  for (const [step, index] of FINISH_ORDER.entries()) {
    await sleep(240 + ((index * 137) % 5) * 80, signal);
    if (session.failNext && step === 5) {
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
}

// ---------- Product page and checkout ----------

function findProduct(productId: string): CatalogProduct | undefined {
  return [...KURTAS, ...WATCHES].find((product) => product.id === productId);
}

export async function getProduct(productId: string, sessionId?: string): Promise<ProductDetails> {
  await sleep(300);
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
    sizes_in_stock: sizesInStock(product),
    ticks: brief ? ticksFor(brief, checksFor(product, brief)) : undefined,
    delivery_date: deliveryDate(product, brief?.pincode ?? loadProfile().pincode),
    total_price: totalPrice(product, brief),
  };
}

export async function checkout(request: CheckoutRequest): Promise<CheckoutResponse> {
  await sleep(400);
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
