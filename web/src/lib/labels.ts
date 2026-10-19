import { rupees, shortDay } from "@/lib/format";
import { strings, type AttributeWords } from "@/lib/strings";
import type { DetailValue, MustHave, NiceToHave, Rule, ShoppingBrief, Tick, Tradeoff } from "@/lib/types";

const words = strings.labels;

export function attributeWords(attribute: string): AttributeWords {
  return words.attributes[attribute] ?? { name: capitalise(attribute.replace(/_/g, " ")) };
}

export function attributeName(attribute: string): string {
  return attributeWords(attribute).name;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function valueText(value: DetailValue): string {
  const text = String(value);
  return words.values[text] ?? text;
}

/** True when the rule asks for the detail to be there, false when it asks for it to be missing, null otherwise. */
function wantedPresence(rule: Rule, value: DetailValue): boolean | null {
  if (rule === "present" || rule === "absent") {
    return Boolean(value) === (rule === "present");
  }
  if (rule === "is" || rule === "is_not") {
    const text = String(value).toLowerCase();
    if (text === "yes" || text === "no") return (text === "yes") === (rule === "is");
    if (text === "none") return rule === "is_not";
  }
  return null;
}

function detailLabel(attribute: string, rule: Rule, value: DetailValue): string {
  const known = words.attributes[attribute];
  const entry = attributeWords(attribute);
  const wanted = wantedPresence(rule, value);
  if (wanted !== null) {
    const thing = entry.presence ?? entry.noun ?? entry.name.toLowerCase();
    return capitalise(wanted ? words.present(thing) : words.absent(thing));
  }
  const text = valueText(value);
  switch (rule) {
    case "is":
      if (entry.phrase) return capitalise(entry.phrase(text));
      return capitalise(known ? words.is(text, entry.noun) : words.unknownIs(entry.name, text));
    case "is_not":
      if (entry.negated) return capitalise(entry.negated(text));
      return capitalise(known ? words.isNot(text, entry.noun) : words.unknownIsNot(entry.name, text));
    case "at_most":
      return capitalise(words.atMost(entry.name, text));
    case "at_least":
      return capitalise(words.atLeast(entry.name, text));
    default:
      return capitalise(text);
  }
}

export function mustHaveLabel(mustHave: Pick<MustHave, "attribute" | "rule" | "value">): string {
  return detailLabel(mustHave.attribute, mustHave.rule, mustHave.value);
}

export function niceToHaveLabel(nice: NiceToHave): string {
  return detailLabel(nice.attribute, "is", nice.value);
}

export function categoryLabel(brief: Pick<ShoppingBrief, "category" | "gender">): string {
  const category = words.categories[brief.category] ?? brief.category;
  const gender = brief.gender ? words.genders[brief.gender] : null;
  return words.category(category, gender);
}

/** A tick shows the same words as its chip on the checklist; the agent's own label is the fallback. */
export function tickLabel(tick: Tick, brief: ShoppingBrief | null): string {
  const mustHave = brief?.must_haves.find((m) => m.id === tick.must_have_id);
  return mustHave ? mustHaveLabel(mustHave) : capitalise(tick.label);
}

/** The attribute a "must_have:<attribute>" trade-off drops, or null for deadline and budget. */
export function tradeoffAttribute(tradeoff: Tradeoff): string | null {
  return tradeoff.relax.startsWith("must_have:") ? tradeoff.relax.slice("must_have:".length) : null;
}

export function tradeoffLabel(tradeoff: Tradeoff, brief: ShoppingBrief): string {
  const text = strings.results;
  if (tradeoff.relax === "deadline" && typeof tradeoff.to === "string") return text.tradeoffDeadline(shortDay(tradeoff.to));
  if (tradeoff.relax === "budget" && tradeoff.to !== null) return text.tradeoffBudget(rupees(Number(tradeoff.to)));
  const attribute = tradeoffAttribute(tradeoff) ?? tradeoff.relax;
  const dropped = brief.must_haves.filter((m) => m.attribute === attribute);
  const label = dropped.length === 1 ? mustHaveLabel(dropped[0]) : attributeName(attribute);
  return dropped.length > 0 && dropped.every((m) => m.from_rule) ? text.tradeoffRule(label) : text.tradeoffMustHave(label);
}

/** The filters memory filled in, as short words: ["Men", "Yellow", "M"]. */
export function rememberedLabels(brief: ShoppingBrief): string[] {
  const labels: string[] = [];
  for (const key of brief.remembered) {
    if (key === "gender" && brief.gender) labels.push(capitalise(words.genders[brief.gender] ?? brief.gender));
    else if (key === "size" && brief.size) labels.push(brief.size);
    else if (key === "budget_max_inr" && brief.budget_max_inr !== null) labels.push(words.budget(rupees(brief.budget_max_inr)));
    else if (key === "min_rating" && brief.min_rating !== null) labels.push(words.minRating(brief.min_rating));
    else {
      labels.push(...brief.must_haves.filter((m) => !m.from_rule && m.attribute === key).map(mustHaveLabel));
      const mix = brief.nice_to_haves.filter((n) => n.weight && n.attribute === key).sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
      if (mix.length > 0) labels.push(words.mix(mix.map(niceToHaveLabel)));
    }
  }
  return labels;
}

/** A nice-to-have chip's words; a colour from memory's mix shows its share: "Blue · 51%". */
export function niceToHaveChip(nice: NiceToHave): string {
  const label = niceToHaveLabel(nice);
  return nice.weight ? words.share(label, Math.round(nice.weight * 100)) : label;
}
