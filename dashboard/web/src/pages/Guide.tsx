import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { fetchJSON } from "@/lib/api";

type Block =
  | { type: "heading"; level: number; text: string; id: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[]; ordered: boolean }
  | { type: "code"; text: string };

type TocItem = { id: string; text: string };
type TocGroup = { id: string; title: string; items: TocItem[] };

const PUBLISHED_DOCS = new Set([
  "OPERATOR_PLATE_GUIDE.md",
  "STRUCTURED_COPY_SYSTEM.md",
  "DEVELOPER_CLOUD_MIGRATION.md",
  "LOCAL_AGENT_README.md",
  "LOCAL_AGENT_UBUNTU.md",
  "LOCAL_AGENT_WINDOWS.md",
  "LOCAL_AGENT_MAC.md",
  "LOCAL_FIRST_OPERATIONS.md",
  "DASHBOARD_EDITABLE_FIELDS.md",
  "README.md",
]);

const REPO_DOCS = [
  { label: "Operator guide", href: "/guide" },
  { label: "Structured copy", href: "/docs/STRUCTURED_COPY_SYSTEM.md" },
  { label: "Local agent setup", href: "/docs/LOCAL_AGENT_README.md" },
  { label: "Developer cloud notes", href: "/docs/DEVELOPER_CLOUD_MIGRATION.md" },
  { label: "All docs on GitHub", href: "https://github.com/Vinay-003/ad-factory/tree/render-setup/docs" },
  { label: "Repo (render-setup)", href: "https://github.com/Vinay-003/ad-factory/tree/render-setup" },
];

