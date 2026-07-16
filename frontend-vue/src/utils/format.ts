const ruNumber = new Intl.NumberFormat('ru-RU')

const ruDecimal = new Intl.NumberFormat('ru-RU', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

const ruInteger = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 0,
})

export function formatAmount(v: number): string {
  return ruDecimal.format(v)
}

export function formatInt(v: number): string {
  return ruInteger.format(Math.round(v))
}

/** A 0..1 fraction as a 1-dp percentage, e.g. 0.7 → "70.0%". */
export function formatPercent(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

export function formatNumber(v: number): string {
  return ruNumber.format(v)
}

export { formatDateTime as formatDate, formatDateOnly } from './datetime'

export function shortenUuid(uuid: string | null | undefined, length = 8): string {
  if (!uuid) return '—'
  return uuid.slice(0, length)
}

export function truncate(text: string | null | undefined, max = 80): string {
  if (!text) return ''
  const s = String(text)
  return s.length > max ? s.slice(0, max) + '…' : s
}

export function copyToClipboard(text: string): Promise<void> {
  return navigator.clipboard.writeText(text)
}

export function syntaxHighlightJson(jsonStr: string): string {
  if (jsonStr === '—') return jsonStr
  
  let json = jsonStr.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  
  return json.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
    let cls = 'text-[#3b82f6]' // number (blue)
    if (/^"/.test(match)) {
      if (/:$/.test(match)) {
        cls = 'text-text-main font-semibold' // key
      } else {
        cls = 'text-status-success' // string
      }
    } else if (/true|false/.test(match)) {
      cls = 'text-status-warning' // boolean
    } else if (/null/.test(match)) {
      cls = 'text-text-muted' // null
    }
    return '<span class="' + cls + '">' + match + '</span>'
  })
}

export function formatJson(obj: Record<string, any> | null | undefined): string {
  if (!obj) return '—'
  let str = ''
  try { str = JSON.stringify(obj, null, 2) } catch { str = String(obj) }
  return syntaxHighlightJson(str)
}

export function formatBody(body: string | null | undefined): string {
  if (!body) return '—'
  let str = ''
  try { str = JSON.stringify(JSON.parse(body), null, 2) } catch { str = body }
  return syntaxHighlightJson(str)
}
