import { useEffect, useState } from "react";
import { localDataPlane } from "@/lib/local-data-plane.js";

export function LazyAsset({
  resourceId,
  deviceId,
  version,
  alt,
  className,
}: {
  resourceId: string;
  deviceId: string;
  version?: number;
  alt: string;
  className?: string;
}) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    if (!resourceId || !deviceId) return;
    let cancelled = false;
    void localDataPlane.assetObjectUrl(resourceId, deviceId, version)
      .then((next) => {
        if (!cancelled) setUrl(next);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [resourceId, deviceId, version]);

  if (!url) return <span className={className} aria-hidden />;
  return <img className={className} src={url} alt={alt} loading="lazy" />;
}
