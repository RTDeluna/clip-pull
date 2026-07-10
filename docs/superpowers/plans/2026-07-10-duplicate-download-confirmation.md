# Duplicate Download Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user submits a batch of URLs with "skip duplicates" off and one or more URLs were already downloaded successfully before, show a styled in-app confirmation (not `confirm()`) listing the duplicate links, and only queue anything after the user chooses "Queue anyway" or "Skip duplicates."

**Architecture:** `POST /queue` gains a two-step contract. The first call (no `duplicate_action`) short-circuits before creating any queue entries whenever duplicates are found and `skip_duplicates` is off, returning `needs_confirmation: true` plus the offending `duplicate_urls` — nothing is queued yet. The frontend shows a `.panel`-styled modal built from that list; the user's choice becomes `duplicate_action` (`"queue_all"` or `"skip_duplicates"`) on a second `POST /queue` call with the same payload, which then queues for real. When `skip_duplicates` is on, or when a batch has no duplicates, behavior is byte-for-byte what it is today — the new branch is never entered.

**Tech Stack:** FastAPI + Pydantic (backend), vanilla JS/HTML/CSS (frontend), pytest + FastAPI TestClient (tests).

## Global Constraints

- Work only in: `frontend/renderer.js`, `frontend/index.html`, `frontend/styles.css`, `backend/queue_routes.py`, `backend/tests/test_queue_routes.py`.
- Do NOT touch `backend/history_store.py`'s detection logic or `backend/settings_store.py`.
- No new dependencies.
- Do not remove or repurpose the existing `/queue` response fields (`invalid_lines`, `skipped_duplicate_urls`, `skipped_inflight_urls`) — only add new ones.
- Reuse existing `.panel` / `.btn` styling for the confirmation UI; no `window.confirm()`.
- `skip_duplicates` setting ON must keep silently skipping with zero prompts (unchanged code path).
- A batch with zero duplicates must behave exactly as today (no prompt, `needs_confirmation` simply reads `false`).

---

### Task 1: Backend — two-step confirmation gate in `POST /queue`

**Files:**
- Modify: `backend/queue_routes.py:1-88` (imports, `QueueRequest`, `post_queue`)
- Test: `backend/tests/test_queue_routes.py`

**Interfaces:**
- Consumes: `HistoryStore.was_previously_downloaded(urls: list[str]) -> set[str]` (unchanged), `SettingsStore.get()["skip_duplicates"] -> bool` (unchanged).
- Produces: `QueueRequest.duplicate_action: Optional[Literal["queue_all", "skip_duplicates"]]` (new request field, default `None`). Response dict gains `"needs_confirmation": bool` and `"duplicate_urls": list[str]` (always present, default `False` / `[]`). When `needs_confirmation` is `True`, `entries` is `[]` and no queue entries or background download task were created. Task 2's tests and Task 5's frontend code rely on these exact field names.

- [ ] **Step 1: Write the failing tests**

Open `backend/tests/test_queue_routes.py`. Replace the existing `test_post_queue_flags_previously_downloaded_urls` test (its old assertion — that a duplicate gets queued immediately with `skip_duplicates` off — is exactly the behavior this feature replaces) with the block below, inserted in the same location (right before `test_post_queue_skips_duplicates_when_setting_enabled`):

```python
def test_post_queue_requires_confirmation_when_duplicate_present_and_setting_disabled():
    client, _, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/999", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["entries"] == []
    assert body["needs_confirmation"] is True
    assert body["duplicate_urls"] == ["https://vimeo.com/999"]


def test_post_queue_queues_all_urls_when_duplicate_action_is_queue_all():
    client, _, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/999",
            "output_folder": "C:/downloads",
            "duplicate_action": "queue_all",
        },
    )
    body = response.json()
    assert body["needs_confirmation"] is False
    assert len(body["entries"]) == 1
    assert body["entries"][0]["previously_downloaded"] is True


def test_post_queue_skips_only_duplicates_when_duplicate_action_is_skip_duplicates():
    client, _, _, history_store, _ = _make_client()
    history_store.record(
        entry_id="e0", batch_id=None, url="https://vimeo.com/999", title="Old",
        output_path="C:/out/Old.mp4", total_size="10MB", status="done",
        error_reason=None, retry_count=0,
    )
    response = client.post(
        "/queue",
        json={
            "urls_text": "https://vimeo.com/999\nhttps://vimeo.com/111",
            "output_folder": "C:/downloads",
            "duplicate_action": "skip_duplicates",
        },
    )
    body = response.json()
    assert body["needs_confirmation"] is False
    assert len(body["entries"]) == 1
    assert body["entries"][0]["url"] == "https://vimeo.com/111"
    assert body["skipped_duplicate_urls"] == ["https://vimeo.com/999"]


def test_post_queue_no_confirmation_needed_when_no_duplicates():
    client, _, _, _, _ = _make_client()
    response = client.post(
        "/queue", json={"urls_text": "https://vimeo.com/111", "output_folder": "C:/downloads"}
    )
    body = response.json()
    assert body["needs_confirmation"] is False
    assert len(body["entries"]) == 1
```

