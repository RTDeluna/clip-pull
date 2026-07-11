// A `iframe[src*="vimeo.com"]`-style CSS selector only finds candidate
// elements — it doesn't verify the match is actually on the hostname (a
// substring match like that is also satisfied by, say,
// "https://evil.example/?redirect=vimeo.com/video/1", which is not a Vimeo
// URL at all). Detectors that return an iframe's raw src verbatim (instead
// of reconstructing a known-safe URL from just an extracted ID) check the
// actual parsed hostname here before trusting it.
function hasTrustedHost(urlString, allowedDomains) {
  try {
    const { hostname } = new URL(urlString, location.href);
    return allowedDomains.some(
      (domain) => hostname === domain || hostname.endsWith("." + domain)
    );
  } catch {
    return false;
  }
}

// =====================================================
// 1️⃣ Original Loom code — unchanged
// =====================================================
// Replace the findLoomLink() function:
function findLoomLink() {
  try {
    // Also catch lazy-loaded iframes that use data-src instead of src
    const iframe = document.querySelector(
      'iframe[src*="loom.com/embed/"], iframe[data-src*="loom.com/embed/"]'
    );
    if (!iframe) return null;

    // Try multiple patterns for Loom URLs
    const src = iframe.src || iframe.getAttribute('data-src') || '';
    const patterns = [
      /embed\/([a-zA-Z0-9]+)/i,
      /share\/([a-zA-Z0-9]+)/i,
      /v\/([a-zA-Z0-9]+)/i
    ];

    for (const pattern of patterns) {
      const match = src.match(pattern);
      if (match) return `https://www.loom.com/share/${match[1]}`;
    }

    return null;
  } catch (error) {
    console.error("Error extracting Loom link:", error);
    return null;
  }
}

// =====================================================
// 🆕 1.1 Loom Thumbnail helper
// =====================================================
// This grabs the Loom video thumbnail from the Open Graph metadata
function findLoomThumbnail() {
  try {
    const metaThumb = document.querySelector('meta[property="og:image"]');
    if (metaThumb && metaThumb.content.includes("loom.com")) {
      return metaThumb.content; // ✅ Typically a CDN thumbnail URL
    }
    return null; // fallback
  } catch (error) {
    console.error("Error extracting Loom thumbnail:", error);
    return null;
  }
}

