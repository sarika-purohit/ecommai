import { rupees, shortDay } from "@/lib/format";
import { strings, type AttributeWords } from "@/lib/strings";
import type { DetailValue, MustHave, NiceToHave, Rule, ShoppingBrief, Tradeoff } from "@/lib/types";

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

export function tradeoffLabel(tradeoff: Tradeoff): string {
  if (tradeoff.relax === "deadline" && typeof tradeoff.to === "string") {
    return strings.results.tradeoffDeadline(shortDay(tradeoff.to));
  }
  if (tradeoff.relax === "budget" && typeof tradeoff.to === "number") {
    return strings.results.tradeoffBudget(rupees(tradeoff.to));
  }
  const attribute = tradeoff.relax.startsWith("must_have:") ? tradeoff.relax.slice("must_have:".length) : tradeoff.relax;
  return strings.results.tradeoffMustHave(attributeName(attribute));
}