Also update `test_post_queue_skips_duplicates_when_setting_enabled` to assert the silent path never asks for confirmation, by adding one line at the end of the test body:

```python
    assert body["needs_confirmation"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_queue_routes.py -v`
Expected: FAIL — `test_post_queue_requires_confirmation_when_duplicate_present_and_setting_disabled` and the other new tests fail with `KeyError: 'needs_confirmation'`; `test_post_queue_skips_duplicates_when_setting_enabled` fails on the new final assertion the same way.

- [ ] **Step 3: Implement the confirmation gate**

In `backend/queue_routes.py`, change the typing import on line 4:

```python
from typing import Literal, Optional
```

Add `duplicate_action` to `QueueRequest` (lines 17-21):

```python
class QueueRequest(BaseModel):
    urls_text: str
    output_folder: str
    referer: Optional[str] = None
    subfolder: Optional[str] = None
    duplicate_action: Optional[Literal["queue_all", "skip_duplicates"]] = None
```

Replace the body of `post_queue` (lines 47-87) with:

```python
    @router.post("/queue", status_code=202)
    async def post_queue(request: QueueRequest) -> dict:
        valid_urls, invalid_lines = parse_url_list(request.urls_text)
        state.referer = request.referer

        previously_downloaded_urls = history_store.was_previously_downloaded(valid_urls)
        duplicate_urls_in_batch = [u for u in valid_urls if u in previously_downloaded_urls]

        skip_duplicates_setting = settings_store.get()["skip_duplicates"]
        skipped_duplicate_urls: list[str] = []

        if skip_duplicates_setting and duplicate_urls_in_batch:
            skipped_duplicate_urls = duplicate_urls_in_batch
            valid_urls = [u for u in valid_urls if u not in previously_downloaded_urls]
            previously_downloaded_urls = set()
        elif duplicate_urls_in_batch and request.duplicate_action is None:
            return {
                "entries": [],
                "invalid_lines": invalid_lines,
                "skipped_duplicate_urls": [],
                "skipped_inflight_urls": [],
                "needs_confirmation": True,
                "duplicate_urls": duplicate_urls_in_batch,
            }
        elif duplicate_urls_in_batch and request.duplicate_action == "skip_duplicates":
            skipped_duplicate_urls = duplicate_urls_in_batch
            valid_urls = [u for u in valid_urls if u not in previously_downloaded_urls]
            previously_downloaded_urls = set()
        # duplicate_action == "queue_all" (or no duplicates in this batch): fall through
        # and queue valid_urls as-is, keeping previously_downloaded_urls for the badge flag.

        resolved_folder = resolve_output_folder(request.output_folder, request.subfolder)
        Path(resolved_folder).mkdir(parents=True, exist_ok=True)

        batch_id = uuid.uuid4().hex if valid_urls else None
        entries = queue_manager.add_entries(
            valid_urls,
            batch_id=batch_id,
            output_folder=resolved_folder,
            previously_downloaded_urls=previously_downloaded_urls,
        )
        created_urls = {entry.url for entry in entries}
        skipped_inflight_urls = [u for u in valid_urls if u not in created_urls]
        if entries:
            track_task(
                asyncio.create_task(
                    orchestrator.download_all(
                        [entry.id for entry in entries],
                        resolved_folder,
                        request.referer,
                    )
                )
            )
        return {
            "entries": [entry.to_dict() for entry in entries],
            "invalid_lines": invalid_lines,
            "skipped_duplicate_urls": skipped_duplicate_urls,
            "skipped_inflight_urls": skipped_inflight_urls,
            "needs_confirmation": False,
            "duplicate_urls": [],
        }
```

