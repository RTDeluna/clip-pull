export interface FeatureCategory {
  icon: string;
  title: string;
  bullets: string[];
}

// Copy pulled only from real, shipped behavior in the app — see the hard
// guardrail below before adding anything here: never describe an
// interactive "continue anyway?" dialog for duplicates — only the passive
// "Already downloaded" badge and the settings toggle to auto-skip actually
// ship today.
export const features: FeatureCategory[] = [
  {
    icon: "IconBatch",
    title: "Paste & Go",
    bullets: [
      "Paste dozens of links at once into a line-numbered editor that flags invalid lines as you type",
      "Works with Vimeo, Loom, and anything else yt-dlp supports",
      "Optional Referer field for videos embedded in gated course platforms",
    ],
  },
  {
    icon: "IconBolt",
    title: "Built for Speed",
    bullets: [
      "Parallel downloads with live per-video progress — percent, speed, ETA, size",
      "Pause any download mid-transfer and resume it later without starting over",
      "Configurable concurrency, plus per-video fragment concurrency for faster individual files",
      "Optional aria2c acceleration, auto-detected on your machine",
    ],
  },
  {
    icon: "IconFolder",
    title: "Stay Organized",
    bullets: [
      "Name a course or batch folder once — every video in that run nests inside it",
      "Filenames auto-derived from each video's real title",
      "An \"Already downloaded\" badge flags links you've grabbed before",
    ],
  },
  {
    icon: "IconClock",
    title: "Know What Happened",
    bullets: [
      "Persistent, searchable download history — filter by status or search title, URL, or file path",
      "Reveal any finished file in its folder with one click",
      "Retry a failed download instantly, with plain-language error messages instead of raw stack traces",
    ],
  },
  {
    icon: "IconSliders",
    title: "Made to Fit",
    bullets: [
      "Dark and light themes, remembered between launches",
      "Set a default output folder, concurrency limits, and duplicate-skip behavior once",
      "A native notification lets you know the moment a batch finishes",
    ],
  },
];
