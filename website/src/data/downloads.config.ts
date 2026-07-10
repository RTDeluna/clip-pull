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
    // TODO: replace with the real GitHub Releases URL once the repo/release exists.
    state: "available",
    version: "1.0.0",
    url: "https://github.com/YOUR_GITHUB_ORG/clip-pull/releases/latest/download/CLIP.PULL-Setup-1.0.0.exe",
    fileSizeMb: 103,
    minOsVersion: "Windows 10 64-bit or later",
  },
  macos: {
    state: "coming-soon",
    minOsVersion: "macOS 12 (Monterey) or later",
  },
};

export const appVersion = "1.0.0";