Note `Path(resolved_folder).mkdir(...)` now runs *after* the confirmation gate — this is intentional so no filesystem side effect happens before the user has confirmed. It still runs before entries are created in every path that actually queues, exactly as before.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_queue_routes.py -v`
Expected: PASS — all tests in the file green, including the 4 new ones and the updated `test_post_queue_skips_duplicates_when_setting_enabled`.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS — no other test file references `/queue`'s old immediate-duplicate-queuing behavior (confirmed by reading `backend/tests/test_downloader.py`, `test_history_routes.py`, `test_history_store.py`, `test_queue_manager.py`, `test_ws_manager.py` — none post to `/queue`), so this should be a clean pass.

- [ ] **Step 6: Commit**

```bash
git add backend/queue_routes.py backend/tests/test_queue_routes.py
git commit -m "feat: require confirmation before queuing previously-downloaded URLs"
```

---

### Task 2: Frontend — confirmation modal markup

**Files:**
- Modify: `frontend/index.html:201-203`

**Interfaces:**
- Produces: DOM element ids `duplicate-confirm-overlay`, `duplicate-confirm-list`, `duplicate-confirm-skip`, `duplicate-confirm-continue` — Task 4's `renderer.js` queries these by id.

- [ ] **Step 1: Add the modal markup**

In `frontend/index.html`, insert the block below between the closing `</div>` of `#view-extension` (line 201) and the `<script type="module" src="theme.js"></script>` line (line 203):

```html
  <div id="duplicate-confirm-overlay" class="modal-overlay" hidden>
    <div class="modal panel" role="alertdialog" aria-modal="true" aria-labelledby="duplicate-confirm-title" aria-describedby="duplicate-confirm-message">
      <h3 id="duplicate-confirm-title" class="queue-title">Already downloaded</h3>
      <p id="duplicate-confirm-message" class="modal__message">
        These links were already downloaded successfully. Queue them again anyway, or skip them?
      </p>
      <ul id="duplicate-confirm-list" class="modal__list"></ul>
      <div class="modal__actions">
        <button id="duplicate-confirm-skip" class="btn btn--ghost" type="button">Skip duplicates</button>
        <button id="duplicate-confirm-continue" class="btn btn--primary" type="button">Queue anyway</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Verify markup is well-formed**

Run: `node -e "require('fs').readFileSync('frontend/index.html','utf8')" && echo OK`
Expected: `OK` (sanity check the file still reads cleanly; this is not an HTML validator, just confirms no accidental corruption from the edit).

Then visually confirm in an editor that the new `<div id="duplicate-confirm-overlay">` block sits inside `<body>`, after `#view-extension`'s closing tag and before the `<script>` block, with matching open/close tags.

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add duplicate-confirmation modal markup"
```

---

### Task 3: Frontend — modal styling

**Files:**
- Modify: `frontend/styles.css:929-939` (insert after), `frontend/styles.css:1045-1058` (reduced-motion block)

**Interfaces:**
- Consumes: existing design tokens (`--glass-bg`, `--glass-border`, `--glass-shadow`, `--radius-*`, `--ease`, `--text-dim`, `--input-bg`, `--font-mono`) and the `.panel` / `.btn` / `.btn--ghost` / `.btn--primary` classes already defined earlier in the file.
- Produces: `.modal-overlay`, `.modal`, `.modal__message`, `.modal__list`, `.modal__actions` classes consumed by Task 2's markup.

- [ ] **Step 1: Add modal styles**

In `frontend/styles.css`, insert the block below immediately after the `.queue-row__duplicate-badge` rule (ends at line 939) and before the `.toast-container` rule (starts at line 941):

```css
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  animation: modal-overlay-in 180ms var(--ease);
}

.modal-overlay[hidden] {
  display: none;
}

