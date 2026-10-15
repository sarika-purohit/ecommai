import type { Metadata } from "next";
import { HomeScreen } from "@/components/home/HomeScreen";
import { strings } from "@/lib/strings";

export const metadata: Metadata = {
  title: strings.meta.pageTitle(strings.home.title),
};

/** The step-by-step search: request, checklist, then results. The store's AI chat is the quick way in. */
export default function AskPage() {
  return <HomeScreen />;
}
