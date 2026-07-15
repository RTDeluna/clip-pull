// Single source of truth for platform download state. Flip a platform to
// "available" (and fill in version/url/fileSizeMb) once a real installer
// is built and hosted — nothing else on the site needs to change.
export type DownloadState = "coming-soon" | "available";

export interface PlatformDownload {
  state: DownloadState;
  version?: string;
  url?: string;
  fileSizeMb?: number;
  minOsVersion?: string;
  notifyUrl?: string;
}

export const downloads: Record<"windows" | "macos", PlatformDownload> = {
  windows: {
    state: "available",
    version: "1.3.2",
    // Version-less on purpose — the release script always replaces this
    // exact asset in place on the one persistent "release" GitHub release,
    // so this URL never needs to change again. Bump `version`/`appVersion`
    // below when you want the displayed version text to stay accurate.
    url: "https://github.com/RTDeluna/clip-pull/releases/latest/download/CLIP.PULL.Setup.exe",
    fileSizeMb: 152.4,
    minOsVersion: "Windows 10 64-bit or later",
  },
  macos: {
    state: "coming-soon",
    minOsVersion: "macOS 12 (Monterey) or later",
  },
};

export const appVersion = "1.3.2";
