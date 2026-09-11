// Where artist/album artwork comes from.
//
// Self-hosted, the backend serves it: nginx and vite both map /api/* onto the
// backend with the prefix stripped. The static demo has no backend, so its
// fixtures carry image_url — the artwork's source on Deezer's CDN — instead.
export const artworkUrl = (item) =>
  item?.image_url || (item?.image_path ? `/api/images/${item.image_path}` : null)
