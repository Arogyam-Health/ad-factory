import { useEffect, useState, type ReactNode } from "react";
import { fetchJSON } from "@/lib/api";
import { Tile } from "@/components/Tile";

type Block =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[] }
  | { type: "code"; text: string };

function inline(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (link) {
        const href = link[2];
        const external = /^https?:\/\//.test(href);
        nodes.push(
          <a key={key} href={href} {...(external ? { target: "_blank", rel: "noreferrer" } : {})}>
            {link[1]}
          </a>,
        );
      }
    }
    key += 1;
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function parseMarkdown(src: string): Block[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith("```")) {
        body.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2].trim() });
      i += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "list", items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !lines[i].startsWith("#") && !lines[i].startsWith("```") && !/^[-*]\s+/.test(lines[i])) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "paragraph", text: para.join(" ") });
  }
  return blocks;
}

export function GuidePage() {
  const [markdown, setMarkdown] = useState("");
  const [status, setStatus] = useState("Loading the operator guide…");

  useEffect(() => {
    let cancelled = false;
    fetchJSON<{ markdown?: string }>("/api/guide")
      .then((data) => {
        if (cancelled) return;
        setMarkdown(data.markdown || "");
        setStatus(data.markdown ? "" : "The operator guide is empty.");
      })
      .catch((err) => {
        if (!cancelled) setStatus(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const blocks = parseMarkdown(markdown);

  return (
    <Tile span="wide" kicker="Copy desk" title="Operator plate guide">
      {status ? <p className="hint">{status}</p> : null}
      <article className="guide-page">
        {blocks.map((block, index) => {
          if (block.type === "heading") {
            if (block.level === 1) return <h2 key={index}>{inline(block.text)}</h2>;
            if (block.level === 2) return <h3 key={index}>{inline(block.text)}</h3>;
            return <h4 key={index}>{inline(block.text)}</h4>;
          }
          if (block.type === "list") {
            return (
              <ul key={index}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{inline(item)}</li>
                ))}
              </ul>
            );
          }
          if (block.type === "code") {
            return (
              <pre key={index}>
                <code>{block.text}</code>
              </pre>
            );
          }
          return <p key={index}>{inline(block.text)}</p>;
        })}
      </article>
    </Tile>
  );
}
