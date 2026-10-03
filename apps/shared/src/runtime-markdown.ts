/** Display-only section picker. The backend independently validates exact old-byte digests. */
export interface MarkdownSection { anchor: string; content: string }

export function markdownSections(content: string): MarkdownSection[] {
  const starts: { anchor: string; level: number; offset: number }[] = []
  let offset = 0
  let fence: string | null = null

  for (const line of content.match(/[^\n]*\n|[^\n]+$/g) ?? []) {
    const stripped = line.replace(/[\r\n]+$/, '')
    const marker = stripped.match(/^[ \t]{0,3}(`{3,}|~{3,})/)

    if (marker) {
      if (fence === null) {fence = marker[1]}
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) {fence = null}
    } else if (fence === null) {
      const heading = stripped.match(/^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/)

      if (heading) {starts.push({ anchor: heading[2].trim(), level: heading[1].length, offset })}
    }

    offset += line.length
  }

  const counts = new Map<string, number>()
  starts.forEach(item => counts.set(item.anchor, (counts.get(item.anchor) ?? 0) + 1))

  return starts.flatMap((item, index) => {
    if (counts.get(item.anchor) !== 1) {return []}
    const end = starts.slice(index + 1).find(next => next.level <= item.level)?.offset ?? content.length

    return [{ anchor: item.anchor, content: content.slice(item.offset, end) }]
  })
}

export async function markdownDigest(content: string): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(content)))).map(value => value.toString(16).padStart(2, '0')).join('')
}
