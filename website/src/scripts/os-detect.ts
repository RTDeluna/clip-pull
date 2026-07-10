// Picks which platform's download button gets the primary-gradient
// treatment. Sets data-os on <html>; CSS attribute selectors do the rest,
// so there's no layout shift — just a same-size style swap.
export function initOsDetect(): void {
  const ua = navigator.userAgent || "";
  let os: "windows" | "macos" | "other" = "other";
  if (/Mac OS X|Macintosh/i.test(ua)) os = "macos";
  else if (/Windows/i.test(ua)) os = "windows";
  document.documentElement.setAttribute("data-os", os);
}
