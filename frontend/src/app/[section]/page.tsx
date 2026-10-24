import { notFound } from "next/navigation";
import { sectionForPath, sectionSlugs } from "@/lib/routes";

export const dynamicParams = false;
export function generateStaticParams() {
  return sectionSlugs.map((section) => ({ section }));
}
export default async function SectionPage({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  if (!sectionForPath(`/${section}/`)) notFound();
  return null;
}
