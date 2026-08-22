const EXACT_COPY_BLOCK = /(^[ \t]*(?:#{1,6}[ \t]+)?EXACT ON-IMAGE COPY(?:[ \t]*[-—:][ \t]*DO NOT ALTER ANYTHING)?[ \t]*\r?\n)([\s\S]*?)(\r?\n[ \t]*Render every character exactly as written\b[^\r\n]*)/im;

export function exactOnImageCopyLines(content) {
  const match = String(content || "").match(EXACT_COPY_BLOCK);
  if (!match) return [];
  return match[2]
    .split(/\r?\n/)
    .map((line) => line.match(/^\s*-\s*([^:\r\n]+):\s*(.*)$/))
    .filter(Boolean)
    .map((line) => ({
      label: line[1].trim(),
      value: line[2],
    }));
}

export function replaceExactOnImageCopy(content, copyLines) {
  const source = String(content || "");
  const newline = source.includes("\r\n") ? "\r\n" : "\n";
  const replacement = (copyLines || [])
    .map(({ label, value }) => `- ${String(label).trim()}: ${String(value ?? "")}`)
    .join(newline);
  if (!replacement.trim()) {
    throw new Error("Exact on-image copy cannot be empty.");
  }
  if (!EXACT_COPY_BLOCK.test(source)) {
    throw new Error("No EXACT ON-IMAGE COPY block found in this prompt.");
  }
  return source.replace(
    EXACT_COPY_BLOCK,
    (_, heading, _oldCopy, closing) => `${heading}${replacement}${closing}`,
  );
}