// =====================================================
// 2️⃣ Add YouTube fallback — as simple as possible
// =====================================================
function findYouTubeLink() {
  try {
    // Method 1: og:image meta tag
    const tag = document.querySelector('meta[property="og:image"][content*="i.ytimg.com/vi/"]');
    if (tag) {
      const match = tag.content.match(/\/vi\/([a-zA-Z0-9_-]{6,})\//);
      if (match) return `https://www.youtube.com/watch?v=${match[1]}`;
    }

    // Method 2: YouTube iframe fallback (when og:image is absent or stale after SPA nav)
    const ytIframe = document.querySelector(
      'iframe[src*="youtube.com/embed/"], iframe[src*="youtube-nocookie.com/embed/"], ' +
      'iframe[data-src*="youtube.com/embed/"]'
    );
    if (ytIframe) {
      const src = ytIframe.src || ytIframe.getAttribute('data-src') || '';
      const match = src.match(/embed\/([a-zA-Z0-9_-]{6,})/);
      if (match) {
        console.log("✅ Found YouTube iframe:", match[1]);
        return `https://www.youtube.com/watch?v=${match[1]}`;
      }
    }

    return null;
  } catch (error) {
    console.error("Error extracting YouTube link:", error);
    return null;
  }
}

// =====================================================
// 🆕 2.1 YouTube Thumbnail helper
// =====================================================
// This uses the YouTube video ID to generate a standard thumbnail URL
function findYouTubeThumbnail(ytLink) {
  try {
    const match = ytLink.match(/v=([a-zA-Z0-9_-]{6,})/);
    if (!match) return null;
    const videoId = match[1];
    return `https://img.youtube.com/vi/${videoId}/maxresdefault.jpg`; // ✅ HD thumbnail
  } catch (error) {
    console.error("Error building YouTube thumbnail:", error);
    return null;
  }
}

// =====================================================
// 3️⃣ Vimeo extraction for specific platform
// =====================================================
function findVimeoLink() {
  try {
    // Method 1: __NEXT_DATA__ (works on initial page load)
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (nextDataElement) {
      try {
        const data = JSON.parse(nextDataElement.textContent);
        const selectedModule = data?.props?.pageProps?.selectedModule;
        if (selectedModule) {
          const module = data?.props?.pageProps?.course?.children?.find(
            c => c.course.id === selectedModule
          );
          if (module?.course?.metadata?.videoLink) {
            const vimeoMatch = module.course.metadata.videoLink.match(/vimeo\.com\/(\d+)/);
            if (vimeoMatch) return `https://player.vimeo.com/video/${vimeoMatch[1]}`;
          }
        }
      } catch (e) { /* fall through to DOM method */ }
    }

    // Method 2: DOM iframe fallback — also handles lazy-loaded iframes (data-src)
    const vimeoIframe = document.querySelector(
      'iframe[src*="player.vimeo.com"], iframe[src*="vimeo.com/video"], ' +
      'iframe[data-src*="player.vimeo.com"], iframe[data-src*="vimeo.com/video"]'
    );
    if (vimeoIframe) {
      const src = vimeoIframe.src || vimeoIframe.getAttribute('data-src') || '';
      if (src && hasTrustedHost(src, ["vimeo.com"])) {
        console.log("✅ Found Vimeo iframe via DOM:", src);
        return src;
      }
    }

    // Method 3: Vimeo Universal Embed Code — uses a div with data-vimeo-id, NOT an iframe
    const vimeoDiv = document.querySelector('[data-vimeo-id]');
    if (vimeoDiv) {
      const videoId = vimeoDiv.getAttribute('data-vimeo-id');
      if (videoId) {
        console.log("✅ Found Vimeo inline embed (data-vimeo-id):", videoId);
        return `https://player.vimeo.com/video/${videoId}`;
      }
    }

    return null;
  } catch (error) {
    console.error("Error extracting Vimeo link:", error);
    return null;
  }
}

// =====================================================
// 🆕 3.1 Vimeo Thumbnail helper (optional enhancement)
// =====================================================
function findVimeoThumbnail(vimeoLink) {
  try {
    // Extract thumbnail directly from Skool's __NEXT_DATA__
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (!nextDataElement) return null;
    
    const d = JSON.parse(nextDataElement.textContent);
    const s = d?.props?.pageProps?.selectedModule;
    const m = d?.props?.pageProps?.course?.children?.find(c => c.course.id === s);

    return m?.course?.metadata?.videoThumbnail || null;

  } catch (error) {
    console.error("Error extracting Vimeo thumbnail:", error);
    return null;
  }
}

// =====================================================
// 4️⃣ NEW: Wistia extraction using your code snippet
// =====================================================
function findWistiaLink() {
  try {
    // Your provided code snippet for extracting Wistia links
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (!nextDataElement) return null;
    
    const d = JSON.parse(nextDataElement.textContent);
    const s = d?.props?.pageProps?.selectedModule;
    const m = d?.props?.pageProps?.course?.children?.find(c => c.course.id === s);
    const videoLink = m?.course?.metadata?.videoLink || null;
    
    // Only return if it's a Wistia link
    if (videoLink && videoLink.includes('wistia.com')) {
      console.log("✅ Found Wistia video link:", videoLink);
      return videoLink;
    }
    
    return null;
  } catch (error) {
    console.error("Error extracting Wistia link:", error);
    return null;
  }
}

// =====================================================
// 🆕 4.1 Wistia Thumbnail helper - IMPROVED VERSION
// =====================================================
function findWistiaThumbnail(wistiaLink) {
  try {
    if (!wistiaLink) return null;
    
    // METHOD 1: Extract from Wistia script tag (YOUR NEW METHOD)
    const wistiaScript = document.querySelector('script[id*="wistia"]');
    if (wistiaScript && wistiaScript.textContent) {
      const thumbnailMatch = wistiaScript.textContent.match(/"thumbnailUrl":"([^"]+)"/);
      if (thumbnailMatch && thumbnailMatch[1]) {
        console.log("✅ Found Wistia thumbnail from script tag:", thumbnailMatch[1]);
        return thumbnailMatch[1];
      }
    }
    
    // METHOD 2: Extract from __NEXT_DATA__ (YOUR ALTERNATIVE METHOD)
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (nextDataElement) {
      try {
        const data = JSON.parse(nextDataElement.textContent);
        const thumbnail = data?.props?.pageProps?.settings?.pageMeta?.image;
        if (thumbnail && thumbnail.includes('wistia')) {
          console.log("✅ Found Wistia thumbnail from NEXT_DATA:", thumbnail);
          return thumbnail;
        }
      } catch (e) {
        // Silent fail, try next method
      }
    }
    
    // METHOD 3: Extract from Skool's __NEXT_DATA__ (existing method)
    if (nextDataElement) {
      try {
        const d = JSON.parse(nextDataElement.textContent);
        const s = d?.props?.pageProps?.selectedModule;
        const m = d?.props?.pageProps?.course?.children?.find(c => c.course.id === s);

        if (m?.course?.metadata?.videoThumbnail) {
          console.log("✅ Found Wistia thumbnail from course metadata:", m.course.metadata.videoThumbnail);
          return m.course.metadata.videoThumbnail;
        }
      } catch (e) {
        // Silent fail, try next method
      }
    }
    
    // METHOD 4: Construct from Wistia media ID (fallback)
    const mediaIdMatch = wistiaLink.match(/medias\/([a-z0-9]+)/) || 
                        wistiaLink.match(/iframe\/([a-z0-9]+)/) ||
                        wistiaLink.match(/\/([a-z0-9]{10,})/);
    
    if (mediaIdMatch && mediaIdMatch[1]) {
      const mediaId = mediaIdMatch[1];
      const constructedThumbnail = `https://embed-ssl.wistia.com/deliveries/${mediaId}.jpg?image_crop_resized=960x600`;
      console.log("✅ Constructed Wistia thumbnail from media ID:", constructedThumbnail);
      return constructedThumbnail;
    }
    
    console.log("❌ Could not extract Wistia thumbnail");
    return null;
    
  } catch (error) {
    console.error("Error extracting Wistia thumbnail:", error);
    return null;
  }
}