@keyframes modal-overlay-in {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.modal {
  width: 100%;
  max-width: 440px;
  max-height: min(560px, 80vh);
  display: flex;
  flex-direction: column;
  margin: 0;
  animation: modal-in 220ms var(--ease);
}

@keyframes modal-in {
  from {
    opacity: 0;
    transform: translateY(10px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.modal__message {
  color: var(--text-dim);
  font-size: 13px;
  line-height: 1.5;
  margin: 0 0 14px;
}

.modal__list {
  list-style: none;
  margin: 0 0 20px;
  padding: 0;
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.modal__list li {
  background: var(--input-bg);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  overflow-wrap: anywhere;
}

.modal__actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.modal__actions .btn {
  width: auto;
  margin-top: 0;
}
```

- [ ] **Step 2: Disable modal animations under reduced motion**

In `frontend/styles.css`, find the `@media (prefers-reduced-motion: reduce)` block (currently lines 1045-1058) and add `.modal-overlay` and `.modal` to the comma-separated selector list that sets `animation: none;`:

```css
@media (prefers-reduced-motion: reduce) {
  .ripple,
  .queue-row--enter,
  .queue-row--leaving,
  .view--enter,
  .toast,
  .toast--leaving,
  .modal-overlay,
  .modal,
  .queue-row--downloading .progress-fill::after {
    animation: none;
  }
  .tab-indicator {
    transition: none;
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/styles.css
git commit -m "style: add duplicate-confirmation modal styling"
```

---

### Task 4: Frontend — wire the confirmation flow into the queue submission

**Files:**
- Modify: `frontend/renderer.js:192-240`

**Interfaces:**
- Consumes: `duplicate-confirm-overlay` / `duplicate-confirm-list` / `duplicate-confirm-skip` / `duplicate-confirm-continue` DOM ids from Task 2; `body.needs_confirmation` / `body.duplicate_urls` from Task 1's `/queue` response; `showToast(message, type)` from `./toast.js` (already imported at the top of the file).
- Produces: no new exports — this is the top-level click handler for `startBtn`.

- [ ] **Step 1: Add the modal-driving helpers**

In `frontend/renderer.js`, insert the block below immediately after the `browseBtn.addEventListener(...)` block (ends at line 197) and before the existing `startBtn.addEventListener("click", ...)` block:

```js
const duplicateOverlay = document.getElementById("duplicate-confirm-overlay");
const duplicateList = document.getElementById("duplicate-confirm-list");
const duplicateSkipBtn = document.getElementById("duplicate-confirm-skip");
const duplicateContinueBtn = document.getElementById("duplicate-confirm-continue");

function confirmDuplicates(duplicateUrls) {
  return new Promise((resolve) => {
    duplicateList.innerHTML = "";
    duplicateUrls.forEach((url) => {
      const li = document.createElement("li");
      li.textContent = url;
      duplicateList.appendChild(li);
    });
    duplicateOverlay.hidden = false;
    duplicateContinueBtn.focus();

    function cleanup(decision) {
      duplicateOverlay.hidden = true;
      duplicateSkipBtn.removeEventListener("click", onSkip);
      duplicateContinueBtn.removeEventListener("click", onContinue);
      duplicateOverlay.removeEventListener("click", onBackdropClick);
      document.removeEventListener("keydown", onKeydown);
      resolve(decision);
    }
    function onSkip() {
      cleanup("skip_duplicates");
    }
    function onContinue() {
      cleanup("queue_all");
    }
    function onBackdropClick(event) {
      if (event.target === duplicateOverlay) cleanup(null);
    }
    function onKeydown(event) {
      if (event.key === "Escape") cleanup(null);
    }

    duplicateSkipBtn.addEventListener("click", onSkip);
    duplicateContinueBtn.addEventListener("click", onContinue);
    duplicateOverlay.addEventListener("click", onBackdropClick);
    document.addEventListener("keydown", onKeydown);
  });
}

async function postQueue(payload) {
  const response = await fetch(`${API_BASE}/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json();
}
```

- [ ] **Step 2: Replace the `startBtn` click handler**

Replace the existing `startBtn.addEventListener("click", ...)` block (lines 199-240) with:

```js
startBtn.addEventListener("click", async () => {
  if (!outputFolderInput.value) {
    showToast("Choose an output folder first.", "error");
    return;
  }
  startBtn.disabled = true;
  invalidLinesEl.hidden = true;

  const payload = {
    urls_text: urlsInput.value,
    output_folder: outputFolderInput.value,
    referer: refererInput.value || null,
    subfolder: courseFolderInput.value || null,
  };

  try {
    let body = await postQueue(payload);

    if (body.invalid_lines && body.invalid_lines.length > 0) {
      invalidLinesEl.hidden = false;
      invalidLinesEl.textContent = `Skipped invalid lines:\n${body.invalid_lines.join("\n")}`;
      showToast(`Skipped ${body.invalid_lines.length} invalid line${body.invalid_lines.length === 1 ? "" : "s"}`, "warning");
    }

    if (body.needs_confirmation) {
      const decision = await confirmDuplicates(body.duplicate_urls || []);
      if (!decision) {
        return;
      }
      body = await postQueue({ ...payload, duplicate_action: decision });
    }

    if (body.entries.length) {
      showToast(`Added ${body.entries.length} link${body.entries.length === 1 ? "" : "s"} to the queue`, "success");
    }

    body.entries.forEach(renderRow);
    urlsInput.value = "";
    renderGutter();
  } catch (error) {
    invalidLinesEl.hidden = false;
    invalidLinesEl.textContent = "Failed to reach the backend: " + error.message;
    showToast("Failed to reach the backend: " + error.message, "error");
  } finally {
    startBtn.disabled = false;
  }
});
```

Note the `return` inside the `if (!decision)` branch still runs the `finally` block (re-enabling `startBtn`) because it's inside the same `try`. `urlsInput.value` is deliberately left untouched on cancel so the user's pasted links aren't lost.

- [ ] **Step 3: Manually verify in the running app**

Run: `npm start` from the repository root (starts the Electron app, which launches the Python backend).

Verify:
1. Paste a URL that has never been downloaded, click **Start Download** → queues immediately, no modal. (Zero-duplicate path.)
2. In Settings, ensure "Skip already-downloaded links automatically" is OFF. Download a URL to completion (or seed one via the History view), then paste that same URL again and click **Start Download** → the modal appears listing that URL, nothing is added to the Queue list yet.
3. Click **Queue anyway** → the entry appears in the Queue list with the "Already downloaded" badge.
4. Repeat with a duplicate + a new URL together, click **Skip duplicates** this time → only the new URL is queued.
5. Open the modal again and click the backdrop, then reopen and press `Escape` → both close the modal without queuing anything and without clearing the textarea.
6. Turn "Skip already-downloaded links automatically" ON in Settings, resubmit a duplicate URL → it's silently skipped exactly as before, no modal appears.
7. Toggle light/dark theme while the modal is open to confirm it reads correctly in both.

If the environment cannot render the Electron UI (headless), state explicitly that this manual UI verification step was skipped and why, rather than claiming it passed.

- [ ] **Step 4: Commit**

```bash
git add frontend/renderer.js
git commit -m "feat: prompt for confirmation before queuing duplicate downloads"
```

---

## Self-Review Notes

- **Spec coverage:** Objective (confirm-before-queue) → Task 1 (backend gate) + Task 4 (frontend wiring). Zero-duplicate no-prompt → Task 1 Step 1 `test_post_queue_no_confirmation_needed_when_no_duplicates` + Task 4 manual check 1. Styled modal reusing `.panel`/`.btn` → Task 2 + Task 3. `skip_duplicates` ON unchanged → Task 1 Step 1 updated assertion + Task 4 manual check 6. Non-breaking response contract → Task 1 Step 3 (`needs_confirmation`/`duplicate_urls` are additive). pytest passes → Task 1 Steps 4-5.
- **Placeholder scan:** No TBD/TODO markers; every step has literal code or exact manual verification instructions.
- **Type consistency:** `duplicate_action` values (`"queue_all"` / `"skip_duplicates"`) match exactly between the Pydantic `Literal` in Task 1, the `confirmDuplicates()` resolve values in Task 4, and the second `postQueue()` call's payload in Task 4. DOM ids match exactly between Task 2's markup and Task 4's `getElementById` calls. Response field names (`needs_confirmation`, `duplicate_urls`) match exactly between Task 1's return statements and Task 4's `body.needs_confirmation` / `body.duplicate_urls` reads.
