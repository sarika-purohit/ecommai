import { strings } from "@/lib/strings";

const rupeeFormat = new Intl.NumberFormat(strings.locale, {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const dayFormat = new Intl.DateTimeFormat(strings.locale, {
  weekday: "short",
  day: "numeric",
  month: "short",
});

export function rupees(amount: number): string {
  return rupeeFormat.format(amount);
}

/** Parses YYYY-MM-DD as a calendar day, so the shown day never shifts with the browser's time zone. */
export function parseIsoDate(iso: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

export function shortDay(iso: string): string {
  const date = parseIsoDate(iso);
  return date ? dayFormat.format(date) : iso;
}

export function toIsoDate(date: Date): string {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

export function todayInIndia(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

const indiaDayFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const indiaShortFormat = new Intl.DateTimeFormat(strings.locale, { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });

/** The calendar day (YYYY-MM-DD) an agent time falls on in India. */
export function dayInIndia(time: string | Date): string {
  return indiaDayFormat.format(typeof time === "string" ? new Date(time) : time);
}

/** "12 Oct": the day an agent time falls on in India. */
export function shortDayInIndia(time: string): string {
  return indiaShortFormat.format(new Date(time));
}

export function addDays(iso: string, days: number): string {
  const date = parseIsoDate(iso) ?? new Date();
  date.setDate(date.getDate() + days);
  return toIsoDate(date);
}

export const PINCODE_PATTERN = /^[1-9][0-9]{5}$/;

export function isValidPincode(value: string): boolean {
  return PINCODE_PATTERN.test(value);
}