// =====================================================
// 5️⃣ Lesson title extraction from __NEXT_DATA__
// =====================================================
function findLessonTitle() {
  try {
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (!nextDataElement) return null;
    const data = JSON.parse(nextDataElement.textContent);
    const selectedModule = data?.props?.pageProps?.selectedModule;
    if (!selectedModule) return null;
    const module = data?.props?.pageProps?.course?.children?.find(
      c => c.course.id === selectedModule
    );
    return module?.course?.name || null;
  } catch {
    return null;
  }
}

// =====================================================
// 5.5 Course name extraction — used as Clip.Pull's "subfolder" so downloads
// land nested under the course, not the individual lesson.
// =====================================================
function findCourseName() {
  try {
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (!nextDataElement) return null;
    const data = JSON.parse(nextDataElement.textContent);
    return data?.props?.pageProps?.course?.name || null;
  } catch {
    return null;
  }
}

// =====================================================
// 5.1 Mux — Skool Native Video (launched July 2025)
// =====================================================
// Skool's own video hosting is powered by Mux. The player uses a <mux-player>
// custom element. Download URL pattern: stream.mux.com/{playbackId}/high.mp4
// Thumbnail pattern:                    image.mux.com/{playbackId}/thumbnail.jpg
function findMuxVideo() {
  try {
    // Method 1: <mux-player> custom element (Skool's built-in player)
    const muxPlayer = document.querySelector('mux-player');
    if (muxPlayer) {
      const playbackId = muxPlayer.getAttribute('playback-id');
      if (playbackId) {
        console.log("✅ Found Mux player, playback-id:", playbackId);
        return `https://stream.mux.com/${playbackId}/high.mp4`;
      }
    }

    // Method 2: __NEXT_DATA__ — check course metadata for Mux playback IDs
    const nextDataElement = document.getElementById("__NEXT_DATA__");
    if (nextDataElement) {
      try {
        const data = JSON.parse(nextDataElement.textContent);
        const selectedModule = data?.props?.pageProps?.selectedModule;
        if (selectedModule) {
          const module = data?.props?.pageProps?.course?.children?.find(
            c => c.course.id === selectedModule
          );
          const meta = module?.course?.metadata;
          // Skool stores the Mux ID under various possible field names
          const playbackId = meta?.muxPlaybackId || meta?.playbackId || meta?.videoPlaybackId;
          if (playbackId) {
            console.log("✅ Found Mux playback ID in NEXT_DATA:", playbackId);
            return `https://stream.mux.com/${playbackId}/high.mp4`;
          }
          // Also check if videoLink itself is a Mux stream URL
          const videoLink = meta?.videoLink;
          if (videoLink && videoLink.includes('stream.mux.com')) {
            const match = videoLink.match(/stream\.mux\.com\/([a-zA-Z0-9]+)/);
            if (match) return `https://stream.mux.com/${match[1]}/high.mp4`;
          }
        }
      } catch (e) { /* fall through */ }
    }

    // Method 3: <video> element with a Mux stream src
    const muxVideo = document.querySelector('video[src*="stream.mux.com"]');
    if (muxVideo) {
      const match = muxVideo.src.match(/stream\.mux\.com\/([a-zA-Z0-9]+)/);
      if (match) {
        console.log("✅ Found Mux stream in video element:", match[1]);
        return `https://stream.mux.com/${match[1]}/high.mp4`;
      }
    }

    return null;
  } catch (error) {
    console.error("Error detecting Mux video:", error);
    return null;
  }
}

