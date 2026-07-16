import api from '@/api/client'

/**
 * Synchronously open a blank tab so the browser counts it as a direct
 * user-gesture action. Must be called INSIDE the same event handler tick
 * as the click — any `await` between the click and ``window.open`` and
 * iOS Safari + most mobile browsers silently block the popup.
 *
 * Returns the window handle (to be navigated later) or ``null`` when
 * the browser blocked the call entirely (popup blocker on a programmatic
 * call detected as suspicious). Callers should pass the handle to
 * ``downloadReceipt`` so the fetched blob is loaded into THIS window,
 * not via a second ``window.open`` that happens after the await.
 */
export function preOpenReceiptTab(): Window | null {
  // about:blank is the canonical placeholder — iOS Safari treats this as
  // a user-initiated navigation; any subsequent .location.href update is
  // allowed even after async work.
  return window.open('about:blank', '_blank')
}

/**
 * Downloads a receipt file using the authenticated API client and opens it
 * in a new tab. Works around the limitation that ``window.open()`` can't
 * pass Authorization headers — we fetch via the api client, materialise
 * a blob URL, and either point a pre-opened tab at it or trigger a
 * download.
 *
 * On mobile (iOS Safari / Android Chrome) callers MUST pre-open a tab
 * synchronously via ``preOpenReceiptTab()`` and pass the handle here,
 * otherwise the post-await ``window.open`` is treated as a programmatic
 * popup and silently blocked. On desktop the pre-open is harmless.
 *
 * Throws an Error whose ``message`` equals the server-side ``detail`` when
 * the response is a JSON error blob, so callers can surface a useful toast.
 */
export async function downloadReceipt(
  url: string,
  preOpened: Window | null = null,
): Promise<void> {
  let response
  try {
    response = await api.get(url, { responseType: 'blob' })
  } catch (err: any) {
    // The pre-opened tab is useless once the request failed — close it
    // so the user isn't stuck looking at about:blank.
    if (preOpened && !preOpened.closed) preOpened.close()
    const blob = err?.response?.data
    let detail: string | null = null
    if (blob instanceof Blob && blob.type.includes('json')) {
      try {
        const text = await blob.text()
        const parsed = JSON.parse(text)?.detail
        if (typeof parsed === 'string') detail = parsed
      } catch {
        // Not valid JSON — fall through and rethrow the original error below.
      }
    }
    if (detail !== null) {
      const out = new Error(detail)
      ;(out as any).response = err.response
      throw out
    }
    throw err
  }

  const blob: Blob = response.data
  const contentDisposition: string = response.headers['content-disposition'] ?? ''
  const contentType: string = response.headers['content-type'] ?? 'application/octet-stream'

  const filenameMatch = contentDisposition.match(/filename="?([^";\n]+)"?/)
  const filename = filenameMatch?.[1] ?? 'receipt'

  const objectUrl = URL.createObjectURL(new Blob([blob], { type: contentType }))

  // Only render a strict allowlist of NON-executable types inline; anything
  // else (esp. image/svg+xml or text/html — markup that can run script in our
  // origin) falls through to the download path, never rendered. The allowlist
  // IS the security boundary. NOTE: do NOT gate on Content-Disposition — the
  // backend serves every receipt via FileResponse, which defaults to
  // `attachment`, so honouring that header would force-download all receipts
  // and defeat inline preview across the app.
  const INLINE_VIEWABLE = new Set(['image/jpeg', 'image/png', 'image/webp', 'application/pdf'])
  const contentTypeBase = contentType.split(';')[0].trim().toLowerCase()
  const isViewable = INLINE_VIEWABLE.has(contentTypeBase)
  if (isViewable) {
    if (preOpened && !preOpened.closed) {
      // Mobile-safe path: navigate the tab we opened synchronously on click.
      preOpened.location.href = objectUrl
    } else {
      // Desktop fallback (or when preOpen failed — popup blocker still off).
      const opened = window.open(objectUrl, '_blank')
      if (!opened) {
        // Browsers that *did* block the post-await window.open: degrade to
        // a download link in the same tab so the user still gets the file.
        const a = document.createElement('a')
        a.href = objectUrl
        a.download = filename
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
      }
    }
  } else {
    // Non-viewable types always download — preOpen wasn't needed; close it.
    if (preOpened && !preOpened.closed) preOpened.close()
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
  }

  setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}
