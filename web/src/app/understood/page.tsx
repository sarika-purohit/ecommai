import type { Metadata } from "next";
import { UnderstoodScreen } from "@/components/understood/UnderstoodScreen";
import { strings } from "@/lib/strings";

export const metadata: Metadata = {
  title: strings.meta.pageTitle(strings.understood.title),
};

export default function UnderstoodPage() {
  return <UnderstoodScreen />;
}