function findMuxThumbnail(muxUrl) {
  const match = muxUrl.match(/stream\.mux\.com\/([a-zA-Z0-9]+)/);
  if (!match) return null;
  return `https://image.mux.com/${match[1]}/thumbnail.jpg?time=0&width=640`;
}

// =====================================================
// 5.2 Bunny Stream detection
// =====================================================
// Bunny Stream embeds use iframe.mediadelivery.net or *.b-cdn.net
function findBunnyVideo() {
  try {
    const bunnyIframe = document.querySelector(
      'iframe[src*="iframe.mediadelivery.net"], ' +
      'iframe[src*=".b-cdn.net"], ' +
      'iframe[data-src*="iframe.mediadelivery.net"], ' +
      'iframe[data-src*=".b-cdn.net"]'
    );
    if (bunnyIframe) {
      const src = bunnyIframe.src || bunnyIframe.getAttribute('data-src') || '';
      if (src && hasTrustedHost(src, ["mediadelivery.net", "b-cdn.net"])) {
        console.log("✅ Found Bunny Stream iframe:", src);
        return src;
      }
    }
    return null;
  } catch (error) {
    console.error("Error detecting Bunny video:", error);
    return null;
  }
}

function findBunnyThumbnail(bunnyUrl) {
  // Extract videoId from /embed/{libraryId}/{videoId}
  const match = bunnyUrl.match(/\/embed\/\d+\/([a-f0-9-]+)/i);
  if (match) {
    // Bunny thumbnail endpoint (requires knowing the pull zone hostname, so this is a best-effort)
    return null; // thumbnail not reliably constructable without pull zone name
  }
  return null;
}

// =====================================================
// 5.5 Native <video> element detection (last resort)
// =====================================================
// Covers cases where Skool embeds a direct MP4 without a third-party player
function findNativeVideo() {
  try {
    const videos = document.querySelectorAll('video');
    for (const video of videos) {
      // Skip blob: URLs — those are HLS/DASH streaming manifests, not direct files
      if (video.src && video.src.startsWith('http') && !video.src.startsWith('blob:')) {
        console.log("✅ Found native <video> element:", video.src);
        return video.src;
      }
      // Check <source> children
      const source = video.querySelector('source[src]');
      if (source) {
        const src = source.getAttribute('src');
        if (src && src.startsWith('http') && !src.startsWith('blob:')) {
          console.log("✅ Found native <video> <source> tag:", src);
          return src;
        }
      }
    }
    return null;
  } catch (error) {
    console.error("Error detecting native video:", error);
    return null;
  }
}

