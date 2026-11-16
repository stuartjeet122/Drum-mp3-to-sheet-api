import { useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, ChevronLeft, ChevronRight, ExternalLink, FileWarning, LoaderCircle, Minus, Plus } from 'lucide-react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { exportUrl } from './api';

export default function PdfScore({ jobId }: { jobId: string }) {
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [error, setError] = useState('');
  const [rendering, setRendering] = useState(true);
  const [width, setWidth] = useState(700);
  const container = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const url = exportUrl(jobId, 'pdf');
  useEffect(() => {
    const observer = new ResizeObserver(entries => setWidth(entries[0].contentRect.width));
    if (container.current) observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let cancelled = false;
    let loaded: PDFDocumentProxy | null = null;
    const abort = new AbortController();
    setDocument(null); setPage(1); setError(''); setRendering(true);
    async function load() {
      const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
      const worker = await import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url');
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
      const response = await fetch(url, { signal: abort.signal });
      if (!response.ok) throw new Error('The score could not be downloaded. Try opening the PDF directly.');
      const bytes = new Uint8Array(await response.arrayBuffer());
      loaded = await pdfjs.getDocument({ data: bytes }).promise;
      if (!cancelled) setDocument(loaded);
      else loaded.destroy();
    }
    load().catch(error => { if (!cancelled) { setError(error instanceof Error ? error.message : 'The score could not be opened.'); setRendering(false); } });
    return () => { cancelled = true; abort.abort(); loaded?.destroy(); };
  }, [url]);
  useEffect(() => {
    if (!document || !canvas.current) return;
    let cancelled = false;
    let task: { cancel: () => void } | undefined;
    setRendering(true);
    document.getPage(page).then(pdfPage => {
      if (cancelled || !canvas.current) return;
      const base = pdfPage.getViewport({ scale: 1 });
      const scale = Math.max(0.2, (width - 48) / base.width) * zoom;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const viewport = pdfPage.getViewport({ scale: scale * ratio });
      const output = canvas.current;
      output.width = viewport.width; output.height = viewport.height;
      output.style.width = `${viewport.width / ratio}px`; output.style.height = `${viewport.height / ratio}px`;
      const renderTask = pdfPage.render({ canvas: output, viewport });
      task = renderTask;
      return renderTask.promise;
    }).then(() => { if (!cancelled) setRendering(false); }).catch(error => {
      if (!cancelled) { setError(error.message || 'This page could not be rendered.'); setRendering(false); }
    });
    return () => { cancelled = true; task?.cancel(); };
  }, [document, page, zoom, width]);

  return <div className="pdf-viewer">
    <div className="pdf-toolbar"><div className="page-navigation"><button className="icon-button" title="Previous page" aria-label="Previous score page" disabled={page <= 1 || !document} onClick={() => setPage(page - 1)}><ChevronLeft size={17} /></button><span>Page <strong>{page}</strong> / {document?.numPages || '-'}</span><button className="icon-button" title="Next page" aria-label="Next score page" disabled={!document || page >= document.numPages} onClick={() => setPage(page + 1)}><ChevronRight size={17} /></button></div><div className="pdf-zoom"><button className="icon-button" title="Zoom out" aria-label="Zoom score out" disabled={zoom <= 0.75} onClick={() => setZoom(Math.max(0.75, zoom - 0.25))}><Minus size={16} /></button><span>{Math.round(zoom * 100)}%</span><button className="icon-button" title="Zoom in" aria-label="Zoom score in" disabled={zoom >= 2} onClick={() => setZoom(Math.min(2, zoom + 0.25))}><Plus size={16} /></button><span className="toolbar-divider" /><a className="icon-button" href={url} target="_blank" rel="noopener noreferrer" title="Open PDF" aria-label="Open PDF in a new tab"><ExternalLink size={16} /></a><a className="icon-button" href={url} download title="Download PDF" aria-label="Download PDF score"><ArrowDownToLine size={17} /></a></div></div>
    <div className="pdf-paper-area" ref={container}>{rendering && !error && <div className="pdf-loading"><LoaderCircle className="spin" size={22} /><span>Opening your score</span></div>}{error ? <div className="pdf-error" role="alert"><FileWarning size={27} /><p>{error}</p><a href={url} className="text-link" target="_blank" rel="noopener noreferrer">Open PDF <ExternalLink size={13} /></a></div> : <canvas ref={canvas} role="img" aria-label={`Drum sheet music, page ${page}`} style={{ visibility: document ? 'visible' : 'hidden' }} />}</div>
  </div>;
}