import Link from "next/link";
import { strings } from "@/lib/strings";

export function NoSession() {
  return (
    <section className="panel p-6">
      <h1 className="display text-[1.75rem]">{strings.understood.noSessionTitle}</h1>
      <p className="mt-2 text-ink-soft">{strings.understood.noSessionBody}</p>
      <Link href="/" className="btn btn-primary mt-5 w-full">
        {strings.understood.startSearch}
      </Link>
    </section>
  );
}

export function ScreenPlaceholder() {
  return (
    <div aria-busy="true" className="flex flex-col gap-4 pt-2">
      <span className="sr-only">{strings.common.loading}</span>
      <div className="h-10 w-3/4 rounded-xl bg-wash" />
      <div className="h-24 rounded-2xl bg-wash" />
      <div className="flex flex-wrap gap-2.5">
        <div className="h-12 w-28 rounded-xl bg-wash" />
        <div className="h-12 w-36 rounded-xl bg-wash" />
        <div className="h-12 w-24 rounded-xl bg-wash" />
      </div>
    </div>
  );
}
