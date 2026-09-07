import { useEffect, useState } from "react";
import { api } from "./api";

/**
 * Resolve an authenticated image URL into an object URL.
 *
 * The portrait endpoint requires a bearer token, which `<img src>` and
 * `background-image` cannot send, so the bytes are fetched and wrapped the
 * same way the map board already loads its scene image.
 */
export function useAuthedImage(token: string, url?: string, version?: string) {
  const [objectUrl, setObjectUrl] = useState<string>();
  useEffect(() => {
    if (!url) {
      setObjectUrl(undefined);
      return;
    }
    const controller = new AbortController();
    let created: string | undefined;
    api.mapAssetBlob(token, url, controller.signal)
      .then((blob) => {
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
      })
      .catch(() => setObjectUrl(undefined));
    return () => {
      controller.abort();
      if (created) URL.revokeObjectURL(created);
    };
    // `version` re-fetches when the portrait is replaced under the same URL.
  }, [token, url, version]);
  return objectUrl;
}
