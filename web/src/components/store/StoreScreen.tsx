"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useId, useState, type FormEvent } from "react";
import { AccountButton } from "@/components/AppHeader";
import { AiChat } from "@/components/chat/AiChat";
import { ArrowLeftIcon, CloseIcon, SearchIcon, SparkleIcon, TagMark } from "@/components/icons";
import { AiTile, ProductTile } from "@/components/store/ProductTile";
import { getCatalog } from "@/lib/api";
import { aiProgressStore, chatOpenStore, chatStore, newChat, showStore, type AiProgress } from "@/lib/chat";
import { errorMessage } from "@/lib/errors";
import { rupees } from "@/lib/format";
import { categoryLabel } from "@/lib/labels";
import { resultsStore, sessionStore, sourceOf, type SearchSession, type StoredResults } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { CatalogItem, CatalogPage, CatalogSort, GenderFilter } from "@/lib/types";

const text = strings.store;
const SORTS: CatalogSort[] = ["relevance", "price_low", "price_high", "rating"];
const GENDERS: ("all" | GenderFilter)[] = ["all", "men", "women"];

interface Filters {
  q: string;
  category: string | null;
  gender: GenderFilter | null;
  /** Lowest star rating, from the "4★ & up" filter. */
  rating: number | null;
  sort: CatalogSort;
}

const RATING_FILTER = 4;

function filtersFrom(params: URLSearchParams): Filters {
  const gender = params.get("gender");
  const sort = params.get("sort") as CatalogSort | null;
  const rating = Number(params.get("rating"));
  return {
    q: params.get("q") ?? "",
    category: params.get("category"),
    gender: gender === "men" || gender === "women" ? gender : null,
    rating: rating >= 1 && rating <= 5 ? rating : null,
    sort: sort && SORTS.includes(sort) ? sort : "relevance",
  };
}

function hrefFor(filters: Filters): string {
  const params = new URLSearchParams();
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.category) params.set("category", filters.category);
  if (filters.gender) params.set("gender", filters.gender);
  if (filters.rating) params.set("rating", String(filters.rating));
  if (filters.sort !== "relevance") params.set("sort", filters.sort);
  const search = params.toString();
  return search ? `/?${search}` : "/";
}

type Load =
  | { status: "loading" }
  | { status: "ready"; page: CatalogPage; items: CatalogItem[]; more: "idle" | "loading" }
  | { status: "error"; message: string };

/** The shop: a normal product list with keyword search, and the AI chat a tap away. */
export function StoreScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const filters = filtersFrom(new URLSearchParams(params.toString()));
  const isClient = useIsClient();
  const chat = chatStore.use();
  const session = sessionStore.use();
  const results = resultsStore.use();
  const progress = aiProgressStore.use();
  const chatOpen = chatOpenStore.use() ?? false;

  const aiSession = isClient && chat?.showResults && session && chat.sessionId === session.sessionId ? session : null;
  const aiResults = aiSession && results && results.sessionId === aiSession.sessionId ? results : null;
  const showAi = Boolean(aiSession && (progress || aiResults));

  function go(next: Partial<Filters>) {
    router.push(hrefFor({ ...filters, ...next }), { scroll: false });
  }

  function search(q: string) {
    // A new search from the search bar is a fresh start for the AI too.
    newChat();
    router.push(hrefFor({ q, category: null, gender: filters.gender, rating: filters.rating, sort: "relevance" }));
  }

  return (
    <div className={chatOpen ? "lg:pr-[28rem]" : undefined}>
      <StoreHeader query={filters.q} onSearch={search} />
      <main className="wide pb-28 pt-4">
        {showAi && aiSession ? (
          <AiResults session={aiSession} results={aiResults} progress={progress} />
        ) : (
          <Catalog filters={filters} go={go} />
        )}
      </main>
      <AiChat />
    </div>
  );
}

function StoreHeader({ query, onSearch }: { query: string; onSearch: (q: string) => void }) {
  const [draft, setDraft] = useState(query);
  const [shown, setShown] = useState(query);
  const inputId = useId();
  if (shown !== query) {
    setShown(query);
    setDraft(query);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSearch(draft);
  }

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-paper/95 backdrop-blur">
      <div className="wide flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
        <Link href="/" className="display flex items-center gap-2 text-[1.375rem] text-indigo" onClick={() => showStore()}>
          <TagMark className="h-5 w-8" />
          {strings.appName}
        </Link>
        <form role="search" onSubmit={submit} className="order-last flex w-full items-center gap-2 sm:order-none sm:w-auto sm:flex-1">
          <label htmlFor={inputId} className="sr-only">
            {text.searchLabel}
          </label>
          <div className="relative flex-1">
            <SearchIcon className="pointer-events-none absolute left-3.5 top-1/2 size-5 -translate-y-1/2 text-ink-soft" />
            <input
              id={inputId}
              type="search"
              className="field h-12 w-full rounded-full pl-11 pr-11 text-[1.0625rem]"
              placeholder={text.searchPlaceholder}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              enterKeyHint="search"
            />
            {draft ? (
              <button
                type="button"
                className="absolute right-1.5 top-1/2 flex size-9 -translate-y-1/2 items-center justify-center rounded-full hover:bg-wash"
                aria-label={text.clearSearch}
                onClick={() => {
                  setDraft("");
                  if (query) onSearch("");
                }}
              >
                <CloseIcon className="size-5" />
              </button>
            ) : null}
          </div>
          <button type="submit" className="btn btn-primary btn-small h-12 min-h-12 rounded-full px-5 text-base">
            {text.searchButton}
          </button>
        </form>
        <div className="ml-auto sm:ml-0">
          <AccountButton />
        </div>
      </div>
    </header>
  );
}

