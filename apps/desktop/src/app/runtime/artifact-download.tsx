import { downloadRuntimeArtifact } from '@hermes/shared/runtime-artifacts'
import type { RuntimeRequest } from '@hermes/shared/runtime-control'
import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

import { useRuntimeUiText } from './runtime-ui-copy'

export function ArtifactDownload({ request, sessionId, connected }: { request: RuntimeRequest; sessionId: string; connected: boolean }) {
  const rt = useRuntimeUiText()
  const [project, setProject] = useState('')
  const [artifact, setArtifact] = useState('')
  const [version, setVersion] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ url: string; name: string; size: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [request, sessionId])
  useEffect(() => () => { if (result) {URL.revokeObjectURL(result.url)} }, [result])

  async function prepareDownload() {
    if (busy || !connected) {return}
    const controller = new AbortController()
    abort.current = controller
    setBusy(true); setError(null); setResult(null)

    try {
      const output = await downloadRuntimeArtifact(request, sessionId, project.trim(), artifact.trim(), Number(version), controller.signal)
      controller.signal.throwIfAborted()
      const url = URL.createObjectURL(new Blob([new Uint8Array(output.bytes).buffer], { type: 'application/octet-stream' }))
      setResult({ url, name: `artifact-${output.metadata.version}`, size: output.bytes.length })
    } catch (caught) {
      if (!controller.signal.aborted) {setError(caught instanceof Error ? caught.message : rt("Download unavailable"))}
    } finally { if (abort.current === controller) {setBusy(false)} }
  }

  return <section className="grid gap-2">
    <h4 className="text-sm font-medium">{rt("Download an exact artifact version")}</h4>
    <form className="grid gap-2" onSubmit={event => { event.preventDefault(); void prepareDownload() }}>
      <label>{rt("Project ID")}<Input disabled={busy} onChange={event => { setProject(event.target.value); setResult(null) }} value={project} /></label>
      <label>{rt("Artifact ID")}<Input disabled={busy} onChange={event => { setArtifact(event.target.value); setResult(null) }} value={artifact} /></label>
      <label>{rt("Version")}<Input disabled={busy} min={1} onChange={event => { setVersion(event.target.value); setResult(null) }} type="number" value={version} /></label>
      <div className="flex gap-2"><Button disabled={busy || !connected || !project || !artifact || !version} size="xs" type="submit" variant="secondary">{rt("Verify complete download")}</Button>
        {busy && <Button onClick={() => { abort.current?.abort(); setBusy(false) }} size="xs" type="button" variant="text">{rt("Cancel download")}</Button>}</div>
    </form>
    {error && <p role="alert">{error}</p>}
    {result && <a download={result.name} href={result.url}>{rt("Save verified file (")}{result.size}{" " + rt("bytes)")}</a>}
    <p className="text-xs text-muted-foreground">{rt("Bytes and version are verified before saving. Active HTML is never previewed. Downloading does not share or publish the artifact.")}</p>
  </section>
}
