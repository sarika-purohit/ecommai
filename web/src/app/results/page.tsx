import type { Metadata } from "next";
import { ResultsScreen } from "@/components/results/ResultsScreen";
import { strings } from "@/lib/strings";

export const metadata: Metadata = {
  title: strings.meta.pageTitle(strings.results.title),
};

export default function ResultsPage() {
  return <ResultsScreen />;
}