function Catalog({ filters, go }: { filters: Filters; go: (next: Partial<Filters>) => void }) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const key = `${filters.q}|${filters.category}|${filters.gender}|${filters.rating}|${filters.sort}|${attempt}`;
  const [loadedKey, setLoadedKey] = useState(key);
  if (loadedKey !== key) {
    setLoadedKey(key);
    setLoad({ status: "loading" });
  }

  useEffect(() => {
    let live = true;
    getCatalog({
      q: filters.q,
      category: filters.category,
      gender: filters.gender,
      min_rating: filters.rating,
      sort: filters.sort,
    }).then(
      (page) => live && setLoad({ status: "ready", page, items: page.items, more: "idle" }),
      (error: unknown) => live && setLoad({ status: "error", message: errorMessage(error) }),
    );
    return () => {
      live = false;
    };
  }, [filters.q, filters.category, filters.gender, filters.rating, filters.sort, attempt]);

  async function loadMore() {
    if (load.status !== "ready") return;
    setLoad({ ...load, more: "loading" });
    try {
      const next = await getCatalog({ ...filters, min_rating: filters.rating, page: load.page.page + 1 });
      setLoad({ status: "ready", page: next, items: [...load.items, ...next.items], more: "idle" });
    } catch {
      setLoad({ ...load, more: "idle" });
    }
  }

  const page = load.status === "ready" ? load.page : null;
  const sortId = useId();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="group" aria-label={text.genderLegend} className="flex gap-1.5">
          {GENDERS.map((gender) => (
            <button
              key={gender}
              type="button"
              className="chip"
              aria-pressed={(filters.gender ?? "all") === gender}
              onClick={() => go({ gender: gender === "all" ? null : gender, category: null })}
            >
              {text.genders[gender]}
            </button>
          ))}
          <button
            type="button"
            className="chip"
            aria-pressed={filters.rating === RATING_FILTER}
            aria-label={text.ratingFilterLabel}
            onClick={() => go({ rating: filters.rating === RATING_FILTER ? null : RATING_FILTER })}
          >
            {text.ratingFilter}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor={sortId} className="text-base text-ink-soft">
            {text.sortLabel}
          </label>
          <select
            id={sortId}
            className="field h-10 min-h-10 w-auto py-0 pr-8 text-base"
            value={filters.sort}
            onChange={(event) => go({ sort: event.target.value as CatalogSort })}
          >
            {SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {text.sorts[sort]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {page && page.categories.length > 1 ? (
        <nav aria-label={text.categoriesLabel} className="-mx-1 overflow-x-auto px-1 pb-1">
          <ul className="flex gap-2">
            <li>
              <button type="button" className="chip" aria-pressed={!filters.category} onClick={() => go({ category: null })}>
                {text.allCategories}
              </button>
            </li>
            {page.categories.map((category) => (
              <li key={category.name}>
                <button
                  type="button"
                  className="chip"
                  aria-pressed={filters.category === category.name}
                  onClick={() => go({ category: filters.category === category.name ? null : category.name })}
                >
                  {strings.labels.categories[category.name] ?? category.name}
                  <span className="ml-1.5 font-normal opacity-70 tabular-nums">{category.count}</span>
                </button>
              </li>
            ))}
          </ul>
        </nav>
      ) : null}

      {load.status === "error" ? (
        <div role="alert" className="panel p-5">
          <p className="font-semibold text-fail">{text.loadFailed}</p>
          <p className="text-base text-ink-soft">{load.message}</p>
          <button type="button" className="btn btn-primary btn-small mt-3" onClick={() => setAttempt((n) => n + 1)}>
            {strings.common.tryAgain}
          </button>
        </div>
      ) : null}

      {page ? <Summary page={page} /> : null}

      {load.status === "loading" ? (
        <Grid>
          {Array.from({ length: 10 }, (_, index) => (
            <li key={index} aria-hidden="true" className="aspect-[3/5] animate-pulse rounded-2xl bg-wash" />
          ))}
        </Grid>
      ) : load.status === "ready" ? (
        <>
          {load.items.length === 0 ? <NothingFound page={load.page} /> : null}
          <Grid>
            {load.items.map((item) => (
              <li key={item.id}>
                <ProductTile item={item} />
              </li>
            ))}
          </Grid>
          {load.items.length < load.page.total ? (
            <button
              type="button"
              className="btn btn-secondary mx-auto w-full max-w-xs"
              disabled={load.more === "loading"}
              aria-busy={load.more === "loading"}
              onClick={() => void loadMore()}
            >
              {load.more === "loading" ? text.loading : text.loadMore}
            </button>
          ) : null}
          <p className="text-center text-[0.9375rem] text-ink-soft">{text.demoNote}</p>
        </>
      ) : null}
    </div>
  );
}

