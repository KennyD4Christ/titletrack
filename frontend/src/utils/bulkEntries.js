// Bulk registration: the form's blocks <-> the API's `entries`. The server is the authority on every rule (it
// checks each label again, inside the commit); this only builds the request and catches the obvious mistakes
// early so a person is not sent to the server to be told them.

export const BULK_MAX = 500;      // labels in one request; the server refuses more
export const PAD_MAX = 10;
export const NOTHING_ENTERED = "Enter at least one plot number or a range.";

let counter = 0;

/** A block is either a list of labels (one per line) or a range with a prefix and suffix: A1 to A40. */
export function newBlock(kind = "labels") {
  counter += 1;
  return kind === "range"
    ? { id: `block-${counter}`, kind, prefix: "", start: "", end: "", pad: "0", suffix: "" }
    : { id: `block-${counter}`, kind, text: "" };
}

const wholeNumber = (value) => /^\d+$/.test(String(value).trim());

export function blockIsEmpty(block) {
  if (block.kind === "labels") return block.text.trim() === "";
  return [block.prefix, block.start, block.end, block.suffix].every((v) => String(v).trim() === "");
}

/** How many labels a block stands for (0 while it cannot be worked out yet). */
export function countLabels(block) {
  if (block.kind === "labels") return block.text.split(/\r?\n/).filter((line) => line.trim() !== "").length;
  if (!wholeNumber(block.start) || !wholeNumber(block.end)) return 0;
  return Math.max(0, Number(block.end) - Number(block.start) + 1);
}

export const totalLabels = (blocks) => blocks.reduce((sum, block) => sum + countLabels(block), 0);

/** The request body's `entries`, leaving out blocks nobody filled in. */
export function buildEntries(blocks) {
  return blocks.filter((block) => !blockIsEmpty(block)).map((block) => (
    block.kind === "labels"
      ? { labels: block.text }
      : {
        prefix: block.prefix, start: Number(block.start), end: Number(block.end),
        pad: Number(String(block.pad).trim() || 0), suffix: block.suffix,
      }));
}

/** A sentence to show instead of calling the server, or null when the blocks look usable. */
export function blocksProblem(blocks) {
  const used = blocks.filter((block) => !blockIsEmpty(block));
  if (used.length === 0) return NOTHING_ENTERED;
  for (const [i, block] of used.entries()) {
    const where = used.length > 1 ? `Block ${i + 1}: ` : "";
    if (block.kind === "range") {
      if (!wholeNumber(block.start) || !wholeNumber(block.end)) return `${where}the range needs a whole number to start and end at.`;
      if (Number(block.end) < Number(block.start)) return `${where}the range ends before it starts.`;
      if (!wholeNumber(block.pad || "0") || Number(block.pad || 0) > PAD_MAX) return `${where}zero-fill must be a number from 0 to ${PAD_MAX}.`;
    }
  }
  if (totalLabels(used) > BULK_MAX) return `A request can register at most ${BULK_MAX} plots. Split it into smaller requests.`;
  return null;
}

/** "A-01 to A-40" for a range that is complete enough to show, else "". Only a hint under the form. */
export function rangeExample(block) {
  if (block.kind !== "range" || !wholeNumber(block.start) || !wholeNumber(block.end)) return "";
  if (Number(block.end) < Number(block.start)) return "";
  const pad = wholeNumber(block.pad || "0") ? Math.min(Number(block.pad || 0), PAD_MAX) : 0;
  const label = (n) => `${block.prefix}${String(n).padStart(pad, "0")}${block.suffix}`;
  return Number(block.start) === Number(block.end) ? label(block.start) : `${label(block.start)} to ${label(block.end)}`;
}