function publishedDocName(value: string): string {
  const raw = value.split(/[?#]/)[0].split("/").pop() || "";
  const name = raw.endsWith(".md") ? raw : raw ? `${raw}.md` : "";
  return PUBLISHED_DOCS.has(name) ? name : "";
}

function docFromPath(pathname: string, param?: string): string {
  return publishedDocName(param || pathname) || "OPERATOR_PLATE_GUIDE.md";
}

function resolveHref(href: string): { href: string; internal: boolean } {
  if (href.startsWith("#")) return { href, internal: false };
  const hash = href.includes("#") ? `#${href.split("#").slice(1).join("#")}` : "";
  if (href === "/guide" || href.startsWith("/guide#")) return { href: `/guide${hash}`, internal: true };
  const fromPath = publishedDocName(href);
  if (fromPath) {
    return {
      href: (fromPath === "OPERATOR_PLATE_GUIDE.md" ? "/guide" : `/docs/${fromPath}`) + hash,
      internal: true,
    };
  }
  const github = href.match(/github\.com\/Vinay-003\/ad-factory\/(?:blob|tree)\/[^/]+\/(?:docs\/)?([^?#]+)$/);
  if (github) {
    const name = publishedDocName(github[1]);
    if (name) {
      return {
        href: (name === "OPERATOR_PLATE_GUIDE.md" ? "/guide" : `/docs/${name}`) + hash,
        internal: true,
      };
    }
  }
  return { href, internal: false };
}

function slugify(text: string, used: Set<string>): string {
  const base = text
    .toLowerCase()
    .replace(/[`*_]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "section";
  let slug = base;
  let n = 2;
  while (used.has(slug)) {
    slug = `${base}-${n}`;
    n += 1;
  }
  used.add(slug);
  return slug;
}

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
        const resolved = resolveHref(link[2]);
        if (resolved.internal) {
          nodes.push(
            <Link key={key} to={resolved.href}>
              {link[1]}
            </Link>,
          );
        } else {
          const external = /^https?:\/\//.test(resolved.href);
          nodes.push(
            <a key={key} href={resolved.href} {...(external ? { target: "_blank", rel: "noreferrer" } : {})}>
              {link[1]}
            </a>,
          );
        }
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
  const used = new Set<string>();
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
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const text = heading[2].trim();
      blocks.push({ type: "heading", level: heading[1].length, text, id: slugify(text, used) });
      i += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "list", items, ordered: false });
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push({ type: "list", items, ordered: true });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !lines[i].startsWith("#") && !lines[i].startsWith("```") && !/^[-*]\s+/.test(lines[i]) && !/^\d+\.\s+/.test(lines[i])) {
      para.push(lines[i].trim());
      i += 1;
    }
    blocks.push({ type: "paragraph", text: para.join(" ") });
  }
  return blocks;
}

function groupToc(blocks: Block[]): TocGroup[] {
  const groups: TocGroup[] = [];
  let current: TocGroup | null = null;
  for (const block of blocks) {
    if (block.type !== "heading") continue;
    if (block.level === 2) {
      current = { id: block.id, title: block.text, items: [] };
      groups.push(current);
    } else if (block.level === 3 && current) {
      current.items.push({ id: block.id, text: block.text });
    }
  }
  return groups;
}

function jumpTo(id: string) {
  const node = document.getElementById(id);
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "start" });
  history.replaceState(null, "", `#${id}`);
}

export function GuidePage() {
  const { pathname } = useLocation();
  const params = useParams();
  const docName = docFromPath(pathname, params.docName);
  const [markdown, setMarkdown] = useState("");
  const [status, setStatus] = useState("Loading the operator guide…");
  const [active, setActive] = useState("");

  useEffect(() => {
    let cancelled = false;
    const url = docName === "OPERATOR_PLATE_GUIDE.md"
      ? "/api/guide"
      : `/api/docs/${encodeURIComponent(docName)}`;
    setStatus("Loading the operator guide…");
    setMarkdown("");
    fetchJSON<{ markdown?: string }>(url)
      .then((data) => {
        if (cancelled) return;
        setMarkdown(data.markdown || "");
        setStatus(data.markdown ? "" : "This doc is empty.");
      })
      .catch((err) => {
        if (!cancelled) setStatus(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [docName]);

  const blocks = useMemo(() => parseMarkdown(markdown), [markdown]);
  const toc = useMemo(() => groupToc(blocks), [blocks]);
  const tocIds = useMemo(
    () => toc.flatMap((group) => [group.id, ...group.items.map((item) => item.id)]),
    [toc],
  );

  useEffect(() => {
    if (!tocIds.length) return;
    const hash = window.location.hash.replace(/^#/, "");
    if (hash && tocIds.includes(hash)) {
      setActive(hash);
      requestAnimationFrame(() => jumpTo(hash));
    } else {
      setActive(toc[0]?.items[0]?.id || toc[0]?.id || "");
    }

    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]?.target.id) setActive(visible[0].target.id);
    }, { rootMargin: "-15% 0px -70% 0px", threshold: [0, 1] });

    for (const id of tocIds) {
      const node = document.getElementById(id);
      if (node) observer.observe(node);
    }
    return () => observer.disconnect();
  }, [toc, tocIds]);

  return (
    <div className="guide-desk">
      <aside className="guide-toc">
        <p className="tile-kicker">Docs</p>
        <h2>Ad Factory</h2>
        <div className="guide-toc-docs">
          {REPO_DOCS.map((item) => (
            item.href.startsWith("/") ? (
              <Link key={item.href} to={item.href}>{item.label}</Link>
            ) : (
              <a key={item.href} href={item.href} target="_blank" rel="noreferrer">
                {item.label}
              </a>
            )
          ))}
        </div>
        {toc.length ? (
          <nav aria-label="Guide sections">
            {toc.map((group) => (
              <div key={group.id} className="guide-toc-group">
                <a
                  href={`#${group.id}`}
                  className={`guide-toc-group-title${active === group.id ? " active" : ""}`}
                  onClick={(event) => {
                    event.preventDefault();
                    setActive(group.id);
                    jumpTo(group.id);
                  }}
                >
                  {group.title}
                </a>
                {group.items.map((item) => (
                  <a
                    key={item.id}
                    href={`#${item.id}`}
                    className={`guide-toc-link${active === item.id ? " active" : ""}`}
                    onClick={(event) => {
                      event.preventDefault();
                      setActive(item.id);
                      jumpTo(item.id);
                    }}
                  >
                    {item.text}
                  </a>
                ))}
              </div>
            ))}
          </nav>
        ) : (
          <p className="hint">Loading sections…</p>
        )}
      </aside>
      <article className="guide-page">
        {status ? <p className="hint">{status}</p> : null}
        {blocks.map((block, index) => {
          if (block.type === "heading") {
            if (block.level === 1) return null;
            if (block.level === 2) return <h2 key={block.id} id={block.id}>{inline(block.text)}</h2>;
            if (block.level === 3) return <h3 key={block.id} id={block.id}>{inline(block.text)}</h3>;
            return <h4 key={block.id} id={block.id}>{inline(block.text)}</h4>;
          }
          if (block.type === "list") {
            const ListTag = block.ordered ? "ol" : "ul";
            return (
              <ListTag key={index}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{inline(item)}</li>
                ))}
              </ListTag>
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
    </div>
  );
}