function Summary({ page }: { page: CatalogPage }) {
  const notes = [
    page.understood.gender ? text.understoodGender(text.genders[page.understood.gender].toLowerCase()) : null,
    page.understood.max_price ? text.understoodPrice(rupees(page.understood.max_price)) : null,
    page.understood.min_rating ? text.understoodRating(page.understood.min_rating) : null,
  ].filter(Boolean);
  return (
    <div role="status">
      <p className="font-semibold">
        {page.query ? text.resultsFor(page.total, page.query) : text.count(page.total)}
        {notes.length > 0 ? <span className="font-normal text-ink-soft"> · {notes.join(" · ")}</span> : null}
      </p>
      {page.partial ? <p className="text-base text-ink-soft">{text.closest}</p> : null}
    </div>
  );
}

function NothingFound({ page }: { page: CatalogPage }) {
  const unmatched = page.understood.unmatched ?? [];
  return (
    <div className="panel flex flex-col items-start gap-2 p-5">
      <p className="font-semibold">{unmatched.length > 0 ? text.notCarried(unmatched) : text.nothing}</p>
      <p className="text-ink-soft">{text.askAiHint}</p>
      <button type="button" className="btn btn-primary btn-small mt-1" onClick={() => chatOpenStore.set(true)}>
        <SparkleIcon className="size-5" />
        {text.askAi}
      </button>
    </div>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4 xl:grid-cols-5">{children}</ul>;
}

interface AiResultsProps {
  session: SearchSession;
  results: StoredResults | null;
  progress: AiProgress | null;
}

/** The AI's results in place of the product list, newest checklist first, while the chat stays open beside them. */
function AiResults({ session, results, progress }: AiResultsProps) {
  const source = sourceOf(session);
  const current = results && results.searchRun === session.searchRun && !progress ? results : null;
  const brief = current?.brief ?? session.brief;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl bg-wash p-4">
        <div className="min-w-0">
          <p className="eyebrow flex items-center gap-1.5 text-indigo">
            <SparkleIcon className="size-4" />
            {text.aiEyebrow} · {text.aiFrom[source]}
          </p>
          <h1 className="display mt-1 text-[1.75rem]">{categoryLabel(brief)}</h1>
          <p className="mt-1 font-semibold" role="status">
            {progress
              ? progress.phase === "understanding"
                ? text.aiUnderstanding
                : text.aiChecking(progress.checked, progress.total, progress.passed)
              : current
                ? text.aiSummary(current.done.checked, current.done.passed)
                : null}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-secondary btn-small" onClick={() => chatOpenStore.set(true)}>
            <SparkleIcon className="size-5" />
            {text.aiOpenChat}
          </button>
          <button type="button" className="btn btn-secondary btn-small" onClick={showStore}>
            <ArrowLeftIcon className="size-5" />
            {text.backToAll}
          </button>
        </div>
      </div>

      {progress || !current ? (
        <Grid>
          {Array.from({ length: Math.max(progress?.phase === "checking" ? progress.total : 0, 5) }, (_, index) => (
            <li key={index} aria-hidden="true" className="aspect-[3/5] animate-pulse rounded-2xl bg-wash" />
          ))}
        </Grid>
      ) : (
        <>
          {current.done.matches.length > 0 ? (
            <section className="flex flex-col gap-2">
              <h2 className="eyebrow text-pass">{text.aiMatches}</h2>
              <Grid>
                {current.done.matches.map((card) => (
                  <li key={card.product_id}>
                    <AiTile card={card} brief={current.brief} />
                  </li>
                ))}
              </Grid>
            </section>
          ) : (
            <div className="panel flex flex-col items-start gap-2 p-5">
              <p className="font-semibold">{text.aiNone}</p>
              <button type="button" className="btn btn-primary btn-small mt-1" onClick={() => chatOpenStore.set(true)}>
                <SparkleIcon className="size-5" />
                {text.aiOpenChat}
              </button>
            </div>
          )}
          {current.done.almost.length > 0 ? (
            <section className="flex flex-col gap-2">
              <h2 className="eyebrow">{text.aiAlmost}</h2>
              <Grid>
                {current.done.almost.map((card) => (
                  <li key={card.product_id}>
                    <AiTile card={card} brief={current.brief} />
                  </li>
                ))}
              </Grid>
            </section>
          ) : null}
          <Link href="/results" className="link self-start">
            {text.fullResults}
          </Link>
        </>
      )}
    </div>
  );
}
