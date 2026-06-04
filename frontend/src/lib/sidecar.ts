/** Parse the two-line .txt sidecar: comma-separated danbooru/WD14 tags on
 *  line 1, optional free-text description on the remaining lines. Mirrors the
 *  logic previously inlined in FrameThumb so the crop-modal panels and the
 *  thumb agree on how the file splits. */
export function splitSidecar(
  text: string,
): { danbooru: string; description: string } {
  if (!text) return { danbooru: "", description: "" };
  const lines = text.split("\n");
  const danbooru = (lines[0] ?? "").trim();
  let rest = lines.slice(1);
  while (rest.length > 0 && rest[0].trim() === "") rest = rest.slice(1);
  const description = rest.join("\n").replace(/\s+$/, "");
  return { danbooru, description };
}

/** Split a danbooru tag line into a clean, de-duplicated, order-preserving
 *  list. Empty/whitespace-only entries are dropped. */
export function parseTags(danbooruLine: string): string[] {
  const raw = danbooruLine
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const t of raw) {
    if (seen.has(t)) continue;
    seen.add(t);
    out.push(t);
  }
  return out;
}