// =====================================================
// 6️⃣ Combined listener — now with all four video sources
// =====================================================
chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
  if (req.action === "getVideoLink") {
   try {
    console.log("🔍 Looking for video links...");
    const courseName = findCourseName();

    // Step 1: Try Loom first
    const loomLink = findLoomLink();
    if (loomLink) {
      console.log("✅ Found Loom video");
      const thumbnail = findLoomThumbnail();
      sendResponse({ link: loomLink, source: "loom", thumbnail: thumbnail, title: findLessonTitle(), courseName });
      return;
    }

    // Step 2: If not Loom, try YouTube
    const ytLink = findYouTubeLink();
    if (ytLink) {
      console.log("✅ Found YouTube video");
      const thumbnail = findYouTubeThumbnail(ytLink);
      sendResponse({ link: ytLink, source: "youtube", thumbnail: thumbnail, title: findLessonTitle(), courseName });
      return;
    }

    // Step 3: If not Loom or YouTube, try Vimeo
    const vimeoLink = findVimeoLink();
    if (vimeoLink) {
      console.log("✅ Found Vimeo video");
      const thumbnail = findVimeoThumbnail(vimeoLink);
      sendResponse({ link: vimeoLink, source: "vimeo", thumbnail: thumbnail, title: findLessonTitle(), courseName });
      return;
    }

    // Step 4: NEW - If not Loom, YouTube, or Vimeo, try Wistia
    // Try Skool __NEXT_DATA__ method first, fall back to general iframe/script detection
    const wistiaLink = findWistiaLink() || findWistiaLinkGeneral();
    if (wistiaLink) {
      console.log("✅ Found Wistia video");
      const thumbnail = findWistiaThumbnail(wistiaLink);
      sendResponse({ link: wistiaLink, source: "wistia", thumbnail: thumbnail, title: findLessonTitle(), courseName });
      return;
    }

    // Step 5: Skool Native video (Mux — launched July 2025)
    const muxLink = findMuxVideo();
    if (muxLink) {
      console.log("✅ Found Skool Native (Mux) video");
      const thumbnail = findMuxThumbnail(muxLink);
      sendResponse({ link: muxLink, source: "mux", thumbnail: thumbnail, title: findLessonTitle(), courseName });
      return;
    }

    // Step 6: Bunny Stream
    const bunnyLink = findBunnyVideo();
    if (bunnyLink) {
      console.log("✅ Found Bunny Stream video");
      const thumbnail = findBunnyThumbnail(bunnyLink);
      sendResponse({ link: bunnyLink, source: "bunny", thumbnail: thumbnail, title: findLessonTitle(), courseName });
      return;
    }

    // Step 7: Native <video> element (direct MP4 embeds)
    const nativeVideo = findNativeVideo();
    if (nativeVideo) {
      console.log("✅ Found native video element");
      sendResponse({ link: nativeVideo, source: "direct", thumbnail: null, title: findLessonTitle(), courseName });
      return;
    }

    // Step 8: Nothing found
    console.log("❌ No video found on this page");
    sendResponse({ link: null, source: null, thumbnail: null, title: null, courseName: null });
   } catch (error) {
    // Defense-in-depth: every individual detector above already guards
    // itself, but this ensures a truly unexpected error (or one from a
    // future detector added without its own try/catch) still sends a
    // response instead of leaving the message port hanging and the
    // fallback chain silently broken.
    console.error("Unexpected error while scanning for a video:", error);
    sendResponse({ link: null, source: null, thumbnail: null, title: null, courseName: null });
   }
  }

  // Return true to indicate we'll send a response asynchronously
  return true;
});

// =====================================================
// 🆕 6️⃣ Additional Wistia detection for non-Skool sites
// =====================================================
function findWistiaLinkGeneral() {
  try {
    // Method 1: Look for Wistia iframes
    const wistiaIframe = document.querySelector('iframe[src*="wistia"], iframe[data-src*="wistia"]');
    if (wistiaIframe) {
      const src = wistiaIframe.src || wistiaIframe.getAttribute('data-src');
      if (src && hasTrustedHost(src, ["wistia.com", "wistia.net"])) {
        console.log("✅ Found Wistia iframe:", src);
        return src;
      }
    }
    
    // Method 2: Look for Wistia script tags
    const wistiaScript = document.querySelector('script[src*="wistia"], script[id*="wistia"]');
    if (wistiaScript) {
      // Try to extract from script content
      if (wistiaScript.textContent) {
        const videoIdMatch = wistiaScript.textContent.match(/"videoId":"([^"]+)"/);
        if (videoIdMatch && videoIdMatch[1]) {
          return `https://fast.wistia.net/embed/iframe/${videoIdMatch[1]}`;
        }
      }
    }
    
    return null;
  } catch (error) {
    console.error("Error in general Wistia detection:", error);
    return null;
  }
}

// =====================================================
// 8️⃣ SPA Navigation observer (Next.js / Skool client-side routing)
// =====================================================
// Skool uses Next.js — navigating between lessons changes the URL without a page reload.
// We patch history.pushState/replaceState so the extension stays aware of lesson changes.
(function () {
  let lastUrl = location.href;

  function onNavigate() {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      console.log("🔄 Lesson navigation detected:", location.pathname);
    }
  }

  const _pushState = history.pushState.bind(history);
  history.pushState = function (...args) { _pushState(...args); onNavigate(); };

  const _replaceState = history.replaceState.bind(history);
  history.replaceState = function (...args) { _replaceState(...args); onNavigate(); };

  window.addEventListener("popstate", onNavigate);
})();

console.log("🚀 Skool Video Downloader loaded");