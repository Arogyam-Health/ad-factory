type Props = {
  className?: string;
  lines?: number;
  width?: string;
};

export function Skeleton({ className = "skel-line", width }: Props) {
  return <div className={`skeleton ${className}`} style={width ? { width } : undefined} aria-hidden="true" />;
}

export function SkeletonLines({ lines = 3 }: { lines?: number }) {
  return (
    <div style={{ display: "grid", gap: 10 }} aria-busy="true" aria-live="polite">
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? "62%" : "100%"} />
      ))}
    </div>
  );
}

export function SkeletonGrid({ count = 6, className = "skel-card" }: { count?: number; className?: string }) {
  return (
    <div className="bento" aria-busy="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="tile tile-third">
          <Skeleton className={className} />
        </div>
      ))}
    </div>
  );
}
